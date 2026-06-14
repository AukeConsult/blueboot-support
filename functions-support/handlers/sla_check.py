"""handlers/sla_check.py — SLA warning scanner.

Called by Cloud Scheduler (e.g. every hour).
Scans cases whose SLA deadline is within the next 60 minutes,
status is still new or follow_up, and a warning has not been sent yet.
Sends a warning email to the assigned agent or fallback admin email.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from flask import Blueprint

from handlers.shared import _err, _ok, _get_db

bp = Blueprint("sla_check", __name__)

_WARN_WINDOW_MINUTES = 60   # warn when deadline is within this many minutes
_ACTIVE_STATUSES     = {"new", "open", "follow_up"}


def _run_sla_check(db) -> dict:
    """Core SLA scan logic — callable from mail_check handler too."""
    now     = datetime.now(timezone.utc)
    cutoff  = now + timedelta(minutes=_WARN_WINDOW_MINUTES)
    now_iso = now.isoformat()
    cut_iso = cutoff.isoformat()

    # Read admin fallback emails from Firestore settings
    meta         = db.collection("settings").document("support_meta").get()
    meta_data    = meta.to_dict() or {}
    # Support both array (admin_emails) and legacy single string (admin_email)
    admin_emails_raw = meta_data.get("admin_emails") or meta_data.get("admin_email") or []
    if isinstance(admin_emails_raw, str):
        admin_emails_raw = [admin_emails_raw]
    admin_emails = [e.strip() for e in admin_emails_raw if e and e.strip()]

    # Query cases with sla_deadline in the warning window
    docs = list(
        db.collection_group("cases")
          .where("sla_deadline", ">=", now_iso)
          .where("sla_deadline", "<=", cut_iso)
          .stream()
    )

    warned = 0
    skipped = 0
    errors: list[str] = []

    for d in docs:
        c = d.to_dict() or {}
        if c.get("status") not in _ACTIVE_STATUSES:
            skipped += 1
            continue
        if c.get("sla_warning_sent"):
            skipped += 1
            continue

        case_id     = c.get("case_id", d.id)
        subject     = c.get("subject", "(no subject)")
        from_client = c.get("from_email", "")
        sla_dt      = c.get("sla_deadline", "")
        mail_account = c.get("mail_account", "")
        # Build recipient list: assigned agent + all admin emails
        recipients = []
        if c.get("assigned_to"):
            recipients.append(c["assigned_to"])
        for e in admin_emails:
            if e not in recipients:
                recipients.append(e)

        if not recipients:
            errors.append(f"Case {case_id}: no recipient (set admin_emails in settings/support_meta)")
            continue
        if not mail_account:
            errors.append(f"Case {case_id}: no mail_account on case doc")
            continue

        try:
            # Format deadline for display
            try:
                sla_fmt = datetime.fromisoformat(sla_dt).strftime("%Y-%m-%d %H:%M UTC")
            except Exception:
                sla_fmt = sla_dt

            from support_mail.reply_sender import send_sla_warning
            for recipient in recipients:
                send_sla_warning(db, mail_account, recipient,
                                 case_id, subject, from_client, sla_fmt)

            # Mark warning sent so we don't re-send next hour
            d.reference.update({"sla_warning_sent": True})
            warned += 1
            print(f"[sla-check] warned Case {case_id} → {recipients}", flush=True)
        except Exception as exc:
            errors.append(f"Case {case_id}: {exc}")

    print(f"[sla-check] done — {warned} warned, {skipped} skipped, {len(errors)} errors", flush=True)
    return {"warned": warned, "skipped": skipped, "errors": errors}


@bp.route("/api/support/check-sla", methods=["POST", "GET"])
def check_sla():
    """Manual trigger endpoint for SLA scan."""
    try:
        result = _run_sla_check(_get_db())
        return _ok("SLA check complete", **result)
    except Exception as exc:
        return _err(str(exc), 500)


@bp.route("/check-sla", methods=["POST", "GET"])
def check_sla_service():
    """Service route — called by Cloud Scheduler (if used standalone)."""
    return check_sla()
