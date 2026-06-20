"""handlers/cases.py — Support case CRUD, reply sending, and note adding.

Firestore structure:
  support_mail_accounts/{account}/cases/{case_id}/history/{msg}
  support_mail_accounts/{account}/cases/{case_id}/actions/{event}

Collection group queries let us list all cases across all accounts without
knowing which account each case belongs to.
"""
from __future__ import annotations

import csv
import io
from datetime import datetime, timezone

from flask import Blueprint, Response, g, jsonify, request

from handlers.shared import _err, _ok, _get_db

bp = Blueprint("cases", __name__)


def _db():
    return getattr(g, "db", None) or _get_db()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _log_action(case_ref, action_type: str, by: str = "system",
                from_val=None, to_val=None, note: str | None = None) -> None:
    case_ref.collection("actions").document().set({
        "type":       action_type,
        "by":         by,
        "at":         _now_iso(),
        "from_value": from_val,
        "to_value":   to_val,
        "note":       note,
    })


def _find_case_ref(db, case_id: str):
    """Find case reference using collection group query (works across all accounts)."""
    docs = list(
        db.collection_group("cases")
          .where("case_id", "==", int(case_id))
          .limit(1)
          .stream()
    )
    return docs[0].reference if docs else None


# ── GET /api/support/cases ────────────────────────────────────────────────────

@bp.route("/api/support/cases", methods=["GET"])
def list_cases():
    """List all cases across all mail accounts (collection group query).

    Paginated via ?cursor= + ?limit= instead of an offset — Firestore
    collection-group queries don't support offset paging at scale. ?cursor=
    is the `updated_at` value of the last row on the previous page; the
    response includes `next_cursor` (null once there are no more rows).
    """
    try:
        db       = _db()
        status   = request.args.get("status")
        account  = request.args.get("account")
        priority = request.args.get("priority")
        tag      = request.args.get("tags")
        search   = (request.args.get("q") or "").strip().lower()
        cursor   = request.args.get("cursor")
        limit    = min(int(request.args.get("limit", 100)), 500)

        query = db.collection_group("cases")
        if status:
            query = query.where("status", "==", status)
        if account:
            query = query.where("mail_account", "==", account)
        if priority:
            query = query.where("priority", "==", priority)
        if tag:
            query = query.where("tags", "array_contains", tag)

        query = query.order_by("updated_at", direction="DESCENDING")
        if cursor:
            query = query.start_after({"updated_at": cursor})
        docs = list(query.limit(limit + 1).stream())  # +1 to detect a next page

        has_more = len(docs) > limit
        docs     = docs[:limit]

        cases = []
        for d in docs:
            c = d.to_dict() or {}
            if search and search not in (c.get("subject") or "").lower() \
                      and search not in (c.get("from_email") or "").lower():
                continue
            cases.append({**c, "id": d.id})

        next_cursor = docs[-1].to_dict().get("updated_at") if (has_more and docs) else None
        return jsonify({"cases": cases, "count": len(cases), "next_cursor": next_cursor})
    except Exception as exc:
        return _err(str(exc), 500)


# ── GET /api/support/cases/export ────────────────────────────────────────────

_EXPORT_MAX_ROWS = 5000
_EXPORT_COLUMNS  = [
    "case_id", "board_no", "mail_account", "subject", "from_email", "from_name",
    "status", "priority", "assigned_to", "tags", "created_at", "updated_at", "sla_deadline",
]


@bp.route("/api/support/cases/export", methods=["GET"])
def export_cases():
    """Export cases matching the current filters as CSV — same filters as
    GET /api/support/cases (status, priority, account, q, tags), but no
    pagination; capped at _EXPORT_MAX_ROWS so a broad filter can't blow up
    memory or response size.
    """
    try:
        db       = _db()
        status   = request.args.get("status")
        account  = request.args.get("account")
        priority = request.args.get("priority")
        tag      = request.args.get("tags")
        search   = (request.args.get("q") or "").strip().lower()

        query = db.collection_group("cases")
        if status:
            query = query.where("status", "==", status)
        if account:
            query = query.where("mail_account", "==", account)
        if priority:
            query = query.where("priority", "==", priority)
        if tag:
            query = query.where("tags", "array_contains", tag)
        query = query.order_by("updated_at", direction="DESCENDING").limit(_EXPORT_MAX_ROWS)

        buf    = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(_EXPORT_COLUMNS)
        for d in query.stream():
            c = d.to_dict() or {}
            if search and search not in (c.get("subject") or "").lower() \
                      and search not in (c.get("from_email") or "").lower():
                continue
            writer.writerow([
                c.get("case_id", d.id),
                c.get("board_no", ""),
                c.get("mail_account", ""),
                c.get("subject", ""),
                c.get("from_email", ""),
                c.get("from_name", ""),
                c.get("status", ""),
                c.get("priority", ""),
                c.get("assigned_to", ""),
                ", ".join(c.get("tags") or []),
                c.get("created_at", ""),
                c.get("updated_at", ""),
                c.get("sla_deadline", ""),
            ])

        return Response(
            buf.getvalue(),
            mimetype="text/csv",
            headers={"Content-Disposition": "attachment; filename=support_cases_export.csv"},
        )
    except Exception as exc:
        return _err(str(exc), 500)


