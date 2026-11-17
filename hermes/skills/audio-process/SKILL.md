---
name: audio-process
description: "Normalise, trim, resample or convert an audio file (wav, mp3, flac, ogg): process-audio.sh, one local file to another, and the 16 kHz mono the voice pipeline wants."
version: 0.1.0
author: JARVIS
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [Media, Audio, Voice, Scripts]
    related_skills: [media-convert, jarvis-voice, file-delivery]
---

# Processing an audio file

"Make this louder", "cut the first ten seconds", "turn it into an mp3", "get
this recording into the shape whisper wants" — all of it is one script:

```bash
process-audio.sh --in /opt/data/work/rec.wav --out /opt/data/work/rec-loud.wav --normalise
process-audio.sh --in /opt/data/work/rec.wav --out /opt/data/work/rec-cut.wav --start 10 --duration 60
process-audio.sh --in /opt/data/work/rec.wav --out /opt/data/work/rec.mp3
process-audio.sh --in /opt/data/work/rec.mp3 --out /opt/data/work/rec-16k.wav --rate 16000 --channels 1
```

It is on `PATH` in the image (`/usr/local/bin/process-audio.sh`, from the
scripts library in the repo). Call it by that name from any directory; do not
look for it under a path, and do not write your own `sox` line if the name is
not found — that is exit code 2's situation, below.

The format comes from `--out`'s extension, so the same call converts (sox in
the image reads and writes wav, flac, mp3, ogg and the rest). The effects run in
the order sox needs them — trim, channels, rate, then the normalise — so the
peak is measured on what is left, not on what was cut. `--normalise` (or
`--normalize`) brings the peak to 0 dBFS. `--start` and `--duration` are
seconds. `--rate 16000 --channels 1` is what faster-whisper and the speaker gate
want; a recording that transcribes badly is usually one that never got this.

It prints **one line of JSON** and nothing else on stdout:

```json
{"ok":true,"path":"/opt/data/work/rec-16k.wav","bytes":1920044,"duration_s":60.0,"rate":16000,"channels":1}
```

Read the field. What is on stderr is progress, not output. The rate, channel
count and duration are read back from the file it wrote, not copied from the
flags.

## Where the files live

`--in` and `--out` must both be under `/opt/data/work` or `/opt/data/files`,
the two directories the shell shares with the rest of the house (ADR-0045).
Anything else is exit 3, on purpose. The shell cannot `curl` a URL — there is
no network in it — so a file that is only at a URL is a browser download into
`/opt/data/files` first. The audio track of a *video* is `convert-media.sh
--audio-only`, then this.

## Do not rebuild this

The effect order, the explicit `rate` and `channels` effects (sox inserts them
itself when the rates differ, but with a warning a caller reading stderr for
errors trips on), the read-back that catches a file nothing will play: all in
here. Reaching for `sox` or `ffmpeg` directly is redoing solved work with the
checks missing.

## The exit codes mean different things

| Code | Meaning | What to do |
|---|---|---|
| 1 | Usage | Fix the arguments; do not retry as-is. `--out` needs an extension; `--rate` is Hz; `--channels` is 1–9 |
| 2 | A tool is missing from the image | File a capability gap and open the merge request. **Never install it at runtime** |
| 3 | A path is refused | Outside the two shared directories, or the input does not exist. Move or name the file; do not relax the check |
| 4 | sox failed | The file is not audio it can read, or a trim past the end. Say which; do not retry the same call |
| 5 | The output cannot be read back | sox returned 0 and left an empty file or one with no duration. Do not publish it |

Nothing is left behind on a failure, so a retry starts clean.

## After it succeeds

The path is inside the container. **Never answer with it.** Publish the file
with `artifact_publish` (`path` from the JSON, `mime` such as `audio/wav` or
`audio/mpeg`); `surface_capabilities` tells you whether the surface the person
is on can play it, and if not, hand it to one that can and say so in one
sentence. To *speak* something, that is `hud_say`, not a file.
