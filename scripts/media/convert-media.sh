#!/usr/bin/env bash
# scripts/media/convert-media.sh — change a media file's container, codec or length.
#
#   convert-media.sh --in IN --out OUT [--start 00:01:05] [--duration 30]
#                    [--audio-only] [--copy] [--height 720]
#
# A local file in, a local file out. The container comes from OUT's extension
# (.mp4, .mkv, .webm, .mp3, .m4a, .wav …) and ffmpeg re-encodes to that
# container's default codec unless --copy asks for the streams untouched: fast,
# and refused by ffmpeg when the container cannot hold them. --audio-only drops
# the video; --height scales to that height keeping the aspect ratio; --start and
# --duration trim (ffmpeg time syntax: seconds or HH:MM:SS.ms).
#
# Both paths must sit under /opt/data/work or /opt/data/files, the two places the
# sandbox and the gateway share (ADR-0045). A file anywhere else is either
# invisible to whoever asked or something nobody meant a shell to rewrite, so it
# is refused rather than converted.
#
# On success it prints one JSON object on stdout; progress goes to stderr. A
# failure exits non-zero and leaves no output file behind, so a caller never
# publishes a half-written one.
#
# Exit codes: 1 usage, 2 a tool is missing, 3 a path is refused (outside the two
# shared roots, or an input that does not exist), 4 ffmpeg failed, 5 the output
# is not what was asked for (empty, or missing the stream the input had).
set -euo pipefail

IN=""; OUT=""; START=""; DURATION=""; HEIGHT=""
AUDIO_ONLY=0; COPY=0

die() { echo "convert-media: $2" >&2; exit "$1"; }

usage() {
  sed -n '2,25p' "$0" | sed 's/^# \{0,1\}//'
  exit 1
}

while [ $# -gt 0 ]; do
  case "$1" in
    --in)         IN="${2:?--in needs a value}"; shift 2 ;;
    --out)        OUT="${2:?--out needs a value}"; shift 2 ;;
    --start)      START="${2:?--start needs a value}"; shift 2 ;;
    --duration)   DURATION="${2:?--duration needs a value}"; shift 2 ;;
    --height)     HEIGHT="${2:?--height needs a value}"; shift 2 ;;
    --audio-only) AUDIO_ONLY=1; shift ;;
    --copy)       COPY=1; shift ;;
    -h|--help)    usage ;;
    *) die 1 "unknown argument: $1" ;;
  esac
done

[ -n "$IN" ] && [ -n "$OUT" ] || die 1 "--in and --out are required"
[ "$COPY" = 0 ] || [ -z "$HEIGHT" ] || die 1 "--copy keeps the streams as they are, so it cannot --height"
[ "$AUDIO_ONLY" = 0 ] || [ -z "$HEIGHT" ] || die 1 "--audio-only drops the video, so there is nothing to --height"
case "$OUT" in *.*) ;; *) die 1 "--out needs an extension: that is how the container is chosen" ;; esac

# Checked up front rather than discovered halfway through. The message names the
# merge request because that is the only way to fix it: production is immutable
# and a runtime install is lost on the next deploy.
for tool in ffmpeg ffprobe; do
  command -v "$tool" >/dev/null 2>&1 || die 2 \
    "$tool is not in this image. File a capability gap and open the merge request that adds it; do not install it at runtime."
done

# Where the sandbox and the gateway both look (ADR-0045). Overridable so a test
# can point it at a temp dir; the sandbox binds nothing else, so the override
# opens no door the sandbox has not already closed.
ROOTS="${AURA_DATA_ROOTS:-/opt/data/work:/opt/data/files}"

# Canonical, symlinks resolved, so a link out of the shared dirs is refused too.
inside() {
  local p r
  p="$(realpath -m -- "$1")"
  IFS=: read -ra roots <<< "$ROOTS"
  for r in "${roots[@]}"; do
    case "$p" in "$r"/*) printf '%s' "$p"; return 0 ;; esac
  done
  return 1
}

IN="$(inside "$IN")" || die 3 "--in is outside the shared dirs ($ROOTS): $IN"
OUT="$(inside "$OUT")" || die 3 "--out is outside the shared dirs ($ROOTS): $OUT"
[ -f "$IN" ] || die 3 "--in does not exist: $IN"
[ "$IN" != "$OUT" ] || die 1 "--in and --out are the same file"
mkdir -p "$(dirname "$OUT")"

# What the input has is what the output must keep: a video with a video stream,
# an audio file (or --audio-only) an audio stream. Checked on the input first so
# that converting an mp3 is not reported as "no video stream".
HAS_VIDEO="$(ffprobe -v error -select_streams v:0 -show_entries stream=codec_type -of csv=p=0 "$IN" || true)"
if [ "$AUDIO_ONLY" = 1 ] || [ "$HAS_VIDEO" != "video" ]; then WANT=audio; SEL=a:0; else WANT=video; SEL=v:0; fi

ARGS=(-v error -y -nostdin)
[ -z "$START" ] || ARGS+=(-ss "$START")
ARGS+=(-i "$IN")
[ -z "$DURATION" ] || ARGS+=(-t "$DURATION")
[ "$AUDIO_ONLY" = 0 ] || ARGS+=(-vn)
[ "$COPY" = 0 ] || ARGS+=(-c copy)
[ -z "$HEIGHT" ] || ARGS+=(-vf "scale=-2:$HEIGHT")

# A failed or half-written output is removed: a file that exists is what a
# caller would publish, and ffmpeg leaves one behind when it dies mid-stream.
OK=0
cleanup() { [ "$OK" = 1 ] || rm -f -- "$OUT"; }
trap cleanup EXIT

echo "convert-media: $IN -> $OUT ($WANT$([ "$COPY" = 0 ] || echo ', stream copy'))" >&2
ffmpeg "${ARGS[@]}" "$OUT" >&2 || die 4 "ffmpeg could not convert $IN"

[ -s "$OUT" ] || die 5 "ffmpeg returned 0 and left an empty file"
STREAM="$(ffprobe -v error -select_streams "$SEL" -show_entries stream=codec_type -of csv=p=0 "$OUT" || true)"
[ "$STREAM" = "$WANT" ] || die 5 "no $WANT stream in the output (ffprobe saw '${STREAM:-nothing}')"
DURATION_S="$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$OUT" || echo 0)"
OK=1

# One line of JSON, so a caller reads a field rather than parses prose.
json_str() { printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g; s/^/"/; s/$/"/'; }
printf '{"ok":true,"path":%s,"bytes":%s,"duration_s":%s,"stream":"%s"}\n' \
  "$(json_str "$OUT")" "$(stat -c %s "$OUT")" "${DURATION_S:-0}" "$WANT"