# ── GET /api/support/cases/<case_id> ─────────────────────────────────────────

@bp.route("/api/support/cases/<case_id>", methods=["GET"])
def get_case(case_id: str):
    """Return a case with a unified timeline (history + actions merged by time)."""
    try:
        db  = _db()
        ref = _find_case_ref(db, case_id)
        if not ref:
            return _err(f"Case {case_id} not found", 404)

        case = {**ref.get().to_dict(), "id": ref.id}

        history = [
            {**m.to_dict(), "id": m.id, "timeline_type": "message"}
            for m in ref.collection("history").order_by("timestamp").stream()
        ]
        actions = [
            {**a.to_dict(), "id": a.id, "timeline_type": "action"}
            for a in ref.collection("actions").order_by("at").stream()
        ]

        # Merge and sort by time
        def _time_key(item):
            return item.get("timestamp") or item.get("at") or ""

        case["timeline"] = sorted(history + actions, key=_time_key)
        return jsonify(case)
    except Exception as exc:
        return _err(str(exc), 500)


# ── Shared update-validation logic (single PATCH + bulk-update) ─────────────

_VALID_STATUSES = {"new", "not_interested", "follow_up", "resolved", "closed"}


def _apply_case_update(ref, body: dict, agent: str) -> dict:
    """Validate a status/assigned_to/priority/tags change and log it to the
    case's action history. Returns the dict of fields that actually changed
    (includes updated_at if non-empty) — caller decides whether/how to write
    it. Raises ValueError on an invalid status so single-update and
    bulk-update callers can each turn that into the right kind of response.
    """
    current = ref.get().to_dict() or {}
    update: dict = {}

    if "status" in body and body["status"] != current.get("status"):
        if body["status"] not in _VALID_STATUSES:
            raise ValueError(f"Invalid status. Choose from: {', '.join(sorted(_VALID_STATUSES))}")
        old = current.get("status", "")
        update["status"] = body["status"]
        _log_action(ref, "status_changed", by=agent, from_val=old, to_val=body["status"])

    if "assigned_to" in body and body["assigned_to"] != current.get("assigned_to"):
        old = current.get("assigned_to", "")
        update["assigned_to"] = body["assigned_to"]
        _log_action(ref, "assigned", by=agent, from_val=old, to_val=body["assigned_to"])

    if "priority" in body and body["priority"] != current.get("priority"):
        old = current.get("priority", "")
        update["priority"] = body["priority"]
        _log_action(ref, "priority_changed", by=agent, from_val=old, to_val=body["priority"])

    if "tags" in body and body["tags"] != current.get("tags"):
        update["tags"] = body["tags"]
        _log_action(ref, "tags_updated", by=agent, note=str(body["tags"]))

    if update:
        update["updated_at"] = _now_iso()
    return update


# ── PATCH /api/support/cases/<case_id> ───────────────────────────────────────

@bp.route("/api/support/cases/<case_id>", methods=["PATCH"])
def update_case(case_id: str):
    """Update status, assigned_to, priority, or tags. Logs change to actions."""
    try:
        db   = _db()
        body = request.get_json(silent=True) or {}
        ref  = _find_case_ref(db, case_id)
        if not ref:
            return _err(f"Case {case_id} not found", 404)

        agent = getattr(g, "user_email", "agent")
        try:
            update = _apply_case_update(ref, body, agent)
        except ValueError as exc:
            return _err(str(exc), 400)

        if not update:
            return _err("Nothing to update", 400)

        ref.update(update)
        return _ok("Updated")
    except Exception as exc:
        return _err(str(exc), 500)


