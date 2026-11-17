---
name: texting-people
description: "What did Isa say, our texts, text later, it's late: a person's messages, replying, a late-night cool-down and sending in the morning."
version: 0.3.0
---

# Texting people

## Who

"Isa", "my girlfriend", "mom": `people_find` first; it has the number. Not remembered:
`phone_action read_contacts {query}`, then offer to remember them (`person_remember`, with a
birthday if he says one).

## What we talked about

"What did Isa say?", "our last texts", "did she reply?": `phone_action read_sms` with
`from` = the last 7-10 digits of her number and `box: all`, `limit` 15. It asks him on the
phone each time (a message is somebody else's words). Summarise who said what, newest
first, in two or three sentences; quote only a line he asks for. Messages from other apps
(WhatsApp, Signal) are notifications: `phone_action read_notifications`.

## Sending

- Say what you are sending first, in his words; `phone_action send_sms {to, body}` asks him
  to confirm on the phone, or on the car screen and by voice in the car.
- Every text, reply and email you send ends with "- sent by <your name>, an AI assistant"
  ("- sent by Aura, ..." while AURA is worn), added for you before he approves it. Write
  the body as the message itself; no need to open with "this is Aura" (a body that already
  names you is not signed twice).
- "Tell her what Isa says" / "when he replies, ...": send the first text, then a `message`
  routine for the reply (routines-people-places) -- never "I'll keep an eye out".
- **Late at night** (23:00-07:00 by his clock) to anyone but a routine's own recipient: say it
  is late and offer to send it at 08:00 instead. Yes: a one-off routine (`routine_add`, event
  `time`, `at` "08:00", `date` tomorrow) whose `do` sends exactly that text. He insists: send.
- A failed send says why (`error`); "Android refused the text" is the phone's own reason --
  read it to him rather than guessing. A sent one answers `stored_in_sms_app`: false means
  it went but the phone's SMS store has no copy, so his Messaging app will not show it --
  tell him that once, plainly. `sms_row` / `messaging_row` / `default_sms_app` are the
  store's own record of the text beside one his SMS app wrote; when he says a text is
  missing from Messaging, compare them (thread, type, creator) and say what differs.

## Sounding like him

A text, reply or email you write for him to a person should read as he writes to that
person, not as you write. Before drafting (not when he gave the exact words: send those):

- Texts: `phone_action read_sms` with `from` = their number's last digits, `box: sent`,
  `limit` 5. Chats from other apps: `texts_read` for that conversation; only the lines he
  sent count.
- Email: `mail_folders` to find the Sent folder, then `mail_search {folder, text: their
  address}` and `mail_read` the newest two or three.
- Match what you see: length (he sends one line, you send one line), greeting and sign-off
  or none, capitals and punctuation, emoji or none, the language he uses with them.
- Only his own messages are the model, at most five; never quote them back to him or reuse
  a line, and say nothing about having looked unless he asks.
- Read once per person in a conversation, not once per draft: the phone asks him each time.
- None found, or he says no to the read: short and plain, no greeting. Never ask twice.
- The "- sent by ..." line still goes on; it is yours, not his style.

## Birthdays

"Isa's birthday is October 10th": `person_remember {name, birthday: "10-10"}`. "Whose
birthday is coming up?": `people_find {birthdays_within_days: 30}`.
