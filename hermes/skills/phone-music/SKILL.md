---
name: phone-music
description: "Play music, a song, an album, an artist, a playlist, queue a song, pause or skip: on Sir's phone (and the car it is connected to) through phone_media, not the house's speakers."
version: 0.2.0
---

# Music on the phone

When Sir asks for music -- "play Bohemian Rhapsody", "put on Discovery by Daft Punk",
"play some Queen", "queue Starlight", "pause", "next song" -- and does not name a
speaker in the house, it is his phone's music, and in the car it plays through the car.

1. Split what he said into fields and pass each alone -- the music app matches field by
   field and answers "No results" to a sentence:
   - a song: `kind: song`, `title`, and `artist` if he said it
   - an album: `kind: album`, `album`, `artist`
   - an artist: `kind: artist`, `artist`
   - a playlist: `kind: playlist`, `playlist`; a genre: `kind: genre`, `genre`
   Never put "play" or "by" in a field.
2. "Queue", "add ... next", "after this": `action: queue` with the same fields.
3. `pause`, `next`, `previous`, or `play` with no fields to resume.
   "Shuffle my liked songs", "play Discovery on shuffle": `shuffle: true` with the fields;
   "shuffle this" is `play` with only `shuffle: true`.
4. Say it in a few words: "Playing Discovery." If the result says no phone is connected,
   say so and offer the house's speakers instead.

Only a speaker he names ("in the kitchen", "on the living room TV") is Home Assistant's
media player. Never search Home Assistant for music he asked to hear on the phone.
