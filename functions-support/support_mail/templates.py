"""support_mail/templates.py — Email templates for the support system."""
from __future__ import annotations


# ── Shared CSS / layout helpers ───────────────────────────────────────────────

_BASE_CSS = """
  body { margin:0; padding:0; background:#f4f5f7;
         font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif; }
  .wrap { max-width:580px; margin:32px auto; background:#ffffff;
          border-radius:10px; overflow:hidden; box-shadow:0 2px 8px rgba(0,0,0,.08); }
  .header { background:#1a1a2e; padding:20px 32px;
            display:flex; align-items:center; gap:14px; }
  .header-icon { width:46px; height:46px; background:#4f46e5; border-radius:50%;
                 display:inline-flex; align-items:center; justify-content:center;
                 font-size:22px; flex-shrink:0; }
  .header-title { color:#ffffff; font-size:16px; font-weight:600; margin:0; }
  .header-sub   { color:#9999bb; font-size:12px; margin:2px 0 0; }
  .body  { padding:32px; }
  .greeting { font-size:15px; color:#1a1a2e; margin:0 0 12px; font-weight:500; }
  .intro { font-size:14px; color:#4a4a6a; line-height:1.65; margin:0 0 24px; }
  .case-card { background:#f0efff; border-left:4px solid #4f46e5;
               border-radius:0 8px 8px 0; padding:16px 20px; margin:0 0 24px; }
  .case-label  { font-size:11px; color:#7c7ca8; text-transform:uppercase;
                 letter-spacing:.07em; margin:0 0 6px; }
  .case-number { font-size:26px; font-weight:700; color:#1a1a2e; margin:0 0 4px; }
  .case-subject{ font-size:13px; color:#4a4a6a; margin:0; }
  .note { font-size:14px; color:#4a4a6a; line-height:1.65; margin:0 0 10px; }
  .sla  { font-size:13px; color:#7c7ca8; margin:10px 0 24px; }
  .sign { font-size:14px; color:#1a1a2e; margin:0; }
  .sign strong { font-weight:600; display:block; margin-top:4px; }
  .footer { background:#f9f9fb; border-top:1px solid #ededf5; padding:16px 32px; }
  .footer p { font-size:12px; color:#9999bb; margin:0; line-height:1.7; }
  .footer a { color:#4f46e5; text-decoration:none; }
"""


def _html_wrap(header_sub: str, body_content: str, footer_content: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<style>{_BASE_CSS}</style>
</head>
<body>
<div class="wrap">
  <div class="header">
    <div class="header-icon">&#128172;</div>
    <div>
      <p class="header-title">Blueboot Support</p>
      <p class="header-sub">{header_sub}</p>
    </div>
  </div>
  <div class="body">
    {body_content}
  </div>
  <div class="footer">
    {footer_content}
  </div>
</div>
</body>
</html>"""


# ── Auto-acknowledgement ──────────────────────────────────────────────────────

def ack_email_html(case_id: int, subject: str, from_name: str,
                   support_email: str = "sales@blueboot.ai") -> str:
    """HTML acknowledgement sent to client when a new case is created."""
    greeting  = f"Hi {from_name.split()[0]}," if from_name else "Hello,"
    body = f"""
    <p class="greeting">{greeting}</p>
    <p class="intro">Thank you for reaching out to Blueboot. We have received your
    message and created a support case for you.</p>
    <div class="case-card">
      <p class="case-label">Your case reference</p>
      <p class="case-number">Case {case_id}</p>
      <p class="case-subject">{subject}</p>
    </div>
    <p class="note">Our team will review your enquiry and respond as soon as possible.</p>
    <p class="note">To add more details or follow up, simply <strong>reply to this
    email</strong> — your reply will be added to Case {case_id} automatically.</p>
    <p class="sla">We aim to respond within 1 business day.</p>
    <p class="sign">
      Best regards,<strong>The Blueboot Team</strong>
    </p>"""
    footer = f"""<p>To ensure your reply is linked to this case, please keep
    <strong>Case {case_id}</strong> in the email subject.&nbsp;
    Need help? Contact us at
    <a href="mailto:{support_email}">{support_email}</a></p>"""
    return _html_wrap("We have received your message", body, footer)


def ack_email_text(case_id: int, subject: str, support_email: str = "sales@blueboot.ai") -> str:
    """Plain-text fallback for the acknowledgement."""
    return (
        f"Thank you for reaching out to Blueboot.\n\n"
        f"Your support case has been created:\n"
        f"  Case Number : Case {case_id}\n"
        f"  Subject     : {subject}\n\n"
        f"Our team will respond within 1 business day.\n\n"
        f"To follow up, reply to this email and keep 'Case {case_id}' in the subject "
        f"so your reply is linked automatically.\n\n"
        f"Best regards,\nThe Blueboot Team\n\n"
        f"---\nNeed help? Email {support_email}"
    )


# ── SLA warning (internal — sent to agent / admin) ────────────────────────────

def sla_warning_html(case_id: int, subject: str, from_email: str,
                     sla_deadline: str, board_url: str = "https://blueboot-support.web.app") -> str:
    """HTML SLA warning email sent to the assigned agent or admin."""
    body = f"""
    <p class="greeting">&#9888;&#65039; SLA Alert</p>
    <p class="intro">The following support case is approaching its response deadline
    and has not yet been replied to.</p>
    <div class="case-card">
      <p class="case-label">Case requiring attention</p>
      <p class="case-number">Case {case_id}</p>
      <p class="case-subject">{subject}</p>
    </div>
    <p class="note"><strong>From:</strong> {from_email}</p>
    <p class="note"><strong>SLA deadline:</strong> {sla_deadline}</p>
    <p class="sla">Please reply to this case as soon as possible to avoid an SLA breach.</p>
    <p class="note">
      <a href="{board_url}/case_detail.html?id={case_id}"
         style="background:#4f46e5;color:#fff;padding:10px 20px;border-radius:6px;
                text-decoration:none;font-size:14px;font-weight:600;display:inline-block;">
        Open Case {case_id}
      </a>
    </p>
    <p class="sign">Blueboot Support System</p>"""
    footer = """<p>This is an automated SLA alert. Do not reply to this email.</p>"""
    return _html_wrap("SLA deadline approaching", body, footer)


def sla_warning_text(case_id: int, subject: str, from_email: str,
                     sla_deadline: str) -> str:
    """Plain-text fallback for SLA warning."""
    return (
        f"SLA ALERT — Case {case_id} is approaching its response deadline.\n\n"
        f"  Case    : Case {case_id}\n"
        f"  Subject : {subject}\n"
        f"  From    : {from_email}\n"
        f"  SLA due : {sla_deadline}\n\n"
        f"Please reply to this case as soon as possible.\n\n"
        f"— Blueboot Support System (automated alert)"
    )


# ── Legacy aliases (kept for backwards compatibility) ─────────────────────────
auto_reply_html  = ack_email_html
auto_reply_text  = ack_email_text
