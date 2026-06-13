"""Tests/run_support.py — Local CLI for testing the Blueboot Support system.

Loads credentials from blueboot-support.secrets.py in the project root.
Never reads/writes the secrets file itself — only executes it to get config.

Usage examples:
  python Tests/run_support.py --stats
  python Tests/run_support.py --check-mail --dry-run
  python Tests/run_support.py --list-cases --status open
  python Tests/run_support.py --case 1112
  python Tests/run_support.py --reply 1112 "Thank you for reaching out."
  python Tests/run_support.py --create-user agent@blueboot.ai --role campaign-user
"""
from __future__ import annotations

import argparse
import os
import sys

# ── Path setup ────────────────────────────────────────────────────────────────
# Project root (two levels up: Tests/ -> functions-support/ -> project root)
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
# functions-support/ is one level up from Tests/
FUNCTIONS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
for p in (ROOT, FUNCTIONS_DIR):
    if p not in sys.path:
        sys.path.insert(0, p)

# ── Load secrets ──────────────────────────────────────────────────────────────
_secrets_path = os.path.join(ROOT, "blueboot-support.secrets.py")
cfg: dict = {}
with open(_secrets_path, encoding="utf-8") as _f:
    exec(compile(_f.read(), _secrets_path, "exec"), cfg)

GCP_PROJECT          = cfg.get("GCP_PROJECT", "blueboot-market")
DEFAULT_MAIL_ACCOUNT = cfg.get("DEFAULT_MAIL_ACCOUNT", "sales@blueboot.ai")
MAIL_CHECK_DAYS      = cfg.get("MAIL_CHECK_DAYS", 7)
SUPPORT_DEDUP_DAYS   = cfg.get("SUPPORT_DEDUP_DAYS", 15)

# ── Firebase init ─────────────────────────────────────────────────────────────
import firebase_admin
from firebase_admin import credentials, firestore as fs, auth as fb_auth

if not firebase_admin._apps:
    cred = credentials.Certificate(cfg["FIREBASE_CREDENTIALS"])
    firebase_admin.initialize_app(cred, {"projectId": GCP_PROJECT})

db = fs.client()

# ── Helpers ───────────────────────────────────────────────────────────────────

def _find_case(case_id: int):
    """Return (case_ref, case_dict) or (None, None)."""
    docs = list(
        db.collection_group("cases")
          .where("case_id", "==", case_id)
          .limit(1)
          .stream()
    )
    if not docs:
        return None, None
    return docs[0].reference, docs[0].to_dict()


def _fmt_date(iso: str | None) -> str:
    if not iso:
        return "—"
    try:
        from datetime import datetime, timezone
        dt = datetime.fromisoformat(iso)
        return dt.astimezone().strftime("%Y-%m-%d %H:%M")
    except Exception:
        return iso


ROLE_LEVELS = {"guest": 0, "user": 1, "campaign-user": 2, "admin": 3}
VALID_ROLES = list(ROLE_LEVELS)

# ── Commands ──────────────────────────────────────────────────────────────────

def cmd_stats(_args):
    """Show case counts by status."""
    docs   = list(db.collection_group("cases").stream())
    counts: dict[str, int] = {}
    overdue = 0
    from datetime import datetime, timezone
    now_iso = datetime.now(timezone.utc).isoformat()
    for d in docs:
        c  = d.to_dict() or {}
        st = c.get("status", "open")
        counts[st] = counts.get(st, 0) + 1
        sla = c.get("sla_deadline") or ""
        if sla and sla < now_iso and st in ("open", "in_progress"):
            overdue += 1
    total = sum(counts.values())
    print(f"\n{'Status':<16} {'Count':>6}")
    print("─" * 24)
    for st, n in sorted(counts.items()):
        print(f"  {st:<14} {n:>6}")
    print("─" * 24)
    print(f"  {'TOTAL':<14} {total:>6}")
    print(f"  {'Overdue':<14} {overdue:>6}")
    print()


