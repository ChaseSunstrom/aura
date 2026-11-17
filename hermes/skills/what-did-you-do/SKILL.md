---
name: what-did-you-do
description: "What did you do today, yesterday, this week: Jarvis's own actions read back from the audit log, said as a short digest."
version: 0.1.0
---

# What did you do

"What did you do today?", "what have you been up to?", "did you send anything yesterday?",
"what did you change this week?". The answer is the audit log, never memory: every gated tool
call, approval, setting, message out, deploy and kill-switch change is in it, arguments
already redacted.

## Read

`audit_query {since: "today", summary: true}`. "Yesterday" is `since: "yesterday", until:
"yesterday"`; "this week" is `since: "week"` (this Monday); a named day is that date
(`YYYY-MM-DD`) as both `since` and `until`. A refused date or an empty range comes back as a sentence:
say it, do not retry with a guess.

## Say it

Counts first, in one sentence ("Forty-two actions today, mostly reading the house."), then
each `notable` record in a few words, most consequential first: a message sent and to whom,
a setting changed, a deploy, a refusal and why, the kill switch. If `notable_total` is larger
than the list, say how many more there were. Nothing notable: "Nothing that changed anything."
Never read a redacted value aloud or guess what it was. In the car, at most three sentences.

Do not save the digest to memory.
