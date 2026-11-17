---
name: drive-fun
description: "Radio show, trivia, hype me up, DJ, entertain me: on a drive, a spoken segment, a quiz or a hype-man between songs."
version: 0.1.0
---

# Fun on a drive

Only when he asks ("give me a radio show", "quiz me", "hype me up", "be my DJ"). Nothing
here starts on its own. In the car every line is spoken: short sentences, no lists, no
numbers he has to remember, never longer than the segment he asked for.

## Radio show ("give me a radio show", "what's happening")

One segment of about a minute, said with `hud_say`, then back to his music:

1. A headline or two he would care about: `feeds {action: news, limit: 2}` first (the sites
   he follows, only what he has not heard); nothing there, `web_search` for today's news in
   his interests (memory says what they are; tech and the homelab if nothing does). One
   sentence each, with where it came from.
2. The road ahead: `route_eta` to where he is going if a route is up; the weather there from
   `weather_forecast` (lat/lon of the destination, or the car's from the request).
3. Something light: an on-this-day fact, or a song pick that fits the drive.
4. Hand back: "Back to the music." and `phone_media` play if music was paused.

Never invent a headline: a search that fails costs the news, not the show.

## Trivia ("quiz me", "trivia")

Five questions, one at a time, on a topic he picks (or general knowledge). Ask, wait for his
answer, say right or the answer in one sentence, keep score, end with the score. Questions
a driver can answer without looking at anything. "Stop" ends it.

## Hype-man DJ ("hype me up", "be my DJ")

Pick the next song to match what he asked (pump-up, chill, focus): `phone_media` play by
artist or genre. Before it, one or two lines of introduction in the persona's voice --
energetic, never cheesy, never longer than ten seconds. Between later songs only if he asks
again.

## Mood

Read the room: no jokes during a call, a tense turn or bad news; a short ride gets a
shorter segment. "Not now" ends any of these at once.
