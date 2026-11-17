---
name: media-fetch
description: "Download a video, fetch this clip into the house: the media_fetch tool that does it, why it verifies the download, and what to do with the artifact id it returns."
version: 0.1.0
author: JARVIS
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [Media, Video, Artifacts, Scripts]
    related_skills: [file-delivery, generating-images]
---

# Fetching a video

"Download that video", "find me a clip of X and play it", "grab this and put it
on the TV" — all of it is one tool call:

```json
media_fetch {"url": "https://…", "quality": 720}
media_fetch {"search": "funny meme compilation 2015"}
```

It asks for approval once (it pulls a remote file into the house) and then runs
`fetch-video.sh` in the gateway, which has the network the shell does not have
(ADR-0045). Do not run the script from the terminal: the download step reaches
nothing there and it exits 3 whatever the URL. Do not fetch another way from the
shell, and do not install a downloader if the tool says one is missing -- that is
exit code 2's situation, below, and the capability-gap skill is the route.

The tool returns the script's **one JSON object**:

```json
{"ok":true,"id":"<64 hex>","url":"http://…/api/artifacts/<id>","title":"…","duration_s":212.4,"quality":720}
```

Read the field. A failure comes back as `{"ok": false, "exit_code": N, "error": "…"}`,
with the code's meaning spelled out in `error`.

## Do not rebuild this

This script exists because the same task once took about forty tool calls of
discovery: finding a downloader, working out a format string, noticing the file
was wrong, inventing a verification step. All of that is in here. If you find
yourself reaching for `yt-dlp` directly, or writing a format selector, or
checking a download by hand, stop — you are redoing solved work, and the version
in your head is missing the checks.

## Why it verifies, and why you should not skip it

A download can succeed and produce something that is not a video:

- a 2 KB HTML error page with a `.mp4` name,
- a zero-length file,
- an audio-only stream, because the format selector fell back,
- a container with a video stream that does not decode.

Every one of those is a file that exists, so "the command returned 0" proves
nothing. The script asks ffprobe for a video stream, ffprobe for a duration over
a second, and ffmpeg for **one real decoded frame**. If it exits 4, the download
happened and the result is not a video — say that, rather than reporting success.

## The exit codes mean different things

| Code | Meaning | What to do |
|---|---|---|
| 1 | Usage | Fix the arguments; do not retry as-is |
| 2 | A tool is missing from the image | File a capability gap and open the merge request. **Never install it at runtime** — production is replaced on the next deploy and fetching it reaches past the egress allowlist |
| 3 | The download failed | The URL, the network or the site. Retrying once is reasonable; twice is not |
| 4 | It downloaded something that is not a video | Do not retry the same URL at the same quality. Try another quality or another source, or say the source does not have one |
| 5 | Publishing failed | The artifact store, not the video. Check the adapter is up |

Retrying a 4 as though it were a 3 is the mistake worth naming: the download
worked, so it will work again, and it will be wrong again.

## After you have the id

The file is in the artifact store, not on a filesystem anyone can reach. **Never
answer with a path** — the id and its URL are what every surface can open.

1. `surface_capabilities` — can the surface this person is on play video?
2. If yes, show it there.
3. If no, hand it to one that can and say so **in one sentence**: "The terminal
   cannot play that, so I have put it on the desktop." Do not describe the video,
   render frames as text, or offer ASCII art unless you were asked for that in
   those words.
4. `artifact_pin` if it should outlive the default day.

## Quality

`quality` is a ceiling, not a request: `720` means "the best at 720p or below".
Ask for what the surface can use rather than the most there is — a phone does not
benefit from 2160p and the download takes proportionally longer.