def cmd_list_cases(args):
    """List cases with optional filters."""
    query = db.collection_group("cases")
    if args.status:
        query = query.where("status", "==", args.status)
    if args.priority:
        query = query.where("priority", "==", args.priority)
    if args.account:
        query = query.where("mail_account", "==", args.account)
    docs = list(query.order_by("updated_at", direction="DESCENDING").limit(100).stream())

    if not docs:
        print("\nNo cases found.\n")
        return

    print(f"\n{'Case':<8} {'Status':<13} {'Pri':<8} {'Subject':<40} {'From':<30} {'Updated'}")
    print("─" * 120)
    for d in docs:
        c = d.to_dict() or {}
        subj = (c.get("subject") or "(no subject)")[:38]
        frm  = (c.get("from_email") or "—")[:28]
        print(f"  {str(c.get('case_id','?')):<6} {c.get('status','?'):<13} "
              f"{c.get('priority','normal'):<8} {subj:<40} {frm:<30} {_fmt_date(c.get('updated_at'))}")
    print(f"\n{len(docs)} case(s) shown.\n")


def cmd_case(args):
    """Show full timeline for a single case."""
    case_id = int(args.case)
    ref, case = _find_case(case_id)
    if not ref:
        print(f"\nCase {case_id} not found.\n")
        return

    print(f"\n{'─'*60}")
    print(f"  Case {case.get('case_id')}  [{case.get('status','?').upper()}]  priority={case.get('priority','normal')}")
    print(f"  Subject : {case.get('subject','(no subject)')}")
    print(f"  From    : {case.get('from_name','')} <{case.get('from_email','')}>")
    print(f"  Account : {case.get('mail_account','—')}")
    print(f"  Created : {_fmt_date(case.get('created_at'))}")
    print(f"  SLA     : {_fmt_date(case.get('sla_deadline'))}")
    print(f"  Tags    : {', '.join(case.get('tags') or []) or '—'}")
    print(f"{'─'*60}\n  TIMELINE\n{'─'*60}")

    history = [
        {**m.to_dict(), "timeline_type": "message", "_time": m.to_dict().get("timestamp","")}
        for m in ref.collection("history").order_by("timestamp").stream()
    ]
    actions = [
        {**a.to_dict(), "timeline_type": "action", "_time": a.to_dict().get("at","")}
        for a in ref.collection("actions").order_by("at").stream()
    ]
    timeline = sorted(history + actions, key=lambda x: x.get("_time",""))

    for item in timeline:
        ts = _fmt_date(item.get("_time"))
        if item["timeline_type"] == "action":
            atype = item.get("type","?")
            by    = item.get("by","system")
            note  = f"  {item.get('from_value','?')} → {item.get('to_value','?')}" if item.get("from_value") else ""
            print(f"  [{ts}] ⚙  {atype} (by {by}){note}")
        else:
            mtype = item.get("type","?")
            frm   = item.get("from_email") or item.get("sent_by","?")
            body  = (item.get("body") or "").strip().replace("\n"," ")[:80]
            print(f"  [{ts}] {'📥' if mtype=='EMAIL_IN' else '📤' if mtype=='EMAIL_OUT' else '📝'} "
                  f"{mtype}  from={frm}")
            print(f"         {body}")
    print(f"{'─'*60}\n")


def cmd_check_mail(args):
    """Fetch new emails and create/update cases."""
    from support_mail.mail_checker import run_mail_check
    print(f"\nChecking mail (dry_run={args.dry_run}, days={MAIL_CHECK_DAYS}, dedup_days={SUPPORT_DEDUP_DAYS})…\n")
    result = run_mail_check(db, dry_run=args.dry_run, days=MAIL_CHECK_DAYS, dedup_days=SUPPORT_DEDUP_DAYS)
    print(f"  New cases   : {result.get('new_cases', 0)}")
    print(f"  Appended    : {result.get('appended', 0)}")
    print(f"  Skipped     : {result.get('skipped', 0)}")
    if args.dry_run:
        print("  (dry-run — nothing written)")
    print()


