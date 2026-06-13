# Blueboot Support

A support case management system built on Firebase (Cloud Functions + Firestore + Hosting).
It reads the `sales@blueboot.ai` inbox (and any other configured mail accounts), creates
support cases automatically, and provides a web board for the support team to monitor and
reply to cases.

Shares the same Firebase project (`blueboot-market`) and auth/role system as Blueboot CRM —
no duplicate user management needed.

---

## Architecture

```
Inbox (IMAP)
    │
    ▼
mail_checker.py  ──►  Firestore: support_mail_accounts/{email}/cases/{id}
    │                              └── history/   (emails in/out, notes)
    │                              └── actions/   (audit trail)
    ▼
Auto-reply (SMTP)  ──►  "RE: Case 1112: Your subject"

Frontend (Bootstrap HTML)
    │
    ▼
supportApi (Cloud Function / Flask)
    ├── GET  /api/support/cases          list all cases
    ├── GET  /api/support/cases/{id}     case + unified timeline
    ├── PATCH /api/support/cases/{id}    update status / priority
    ├── POST /api/support/cases/{id}/reply   send manual reply
    ├── POST /api/support/cases/{id}/note    add internal note
    ├── GET  /api/support/stats          dashboard counts
    └── POST /api/support/check-mail     trigger mail check
```

---

## Firestore Structure

```
support_mail_accounts/
  {account_email}/               e.g. sales@blueboot.ai
    cases/
      {case_id}/                 e.g. 1112
        case_id:        1112
        subject:        "Enquiry on subscription"
        from_email:     "client@example.com"
        from_name:      "John Smith"
        mail_account:   "sales@blueboot.ai"
        status:         open | in_progress | resolved | closed
        priority:       high | normal | low
        tags:           []
        assigned_to:    "agent@blueboot.ai"
        sla_deadline:   "2026-06-14T10:00:00+00:00"
        created_at:     "2026-06-13T10:00:00+00:00"
        updated_at:     "2026-06-13T12:00:00+00:00"
        last_history_at:        "2026-06-13T12:00:00+00:00"
        last_history_direction: "IN" | "OUT"

        history/
          {auto_id}/
            type:       EMAIL_IN | EMAIL_OUT | NOTE
            from_email: "client@example.com"
            to_email:   "sales@blueboot.ai"
            subject:    "..."
            body:       "..."
            is_auto_reply: false
            sent_by:    "agent@blueboot.ai"   (for EMAIL_OUT / NOTE)
            timestamp:  "2026-06-13T10:00:00+00:00"

        actions/
          {auto_id}/
            type:       created | status_changed | assigned | priority_changed
                        replied | auto_replied | email_received | note_added
            by:         "system" | "agent@blueboot.ai" | "cli"
            at:         "2026-06-13T10:00:00+00:00"
            from_value: "open"
            to_value:   "in_progress"
            note:       "Priority: high"

support_email_index/
  {message_id_key}/              dedup index — prevents double-processing
    case_id, account, direction, processed_at

settings/
  support_meta/
    next_case_id: 1              atomic counter for sequential case IDs
  mail_accounts/
    accounts/{email}/            shared with CRM — SMTP/IMAP credentials
  users/
    users/{email}/               shared with CRM — roles
      role: campaign-user
```

---

## Role System

Shared with CRM. Roles are set in Firestore at `settings/users/users/{email}`.

| Role | Access |
|---|---|
| `guest` | Blocked entirely |
| `user` | Read-only — can view cases but not reply |
| `campaign-user` | Full access — can reply, update status, add notes |
| `admin` | Full access |

---

## Project Structure

```
blueboot-support/
  blueboot-support.secrets.py   ← local secrets (gitignored — never commit)
  firebase.json                 ← Firebase config
  .firebaserc                   ← project: blueboot-market
  .gitignore
  README.md

  functions-support/            ← Cloud Function source
    main.py                     ← Flask app + supportApi entry point
    requirements.txt
    handlers/
      shared.py                 ← Firebase init, role helpers, response helpers
      cases.py                  ← case CRUD, reply, note endpoints
      mail_check.py             ← /check-mail endpoint
    support_mail/
      mail_checker.py           ← IMAP reader, case creator, auto-reply logic
      reply_sender.py           ← SMTP sender
      templates.py              ← auto-reply HTML/text templates

  public/                       ← Static frontend (Firebase Hosting)
    login.html                  ← Sign-in page
    index.html                  ← Cases board
    case_detail.html            ← Case thread + reply composer
    css/styles.css
    js/
      firebase-config.js        ← Real config (gitignored)
      firebase-config.example.js ← Template (committed)

  Tests/
    run_support.py              ← Local test CLI
    run_support.bat             ← Windows launcher
    run_support.sh              ← Bash launcher
```

---

## Setup

### 1. Clone and create virtual environment

```bash
cd "C:\My_data\Extra Data\blueboot-support"
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # Mac/Linux
pip install -r functions-support/requirements.txt
```

### 2. Configure secrets

Edit `blueboot-support.secrets.py` and fill in:

