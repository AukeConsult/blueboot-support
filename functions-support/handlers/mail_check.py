"""handlers/mail_check.py — Trigger endpoint for mail checking."""
from __future__ import annotations

from flask import Blueprint, request

from handlers.shared import _err, _ok, _get_db

bp = Blueprint("mail_check", __name__)


@bp.route("/api/support/check-mail", methods=["POST", "GET"])
def check_mail():
    """Trigger a mail check. Called by Cloud Scheduler or manually.
    Query params:
      account  — limit to one mail account email (optional)
      days     — lookback window in days (default 7)
    """
    try:
        db      = _get_db()
        account = request.args.get("account") or None
        days    = int(request.args.get("days", 7))

        # Read dedup_days from Firestore settings (configurable without redeploy)
        meta_snap  = db.collection("settings").document("support_meta").get()
        dedup_days = int((meta_snap.to_dict() or {}).get("dedup_days", 15))

        from support_mail.mail_checker import run_mail_check
        result = run_mail_check(db, account_email=account, days=days, dedup_days=dedup_days)
        return _ok("Mail check complete", **result)
    except Exception as exc:
        return _err(str(exc), 500)


@bp.route("/check-mail", methods=["POST", "GET"])
def check_mail_service():
    """Service route — same logic, called by Cloud Scheduler."""
    return check_mail()
