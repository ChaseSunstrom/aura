---
name: media-convert
description: "Convert a video to mp4, trim a clip, extract its audio: convert-media.sh, one local file to another, its exit codes, and how the result is shown."
version: 0.1.0
author: JARVIS
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [Media, Video, Audio, Scripts]
    related_skills: [media-fetch, audio-process, file-delivery]
---

# Converting, trimming or extracting from a media file

"Convert this to mp4", "cut the first thirty seconds", "just the audio of that
clip", "make it 720p so the phone can play it" — all of it is one script:

```bash
convert-media.sh --in /opt/data/work/talk.mkv --out /opt/data/work/talk.mp4
convert-media.sh --in /opt/data/work/talk.mp4 --out /opt/data/work/intro.mp4 --start 00:01:05 --duration 30
convert-media.sh --in /opt/data/work/talk.mp4 --out /opt/data/work/talk.mp3 --audio-only
convert-media.sh --in /opt/data/work/talk.mp4 --out /opt/data/work/talk-720.mp4 --height 720
```

It is on `PATH` in the image (`/usr/local/bin/convert-media.sh`, from the
scripts library in the repo). Call it by that name from any directory; do not
look for it under a path, and do not write your own ffmpeg line if the name is
not found — that is exit code 2's situation, below.

The container comes from `--out`'s extension. Without `--copy` ffmpeg re-encodes
to that container's default codec, which always works and takes real time on a
long file; `--copy` keeps the streams as they are, which is instant and is
refused by ffmpeg when the container cannot hold them (an mkv's codecs into an
mp4, usually fine; into a webm, usually not). Try `--copy` first for a pure
container change and fall back without it on exit 4.

It prints **one line of JSON** and nothing else on stdout:

```json
{"ok":true,"path":"/opt/data/work/talk.mp4","bytes":18337122,"duration_s":212.4,"stream":"video"}
```

Read the field. What is on stderr is progress, not output.

## Where the files live

`--in` and `--out` must both be under `/opt/data/work` or `/opt/data/files`,
the two directories the shell shares with the rest of the house (ADR-0045).
Anything else is exit 3, on purpose: a file elsewhere is either invisible to
whoever asked or something nobody meant a shell to rewrite. The input is already
there if the person put it there, if the browser downloaded it
(`/opt/data/files`), or if you wrote it. The shell cannot `curl` a URL — there
is no network in it — so a file that is only at a URL is a `media-fetch` job or
a browser download, not a shell one.

## Do not rebuild this

Working out a format string, remembering that `-ss` before `-i` is the fast
seek, checking that the output actually has the stream you asked for: all of it
is in here. Reaching for `ffmpeg` directly is redoing solved work with the
checks missing.

## The exit codes mean different things

| Code | Meaning | What to do |
|---|---|---|
| 1 | Usage | Fix the arguments; do not retry as-is. `--copy` cannot `--height`; `--out` needs an extension |
| 2 | A tool is missing from the image | File a capability gap and open the merge request. **Never install it at runtime** |
| 3 | A path is refused | Outside the two shared directories, or the input does not exist. Move or name the file; do not relax the check |
| 4 | ffmpeg failed | The file or the flags. `--copy` into a container that cannot hold the codec is the usual one: drop `--copy` and re-encode |
| 5 | The output is not what was asked for | ffmpeg returned 0 and left an empty file or one with no stream. Do not publish it; say so |

Nothing is left behind on a failure, so a retry starts clean.

## After it succeeds

The path is inside the container. **Never answer with it.** Publish the file
with `artifact_publish` (`path` from the JSON, `mime` such as `video/mp4` or
`audio/mpeg`), then `surface_capabilities` tells you whether the surface the
person is on can play it; if not, hand it to one that can and say so in one
sentence. The `media-fetch` skill's "After you have the id" section is the same
procedure.
