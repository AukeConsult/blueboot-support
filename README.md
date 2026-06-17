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
POST /api/support/cases/{id}/transfer   move a case to another board (linked copy)
GET  /api/support/stats              dashboard counts
POST /api/support/check-mail         trigger mail check + SLA scan
POST /api/support/check-sla          trigger SLA scan only (manual)
```

---

## Two Boards: Sales & Support

Cases live in the same Firestore structure regardless of mailbox — each board is
just a frontend filter, scoped by a small `BOARD_ACCOUNTS` list at the top of its
page script:

| Board | Page | Mailbox(es) | Case detail page |
|---|---|---|---|
| Sales | `index.html` | `sales@blueboot.ai` | `case_detail.html` |
| Support | `support_index.html` | `support@blueboot.ai` | `support_case_detail.html` |

To add another mailbox to a board later, edit the `BOARD_ACCOUNTS` array in that
page — no backend change needed.

**Case numbering** — each board has its own independent "Case #" sequence (Sales
Case 1, 2, 3… and Support Case 1, 2, 3… in parallel). This is just the number shown
on screen and in emails; internally every case still has a unique system ID so
nothing is ever ambiguous.

**Transfer a case** — if a case lands on the wrong board (e.g. a support request
sent to sales@), open it and click **Transfer to Support**. This creates a linked
copy on the Support board (with its own new board number) with the full message
history, and **closes the original case** on its origin board (marked "Transferred")
so its history isn't lost but it no longer shows as active there.

**Board label on case rows** — every case ID shown anywhere in the UI is prefixed
with its board name ("Sales Case 5", "Support Case 12"), including page titles,
transfer confirmations, and transfer badges. This makes the board obvious at a
glance without needing a separate colored badge.

**Description (currently off)** — every new case has a short, auto-generated
snippet of its content stored on it (`description` field), computed by stripping
greeting lines, quoted replies, forwarded-message headers, and signatures from the
first inbound email and keeping the first ~140 characters of what's left. It's not
shown as a separate column on the board lists right now — in practice it often
matched the Subject too closely to be worth the extra column — but the data is
still being captured on every new case in case it's useful later (e.g. on the case
detail page, or as a tooltip).

**Recognizing client mail** — both boards already share the same sender filter
(skips bounce/auto-reply messages so they never create a case); there is no
separate "client recognition" rule on either board, by design — every other
inbound sender becomes a case.

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
    index.html                    ← Sales board (sales@blueboot.ai)
    case_detail.html              ← Sales case thread + reply composer
    support_index.html            ← Support board (support@blueboot.ai)
    support_case_detail.html      ← Support case thread + reply composer
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

### 5. Add a mailbox for a new board (e.g. support@)

1. Create the mailbox in cPanel (see `doc/cpanel-email-setup.md` if present, or
   the deliverability checklist shared separately).
2. Add its credentials to Firestore at `settings/mail_accounts/accounts/support@blueboot.ai`
   — same fields as `sales@blueboot.ai` (`username`, `password`, `host`/`imap_host`,
   `smtp_host`, `port`/`imap_port`/`smtp_port`, `ssl`, `display_name`).
3. That's it — `mail_checker.py` will start polling it, and `support_index.html`
   already filters to `support@blueboot.ai` via its `BOARD_ACCOUNTS` constant.

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
| `--board-no ACCOUNT --times N` | Bump that mailbox's board-number counter N times (test the per-board sequence) |
| `--transfer ID --to-account EMAIL` | Transfer a case to another board's mailbox — closes the original, creates a numbered copy on the destination |
| `--transfer ID --to-account EMAIL --dry-run` | Preview a transfer — nothing written |
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
  next_case_id: 10          ← global internal ID counter, auto-increments, do not edit
  board_seq: { sales@blueboot.ai: 7, support@blueboot.ai: 3 }  ← per-board "Case #" counters, do not edit
  admin_emails: [...]       ← managed via --sync-settings
  dedup_days: 15            ← managed via --sync-settings

support_mail_accounts/{account}/cases/{case_id}/
  case_id, board_no, subject, from_email, from_name, mail_account
  status, priority, assigned_to, tags
  sla_deadline, sla_warning_sent
  created_at, updated_at, last_history_at, last_history_direction
  transferred_to: { case_id, board_no, account }      ← set on the origin case (status → closed)
  transferred_from: { case_id, board_no, account }    ← set on the new copy

  history/{auto_id}/    ← EMAIL_IN | EMAIL_OUT | NOTE
  actions/{auto_id}/    ← created | status_changed | replied | email_received | transferred_to | transferred_from

support_email_index/{message_id}/   ← dedup — prevents double-processing
```
