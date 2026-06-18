"""handlers/channels.py — list and manage configured mail-account channels.

A "channel" is just a mailbox configured under
settings/mail_accounts/accounts/{email}. Support is the main channel (the
hub every other channel can transfer cases into); every other mailbox is a
secondary channel. Label / main-channel resolution lives in
support_mail.mail_checker (_board_label / _is_main_channel) so the email
ack/reply/transfer code paths and this listing endpoint always agree.

Writes (POST/PATCH/DELETE) store mailbox IMAP/SMTP credentials, so they are
restricted to role=admin explicitly (see _require_admin) regardless of the
blueprint's default minimum role in main.py.
"""
from __future__ import annotations

from datetime import datetime, timezone

from flask import Blueprint, g, jsonify, request

from handlers.shared import _err, _ok, _get_db, _get_user_role

bp = Blueprint("channels", __name__)


def _db():
    return getattr(g, "db", None) or _get_db()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _require_admin(db):
    """Return an error response if the current user is not an admin, else None."""
    email = getattr(g, "user_email", "")
    role  = getattr(g, "user_role", None) or _get_user_role(db, email)
    if role != "admin":
        return _err("Requires role: admin", 403)
    return None


def _accounts_col(db):
    return db.collection("settings").document("mail_accounts").collection("accounts")


# ── Per-channel unread / overdue counts ───────────────────────────────────────

_ACTIVE_STATUSES = ("new", "open", "follow_up")


def _channel_counts(db) -> dict:
    """One collection_group query, grouped by mail_account in Python.

    Mirrors the unread/overdue definitions used on the board
    (handlers/cases.py get_stats, public/board.html isUnread/isOverdue):
      - unread_count:  active case whose last message direction was IN
      - overdue_count: active case whose sla_deadline has passed
    """
    counts: dict[str, dict[str, int]] = {}
    now_iso = _now_iso()
    for d in db.collection_group("cases").stream():
        c = d.to_dict() or {}
        account = c.get("mail_account")
        if not account:
            continue
        status = c.get("status", "new")
        if status not in _ACTIVE_STATUSES:
            continue
        bucket = counts.setdefault(account, {"unread_count": 0, "overdue_count": 0})
        if c.get("last_history_direction") == "IN":
            bucket["unread_count"] += 1
        sla = c.get("sla_deadline") or ""
        if sla and sla < now_iso:
            bucket["overdue_count"] += 1
    return counts


# ── GET /api/support/channels ─────────────────────────────────────────────────

@bp.route("/api/support/channels", methods=["GET"])
def list_channels():
    """List every configured mailbox as a channel, main channel first."""
    try:
        db = _db()
        from support_mail.mail_checker import _board_label, _is_main_channel

        docs = list(_accounts_col(db).stream())
        counts = _channel_counts(db)
        channels = []
        for d in docs:
            account = d.id
            bucket = counts.get(account, {})
            channels.append({
                "account":       account,
                "label":         _board_label(account, db),
                "is_main":       _is_main_channel(account, db),
                "unread_count":  bucket.get("unread_count", 0),
                "overdue_count": bucket.get("overdue_count", 0),
            })

        channels.sort(key=lambda c: (not c["is_main"], c["label"].lower()))
        return jsonify({"channels": channels})
    except Exception as exc:
        return _err(str(exc), 500)


# ── POST /api/support/channels — create a new channel (admin only) ───────────

