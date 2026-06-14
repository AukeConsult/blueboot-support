"""support_mail/reply_sender.py — Send support emails via SMTP.

Loads SMTP credentials from Firestore settings/mail_accounts/accounts/{email}
— the same document the CRM uses — so no credential duplication.
"""
from __future__ import annotations

import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText


_SMTP_PORTS = {25, 465, 587, 2525}


def _load_account(db, account_email: str) -> dict:
    key = (account_email or "").strip().lower()
    if not key:
        raise RuntimeError("No account email provided")
    doc = (
        db.collection("settings")
          .document("mail_accounts")
          .collection("accounts")
          .document(key)
          .get()
    )
    if not doc.exists:
        raise RuntimeError(
            f"No Firestore mail account found for '{key}'. "
            f"Add it at settings/mail_accounts/accounts/{key}"
        )
    return doc.to_dict() or {}


def _send(db, from_account: str, to_email: str, subject: str,
          body_text: str, body_html: str | None = None) -> None:
    """Core SMTP send — shared by all email types."""
    d = _load_account(db, from_account)

    smtp_host = str(d.get("smtp_host") or d.get("host") or "").strip()
    smtp_port = int(d.get("smtp_port") or d.get("port") or 465)
    smtp_ssl  = bool(d.get("smtp_ssl") if "smtp_ssl" in d else d.get("ssl", True))
    username  = str(d.get("username") or d.get("email") or from_account).strip()
    password  = str(d.get("password") or "")
    display   = str(d.get("display_name") or "Blueboot Support").strip()

    if not smtp_host:
        raise RuntimeError(f"smtp_host not configured for {from_account}")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = f"{display} <{from_account}>"
    msg["To"]      = to_email

    msg.attach(MIMEText(body_text, "plain", "utf-8"))
    if body_html:
        msg.attach(MIMEText(body_html, "html", "utf-8"))

    ctx = ssl.create_default_context()
    if smtp_ssl:
        with smtplib.SMTP_SSL(smtp_host, smtp_port, context=ctx) as server:
            server.login(username, password)
            server.sendmail(from_account, [to_email], msg.as_string())
    else:
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.ehlo()
            server.starttls(context=ctx)
            server.login(username, password)
            server.sendmail(from_account, [to_email], msg.as_string())


def send_reply_email(db, from_account: str, to_email: str,
                     subject: str, body_text: str,
                     body_html: str | None = None) -> None:
    """Send a manual agent reply to a client."""
    _send(db, from_account, to_email, subject, body_text, body_html)


def send_ack_email(db, from_account: str, to_email: str, to_name: str,
                   case_id: int, subject: str) -> None:
    """Send an auto-acknowledgement to the client when a new case is created."""
    from support_mail.templates import ack_email_html, ack_email_text
    ack_subject = f"RE: Case {case_id}: {subject}"
    html = ack_email_html(case_id, subject, to_name, support_email=from_account)
    text = ack_email_text(case_id, subject, support_email=from_account)
    _send(db, from_account, to_email, ack_subject, text, html)


def send_sla_warning(db, from_account: str, to_email: str,
                     case_id: int, subject: str, from_client: str,
                     sla_deadline: str) -> None:
    """Send an SLA warning email to an agent or admin."""
    from support_mail.templates import sla_warning_html, sla_warning_text
    warn_subject = f"⚠️ SLA Alert: Case {case_id} approaching deadline"
    html = sla_warning_html(case_id, subject, from_client, sla_deadline)
    text = sla_warning_text(case_id, subject, from_client, sla_deadline)
    _send(db, from_account, to_email, warn_subject, text, html)
