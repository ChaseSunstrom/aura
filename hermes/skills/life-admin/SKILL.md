---
name: life-admin
description: "What's due, renewals, warranty, refill, bills, deadlines: note a due date, read what's coming up, tick it off."
version: 0.1.0
---

# Life admin: what is due

One ledger of dated obligations, Jarvis's own (`ledger_add`, `ledger_list`, `ledger_done`).
The morning brief reads the next week of it; nothing else nags.

## Noting one

- "My registration is due October 20th", "remind me the passport expires in March":
  `ledger_add {title, due: "YYYY-MM-DD", kind: renewal}`. Resolve relative dates yourself
  ("end of the month" is its last day); a month with no day is its first. Say it back once.
- **A receipt** (a photo or a PDF he sends): read it (image-inspection / reading-documents),
  then `ledger_add {title: "<item> warranty", kind: warranty, due: purchase date + the
  warranty's length, notes: store, price, serial}`. A year when nothing says otherwise.
- **Medication**: "I just picked up a 30-day Lipitor", `ledger_add {title: "Lipitor refill",
  kind: refill, due: today + 27 days, every_days: 30}` -- a few days early, so there is time.
- **Bills**: `kind: bill` with the amount in `notes`; `every_days` 30 for a monthly one.

## Reading it

"What's due?", "anything coming up?": `ledger_list {within_days: 30}`. Overdue first, then
by date; in the car at most three, said as "X on Tuesday". "When does the TV warranty end?":
`ledger_list {within_days: 3650, kind: warranty}`.

## Ticking off

"I renewed the registration", "paid it": `ledger_done {title: "registration"}`. A recurring
one moves to its next date; say when.

Never put these in fact_store: a date is not knowledge, and memory is searched every turn.
