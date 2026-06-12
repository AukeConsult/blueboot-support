"""support_mail/mail_checker.py — Core mail-reading logic for the support system.

KEY DESIGN: Uses IMAP SINCE {date} (not UNSEEN) so that emails already read by
the CRM inbound-read job are still found. Dedup is handled by the
support_email_index Firestore collection — each processed Message-ID gets a doc
there, so the same email is never processed twice regardless of read state.
"""
from __future__ import annotations

import imaplib
import re
import ssl
from datetime import datetime, timedelta, timezone
from email import message_from_bytes
from email.header import decode_header
from email.utils import parsedate_to_datetime

from support_mail.templates import auto_reply_html, auto_reply_text
from support_mail.reply_sender import send_reply_email

_SMTP_PORTS = {25, 465, 587, 2525}
_CASE_RE    = re.compile(r"\bCase\s+(\d+)\b", re.IGNORECASE)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _decode_str(val: str | None) -> str:
    if not val:
        return ""
    parts = decode_header(val)
    out   = []
    for raw, enc in parts:
        if isinstance(raw, bytes):
            out.append(raw.decode(enc or "utf-8", errors="replace"))
        else:
            out.append(raw)
    return " ".join(out).strip()


def _extract_email(addr: str) -> str:
    if not addr:
        return ""
    m = re.search(r"<([^>]+)>", addr)
    return (m.group(1) if m else addr).strip().lower()


def _extract_name(addr: str) -> str:
    """Extract display name from 'Name <email>' format."""
    m = re.match(r'^(.+?)\s*<', addr or "")
    if m:
        return _decode_str(m.group(1)).strip('"').strip()
    return ""


def _msg_id_key(message_id: str) -> str:
    """Sanitize Message-ID for use as a Firestore doc ID."""
    key = (message_id or "").strip().lstrip("<").rstrip(">")
    key = re.sub(r"[/\\.]", "_", key)
    return key[:500] or "no_id"


def _imap_host(ma: dict) -> str:
    imap_host = str(ma.get("imap_host") or "").strip()
    if imap_host:
        return imap_host
    host = str(ma.get("host") or "").strip()
    return host


def _imap_connect(ma: dict, account_email: str) -> imaplib.IMAP4:
    host     = _imap_host(ma)
    use_ssl  = ma.get("ssl", True)
    raw_port = ma.get("imap_port")
    if raw_port in (None, ""):
        fallback = int(ma.get("port") or 0)
        port = 993 if fallback in _SMTP_PORTS or fallback <= 0 else fallback
    else:
        port = int(raw_port)
    if port in _SMTP_PORTS:
        port = 993 if use_ssl else 143
    if not host:
        raise ValueError(f"IMAP host not configured for {account_email}")

    ctx = ssl.create_default_context()
    try:
        conn = (imaplib.IMAP4_SSL(host, port, ssl_context=ctx)
                if use_ssl else imaplib.IMAP4(host, port))
        conn.login(ma.get("username", account_email), ma.get("password", ""))
        return conn
    except Exception as exc:
        raise ValueError(
            f"IMAP connect failed for {account_email} ({host}:{port} ssl={use_ssl}): {exc}"
        ) from exc


