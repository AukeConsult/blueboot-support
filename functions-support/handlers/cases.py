"""handlers/cases.py — Support case CRUD and reply sending."""
from __future__ import annotations

from datetime import datetime, timezone

from flask import Blueprint, g, jsonify, request

from handlers.shared import _err, _ok, _get_db

bp = Blueprint("cases", __name__)


def _db():
    return getattr(g, "db", None) or _get_db()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── GET /api/support/cases ────────────────────────────────────────────────────

@bp.route("/api/support/cases", methods=["GET"])
def list_cases():
    """List all support cases with optional status/account filters."""
    try:
        db     = _db()
        status = request.args.get("status")   # open|in_progress|resolved|closed
        account = request.args.get("account") # mail account email
        search  = (request.args.get("q") or "").strip().lower()
        limit   = min(int(request.args.get("limit", 100)), 500)

        col = db.collection("support_cases")
        if status:
            col = col.where("status", "==", status)
        if account:
            col = col.where("mail_account", "==", account)

        docs = list(col.order_by("updated_at", direction="DESCENDING").limit(limit).stream())
        cases = []
        for d in docs:
            c = d.to_dict() or {}
            if search and search not in (c.get("subject") or "").lower() \
                      and search not in (c.get("from_email") or "").lower():
                continue
            cases.append({**c, "id": d.id})

        return jsonify({"cases": cases, "count": len(cases)})
    except Exception as exc:
        return _err(str(exc), 500)


# ── GET /api/support/cases/<case_id> ─────────────────────────────────────────

@bp.route("/api/support/cases/<case_id>", methods=["GET"])
def get_case(case_id: str):
    """Get a single case plus its full message thread."""
    try:
        db  = _db()
        ref = db.collection("support_cases").document(str(case_id))
        doc = ref.get()
        if not doc.exists:
            return _err(f"Case {case_id} not found", 404)

        case = {**doc.to_dict(), "id": doc.id}

        msgs = list(
            ref.collection("messages")
               .order_by("timestamp")
               .stream()
        )
        case["messages"] = [{**m.to_dict(), "id": m.id} for m in msgs]
        return jsonify(case)
    except Exception as exc:
        return _err(str(exc), 500)


# ── PATCH /api/support/cases/<case_id> ───────────────────────────────────────

@bp.route("/api/support/cases/<case_id>", methods=["PATCH"])
def update_case(case_id: str):
    """Update status and/or assigned_to on a case."""
    try:
        db   = _db()
        body = request.get_json(silent=True) or {}
        ref  = db.collection("support_cases").document(str(case_id))
        doc  = ref.get()
        if not doc.exists:
            return _err(f"Case {case_id} not found", 404)

        allowed = {"status", "assigned_to", "note"}
        update  = {k: v for k, v in body.items() if k in allowed}
        if not update:
            return _err("Nothing to update", 400)

        update["updated_at"] = _now_iso()
        ref.update(update)
        return _ok("Updated")
    except Exception as exc:
        return _err(str(exc), 500)


# ── POST /api/support/cases/<case_id>/reply ───────────────────────────────────

@bp.route("/api/support/cases/<case_id>/reply", methods=["POST"])
def send_reply(case_id: str):
    """Send a manual reply email from the agent and log it to the case thread."""
    try:
        db   = _db()
        body = request.get_json(silent=True) or {}
        text = (body.get("body") or "").strip()
        if not text:
            return _err("Reply body is required", 400)

        ref = db.collection("support_cases").document(str(case_id))
        doc = ref.get()
        if not doc.exists:
            return _err(f"Case {case_id} not found", 404)

        case         = doc.to_dict() or {}
        to_email     = case.get("from_email", "")
        mail_account = case.get("mail_account", "")
        subject      = f"RE: Case {case_id}: {case.get('subject', '')}"

        from support_mail.reply_sender import send_reply_email
        send_reply_email(db, mail_account, to_email, subject, text)

        now = _now_iso()
        msg_ref = ref.collection("messages").document()
        msg_ref.set({
            "direction":    "OUT",
            "from_email":   mail_account,
            "to_email":     to_email,
            "subject":      subject,
            "body":         text,
            "is_auto_reply": False,
            "sent_by":      getattr(g, "user_email", "agent"),
            "timestamp":    now,
        })

        ref.update({
            "updated_at":              now,
            "last_message_at":         now,
            "last_message_direction":  "OUT",
        })

        return _ok("Reply sent")
    except Exception as exc:
        return _err(str(exc), 500)


# ── GET /api/support/stats ────────────────────────────────────────────────────

@bp.route("/api/support/stats", methods=["GET"])
def get_stats():
    """Quick counts for the dashboard metric cards."""
    try:
        db   = _db()
        docs = list(db.collection("support_cases").stream())
        counts = {"open": 0, "in_progress": 0, "resolved": 0, "closed": 0, "total": 0}
        for d in docs:
            st = (d.to_dict() or {}).get("status", "open")
            counts["total"] += 1
            if st in counts:
                counts[st] += 1
        return jsonify(counts)
    except Exception as exc:
        return _err(str(exc), 500)