# ── POST /api/support/cases/bulk-update ──────────────────────────────────────

_MAX_BULK = 200


@bp.route("/api/support/cases/bulk-update", methods=["POST"])
def bulk_update_cases():
    """Apply the same status/priority/assigned_to/tags change to many cases
    at once. Each case is updated independently — one missing case_id or one
    invalid value never aborts the rest of the batch. Returns a per-case
    result list so the frontend can report exactly which ones failed.
    """
    try:
        db          = _db()
        body        = request.get_json(silent=True) or {}
        case_ids    = body.get("case_ids") or []
        update_body = body.get("update") or {}

        if not isinstance(case_ids, list) or not case_ids:
            return _err("case_ids must be a non-empty list", 400)
        if len(case_ids) > _MAX_BULK:
            return _err(f"Too many cases at once — max {_MAX_BULK}", 400)
        if not isinstance(update_body, dict) or not update_body:
            return _err("update is required", 400)

        agent   = getattr(g, "user_email", "agent")
        results = []
        for cid in case_ids:
            try:
                ref = _find_case_ref(db, str(cid))
                if not ref:
                    results.append({"case_id": cid, "ok": False, "error": "Not found"})
                    continue
                update = _apply_case_update(ref, update_body, agent)
                if update:
                    ref.update(update)
                results.append({"case_id": cid, "ok": True})
            except Exception as exc:
                results.append({"case_id": cid, "ok": False, "error": str(exc)})

        succeeded = sum(1 for r in results if r["ok"])
        return jsonify({
            "status":    "ok",
            "succeeded": succeeded,
            "failed":    len(results) - succeeded,
            "results":   results,
        })
    except Exception as exc:
        return _err(str(exc), 500)


# ── POST /api/support/cases/<case_id>/reply ───────────────────────────────────

@bp.route("/api/support/cases/<case_id>/reply", methods=["POST"])
def send_reply(case_id: str):
    """Send a manual reply email from the agent and log it to history + actions."""
    try:
        db   = _db()
        body = request.get_json(silent=True) or {}
        text = (body.get("body") or "").strip()
        if not text:
            return _err("Reply body is required", 400)

        ref = _find_case_ref(db, case_id)
        if not ref:
            return _err(f"Case {case_id} not found", 404)

        case         = ref.get().to_dict() or {}
        to_email     = case.get("from_email", "")
        mail_account = case.get("mail_account", "")
        from support_mail.mail_checker import _clean_subject, _board_label
        subject_line = _clean_subject(case.get("subject", ""))
        case_label   = f"{_board_label(mail_account, db)} Case {case.get('board_no', case_id)}"
        subject      = f"RE: {case_label}: {subject_line}"
        agent        = getattr(g, "user_email", "agent")

        from support_mail.reply_sender import send_reply_email
        send_reply_email(db, mail_account, to_email, subject, text)

        now = _now_iso()
        ref.collection("history").document().set({
            "type":          "EMAIL_OUT",
            "from_email":    mail_account,
            "to_email":      to_email,
            "subject":       subject,
            "body":          text,
            "is_auto_reply": False,
            "sent_by":       agent,
            "timestamp":     now,
        })
        _log_action(ref, "replied", by=agent, to_val=to_email)
        ref.update({
            "updated_at":              now,
            "last_history_at":         now,
            "last_history_direction":  "OUT",
            "status":                  "follow_up",
        })
        return _ok("Reply sent")
    except Exception as exc:
        return _err(str(exc), 500)


# ── POST /api/support/cases/<case_id>/note ──────────────────────────

@bp.route("/api/support/cases/<case_id>/note", methods=["POST"])
def add_note(case_id: str):
    """Add an internal note — visible to agents only, never emailed to the customer."""
    try:
        db   = _db()
        body = request.get_json(silent=True) or {}
        text = (body.get("text") or "").strip()
        if not text:
            return _err("Note text is required", 400)

        ref = _find_case_ref(db, case_id)
        if not ref:
            return _err(f"Case {case_id} not found", 404)

        agent = getattr(g, "user_email", "agent")
        now   = _now_iso()
        ref.collection("history").document().set({
            "type":      "NOTE",
            "body":      text,
            "sent_by":   agent,
            "timestamp": now,
        })
        _log_action(ref, "note_added", by=agent)
        ref.update({"updated_at": now})
        return _ok("Note added")
    except Exception as exc:
        return _err(str(exc), 500)


