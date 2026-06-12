"""support_mail/templates.py — Email templates for the support system."""
from __future__ import annotations


def auto_reply_html(case_id: int, subject: str, from_name: str) -> str:
    """HTML body for the auto-reply sent when a new case is created."""
    greeting = f"Hi {from_name}," if from_name else "Hello,"
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<style>
  body {{ margin: 0; padding: 0; background: #f4f5f7; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; }}
  .wrap {{ max-width: 580px; margin: 32px auto; background: #ffffff; border-radius: 10px; overflow: hidden; box-shadow: 0 2px 8px rgba(0,0,0,.08); }}
  .header {{ background: #1a1a2e; padding: 24px 32px; display: flex; align-items: center; gap: 14px; }}
  .header-icon {{ width: 44px; height: 44px; background: #4f46e5; border-radius: 50%; display: inline-flex; align-items: center; justify-content: center; font-size: 22px; }}
  .header-title {{ color: #ffffff; font-size: 17px; font-weight: 600; margin: 0; }}
  .header-sub {{ color: #9999bb; font-size: 13px; margin: 2px 0 0; }}
  .body {{ padding: 32px; }}
  .greeting {{ font-size: 15px; color: #1a1a2e; margin: 0 0 16px; }}
  .intro {{ font-size: 14px; color: #4a4a6a; line-height: 1.6; margin: 0 0 24px; }}
  .case-card {{ background: #f0efff; border-left: 4px solid #4f46e5; border-radius: 0 8px 8px 0; padding: 16px 20px; margin: 0 0 24px; }}
  .case-label {{ font-size: 12px; color: #7c7ca8; text-transform: uppercase; letter-spacing: .06em; margin: 0 0 6px; }}
  .case-number {{ font-size: 26px; font-weight: 700; color: #1a1a2e; margin: 0 0 4px; }}
  .case-subject {{ font-size: 13px; color: #4a4a6a; margin: 0; }}
  .note {{ font-size: 14px; color: #4a4a6a; line-height: 1.6; margin: 0 0 8px; }}
  .sla {{ font-size: 13px; color: #7c7ca8; margin: 0 0 24px; }}
  .sign {{ font-size: 14px; color: #1a1a2e; margin: 0; }}
  .sign strong {{ font-weight: 600; display: block; margin-top: 4px; }}
  .footer {{ background: #f9f9fb; border-top: 1px solid #ededf5; padding: 16px 32px; }}
  .footer p {{ font-size: 12px; color: #9999bb; margin: 0; line-height: 1.6; }}
</style>
</head>
<body>
<div class="wrap">
  <div class="header">
    <div class="header-icon">💬</div>
    <div>
      <p class="header-title">Blueboot Support</p>
      <p class="header-sub">We have received your message</p>
    </div>
  </div>
  <div class="body">
    <p class="greeting">{greeting}</p>
    <p class="intro">Thank you for reaching out. Your message has been received and a support case has been created for you.</p>
    <div class="case-card">
      <p class="case-label">Your case reference</p>
      <p class="case-number">Case {case_id}</p>
      <p class="case-subject">{subject}</p>
    </div>
    <p class="note">Our team will review your enquiry and respond as soon as possible.</p>
    <p class="note">To add more details or follow up, simply <strong>reply to this email</strong> — your reply will be added to Case {case_id} automatically.</p>
    <p class="sla">We aim to respond within 1 business day.</p>
    <p class="sign">
      Best regards,
      <strong>The Blueboot Team</strong>
    </p>
  </div>
  <div class="footer">
    <p>To ensure your reply is linked to this case, keep <strong>Case {case_id}</strong> in the email subject.<br>
    Need help? Email us at <a href="mailto:sales@blueboot.ai" style="color:#4f46e5;">sales@blueboot.ai</a></p>
  </div>
</div>
</body>
</html>"""


def auto_reply_text(case_id: int, subject: str) -> str:
    """Plain-text fallback for the auto-reply."""
    return (
        f"Thank you for reaching out to Blueboot.\n\n"
        f"Your support case has been created:\n"
        f"  Case Number: Case {case_id}\n"
        f"  Subject: {subject}\n\n"
        f"Our team will respond within 1 business day.\n\n"
        f"To follow up, reply to this email — keep 'Case {case_id}' "
        f"in the subject so your reply is linked automatically.\n\n"
        f"Best regards,\nThe Blueboot Team"
    )
