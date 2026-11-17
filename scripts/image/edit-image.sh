#!/usr/bin/env bash
# scripts/image/edit-image.sh — resize, crop, rotate, convert or strip an image.
#
#   edit-image.sh --in IN --out OUT [--resize 1280x720|50%] [--crop WxH+X+Y]
#                 [--rotate 90] [--quality 85] [--strip]
#
# A local file in, a local file out. The format comes from OUT's extension
# (.png, .jpg, .webp, .gif …), so the same call converts. The operations run in
# the order a person means them: crop first, then resize, then rotate. --resize
# takes ImageMagick geometry: "1280x720" fits inside that box keeping the aspect
# ratio, "1280x720!" forces it, "50%" scales. --strip drops EXIF, ICC and the
# rest of the metadata, which is what "remove the location from this photo"
# means. --quality is 1-100 and only matters to lossy formats.
#
# Both paths must sit under /opt/data/work or /opt/data/files, the two places the
# sandbox and the gateway share (ADR-0045). A file anywhere else is either
# invisible to whoever asked or something nobody meant a shell to rewrite, so it
# is refused rather than edited.
#
# On success it prints one JSON object on stdout; progress goes to stderr. A
# failure exits non-zero and leaves no output file behind.
#
# Exit codes: 1 usage, 2 a tool is missing, 3 a path is refused (outside the two
# shared roots, or an input that does not exist), 4 magick failed, 5 the output
# is not an image magick can read back.
set -euo pipefail

IN=""; OUT=""; RESIZE=""; CROP=""; ROTATE=""; QUALITY=""
STRIP=0

die() { echo "edit-image: $2" >&2; exit "$1"; }

usage() {
  sed -n '2,25p' "$0" | sed 's/^# \{0,1\}//'
  exit 1
}

while [ $# -gt 0 ]; do
  case "$1" in
    --in)       IN="${2:?--in needs a value}"; shift 2 ;;
    --out)      OUT="${2:?--out needs a value}"; shift 2 ;;
    --resize)   RESIZE="${2:?--resize needs a value}"; shift 2 ;;
    --crop)     CROP="${2:?--crop needs a value}"; shift 2 ;;
    --rotate)   ROTATE="${2:?--rotate needs a value}"; shift 2 ;;
    --quality)  QUALITY="${2:?--quality needs a value}"; shift 2 ;;
    --strip)    STRIP=1; shift ;;
    -h|--help)  usage ;;
    *) die 1 "unknown argument: $1" ;;
  esac
done

[ -n "$IN" ] && [ -n "$OUT" ] || die 1 "--in and --out are required"
case "$OUT" in *.*) ;; *) die 1 "--out needs an extension: that is how the format is chosen" ;; esac
case "$QUALITY" in ""|[1-9]|[1-9][0-9]|100) ;; *) die 1 "--quality is 1-100, not '$QUALITY'" ;; esac

# Checked up front. The message names the merge request because that is the only
# way to fix it: production is immutable and a runtime install is lost on the
# next deploy.
command -v magick >/dev/null 2>&1 || die 2 \
  "magick is not in this image. File a capability gap and open the merge request that adds it; do not install it at runtime."

# Where the sandbox and the gateway both look (ADR-0045). Overridable so a test
# can point it at a temp dir; the sandbox binds nothing else, so the override
# opens no door the sandbox has not already closed.
ROOTS="${AURA_DATA_ROOTS:-/opt/data/work:/opt/data/files}"

# Canonical, symlinks resolved, so a link out of the shared dirs is refused too.
# An absolute path also cannot be read by magick as a coder prefix ("label:",
# "http:"), which is the other thing a stray argument could turn into.
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

# `+repage` after a crop: without it the canvas keeps the old size and a PNG or
# GIF carries the offset, so the "cropped" image opens at the original dimensions.
ARGS=("$IN")
[ -z "$CROP" ] || ARGS+=(-crop "$CROP" +repage)
[ -z "$RESIZE" ] || ARGS+=(-resize "$RESIZE")
[ -z "$ROTATE" ] || ARGS+=(-rotate "$ROTATE")
[ "$STRIP" = 0 ] || ARGS+=(-strip)
[ -z "$QUALITY" ] || ARGS+=(-quality "$QUALITY")

OK=0
cleanup() { [ "$OK" = 1 ] || rm -f -- "$OUT"; }
trap cleanup EXIT

echo "edit-image: $IN -> $OUT" >&2
magick "${ARGS[@]}" "$OUT" >&2 || die 4 "magick could not edit $IN"

[ -s "$OUT" ] || die 5 "magick returned 0 and left an empty file"
# Read back: a format magick can write but not decode, or a truncated file, is
# something a viewer will not open either.
DIMS="$(magick identify -format '%w %h' "$OUT" 2>/dev/null || true)"
case "$DIMS" in
  [0-9]*' '[0-9]*) ;;
  *) die 5 "the output is not an image magick can read back (identify said '${DIMS:-nothing}')" ;;
esac
OK=1

json_str() { printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g; s/^/"/; s/$/"/'; }
printf '{"ok":true,"path":%s,"bytes":%s,"width":%s,"height":%s}\n' \
  "$(json_str "$OUT")" "$(stat -c %s "$OUT")" "${DIMS% *}" "${DIMS#* }"
