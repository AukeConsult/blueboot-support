# Blueboot Support — Plain-English Project Guide

This explains what the Blueboot Support system is, how its pieces fit together, and what
every file does — written for someone who hasn't written code before. Keep this alongside
`README.md` (which is the more technical reference).

---

## 1. What this system actually does

Blueboot Support turns email inboxes (support@, sales@, contact@, etc.) into a shared
ticket board. When a customer emails one of those addresses, the system automatically:

1. Reads the inbox.
2. Creates a "case" (a support ticket) from that email.
3. Sends the customer an automatic acknowledgement ("we got your message, this is Case 12").
4. Shows the case on a web page where your team can see it, reply to it, add notes, and
   track whether it's overdue.

Think of it like a shared inbox dashboard layered on top of your normal email accounts —
nobody needs to log into the actual mailbox to handle a request.

---

## 2. The two halves of the system

Every web app like this has two halves that talk to each other over the internet:

- **The backend** — code that runs on Google's servers (not on anyone's computer). It
  reads email, talks to the database, and answers questions like "give me all open
  cases." It never shows anything visually — it only returns data.
- **The frontend** — the actual web pages your team opens in a browser (`board.html`,
  `case_detail.html`, etc.). These are what render the buttons, tables, and forms you see,
  and they ask the backend for data whenever the page needs something.

```
   Your browser                         Google Cloud
 ┌────────────────┐   "give me cases"  ┌──────────────────┐   reads/writes   ┌─────────────┐
 │  board.html     │ ─────────────────▶ │  Backend (Flask   │ ───────────────▶ │  Firestore   │
 │  case_detail... │ ◀───────────────── │  app, Python)     │ ◀─────────────── │  (database)  │
 └────────────────┘    JSON data back   └──────────────────┘                  └─────────────┘
                                                   │
                                                   │ reads/sends email
                                                   ▼
                                          ┌──────────────────┐
                                          │  Mail servers     │
                                          │  (IMAP / SMTP)    │
                                          └──────────────────┘
```

A few terms that will come up repeatedly:

- **API / endpoint** — a specific "question you can ask" the backend. E.g.
  `GET /api/support/cases` means "give me the list of cases." Each page in this app sends
  several of these requests every time it loads or you click a button.
- **JSON** — the plain-text format the backend and frontend use to exchange data. It looks
  like `{"status": "new", "subject": "Help!"}` — just labeled values, nothing scary.
- **Firestore** — Google's database. Think of it as a giant filing cabinet of folders and
  documents (not spreadsheet rows). "Collections" are folders, "documents" are individual
  records inside them.
- **Cloud Function** — the backend code doesn't run on a server that's always on. Google
  starts it up only when a request comes in, runs it, then shuts it down. This is why the
  whole backend is described as "one Cloud Function" (`supportApi`) in the code.
- **IMAP / SMTP** — the two protocols email runs on. IMAP is how the system *reads* a
  mailbox's inbox; SMTP is how it *sends* mail (replies, acknowledgements, warnings).

---

## 3. How a support case actually gets created (step by step)

This is the most important flow to understand, because almost everything else exists to
support it.

1. **A customer emails `sales@blueboot.ai`** (or any configured channel address).
2. **Every 10 minutes** (or whenever someone clicks "Check mail" in the browser), the
   backend logs into that mailbox over IMAP and looks at every email from the last 7 days.
3. For each email, it asks a few questions before doing anything:
   - Is this from an automated system (a bounce, an out-of-office reply, "noreply@...")?
     If so, ignore it completely — these never become cases.
   - Does the subject line already mention an existing case number (e.g. "Case 12")? If
     so, this is a reply to an existing conversation — add it to that case's history
     instead of creating a new one.
   - Otherwise, has this same customer emailed recently (within the last 15 days) and
     that case is still open? If so, merge this email into that existing case rather than
     opening a duplicate.
   - If none of those apply, it's a brand-new case.
4. **For a new case**, the system:
   - Assigns it a number (both a global internal ID, and a friendlier "Case 5" number
     that's specific to that one mailbox).
   - Looks for urgency keywords ("urgent", "broken", "asap") to set a priority of
     high / normal / low.
   - Sets a 24-hour SLA deadline (a "by when this should be answered" clock).
   - Saves all of this to the database.
   - Sends the customer an automatic "We got your message — this is Case 5" email.
5. **The case now shows up on the board** the next time anyone loads `board.html`, with a
   live countdown toward its SLA deadline.

Nothing in this flow needs a human until step 5 — the system runs unattended, on a timer
set up in Google Cloud Scheduler.

---

## 4. How an agent replies to a case

1. Someone on your team opens `board.html`, sees the new case, and clicks **Reply**.
2. That opens `case_detail.html`, which shows the full email thread (every message in and
   out, in order) plus a text box.
3. They type a reply and hit send. The backend:
   - Sends the actual email to the customer over SMTP.
   - Records that reply in the case's history (so it's part of the permanent thread).
   - Automatically flips the case's status to "Follow Up" (waiting on the customer now).