def cmd_reply(args):
    """Send a manual reply to a case."""
    case_id = int(args.reply)
    ref, case = _find_case(case_id)
    if not ref:
        print(f"\nCase {case_id} not found.\n")
        return

    body         = args.message
    mail_account = case.get("mail_account","")
    to_email     = case.get("from_email","")
    subject      = f"RE: Case {case_id}: {case.get('subject','')}"

    print(f"\nReply to  : {to_email}")
    print(f"From      : {mail_account}")
    print(f"Subject   : {subject}")
    print(f"Body      : {body[:80]}…" if len(body)>80 else f"Body      : {body}")

    if args.dry_run:
        print("\n(dry-run — email not sent)\n")
        return

    confirm = input("\nSend? [y/N] ").strip().lower()
    if confirm != "y":
        print("Aborted.\n")
        return

    from support_mail.reply_sender import send_reply_email
    from datetime import datetime, timezone
    send_reply_email(db, mail_account, to_email, subject, body)

    now = datetime.now(timezone.utc).isoformat()
    ref.collection("history").document().set({
        "type": "EMAIL_OUT", "from_email": mail_account,
        "to_email": to_email, "subject": subject,
        "body": body, "is_auto_reply": False,
        "sent_by": "cli", "timestamp": now,
    })
    ref.collection("actions").document().set({
        "type": "replied", "by": "cli", "at": now,
        "to_value": to_email, "from_value": None, "note": None,
    })
    ref.update({"updated_at": now, "last_history_at": now, "last_history_direction": "OUT"})
    print("Reply sent and logged.\n")


def cmd_create_user(args):
    """Create a Firebase Auth user and assign a role in Firestore."""
    email    = args.create_user.strip().lower()
    role     = args.role
    password = args.password

    if role not in VALID_ROLES:
        print(f"Invalid role '{role}'. Choose from: {', '.join(VALID_ROLES)}\n")
        sys.exit(1)

    # Create Firebase Auth user
    try:
        kwargs: dict = {"email": email, "email_verified": False}
        if password:
            kwargs["password"] = password
        user = fb_auth.create_user(**kwargs)
        print(f"\nCreated Firebase Auth user: {user.uid}  ({email})")
    except fb_auth.EmailAlreadyExistsError:
        print(f"\nUser {email} already exists in Firebase Auth — updating role only.")

    # Set role in Firestore (same path as CRM)
    db.collection("settings").document("users") \
      .collection("users").document(email) \
      .set({"email": email, "role": role}, merge=True)
    print(f"Role set to '{role}' in Firestore.")

    if not password:
        print(f"\nNo password set — send a password-reset email from Firebase Console")
        print(f"or use the 'Forgot password?' link on the login page.\n")
    else:
        print(f"Password set. User can sign in immediately.\n")


# ── Main ──────────────────────────────────────────────────────────────────────

def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Blueboot Support — local test CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    # Commands
    parser.add_argument("--stats",        action="store_true",  help="Show case counts by status")
    parser.add_argument("--list-cases",   action="store_true",  help="List cases")
    parser.add_argument("--case",         metavar="ID",         help="Show timeline for a case")
    parser.add_argument("--check-mail",   action="store_true",  help="Fetch new emails")
    parser.add_argument("--reply",        metavar="CASE_ID",    help="Send reply to a case")
    parser.add_argument("--message",      metavar="TEXT",       help="Reply body (use with --reply)")
    parser.add_argument("--create-user",  metavar="EMAIL",      help="Create a user account")
    parser.add_argument("--role",         default="campaign-user",
                        choices=VALID_ROLES,                    help="Role for --create-user")
    parser.add_argument("--password",     metavar="PASS",       help="Password for --create-user (optional)")

    # Filters (for --list-cases)
    parser.add_argument("--status",       metavar="STATUS",     help="Filter by status")
    parser.add_argument("--priority",     metavar="PRIORITY",   help="Filter by priority")
    parser.add_argument("--account",      metavar="EMAIL",      help="Filter by mail account")

    # Flags
    parser.add_argument("--dry-run",      action="store_true",  help="Preview only — no writes or emails")

    args = parser.parse_args(argv)

    if args.stats:
        cmd_stats(args)
    elif args.list_cases:
        cmd_list_cases(args)
    elif args.case:
        cmd_case(args)
    elif args.check_mail:
        cmd_check_mail(args)
    elif args.reply:
        if not args.message:
            parser.error("--reply requires --message")
        cmd_reply(args)
    elif args.create_user:
        cmd_create_user(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
