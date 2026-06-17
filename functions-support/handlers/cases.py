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

        _VALID_STATUSES = {"new", "not_interested", "follow_up", "resolved", "closed"}
        if "status" in body and body["status"] != current.get("status"):
            if body["status"] not in _VALID_STATUSES:
                return _err(f"Invalid status. Choose from: {', '.join(sorted(_VALID_STATUSES))}", 400)
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
        from support_mail.mail_checker import _clean_subject, _board_label
        subject_line = _clean_subject(case.get("subject", ""))
        case_label   = f"{_board_label(mail_account)} Case {case.get('board_no', case_id)}"
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


# ── POST /api/support/cases/<case_id>/transfer ────────────────────────────────

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

        old_label = f"{_board_label(case.get('mail_account'))} Case {case.get('board_no', case.get('case_id'))}"
        new_label = f"{_board_label(to_account)} Case {new_board_no}"
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


# ── GET /api/support/stats ────────────────────────────────────────────────────

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