@bp.route("/api/support/channels", methods=["POST"])
def create_channel():
    """Create a new mailbox/channel. Admin only."""
    try:
        db = _db()
        denied = _require_admin(db)
        if denied:
            return denied

        body    = request.get_json(silent=True) or {}
        account = (body.get("account") or "").strip().lower()
        if not account or "@" not in account:
            return _err("A valid account email is required", 400)

        ref = _accounts_col(db).document(account)
        if ref.get().exists:
            return _err(f"Channel {account} already exists", 400)

        required = ("username", "password", "imap_host", "smtp_host")
        missing  = [f for f in required if not body.get(f)]
        if missing:
            return _err(f"Missing required field(s): {', '.join(missing)}", 400)

        doc = {
            "username":     body.get("username"),
            "password":     body.get("password"),
            "imap_host":    body.get("imap_host"),
            "imap_port":    body.get("imap_port", 993),
            "smtp_host":    body.get("smtp_host"),
            "smtp_port":    body.get("smtp_port", 465),
            "ssl":          body.get("ssl", True),
            "display_name": body.get("display_name", ""),
            "label":        body.get("label", ""),
            "is_main":      bool(body.get("is_main", False)),
            "created_at":   _now_iso(),
            "created_by":   getattr(g, "user_email", "admin"),
        }
        ref.set(doc)
        return _ok(f"Channel {account} created", account=account)
    except Exception as exc:
        return _err(str(exc), 500)


# ── GET /api/support/channels/<account> — full config, admin only ───────────
# list_channels() exposes label/is_main/counts to every signed-in user; the
# raw IMAP/SMTP settings (everything except the password) are only needed by
# the admin edit form, so they're gated the same way as the write routes.

@bp.route("/api/support/channels/<account>", methods=["GET"])
def get_channel(account: str):
    """Return one channel's full config (password excluded). Admin only."""
    try:
        db = _db()
        denied = _require_admin(db)
        if denied:
            return denied

        account = account.strip().lower()
        ref = _accounts_col(db).document(account)
        snap = ref.get()
        if not snap.exists:
            return _err(f"Channel {account} not found", 404)

        doc = snap.to_dict() or {}
        doc.pop("password", None)
        doc["account"] = account
        return jsonify(doc)
    except Exception as exc:
        return _err(str(exc), 500)


# ── PATCH /api/support/channels/<account> — edit a channel (admin only) ──────

@bp.route("/api/support/channels/<account>", methods=["PATCH"])
def update_channel(account: str):
    """Update label / is_main / credentials for an existing channel. Admin only."""
    try:
        db = _db()
        denied = _require_admin(db)
        if denied:
            return denied

        account = account.strip().lower()
        ref = _accounts_col(db).document(account)
        if not ref.get().exists:
            return _err(f"Channel {account} not found", 404)

        body   = request.get_json(silent=True) or {}
        update = {}
        for field in ("username", "imap_host", "imap_port", "smtp_host", "smtp_port",
                      "ssl", "display_name", "label", "is_main"):
            if field in body:
                update[field] = body[field]
        # Only overwrite the password if a non-empty one was supplied —
        # leaving the field blank in the edit form keeps the existing one.
        if body.get("password"):
            update["password"] = body["password"]

        if not update:
            return _err("Nothing to update", 400)

        update["updated_at"] = _now_iso()
        update["updated_by"] = getattr(g, "user_email", "admin")
        ref.update(update)
        return _ok(f"Channel {account} updated", account=account)
    except Exception as exc:
        return _err(str(exc), 500)


# ── DELETE /api/support/channels/<account> — remove a channel (admin only) ───

@bp.route("/api/support/channels/<account>", methods=["DELETE"])
def delete_channel(account: str):
    """Remove a channel's mailbox config. Does not delete its existing cases —
    they remain accessible via case_detail.html / search, just stop being
    polled and disappear from the pill nav. Admin only."""
    try:
        db = _db()
        denied = _require_admin(db)
        if denied:
            return denied

        account = account.strip().lower()
        ref = _accounts_col(db).document(account)
        snap = ref.get()
        if not snap.exists:
            return _err(f"Channel {account} not found", 404)

        from support_mail.mail_checker import _is_main_channel
        if _is_main_channel(account, db):
            return _err("Cannot delete the main channel", 400)

        ref.delete()
        return _ok(f"Channel {account} deleted", account=account)
    except Exception as exc:
        return _err(str(exc), 500)
