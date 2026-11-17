---
name: catch-me-up
description: "Catch me up, what did I miss, what's going on: one spoken digest of texts, notices, calendar, goals and the house since he last asked."
version: 0.1.0
---

# Catch me up

"Catch me up", "what did I miss?", "anything I should know?", "what's going on?". One digest,
said, not a report. In the car, at most four sentences; anywhere else, at most six lines.

## Gather (skip whatever fails; a failed source costs its line, never the digest)

1. **Messages and notices**: `phone_action read_notifications` for what is waiting on the
   phone (texts and chats, apps, missed calls) — it asks him nothing; leave out the ones
   Jarvis itself posted. Name who wrote and what they want, never read a whole message unless
   he asks.
2. **Texts, when he asks for them**: `phone_action read_sms` reads the SMS store itself, and
   it is confirm-tier — it asks him first, so it is for "what did she say exactly?", not for
   the digest (in the car that prompt lands while he is driving).
3. **Next on the calendar**: `phone_action read_calendar {days_ahead: 1, limit: 3}`. If one has
   a place and he is in the car, `route_eta` to it and say when to leave.
4. **Goals and jobs**: `goal_status` for anything running or waiting on him.
5. **The house**: only what changed and matters (a door left open, an alarm), from
   `house_history` over the last few hours.

## Say it

Most important first: a person waiting on him, then something time-bound, then the rest.
Nothing in a source: leave it out, do not say "no new texts". Everything quiet: "Nothing
needs you." End with one offer when there is an obvious next step ("Want me to reply to
Isa?"), never a list of options.

Do not save the digest to memory.
