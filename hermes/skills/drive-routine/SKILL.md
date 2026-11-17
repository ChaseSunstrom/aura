---
name: drive-routine
description: "When I start driving, on the way to work, in the car: setting up what Jarvis does each time Sir gets in the car, answering the phone's '[event: drive started]', and turning a pattern you notice into a routine he agrees to."
version: 0.1.0
---

# The drive routine

The phone tells you when a drive starts. Once per trip, a moment after the car link is up,
a turn arrives on the car surface reading:

```
[event: drive started] Sir has just connected to the car; nobody asked anything. Without reading a skill: phone_action read_calendar {days_ahead: 1, limit: 3} and phone_action read_notifications {limit: 5}. If an event starts within two hours and has a location, place_search that location and car_navigate to it. Then say at most two short sentences: the route if you set one, and who has messaged him from a messaging app (names only, never what they wrote). When the route is over twenty minutes, end by offering a radio show. No route and no messages: reply with exactly (quiet).
```

It is an event, not a question from Sir. Nobody is waiting on an answer, and the event
carries the whole procedure so the turn does not stop to read this skill first.

Routines saved with `routine_add` (event `car_connected`) do **not** run here: the phone
fires each one as its own `[routine: ...]` turn (the routines-people-places skill). An older
routine kept in memory does not run either: move it to `routine_add` when you find one.

## Answering the event

1. Look at where he is probably going: the phone's calendar (`phone_action read_calendar`;
   `calendar_events` only answers when the house has a calendar configured) for the next two hours. The first
   event with a location he has not already been routed to: `car_navigate` to that place and
   say it in one sentence ("Your dentist appointment is at 3:30 on Main Street; the route is
   up."). An event with no location, or no calendar, adds nothing. This is not a routine and
   needs none (the owner, 2026-10-06: "make it actually good ... maps").
2. Who is waiting on him: `phone_action read_notifications` lists the message notifications
   on the phone without asking him anything (`read_sms` would put an approval in front of him
   at every drive start). Say who, from a messaging app, by name only — never what they
   wrote; he asks for that ("what did Isa say?").
3. A route over twenty minutes: end with one offer of a radio show ("It's a long one — want a
   radio show?"). A question waits for his answer; yes runs the drive-fun skill's radio show.
   Offer it, never start it, and never on a short drive.
4. No route and no messages: reply with exactly `(quiet)` — the phone says nothing for that
   answer. Do not greet him, do not offer anything (the owner, 2026-10-08, asked for Jarvis to
   be proactive, not chatty).

Learned places (`jarvis-hud/learned.py`): every `car_navigate` destination and every routine
arrival is a place with a visit count. When the drive starts, the open `later_add` asks whose
`where` is one of those places come with the event as a quoted note, to name in one sentence;
on arriving at a place, the last thing the later queue held about it does, once per visit.
Nothing is added when nothing applies.

## The drive's end

When the car has been gone fifteen minutes after a drive of five minutes or more (a link that
drops and returns is the same drive), the phone sends one turn on its own link, not the car's:

```
[event: drive ended] Sir's drive is over; nobody asked anything. Without reading a skill: later_release {when: parked}, then loops_list. Then say at most two short sentences: what you have started on from his later queue, if any was released, and what is still open from this drive, by name. If you mis-heard or failed him on this drive, end by asking exactly: Anything I got wrong on that drive? Nothing released, nothing open and nothing wrong: reply with exactly (quiet).
```

`later_release` makes the asks queued for `parked` due now, so the background worker takes
them at its next ten-minute check; it only re-dates, so nothing gated runs unasked. The reply
reaches him as a phone notification, so keep it to the two sentences. This event cannot
`later_add` or `loops_close` (events are not Sir speaking); an answer to the question is his
next turn, and he can then.

## Setting one up

"When I get in the car, tell me the weather and my first meeting" is a routine:
`routine_add` with `when: {event: car_connected}` (conditions as `days` / `after` /
`before`), and read `said` back. When you find an old **Drive routine** in memory, move
it to `routine_add` the next time it comes up, and remove it from memory.

Time-based parts ("at 7 every weekday, tell me …") are cron jobs, not the drive routine —
see the jarvis-cron-jobs skill.

## Learning the routine

When you notice the same request on several drives ("traffic to work" three mornings running),
offer once, at the end of an answer, to make it the drive routine. Never add one without his
yes; never offer the same one twice.
