---
name: image-edit
description: "Resize, crop, rotate, convert or strip the metadata from an image: edit-image.sh, one local file to another, its exit codes, and how the result is shown."
version: 0.1.0
author: JARVIS
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [Media, Image, Scripts]
    related_skills: [generating-images, image-inspection, file-delivery]
---

# Editing an image

"Make this smaller", "crop it to the face", "turn it into a png", "remove the
location from this photo before I send it" — all of it is one script:

```bash
edit-image.sh --in /opt/data/work/photo.jpg --out /opt/data/work/photo-small.jpg --resize 1280x720
edit-image.sh --in /opt/data/work/photo.jpg --out /opt/data/work/face.png --crop 400x400+120+80
edit-image.sh --in /opt/data/work/photo.jpg --out /opt/data/work/photo-clean.jpg --strip
edit-image.sh --in /opt/data/work/scan.png --out /opt/data/work/scan.jpg --rotate 90 --quality 85
```

It is on `PATH` in the image (`/usr/local/bin/edit-image.sh`, from the scripts
library in the repo). Call it by that name from any directory; do not look for
it under a path, and do not write your own `magick` line if the name is not
found — that is exit code 2's situation, below.

The format comes from `--out`'s extension, so the same call converts. The
operations run in the order a person means them: crop, then resize, then
rotate. `--resize` is ImageMagick geometry: `1280x720` fits inside that box
keeping the aspect ratio, `1280x720!` forces it, `50%` scales. `--strip` drops
EXIF, ICC and the rest of the metadata — GPS included — which is what "remove
the location" means. `--quality` is 1–100 and only matters to lossy formats.

It prints **one line of JSON** and nothing else on stdout:

```json
{"ok":true,"path":"/opt/data/work/photo-small.jpg","bytes":183371,"width":1280,"height":720}
```

Read the field. What is on stderr is progress, not output. The width and height
are read back from the file it wrote, not computed from the flags, so they are
what a viewer will see.

## Where the files live

`--in` and `--out` must both be under `/opt/data/work` or `/opt/data/files`,
the two directories the shell shares with the rest of the house (ADR-0045).
Anything else is exit 3, on purpose. A generated image (`image_generate`) is
already on the container; a photo the person sent arrives under one of those
directories; a browser download lands in `/opt/data/files`. The shell cannot
`curl` a URL — there is no network in it.

## Do not rebuild this

`+repage` after a crop, the read-back that catches a format magick can write but
not decode, the refusal of a path a coder prefix could hijack: all in here.
Reaching for `magick` or `convert` directly is redoing solved work with the
checks missing.

## The exit codes mean different things

| Code | Meaning | What to do |
|---|---|---|
| 1 | Usage | Fix the arguments; do not retry as-is. `--out` needs an extension; `--quality` is 1–100 |
| 2 | A tool is missing from the image | File a capability gap and open the merge request. **Never install it at runtime** |
| 3 | A path is refused | Outside the two shared directories, or the input does not exist. Move or name the file; do not relax the check |
| 4 | magick failed | The file is not an image it can read, or the geometry is nonsense. Say which; do not retry the same call |
| 5 | The output cannot be read back | magick returned 0 and left an empty or undecodable file. Do not publish it; try another format |

Nothing is left behind on a failure, so a retry starts clean.

## After it succeeds

The path is inside the container. **Never answer with it.** Publish the file
with `artifact_publish` (`path` from the JSON, `mime` such as `image/png` or
`image/jpeg`) and it shows where the person is. If they asked what is *in* the
image rather than for a changed one, that is the `image-inspection` skill, not
this one.