def _fetch_messages(conn: imaplib.IMAP4, since: datetime, limit: int = 200) -> list[dict]:
    """Fetch email metadata + body since cutoff. Uses ALL, not UNSEEN."""
    since_str = since.strftime("%d-%b-%Y")
    typ, data = conn.uid("search", None, f"SINCE {since_str}")
    if typ != "OK" or not data[0]:
        return []

    all_uids = data[0].split()
    batch    = all_uids[-limit:]
    if not batch:
        return []

    uid_set  = b",".join(batch)
    typ, raw = conn.uid(
        "fetch", uid_set,
        "(UID BODY.PEEK[])"     # full message body
    )
    if typ != "OK" or not raw:
        return []

    msgs = []
    i = 0
    while i < len(raw):
        item = raw[i]
        if not isinstance(item, tuple) or len(item) < 2:
            i += 1
            continue
        meta    = item[0] if isinstance(item[0], bytes) else b""
        payload = item[1] if isinstance(item[1], bytes) else b""
        uid_m   = re.search(rb"UID\s+(\d+)", meta)
        uid     = uid_m.group(1).decode() if uid_m else str(i)

        parsed = message_from_bytes(payload)
        mid    = parsed.get("Message-ID", "").strip()
        subj   = _decode_str(parsed.get("Subject", "")) or "(no subject)"
        from_  = _decode_str(parsed.get("From", ""))
        to_    = _decode_str(parsed.get("To", ""))
        raw_d  = parsed.get("Date", "")
        try:
            date_str = parsedate_to_datetime(raw_d).isoformat()
        except Exception:
            date_str = datetime.now(timezone.utc).isoformat()

        # Extract plain-text body
        body = ""
        if parsed.is_multipart():
            for part in parsed.walk():
                ct = part.get_content_type()
                if ct == "text/plain" and not part.get("Content-Disposition"):
                    try:
                        charset = part.get_content_charset() or "utf-8"
                        body = part.get_payload(decode=True).decode(charset, errors="replace")
                        break
                    except Exception:
                        pass
        else:
            try:
                charset = parsed.get_content_charset() or "utf-8"
                body = parsed.get_payload(decode=True).decode(charset, errors="replace")
            except Exception:
                pass

        msgs.append({
            "uid":        uid,
            "message_id": mid,
            "subject":    subj,
            "from":       from_,
            "to":         to_,
            "date":       date_str,
            "body":       body[:10_000],   # cap body at 10 KB
        })
        i += 1
    return msgs


def _next_case_id(db) -> int:
    """Atomically increment and return the next support case ID."""
    from google.cloud import firestore

    counter_ref = db.collection("settings").document("support_meta")

    @firestore.transactional
    def _increment(transaction, ref):
        snap    = ref.get(transaction=transaction)
        next_id = ((snap.to_dict() or {}).get("next_case_id") or 0) + 1
        transaction.set(ref, {"next_case_id": next_id}, merge=True)
        return next_id

    tx = db.transaction()
    return _increment(tx, counter_ref)


def _already_processed(db, msg_id_key: str) -> bool:
    doc = db.collection("support_email_index").document(msg_id_key).get()
    return doc.exists


def _mark_processed(db, msg_id_key: str, case_id: int, direction: str) -> None:
    db.collection("support_email_index").document(msg_id_key).set({
        "case_id":      case_id,
        "direction":    direction,
        "processed_at": datetime.now(timezone.utc).isoformat(),
    })


# ── Main entry point ──────────────────────────────────────────────────────────

