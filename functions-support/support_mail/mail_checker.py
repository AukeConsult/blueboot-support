"""support_mail/mail_checker.py — Core mail-reading logic.

Firestore structure (per request):
  support_mail_accounts/{account_email}/
    cases/{case_id}/
      history/{auto_id}   — every email IN/OUT and internal NOTEs
      actions/{auto_id}   — audit trail: created, status_changed, replied, etc.

Dedup index (root-level for fast lookups):
  support_email_index/{msg_id_key}

Counter:
  settings/support_meta.next_case_id

KEY: Uses IMAP SINCE {date} (not UNSEEN) so emails already read by the
CRM job are still found. Dedup is handled by support_email_index.
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

_SMTP_PORTS   = {25, 465, 587, 2525}
_CASE_RE      = re.compile(r"\bCase\s+(\d+)\b", re.IGNORECASE)

# ── Priority keyword sets ─────────────────────────────────────────────────────
_HIGH_KW = {"urgent", "asap", "emergency", "critical", "immediately",
            "not working", "broken", "down", "failed", "error", "crash"}
_LOW_KW  = {"thank you", "thanks", "fyi", "just wanted", "no rush"}


def _detect_priority(subject: str, body: str) -> str:
    text = (subject + " " + body[:500]).lower()
    if any(w in text for w in _HIGH_KW):
        return "high"
    if any(w in text for w in _LOW_KW):
        return "low"
    return "normal"


# ── String helpers ────────────────────────────────────────────────────────────

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
    m = re.match(r'^(.+?)\s*<', addr or "")
    if m:
        return _decode_str(m.group(1)).strip('"').strip()
    return ""


def _msg_id_key(message_id: str) -> str:
    key = (message_id or "").strip().lstrip("<").rstrip(">")
    key = re.sub(r"[/\\.]", "_", key)
    return key[:500] or "no_id"


# ── IMAP helpers ──────────────────────────────────────────────────────────────

def _imap_host(ma: dict) -> str:
    return str(ma.get("imap_host") or ma.get("host") or "").strip()


def _imap_connect(ma: dict, account_email: str) -> imaplib.IMAP4:
    host    = _imap_host(ma)
    use_ssl = ma.get("ssl", True)
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
    """Fetch full email (headers + body) using SINCE — NOT UNSEEN."""
    since_str = since.strftime("%d-%b-%Y")
    typ, data = conn.uid("search", None, f"SINCE {since_str}")
    if typ != "OK" or not data[0]:
        return []
    all_uids = data[0].split()
    batch    = all_uids[-limit:]
    if not batch:
        return []
    uid_set  = b",".join(batch)
    typ, raw = conn.uid("fetch", uid_set, "(UID BODY.PEEK[])")
    if typ != "OK" or not raw:
        return []

    msgs = []
    for item in raw:
        if not isinstance(item, tuple) or len(item) < 2:
            continue
        meta    = item[0] if isinstance(item[0], bytes) else b""
        payload = item[1] if isinstance(item[1], bytes) else b""
        uid_m   = re.search(rb"UID\s+(\d+)", meta)
        uid     = uid_m.group(1).decode() if uid_m else "0"

        parsed  = message_from_bytes(payload)
        mid     = parsed.get("Message-ID", "").strip()
        subj    = _decode_str(parsed.get("Subject", "")) or "(no subject)"
        from_   = _decode_str(parsed.get("From", ""))
        to_     = _decode_str(parsed.get("To", ""))
        raw_d   = parsed.get("Date", "")
        try:
            date_str = parsedate_to_datetime(raw_d).isoformat()
        except Exception:
            date_str = datetime.now(timezone.utc).isoformat()

        # Extract plain-text body
        body = ""
        if parsed.is_multipart():
            for part in parsed.walk():
                if part.get_content_type() == "text/plain" and not part.get("Content-Disposition"):
                    try:
                        charset = part.get_content_charset() or "utf-8"
                        body    = part.get_payload(decode=True).decode(charset, errors="replace")
                        break
                    except Exception:
                        pass
        else:
            try:
                charset = parsed.get_content_charset() or "utf-8"
                body    = parsed.get_payload(decode=True).decode(charset, errors="replace")
            except Exception:
                pass

        msgs.append({
            "uid": uid, "message_id": mid, "subject": subj,
            "from": from_, "to": to_, "date": date_str,
            "body": body[:10_000],
        })
    return msgs


# ── Firestore helpers ─────────────────────────────────────────────────────────

def _next_case_id(db) -> int:
    """Atomically increment the global case counter."""
    from google.cloud import firestore

    counter_ref = db.collection("settings").document("support_meta")

    @firestore.transactional
    def _inc(tx, ref):
        snap    = ref.get(transaction=tx)
        next_id = ((snap.to_dict() or {}).get("next_case_id") or 0) + 1
        tx.set(ref, {"next_case_id": next_id}, merge=True)
        return next_id

    return _inc(db.transaction(), counter_ref)


def _case_ref(db, account_email: str, case_id: int):
    """Return reference to support_mail_accounts/{email}/cases/{id}."""
    return (db.collection("support_mail_accounts")
              .document(account_email)
              .collection("cases")
              .document(str(case_id)))


def _log_action(case_ref, action_type: str, by: str = "system",
                from_val=None, to_val=None, note: str | None = None) -> None:
    case_ref.collection("actions").document().set({
        "type":       action_type,
        "by":         by,
        "at":         datetime.now(timezone.utc).isoformat(),
        "from_value": from_val,
        "to_value":   to_val,
        "note":       note,
    })


def _already_processed(db, msg_id_key: str) -> bool:
    return db.collection("support_email_index").document(msg_id_key).get().exists


def _mark_processed(db, msg_id_key: str, case_id: int,
                    account: str, direction: str) -> None:
    db.collection("support_email_index").document(msg_id_key).set({
        "case_id":      case_id,
        "account":      account,
        "direction":    direction,
        "processed_at": datetime.now(timezone.utc).isoformat(),
    })


# ── Main entry point ──────────────────────────────────────────────────────────

def run_mail_check(db, account_email: str | None = None, days: int = 7) -> dict:
    """Check INBOX of each mail account (SINCE, not UNSEEN) and create/update cases."""
    now   = datetime.now(timezone.utc)
    since = now - timedelta(days=days)

    ma_col  = db.collection("settings").document("mail_accounts").collection("accounts")
    all_mas = {d.id: d.to_dict() for d in ma_col.stream()}
    if not all_mas:
        return {"error": "No mail accounts configured", "new_cases": 0, "appended": 0}

    if account_email:
        key     = account_email.strip().lower()
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
        finally:
            try:
                conn.logout()
            except Exception:
                pass

        for msg in msgs:
            mid_key = _msg_id_key(msg["message_id"])
            if _already_processed(db, mid_key):
                continue

            from_addr = _extract_email(msg["from"])
            from_name = _extract_name(msg["from"])
            subject   = msg["subject"]
            case_match = _CASE_RE.search(subject)

            if case_match:
                # ── Append to existing case ───────────────────────────────
                case_id_int = int(case_match.group(1))
                ref         = _case_ref(db, acc_email, case_id_int)
                if not ref.get().exists:
                    case_match = None   # case not found — fall through to new case
                else:
                    now_iso = now.isoformat()
                    ref.collection("history").document().set({
                        "type":          "EMAIL_IN",
                        "from_email":    from_addr,
                        "to_email":      acc_email,
                        "subject":       subject,
                        "body":          msg["body"],
                        "is_auto_reply": False,
                        "email_id":      mid_key,
                        "timestamp":     msg["date"],
                    })
                    ref.update({
                        "updated_at":              now_iso,
                        "last_history_at":         msg["date"],
                        "last_history_direction":  "IN",
                        "status":                  "open",   # re-open if resolved
                    })
                    _log_action(ref, "email_received", by=from_addr)
                    _mark_processed(db, mid_key, case_id_int, acc_email, "IN")
                    appended += 1
                    print(f"[support-mail]   appended to Case {case_id_int}", flush=True)

            if not case_match:
                # ── Create new case ───────────────────────────────────────
                try:
                    case_id_int = _next_case_id(db)
                    priority    = _detect_priority(subject, msg["body"])
                    now_iso     = now.isoformat()
                    sla_iso     = (now + timedelta(hours=24)).isoformat()

                    ref = _case_ref(db, acc_email, case_id_int)
                    ref.set({
                        "case_id":               case_id_int,
                        "mail_account":          acc_email,
                        "subject":               subject,
                        "from_email":            from_addr,
                        "from_name":             from_name,
                        "status":                "open",
                        "priority":              priority,
                        "tags":                  [],
                        "assigned_to":           None,
                        "sla_deadline":          sla_iso,
                        "created_at":            now_iso,
                        "updated_at":            now_iso,
                        "last_history_at":       msg["date"],
                        "last_history_direction":"IN",
                    })
                    # Ensure parent account doc exists
                    (db.collection("support_mail_accounts")
                       .document(acc_email)
                       .set({"email": acc_email}, merge=True))

                    ref.collection("history").document().set({
                        "type":          "EMAIL_IN",
                        "from_email":    from_addr,
                        "to_email":      acc_email,
                        "subject":       subject,
                        "body":          msg["body"],
                        "is_auto_reply": False,
                        "email_id":      mid_key,
                        "timestamp":     msg["date"],
                    })
                    _log_action(ref, "created", by="system", note=f"Priority: {priority}")
                    _mark_processed(db, mid_key, case_id_int, acc_email, "IN")
                    new_cases += 1
                    print(f"[support-mail]   created Case {case_id_int} ({priority}): {subject[:60]}", flush=True)

                    # Auto-reply
                    reply_subj = f"RE: Case {case_id_int}: {subject}"
                    html_body  = auto_reply_html(case_id_int, subject, from_name)
                    text_body  = auto_reply_text(case_id_int, subject)
                    try:
                        send_reply_email(db, acc_email, from_addr, reply_subj, text_body, html_body)
                        auto_now = datetime.now(timezone.utc).isoformat()
                        ref.collection("history").document().set({
                            "type":          "EMAIL_OUT",
                            "from_email":    acc_email,
                            "to_email":      from_addr,
                            "subject":       reply_subj,
                            "body":          text_body,
                            "is_auto_reply": True,
                            "timestamp":     auto_now,
                        })
                        ref.update({
                            "last_history_at":        auto_now,
                            "last_history_direction": "OUT",
                        })
                        _log_action(ref, "auto_replied", by="system")
                        print(f"[support-mail]   auto-reply sent to {from_addr}", flush=True)
                    except Exception as exc:
                        errors.append(f"Case {case_id_int} auto-reply failed: {exc}")

                except Exception as exc:
                    errors.append(f"Case create failed for {from_addr}: {exc}")

    print(f"[support-mail] done — {new_cases} new, {appended} appended", flush=True)
    return {"new_cases": new_cases, "appended": appended, "days": days, "errors": errors}