4. If the customer replies again, that new email comes back in through the same mail-check
   process described above and gets appended to the same case — re-opening it as "New."

Agents can also add **internal notes** (visible only to the team, never emailed to the
customer), change a case's status or priority by hand, or **transfer** a case to a
different channel if it landed in the wrong inbox (e.g. a billing question that arrived at
sales@ gets transferred to support@, the "main" channel).

---

## 5. The "channel" concept

A **channel** is just one configured mailbox (support@, sales@, contact@, etc.). The system
was built so that adding a brand-new inbox never requires writing or changing any code —
you just add its login details to the database and it appears automatically:

- A row of pill-shaped buttons at the top of every page lets you switch between channels.
- Each pill now shows a small badge: blue with a number = "this many cases are waiting on
  a reply," red = "this many are overdue."
- One channel is marked as the **main channel** (Support, by default). Every other channel
  can transfer a case into the main channel, which is useful when a request was sent to
  the wrong address.
- Adding a channel: paste its IMAP/SMTP details into Firestore (or, now, use the new
  **Channels** admin page described below) — nothing else changes.

---

## 6. Who can do what (roles)

Every signed-in user has a role stored in the database, and the backend checks that role
before allowing any action:

| Role | Can view cases? | Can reply / change status / add notes? | Can add/edit/delete channels (mailbox passwords)? |
|---|---|---|---|
| `guest` (no role assigned) | No | No | No |
| `user` | Yes | No | No |
| `campaign-user` | Yes | Yes | No |
| `admin` | Yes | Yes | Yes |

This means a brand-new team member who hasn't been assigned a role yet sees nothing
(blocked entirely), a `user` can browse and read but not touch anything, and only an
`admin` can ever see or change a mailbox's actual email password.

---

## 7. The features added most recently, explained

These were the four enhancements implemented this round:

**Channel badges (unread / overdue counts).** The pill buttons at the top of the page now
show how many cases on that channel are waiting for a reply (blue) or are past their
deadline (red), so you can tell at a glance which inbox needs attention without clicking
into it.

**"My cases" filter.** A toggle switch on the board hides every case except the ones
assigned to you. Useful once a team has more than one person replying to tickets.

**Channel management page (admins only).** A new page, `channels_admin.html`, reachable
via a **Channels** button that only appears for admins. It lists every configured mailbox
in a table and lets you add a new one, edit an existing one's settings (including
resetting its password), or delete one — all through a form, instead of needing someone to
edit the database by hand. The main channel can't be deleted, since every other channel
depends on it as a transfer destination.

**Cross-channel search.** A search box in the top navigation bar (separate from the
per-channel search box further down the page) searches every channel's cases at once by
subject or sender — handy when you don't remember which inbox a conversation came in on.

**Assigning cases to teammates.** Every case already stored who it was assigned to in
the database, but there was no button anywhere to actually set it — this round added one.
Each case row on the board now has an **Assign to me** button plus a small dropdown to
hand it to someone else instead; the same two controls were added to the case detail
page's header. Whoever a case is assigned to shows as a small chip next to the case ID.

---

## 7b. Pagination, bulk actions, and tags — the latest round

**Pages of cases instead of one long list.** The board now loads cases a page at a time
(100 per page) with Prev/Next buttons, instead of fetching everything at once. The four
stat cards at the top (New / Follow Up / Resolved / Overdue) always reflect the whole
channel regardless of which page or filters you're looking at — they're fetched
separately on purpose, so paging or filtering the table never changes what the stat
cards say.

**Export to CSV.** An **Export CSV** button on the board downloads the currently
filtered list of cases as a spreadsheet file, so the team can share a snapshot or
work with it outside the board.

**Select several cases and update them all at once.** Checkboxes next to each case row
(plus a "select all" checkbox in the header) let you select multiple cases. A small
toolbar appears showing how many are selected, with dropdowns to set a status,
priority, or assignee in one click — instead of opening each case individually.

**Tags.** Any case can now be labeled with one or more free-form tags (e.g. "billing",
"vip", "follow-up-next-week"). Tags show as small rounded chips on the case detail
page, where you can add a new one by typing it and pressing Enter, or remove one by
clicking its **×**. The board's filter bar has a tag box to show only cases carrying a
given tag.

**Saved filters.** Once you've set up a combination of filters you use often (a status,
a priority, a tag, "my cases" toggled on), you can save it under a name and pick it
again later from a dropdown — instead of resetting every filter by hand each time. Each
person's saved filters are their own; nobody else sees them.

---

## 8. A map of every file, and what it's for

### Backend (`functions-support/`) — the part that runs on Google's servers