```python
FIREBASE_CREDENTIALS = {
    "type": "service_account",
    "project_id": "blueboot-market",
    "private_key_id": "...",
    "private_key": "-----BEGIN RSA PRIVATE KEY-----\n...\n-----END RSA PRIVATE KEY-----\n",
    "client_email": "firebase-adminsdk-...@blueboot-market.iam.gserviceaccount.com",
    ...
}
GCP_PROJECT          = "blueboot-market"
DEFAULT_MAIL_ACCOUNT = "sales@blueboot.ai"
MAIL_CHECK_DAYS      = 7
```

### 3. Configure frontend

Copy `public/js/firebase-config.example.js` to `public/js/firebase-config.js`
and fill in your Firebase web app config and the deployed API URL.

### 4. Create first user

```bash
python Tests/run_support.py --create-user you@blueboot.ai --role admin --password YourPassword123
```

Then open `login.html` and sign in.

---

## CLI Reference

Run all commands from the project root.

**Windows:**
```
Tests\run_support.bat [command] [options]
```

**Mac/Linux:**
```
bash Tests/run_support.sh [command] [options]
```

**Or directly:**
```
python Tests/run_support.py [command] [options]
```

---

### --stats
Show case counts by status and how many are overdue.

```bash
python Tests/run_support.py --stats
```

Output:
```
Status           Count
────────────────────────
  closed             2
  in_progress        1
  open               5
────────────────────────
  TOTAL              8
  Overdue            1
```

---

### --list-cases
List cases with optional filters.

```bash
python Tests/run_support.py --list-cases
python Tests/run_support.py --list-cases --status open
python Tests/run_support.py --list-cases --status open --priority high
python Tests/run_support.py --list-cases --account sales@blueboot.ai
```

Options:
- `--status` — filter by: `open`, `in_progress`, `resolved`, `closed`
- `--priority` — filter by: `high`, `normal`, `low`
- `--account` — filter by mail account email

---

### --case
Show the full timeline (emails + notes + audit events) for a single case.

```bash
python Tests/run_support.py --case 1112
```

---

### --check-mail
Fetch new emails from the inbox and create/update cases.

```bash
python Tests/run_support.py --check-mail
python Tests/run_support.py --check-mail --dry-run    # preview only — nothing written
```

Reads `MAIL_CHECK_DAYS` days back (default 7). Uses `support_email_index` in
Firestore to deduplicate — already-processed emails are skipped.

---

### --reply
Send a manual reply to a case from the support inbox.

```bash
python Tests/run_support.py --reply 1112 --message "Thank you for reaching out. We will get back to you shortly."
python Tests/run_support.py --reply 1112 --message "..." --dry-run    # preview only
```

The reply is sent via SMTP and logged to the case history in Firestore.
Subject is automatically formatted as `RE: Case 1112: {original subject}`.

---

### --create-user
Create a new support team member account.

```bash
# Create with password (user can sign in immediately)
python Tests/run_support.py --create-user agent@blueboot.ai --role campaign-user --password SecurePass123

# Create without password (user sets own via "Forgot password?" on login page)
python Tests/run_support.py --create-user agent@blueboot.ai --role campaign-user

# Create an admin
python Tests/run_support.py --create-user admin@blueboot.ai --role admin --password AdminPass123
```

Roles:
- `campaign-user` — standard support agent (read + write)
- `admin` — full access
- `user` — read-only observer
- `guest` — blocked (default for unrecognised accounts)

---

## Deployment

### First-time: create hosting site
```bash
firebase hosting:sites:create blueboot-support
```

### Deploy Cloud Function + frontend
```bash
firebase deploy --only functions:support,hosting:blueboot-support
```

### Deploy frontend only (after HTML/CSS changes)
```bash
firebase deploy --only hosting:blueboot-support
```

### Deploy function only
```bash
firebase deploy --only functions:support
```

---

## Automated mail checking (Cloud Scheduler)

Set up a Cloud Scheduler job to call the mail-check endpoint every 10 minutes:

- **URL**: `https://us-central1-blueboot-market.cloudfunctions.net/supportApi/check-mail`
- **Method**: POST
- **Schedule**: `*/10 * * * *`
- **Auth**: Use a service account with `Cloud Run Invoker` role

---

## Frontend URLs

| Environment | URL |
|---|---|
| Production | https://blueboot-support.web.app |
| Local (open file) | `public/login.html` |

---

## Key design decisions

- **Sequential case IDs**: Atomic Firestore transaction on `settings/support_meta.next_case_id` — guarantees Case 1, Case 2, … Case 1112 with no gaps or duplicates.
- **Dedup**: Every processed email's `Message-ID` is written to `support_email_index`. Both the CRM and the support system can read the same inbox without creating duplicate cases.
- **Thread detection**: Email subjects are scanned for `Case {N}` to detect replies to existing cases. Replies are appended to the existing case, not opened as new ones.
- **Shared credentials**: SMTP/IMAP credentials live in `settings/mail_accounts` — managed by CRM, read by support. No duplication.
- **Priority auto-detection**: Keywords in the subject and first 500 chars of body determine `high`/`normal`/`low` priority on case creation.
- **SLA deadline**: Set to 24 hours after case creation. Overdue cases (open/in_progress past deadline) are highlighted in the dashboard.
