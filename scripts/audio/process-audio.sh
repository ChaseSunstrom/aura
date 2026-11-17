#!/usr/bin/env bash
# scripts/audio/process-audio.sh — normalise, trim, resample or convert an audio file.
#
#   process-audio.sh --in IN --out OUT [--normalise] [--start 5] [--duration 30]
#                    [--rate 16000] [--channels 1]
#
# A local file in, a local file out. The format comes from OUT's extension
# (.wav, .flac, .mp3, .ogg …), so the same call converts. The effects run in the
# order sox needs them: trim, then channels, then rate, then the normalise, so
# the peak is measured on what is left rather than on what was cut. --normalise
# (or --normalize) brings the peak to 0 dBFS; --rate and --channels are what the
# voice pipeline wants (16000 and 1 for faster-whisper). --start and --duration
# are seconds.
#
# Both paths must sit under /opt/data/work or /opt/data/files, the two places the
# sandbox and the gateway share (ADR-0045). A file anywhere else is either
# invisible to whoever asked or something nobody meant a shell to rewrite, so it
# is refused rather than processed.
#
# On success it prints one JSON object on stdout; progress goes to stderr. A
# failure exits non-zero and leaves no output file behind.
#
# Exit codes: 1 usage, 2 a tool is missing, 3 a path is refused (outside the two
# shared roots, or an input that does not exist), 4 sox failed, 5 the output is
# not what was asked for (empty, or no audio sox can read back).
set -euo pipefail

IN=""; OUT=""; START=""; DURATION=""; RATE=""; CHANNELS=""
NORMALISE=0

die() { echo "process-audio: $2" >&2; exit "$1"; }

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
    --rate)       RATE="${2:?--rate needs a value}"; shift 2 ;;
    --channels)   CHANNELS="${2:?--channels needs a value}"; shift 2 ;;
    --normalise|--normalize) NORMALISE=1; shift ;;
    -h|--help)    usage ;;
    *) die 1 "unknown argument: $1" ;;
  esac
done

[ -n "$IN" ] && [ -n "$OUT" ] || die 1 "--in and --out are required"
case "$OUT" in *.*) ;; *) die 1 "--out needs an extension: that is how the format is chosen" ;; esac
case "$RATE" in ""|[1-9][0-9]*) ;; *) die 1 "--rate is a sample rate in Hz, not '$RATE'" ;; esac
case "$CHANNELS" in ""|[1-9]) ;; *) die 1 "--channels is 1-9, not '$CHANNELS'" ;; esac

# Checked up front. The message names the merge request because that is the only
# way to fix it: production is immutable and a runtime install is lost on the
# next deploy.
command -v sox >/dev/null 2>&1 || die 2 \
  "sox is not in this image. File a capability gap and open the merge request that adds it; do not install it at runtime."

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

# Effects, in sox's order. Explicit `rate` and `channels` effects rather than
# output-file options: sox inserts those effects itself when the rates differ,
# but only with a warning that a caller reading stderr for errors would trip on.
EFFECTS=()
if [ -n "$START$DURATION" ]; then EFFECTS+=(trim "${START:-0}"); [ -z "$DURATION" ] || EFFECTS+=("$DURATION"); fi
[ -z "$CHANNELS" ] || EFFECTS+=(channels "$CHANNELS")
[ -z "$RATE" ] || EFFECTS+=(rate -h "$RATE")
[ "$NORMALISE" = 0 ] || EFFECTS+=(gain -n)

OK=0
cleanup() { [ "$OK" = 1 ] || rm -f -- "$OUT"; }
trap cleanup EXIT

echo "process-audio: $IN -> $OUT${EFFECTS:+ (${EFFECTS[*]})}" >&2
sox "$IN" "$OUT" "${EFFECTS[@]}" >&2 || die 4 "sox could not process $IN"

[ -s "$OUT" ] || die 5 "sox returned 0 and left an empty file"
# Read back: a file sox cannot report a duration for is not audio anything will play.
DURATION_S="$(sox --i -D "$OUT" 2>/dev/null || true)"
awk -v d="${DURATION_S:-0}" 'BEGIN { exit (d > 0) ? 0 : 1 }' \
  || die 5 "the output has no audio sox can read back (duration '${DURATION_S:-nothing}')"
OK=1

json_str() { printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g; s/^/"/; s/$/"/'; }
printf '{"ok":true,"path":%s,"bytes":%s,"duration_s":%s,"rate":%s,"channels":%s}\n' \
  "$(json_str "$OUT")" "$(stat -c %s "$OUT")" "$DURATION_S" \
  "$(sox --i -r "$OUT" 2>/dev/null || echo 0)" "$(sox --i -c "$OUT" 2>/dev/null || echo 0)"