| File | What it does |
|---|---|
| `main.py` | The single entry point. Starts the web app, registers all the feature modules below, and checks that every request has a valid signed-in user before anything else runs. Also answers `GET /api/support/me` (used by pages to ask "who am I, and am I an admin?"). |
| `handlers/shared.py` | Shared plumbing used by everything else: connecting to the database, looking up a user's role, and standard "success"/"error" response formats. |
| `handlers/cases.py` | Everything about cases: listing them, viewing one case's full thread, changing status/priority/assigned person, sending a reply, adding an internal note, transferring a case to another channel, and the dashboard counters (New/Follow Up/Resolved/Overdue). |
| `handlers/mail_check.py` | The endpoint that triggers "go read the inbox now" — called automatically every 10 minutes, or manually by the **Check mail** button. |
| `handlers/sla_check.py` | Scans for cases approaching their 24-hour deadline and emails a warning to the assigned agent (or a fallback admin) before it's overdue. |
| `handlers/channels.py` | Lists every configured mailbox/channel (with the new unread/overdue badges), and — for admins only — creates, edits, or deletes one. |
| `handlers/users.py` | Lists every teammate who has a role, for the "assign this case to..." dropdowns on the board and case detail page. |
| `handlers/saved_filters.py` | Stores and returns each signed-in user's own saved filter combinations, for the dropdown on the board. |
| `support_mail/mail_checker.py` | The actual mail-reading logic described in section 3: connecting over IMAP, deciding what's spam/bounce vs. a real message, detecting whether it's a reply to an existing case, and creating new case records. |
| `support_mail/reply_sender.py` | The actual mail-sending logic: connecting over SMTP and sending replies, acknowledgements, and SLA warning emails. |
| `support_mail/templates.py` | The HTML/text templates used for those automatic emails. |
| `Tests/run_support.py` (+ `.bat`/`.sh` launchers) | A command-line tool for running things manually from a terminal — checking mail, viewing stats, creating a user account, etc. — useful for testing without opening a browser. |

### Frontend (`public/`) — the actual web pages

| File | What it does |
|---|---|
| `login.html` | Sign-in page. |
| `board.html` | The main case list for one channel: stats cards, search/filter bar, the cross-channel search box, and the table of cases with quick-action buttons (Reply, Follow Up, Not Interested, Transfer). |
| `case_detail.html` | One case's full conversation thread plus the reply box and internal notes. |
| `channels_admin.html` | The new admin-only page for managing mailboxes (add/edit/delete). |
| `index.html`, `support_index.html`, `support_case_detail.html` | Old bookmarked links — they just redirect to the right page on the new system, so nothing breaks for people with saved links. |
| `js/channels.js` | A small shared helper used by every page: fetches the channel list once, and draws the pill-button row at the top of the page (including the new badges). |
| `js/firebase-config.js` | Connection details for the login system (which Firebase project to talk to). Not committed to version control since it's environment-specific. |
| `css/styles.css` | All the visual styling (colors, spacing, badges, buttons) — written once and reused by every page, rather than each page inventing its own look. |

### Top-level

| File | What it does |
|---|---|
| `README.md` | The technical reference: API list, Firestore data shape, setup steps, deployment commands. |
| `firebase.json` / `.firebaserc` | Configuration telling Google Cloud which project this belongs to and how to deploy it. |
| `blueboot-support.secrets.py` | Local secrets file (database credentials, admin email list) — never shared or committed. |

---

## 9. How data is actually stored (Firestore, in plain terms)

Imagine a filing cabinet:

```
settings/                              ← system-wide configuration
  mail_accounts/accounts/{email}       ← one folder per mailbox: host, port, password, label...
  users/users/{email}                  ← one folder per teammate: their role
  support_meta                         ← case ID counters, admin email list

support_mail_accounts/{email}/cases/{case_id}/   ← one folder per case, inside its channel
  history/{message}                    ← every email in/out + internal notes, in order
  actions/{event}                      ← an audit trail: "status changed," "replied," etc.

support_email_index/{message_id}       ← a checklist of "already processed" emails,
                                          so the system never creates the same case twice
```

Nothing here is a spreadsheet — it's nested folders of labeled records, which is why the
backend code talks about "documents" and "collections" instead of rows and columns.

---

## 10. Where things run, and how a change goes live

The backend code lives in Google Cloud as a single Cloud Function. The frontend pages are
hosted as static files (no server needed to "run" them — they're just files a browser
downloads and executes). Both are deployed with one command:

```
firebase deploy --only functions:support,hosting:blueboot-support
```

A Cloud Scheduler job calls the backend automatically every hour to check mail and scan
for SLA warnings — that's why cases can appear without anyone touching the board.

---

## 11. If something looks wrong

A few starting points, in plain terms:

- **A case isn't showing up** — check whether the email was actually sent to a configured
  channel address, and whether it might have been filtered out as an automated/bounce
  message (the system deliberately ignores anything that looks like a noreply/bounce).
- **A reply didn't go out** — that's the SMTP (sending) side; double-check the channel's
  password and SMTP host on the Channels admin page (admins only).
- **No new mail is appearing at all** — that's the IMAP (reading) side; same idea, check
  the channel's IMAP host/port/password.
- **Someone can't see the board at all** — they probably don't have a role assigned yet
  (`guest`). An admin needs to set their role in the Channels/user settings.

---

*This guide describes the system as of the latest update (channel badges, "My cases"
filter, channel management page, cross-channel search, paginated board with CSV
export, bulk case actions, and tags with saved filters). For the exact API list and
data shapes, see `README.md`.*
