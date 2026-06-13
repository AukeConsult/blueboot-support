"""handlers/cases.py — Support case CRUD, reply sending, and note adding.

Firestore structure:
  support_mail_accounts/{account}/cases/{case_id}/history/{msg}
  support_mail_accounts/{account}/cases/{case_id}/actions/{event}

Collection group queries let us list all cases across all accounts without
knowing which account each case belongs to.
"""
from __future__ import annotations

from datetime import datetime, timezone

from flask import Blueprint, g, jsonify, request

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
    """List all cases across all mail accounts (collection group query)."""
    try:
        db      = _db()
        status  = request.args.get("status")
        account = request.args.get("account")
        priority = request.args.get("priority")
        search  = (request.args.get("q") or "").strip().lower()
        limit   = min(int(request.args.get("limit", 100)), 500)

        query = db.collection_group("cases")
        if status:
            query = query.where("status", "==", status)
        if account:
            query = query.where("mail_account", "==", account)
        if priority:
            query = query.where("priority", "==", priority)

        query = query.order_by("updated_at", direction="DESCENDING").limit(limit)
        docs  = list(query.stream())

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

        current = ref.get().to_dict() or {}
        agent   = getattr(g, "user_email", "agent")
        update  = {}

        if "status" in body and body["status"] != current.get("status"):
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

        if "tags" in body:
            update["tags"] = body["tags"]
            _log_action(ref, "tags_updated", by=agent, note=str(body["tags"]))

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
        subject_line = case.get("subject", "")
        subject      = f"RE: Case {case_id}: {subject_line}"
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
        })
        return _ok("Reply sent")
    except Exception as exc:
        return _err(str(exc), 500)


# ── POST /api/support/cases/<case_id>/note ────────────────────────────────────

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


# ── GET /api/support/stats ────────────────────────────────────────────────────

@bp.route("/api/support/stats", methods=["GET"])
def get_stats():
    """Dashboard metric counts using collection group query."""
    try:
        db     = _db()
        docs   = list(db.collection_group("cases").stream())
        counts = {"open": 0, "in_progress": 0, "resolved": 0,
                  "closed": 0, "total": 0, "overdue": 0}
        now_iso = _now_iso()
        for d in docs:
            c = d.to_dict() or {}
            st = c.get("status", "open")
            counts["total"] += 1
            if st in counts:
                counts[st] += 1
            # Count overdue (open/in_progress past SLA deadline)
            sla = c.get("sla_deadline") or ""
            if sla and sla < now_iso and st in ("open", "in_progress"):
                counts["overdue"] += 1
        return jsonify(counts)
    except Exception as exc:
        return _err(str(exc), 500)
