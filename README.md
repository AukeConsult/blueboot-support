# Blueboot Support

Support case management built on Firebase. Reads configured mail inboxes via IMAP,
creates cases automatically, sends client acknowledgements, and provides a web board
for the team to monitor and reply.

Shares the `blueboot-market` Firebase project with the CRM — same auth, roles, and
mail account credentials. No duplicate setup needed.

**Board**: https://blueboot-support.web.app

---

## How it works

```
Inbox (IMAP)
    │
    ▼
mail_checker.py ──► New case created in Firestore
    │               └── Auto-acknowledgement sent to client (Case 1112)
    │               └── Dedup: same sender within 15 days → merged into existing case
    ▼
Cloud Scheduler (support-mail-check) — runs every 10 min
    └── Checks mail + scans for SLA warnings in one call

Agent replies manually from the board
    └── Reply logged to case history, status → Follow Up
```

**API endpoints:**
```
GET  /api/support/cases              list all cases
GET  /api/support/cases/{id}         case + unified timeline
PATCH /api/support/cases/{id}        update status / priority / assigned_to
POST /api/support/cases/{id}/reply   send manual reply
POST /api/support/cases/{id}/note    add internal note
GET  /api/support/stats              dashboard counts
POST /api/support/check-mail         trigger mail check + SLA scan
POST /api/support/check-sla          trigger SLA scan only (manual)
```

---

## Case Statuses

| Status | Meaning |
|---|---|
| `new` | Just received, no reply yet |
| `follow_up` | Agent replied, awaiting customer response |
| `not_interested` | Customer not interested |
| `resolved` | Issue resolved |
| `closed` | Closed without resolution |

SLA deadline is set to **24 hours** after case creation. Overdue cases are highlighted on the board.

---

## Roles

Shared with CRM. Set in Firestore `settings/users/users/{email}.role`.

| Role | Access |
|---|---|
| `campaign-user` | Full access — reply, update, add notes |
| `admin` | Full access |
| `user` | Read-only |
| `guest` | Blocked |

---

## Project Structure

```
blueboot-support/
  blueboot-support.secrets.py     ← local secrets (gitignored)
  firebase.json
  .firebaserc                     ← project: blueboot-market
  README.md

  functions-support/              ← Cloud Function source
    main.py
    requirements.txt
    handlers/
      shared.py                   ← Firebase init, auth helpers
      cases.py                    ← case CRUD, reply, note, stats
      mail_check.py               ← /check-mail endpoint
      sla_check.py                ← /check-sla endpoint + _run_sla_check()
    support_mail/
      mail_checker.py             ← IMAP reader + case creator
      reply_sender.py             ← SMTP sender (ack, reply, SLA warning)
      templates.py                ← HTML email templates
    Tests/
      run_support.py              ← Local CLI
      run_support.bat             ← Windows launcher
      run_support.sh              ← Bash launcher

  public/                         ← Static frontend
    login.html
    index.html                    ← Cases board
    case_detail.html              ← Case thread + reply composer
    css/styles.css
    js/
      firebase-config.js          ← Real config (gitignored)
      firebase-config.example.js  ← Template (committed)
```

---

## Setup

### 1. Secrets file

Edit `blueboot-support.secrets.py`:

```python
FIREBASE_CREDENTIALS = { ... }   # paste from Firebase Console → Service accounts
GCP_PROJECT          = "blueboot-market"
DEFAULT_MAIL_ACCOUNT = "sales@blueboot.ai"
MAIL_CHECK_DAYS      = 7
SUPPORT_DEDUP_DAYS   = 15        # merge emails from same sender within N days
SUPPORT_ADMIN_EMAILS = ["ram@blueboot.ai", "sales@blueboot.ai"]  # SLA warnings go here
```

### 2. Sync settings to Firestore

Run once after adding/changing `SUPPORT_ADMIN_EMAILS` or `SUPPORT_DEDUP_DAYS`:

```
functions-support\Tests\run_support.bat --sync-settings
```

This pushes the values to `settings/support_meta` in Firestore so the Cloud Function
can read them. No manual Firestore edits needed.

### 3. Frontend config

Copy `public/js/firebase-config.example.js` → `public/js/firebase-config.js`
and fill in your Firebase web app config + deployed API URL.

### 4. Create first admin user

```
functions-support\Tests\run_support.bat --create-user you@blueboot.ai --role admin
```

---

## CLI Reference

Run from inside `functions-support/`:

```
Tests\run_support.bat [command]       ← Windows
bash Tests/run_support.sh [command]   ← Mac/Linux
```

| Command | Description |
|---|---|
| `--sync-settings` | Push `SUPPORT_ADMIN_EMAILS` + `SUPPORT_DEDUP_DAYS` from secrets to Firestore |
| `--stats` | Case counts by status + overdue count |
| `--list-cases` | List cases (add `--status`, `--priority`, `--account` to filter) |
| `--case ID` | Full timeline for a single case |
| `--check-mail` | Fetch new emails and create/update cases |
| `--check-mail --dry-run` | Preview only — nothing written or sent |
| `--reply ID --message "..."` | Send a reply to a case |
| `--create-user EMAIL --role ROLE` | Create a support team member account |

---

## Deployment

```bash
# Full deploy (function + frontend)
firebase deploy --only functions:support,hosting:blueboot-support

# Frontend only (after HTML/CSS changes)
firebase deploy --only hosting:blueboot-support

# Function only
firebase deploy --only functions:support
```

After first deployment, click the Firestore index link shown in the browser
console error to create the collection group index (takes ~2 minutes).

---

## Scheduler

Existing job: **`support-mail-check`**
- URL: `https://us-central1-blueboot-market.cloudfunctions.net/supportApi/check-mail`
- Runs every 1 hour
- Checks mail **and** scans SLA warnings in one call — no second scheduler needed

---

## Firestore structure (reference)

```
settings/support_meta
  next_case_id: 10          ← auto-increments, do not edit
  admin_emails: [...]       ← managed via --sync-settings
  dedup_days: 15            ← managed via --sync-settings

support_mail_accounts/{account}/cases/{case_id}/
  case_id, subject, from_email, from_name, mail_account
  status, priority, assigned_to, tags
  sla_deadline, sla_warning_sent
  created_at, updated_at, last_history_at, last_history_direction

  history/{auto_id}/    ← EMAIL_IN | EMAIL_OUT | NOTE
  actions/{auto_id}/    ← created | status_changed | replied | email_received | …

support_email_index/{message_id}/   ← dedup — prevents double-processing
```
