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
GET  /api/support/cases              list all cases (?account=, ?status=, ?priority=, ?q=, ?limit=)
GET  /api/support/cases/{id}         case + unified timeline
PATCH /api/support/cases/{id}        update status / priority / assigned_to
POST /api/support/cases/{id}/reply   send manual reply
POST /api/support/cases/{id}/note    add internal note
POST /api/support/cases/{id}/transfer   move a case to another channel (linked copy)
GET  /api/support/channels           list every configured channel (account, label, is_main, unread/overdue counts)
GET  /api/support/channels/{account} full channel config, password excluded — admin only
POST /api/support/channels           create a new channel/mailbox — admin only
PATCH /api/support/channels/{account} update a channel's settings/credentials — admin only
DELETE /api/support/channels/{account} remove a channel (refuses to delete the main channel) — admin only
GET  /api/support/me                 current signed-in user's email + role
GET  /api/support/stats              dashboard counts
POST /api/support/check-mail         trigger mail check + SLA scan
POST /api/support/check-sla          trigger SLA scan only (manual)
```

`GET /api/support/cases` with no `?account=` searches every channel at once — this
powers the cross-channel search box in the board navbar.

---

## Channels: Support is the main channel

Cases live in the same Firestore structure regardless of mailbox. Every configured
mailbox (`settings/mail_accounts/accounts/{email}`) is its own **channel** on a
single, generic board page — there is no longer one HTML file per mailbox. Support
is the **main channel**: every other channel can transfer a case into it, but it
can't transfer into anything else.

| Page | Purpose |
|---|---|
| `board.html?account=<email>` | The channel board — case list, stats, filters, for one mailbox |
| `case_detail.html?id=<caseId>` | The single canonical case detail page, for any channel |

Visiting `board.html` with no `?account=` defaults to the main channel (Support).
A pill nav at the top of both pages (rendered by `js/channels.js` from
`GET /api/support/channels`) lists every channel and links to its board, so the
nav updates automatically as channels are added, renamed, or removed — no HTML
changes needed.

`index.html`, `support_index.html`, and `support_case_detail.html` are now thin
redirect stubs (to `board.html?account=sales@blueboot.ai`,
`board.html?account=support@blueboot.ai`, and `case_detail.html?id=...`
respectively) kept only so old bookmarks keep working.

**`GET /api/support/channels`** returns:
```json
{
  "channels": [
    { "account": "support@blueboot.ai", "label": "Support", "is_main": true,
      "unread_count": 2, "overdue_count": 0 },
    { "account": "sales@blueboot.ai",   "label": "Sales",   "is_main": false,
      "unread_count": 0, "overdue_count": 1 },
    { "account": "contact@blueboot.ai", "label": "Contact",  "is_main": false,
      "unread_count": 0, "overdue_count": 0 }
  ]
}
```
Sorted main-channel-first, then alphabetically by label. `label` and `is_main` are
optional fields on each `settings/mail_accounts/accounts/{email}` document — if
either is missing, the email's local part (before the `@`) is used as the label
and the account is treated as a non-main channel, except `support@blueboot.ai`
which falls back to "Support" / main for backward compatibility. `unread_count`
(active cases whose last message is from the customer) and `overdue_count`
(active cases past their SLA deadline) are shown as small badges on each
channel's pill in the nav — red for overdue, blue otherwise, overdue takes
priority when a channel has both.

**Managing channels (admins only)** — click **Channels** in the navbar (only
visible to admins) to open `channels_admin.html`, a page to add, edit, or delete
mailboxes without touching Firestore directly. Creating or editing a channel
still requires the same fields as the manual Firestore setup below (IMAP/SMTP
host, port, SSL, username, password); editing leaves the password unchanged if
the field is left blank. The main channel can't be deleted. These write actions
call `POST` / `PATCH` / `DELETE /api/support/channels[/{account}]`, which are
restricted to `role=admin` regardless of the signed-in user's blueprint-level
access — see Roles below. `GET /api/support/me` is what the board uses to decide
whether to show the **Channels** link for the current user.

**Finding a case across every channel** — the search box in the board's navbar
(`board.html`) searches subject and sender across all channels at once, not just
the one currently open, using the same `GET /api/support/cases?q=` the per-board
search box uses, just without an `?account=` filter. Each result shows which
channel it belongs to.

**"My cases" filter** — a toggle on the board filters the visible list down to
cases whose `assigned_to` matches the signed-in user's email.

**Adding a new channel** — no code or HTML changes needed:
1. Add the mailbox to `settings/mail_accounts/accounts/{email}` in Firestore
   (IMAP/SMTP credentials, same shape as the existing accounts).
2. Optionally set `label` (e.g. `"Contact"`) — defaults to the email's local part.
3. Leave `is_main` unset (or `false`) — only Support should be `true`.
4. The new channel appears in the pill nav and at
   `board.html?account=<email>` automatically next time the page loads.

**Case numbering** — each channel has its own independent "Case #" sequence
(Sales Case 1, 2, 3… and Support Case 1, 2, 3… in parallel). This is just the
number shown on screen and in emails; internally every case still has a unique
system ID so nothing is ever ambiguous.

**Transfer a case** — if a case lands on the wrong channel (e.g. a support
request sent to sales@), open it and click **Transfer to Support** (every
non-main channel shows this button; the label always names the current main
channel). This creates a linked copy on the Support board (with its own new
board number) with the full message history, and **closes the original case**
on its origin channel (marked "Transferred") so its history isn't lost but it
no longer shows as active there.

**Board label on case rows** — every case ID shown anywhere in the UI is prefixed
with its channel name ("Sales Case 5", "Support Case 12", "Contact Case 3"),
resolved dynamically from `/api/support/channels`, including page titles,
transfer confirmations, and transfer badges. This makes the channel obvious at a
glance without needing a separate colored badge.

**Description (currently off)** — every new case has a short, auto-generated
snippet of its content stored on it (`description` field), computed by stripping
greeting lines, quoted replies, forwarded-message headers, and signatures from the
first inbound email and keeping the first ~140 characters of what's left. It's not
shown as a separate column on the board lists right now — in practice it often
matched the Subject too closely to be worth the extra column — but the data is
still being captured on every new case in case it's useful later (e.g. on the case
detail page, or as a tooltip).

**Recognizing client mail** — every channel shares the same sender filter
(skips bounce/auto-reply messages so they never create a case); there is no
separate "client recognition" rule per channel, by design — every other
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
| `campaign-user` | Full access — reply, update, add notes. Cannot add/edit/delete channels. |
| `admin` | Full access, including adding/editing/deleting channels (mailbox credentials) |
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
    board.html                    ← Generic channel board (?account=<email>; defaults to Support)
    case_detail.html              ← Canonical case thread + reply composer, any channel
    channels_admin.html           ← Add/edit/delete channels — admin only
    index.html                    ← Redirect stub → board.html?account=sales@blueboot.ai
    support_index.html            ← Redirect stub → board.html?account=support@blueboot.ai
    support_case_detail.html      ← Redirect stub → case_detail.html?id=...
    css/styles.css
    js/
      channels.js                 ← Shared channel-nav module (fetches /api/support/channels)
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

### 5. Add a mailbox for a new channel (e.g. contact@)

1. Create the mailbox in cPanel (see `doc/cpanel-email-setup.md` if present, or
   the deliverability checklist shared separately).
2. Add its credentials to Firestore at `settings/mail_accounts/accounts/contact@blueboot.ai`
   — same fields as `sales@blueboot.ai` (`username`, `password`, `host`/`imap_host`,
   `smtp_host`, `port`/`imap_port`/`smtp_port`, `ssl`, `display_name`), plus an
   optional `label` (e.g. `"Contact"` — defaults to the email's local part if
   omitted). Leave `is_main` unset; only Support is the main channel.
3. That's it — `mail_checker.py` will start polling it, and it appears
   automatically in the channel pill nav and at
   `board.html?account=contact@blueboot.ai` — see "Channels: Support is the
   main channel" above for details. No HTML or code changes needed.

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