def run_mail_check(db, account_email: str | None = None, days: int = 7) -> dict:
    """Read INBOX of each configured mail account (using SINCE, not UNSEEN).

    For each email:
    - Skip if already in support_email_index (dedup).
    - If subject contains 'Case N' → append to that case.
    - Otherwise → create a new case and send auto-reply.

    Returns a stats dict.
    """
    now    = datetime.now(timezone.utc)
    since  = now - timedelta(days=days)

    # 1. Load mail accounts (shared with CRM)
    ma_col  = db.collection("settings").document("mail_accounts").collection("accounts")
    all_mas = {d.id: d.to_dict() for d in ma_col.stream()}
    if not all_mas:
        return {"error": "No mail accounts configured", "new_cases": 0, "appended": 0}

    # Filter to requested account
    if account_email:
        key = account_email.strip().lower()
        all_mas = {k: v for k, v in all_mas.items() if k == key}
        if not all_mas:
            return {"error": f"Account {account_email} not found", "new_cases": 0, "appended": 0}

    new_cases = 0
    appended  = 0
    errors: list[str] = []

    for acc_email, ma in all_mas.items():
        print(f"[support-mail] checking {acc_email}", flush=True)
        try:
            conn = _imap_connect(ma, acc_email)
        except Exception as exc:
            errors.append(f"{acc_email}: connect failed — {exc}")
            continue

        try:
            conn.select("INBOX", readonly=True)
            msgs = _fetch_messages(conn, since)
            print(f"[support-mail] {acc_email}: {len(msgs)} emails in window", flush=True)
        except Exception as exc:
            errors.append(f"{acc_email}: fetch failed — {exc}")
            try:
                conn.logout()
            except Exception:
                pass
            continue

        try:
            conn.logout()
        except Exception:
            pass

        for msg in msgs:
            mid_key = _msg_id_key(msg["message_id"])

            if _already_processed(db, mid_key):
                continue    # already handled — skip

            from_addr = _extract_email(msg["from"])
            from_name = _extract_name(msg["from"])
            subject   = msg["subject"]

            # Check if this is a reply to an existing case
            case_match = _CASE_RE.search(subject)

            if case_match:
                # Append to existing case
                case_id_int = int(case_match.group(1))
                case_ref    = db.collection("support_cases").document(str(case_id_int))
                case_doc    = case_ref.get()
                if case_doc.exists:
                    now_iso = now.isoformat()
                    case_ref.collection("messages").document().set({
                        "direction":     "IN",
                        "from_email":    from_addr,
                        "to_email":      acc_email,
                        "subject":       subject,
                        "body":          msg["body"],
                        "is_auto_reply": False,
                        "email_id":      mid_key,
                        "timestamp":     msg["date"],
                    })
                    case_ref.update({
                        "updated_at":             now_iso,
                        "last_message_at":        msg["date"],
                        "last_message_direction": "IN",
                        # Re-open if was resolved/closed
                        "status": "open",
                    })
                    _mark_processed(db, mid_key, case_id_int, "IN")
                    appended += 1
                    print(f"[support-mail]   appended to Case {case_id_int}", flush=True)
                else:
                    # Case ID in subject but doc doesn't exist — create new
                    case_match = None  # fall through to new case logic below

            if not case_match:
                # Create new case
                try:
                    case_id_int = _next_case_id(db)
                    now_iso     = now.isoformat()
                    case_ref    = db.collection("support_cases").document(str(case_id_int))
                    case_ref.set({
                        "case_id":                case_id_int,
                        "subject":                subject,
                        "from_email":             from_addr,
                        "from_name":              from_name,
                        "mail_account":           acc_email,
                        "status":                 "open",
                        "assigned_to":            None,
                        "created_at":             now_iso,
                        "updated_at":             now_iso,
                        "last_message_at":        msg["date"],
                        "last_message_direction": "IN",
                    })
                    # Save first message
                    case_ref.collection("messages").document().set({
                        "direction":     "IN",
                        "from_email":    from_addr,
                        "to_email":      acc_email,
                        "subject":       subject,
                        "body":          msg["body"],
                        "is_auto_reply": False,
                        "email_id":      mid_key,
                        "timestamp":     msg["date"],
                    })
                    _mark_processed(db, mid_key, case_id_int, "IN")
                    new_cases += 1
                    print(f"[support-mail]   created Case {case_id_int}: {subject[:60]}", flush=True)

                    # Send auto-reply
                    reply_subject = f"RE: Case {case_id_int}: {subject}"
                    html_body = auto_reply_html(case_id_int, subject, from_name)
                    text_body = auto_reply_text(case_id_int, subject)
                    try:
                        send_reply_email(
                            db, acc_email, from_addr,
                            reply_subject, text_body, html_body
                        )
                        # Log auto-reply in thread
                        auto_now = datetime.now(timezone.utc).isoformat()
                        case_ref.collection("messages").document().set({
                            "direction":     "OUT",
                            "from_email":    acc_email,
                            "to_email":      from_addr,
                            "subject":       reply_subject,
                            "body":          text_body,
                            "is_auto_reply": True,
                            "timestamp":     auto_now,
                        })
                        case_ref.update({
                            "last_message_at":        auto_now,
                            "last_message_direction": "OUT",
                        })
                        print(f"[support-mail]   auto-reply sent to {from_addr}", flush=True)
                    except Exception as exc:
                        errors.append(f"Case {case_id_int} auto-reply failed: {exc}")

                except Exception as exc:
                    errors.append(f"Case create failed for {from_addr}: {exc}")

    print(f"[support-mail] done — {new_cases} new cases, {appended} appended", flush=True)
    return {
        "new_cases": new_cases,
        "appended":  appended,
        "days":      days,
        "errors":    errors,
    }