# ── POST /api/support/cases/<case_id>/transfer ────────────────────

@bp.route("/api/support/cases/<case_id>/transfer", methods=["POST"])
def transfer_case(case_id: str):
    """Create a linked copy of this case under a different mail account/board.

    The original case stays exactly where it is (so its board history is
    preserved); a new case is created on the target board with a fresh
    case_id, the full message history copied over, and a back-reference in
    both directions (`transferred_from` / `transferred_to`).
    """
    try:
        db   = _db()
        body = request.get_json(silent=True) or {}
        to_account = (body.get("to_account") or "").strip().lower()
        if not to_account:
            return _err("to_account is required", 400)

        ref = _find_case_ref(db, case_id)
        if not ref:
            return _err(f"Case {case_id} not found", 404)

        case = ref.get().to_dict() or {}
        if case.get("mail_account") == to_account:
            return _err("Case is already on that board", 400)
        if case.get("transferred_to"):
            return _err(
                f"Case already transferred to Case {case['transferred_to'].get('case_id')}", 400
            )

        agent = getattr(g, "user_email", "agent")
        now   = _now_iso()

        from support_mail.mail_checker import _next_case_id, _next_board_no, _board_label
        new_case_id  = _next_case_id(db)
        new_board_no = _next_board_no(db, to_account)
        new_ref = (db.collection("support_mail_accounts")
                     .document(to_account)
                     .collection("cases")
                     .document(str(new_case_id)))

        new_case = dict(case)
        new_case.pop("transferred_to", None)
        new_case.update({
            "case_id":           new_case_id,
            "board_no":          new_board_no,
            "mail_account":      to_account,
            "status":            "new",
            "created_at":        now,
            "updated_at":        now,
            "transferred_from":  {
                "case_id":  case.get("case_id"),
                "board_no": case.get("board_no"),
                "account":  case.get("mail_account"),
            },
        })
        new_ref.set(new_case)

        # Ensure parent account doc exists
        db.collection("support_mail_accounts").document(to_account).set(
            {"email": to_account}, merge=True
        )

        # Copy full message history so the destination board has full context
        for h in ref.collection("history").order_by("timestamp").stream():
            new_ref.collection("history").document(h.id).set(h.to_dict())

        old_label = f"{_board_label(case.get('mail_account'), db)} Case {case.get('board_no', case.get('case_id'))}"
        new_label = f"{_board_label(to_account, db)} Case {new_board_no}"
        _log_action(new_ref, "transferred_from", by=agent,
                    note=f"Transferred from {old_label}")
        _log_action(ref, "transferred_to", by=agent,
                    note=f"Transferred to {new_label}")

        # Original case is done on this board — close it (history stays intact)
        ref.update({
            "transferred_to": {"case_id": new_case_id, "board_no": new_board_no, "account": to_account},
            "status":         "closed",
            "updated_at":     now,
        })

        return _ok("Transferred", new_case_id=new_case_id, new_board_no=new_board_no, to_account=to_account)
    except Exception as exc:
        return _err(str(exc), 500)


# ── GET /api/support/stats ───────────────────────────

@bp.route("/api/support/stats", methods=["GET"])
def get_stats():
    """Dashboard metric counts using collection group query."""
    try:
        db     = _db()
        docs   = list(db.collection_group("cases").stream())
        counts = {"new": 0, "follow_up": 0, "not_interested": 0,
                  "resolved": 0, "total": 0, "overdue": 0}
        now_iso = _now_iso()
        for d in docs:
            c  = d.to_dict() or {}
            st = c.get("status", "new")
            counts["total"] += 1
            # Normalise legacy statuses
            if st in ("new", "open"):
                counts["new"] += 1
            elif st == "follow_up":
                counts["follow_up"] += 1
            elif st == "not_interested":
                counts["not_interested"] += 1
            elif st in ("resolved", "closed", "replied"):
                counts["resolved"] += 1
            # Overdue: past SLA and still needs agent action
            sla = c.get("sla_deadline") or ""
            if sla and sla < now_iso and st in ("new", "open", "follow_up"):
                counts["overdue"] += 1
        return jsonify(counts)
    except Exception as exc:
        return _err(str(exc), 500)
