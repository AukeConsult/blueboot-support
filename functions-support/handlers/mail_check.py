"""handlers/mail_check.py — Trigger endpoint for mail checking + SLA scan."""
from __future__ import annotations

from flask import Blueprint, request

from handlers.shared import _err, _ok, _get_db

bp = Blueprint("mail_check", __name__)


@bp.route("/api/support/check-mail", methods=["POST", "GET"])
def check_mail():
    """Trigger a mail check then run the SLA scan.
    Called by Cloud Scheduler (support-mail-check job) or manually from the board.
    Query params:
      account — limit to one mail account email (optional)
      days    — lookback window in days (default 7)
    """
    try:
        db      = _get_db()
        account = request.args.get("account") or None
        days    = int(request.args.get("days", 7))

        # Read settings from Firestore (configurable without redeploy)
        meta_snap = db.collection("settings").document("support_meta").get()
        meta      = meta_snap.to_dict() or {}
        dedup_days = int(meta.get("dedup_days", 15))

        # ── Step 1: check mail ────────────────────────────────────────────────
        from support_mail.mail_checker import run_mail_check
        mail_result = run_mail_check(db, account_email=account, days=days,
                                     dedup_days=dedup_days)

        # ── Step 2: SLA scan (runs after every mail check, lightweight) ───────
        sla_result = {"warned": 0, "errors": []}
        try:
            from handlers.sla_check import _run_sla_check
            sla_result = _run_sla_check(db)
        except Exception as sla_exc:
            sla_result["errors"].append(str(sla_exc))

        return _ok("Mail check complete",
                   **mail_result,
                   sla_warned=sla_result.get("warned", 0),
                   sla_errors=sla_result.get("errors", []))
    except Exception as exc:
        return _err(str(exc), 500)


@bp.route("/check-mail", methods=["POST", "GET"])
def check_mail_service():
    """Service route — same logic, called by Cloud Scheduler."""
    return check_mail()
