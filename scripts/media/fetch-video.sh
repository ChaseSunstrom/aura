#!/usr/bin/env bash
# scripts/media/fetch-video.sh — download a video and publish it as an artifact.
#
# The media brief's worked example, and the reason it exists is in its Part 6:
# the YouTube episode was "maybe forty tool calls of discovery for a task that is
# fundamentally six steps. That work should have become an asset." This is the
# asset. Six steps, one exit code, no discovery.
#
#   fetch-video.sh --url URL [--quality 720] [--title "..."] [--ttl never]
#   fetch-video.sh --search "funny meme compilation 2015" [--quality 720]
#
# On success it prints one JSON object with the artifact id and its URL, so a
# caller reads a field rather than a paragraph. On failure it prints why to
# stderr and exits non-zero — never a partial success, because a caller that
# cannot tell the difference will report a download that did not happen.
#
# Exit codes: 1 usage, 2 a tool is missing, 3 the download failed, 4 the file is
# not what was asked for, 5 publishing failed.
#
# The verification step is the interesting one and it is kept deliberately: the
# frame-extraction check the agent invented on its own was correct. A download
# can "succeed" and leave a 2 KB HTML error page, a zero-length file, or an audio
# stream with no video, and every one of those is a file that exists. Asking
# ffprobe for a video stream and ffmpeg for one real frame is what tells you a
# video is a video.
set -euo pipefail

QUALITY=720
TTL=""
TITLE=""
URL=""
SEARCH=""
KEEP_TMP=0

die() { echo "fetch-video: $2" >&2; exit "$1"; }

usage() {
  sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'
  exit 1
}

while [ $# -gt 0 ]; do
  case "$1" in
    --url)      URL="${2:?--url needs a value}"; shift 2 ;;
    --search)   SEARCH="${2:?--search needs a value}"; shift 2 ;;
    --quality)  QUALITY="${2:?--quality needs a value}"; shift 2 ;;
    --title)    TITLE="${2:?--title needs a value}"; shift 2 ;;
    --ttl)      TTL="${2:?--ttl needs a value}"; shift 2 ;;
    --keep-tmp) KEEP_TMP=1; shift ;;
    -h|--help)  usage ;;
    *) die 1 "unknown argument: $1" ;;
  esac
done

[ -n "$URL$SEARCH" ] || die 1 "one of --url or --search is required"
[ -z "$URL" ] || [ -z "$SEARCH" ] || die 1 "--url and --search are mutually exclusive"

# Checked up front rather than discovered halfway through a download. The message
# names the merge request because that is the only way to fix it: production is
# immutable and a runtime install is lost on the next deploy.
for tool in yt-dlp ffprobe ffmpeg curl; do
  command -v "$tool" >/dev/null 2>&1 || die 2 \
    "$tool is not in this image. File a capability gap and open the merge request that adds it; do not install it at runtime."
done

: "${AURA_API_KEY:?AURA_API_KEY is not set, so the artifact store cannot be reached}"
ADAPTER="${AURA_ADAPTER_BASE:-http://127.0.0.1:8643}"

TMP="$(mktemp -d)"
cleanup() { [ "$KEEP_TMP" = 1 ] || rm -rf -- "$TMP"; }
trap cleanup EXIT

# 1-2. Resolve and download. `ytsearch1:` keeps a search to one result: this is a
# fetch, not a browse, and a caller that wanted to choose would have passed a URL.
TARGET="${URL:-ytsearch1:$SEARCH}"
echo "fetch-video: downloading $TARGET at ${QUALITY}p" >&2
yt-dlp \
  --no-playlist \
  --no-progress \
  --format "bestvideo[height<=${QUALITY}]+bestaudio/best[height<=${QUALITY}]" \
  --merge-output-format mp4 \
  --output "$TMP/video.%(ext)s" \
  --print-to-file "%(title)s" "$TMP/title.txt" \
  "$TARGET" >&2 || die 3 "yt-dlp could not download $TARGET"

FILE="$(find "$TMP" -maxdepth 1 -name 'video.*' -type f | head -1)"
[ -n "$FILE" ] || die 3 "yt-dlp reported success but produced no file"
[ -s "$FILE" ] || die 4 "the downloaded file is empty"

# 3. Verify it is a video, not a file. A download can succeed and leave an HTML
# error page or an audio-only stream; both exist, neither plays.
STREAMS="$(ffprobe -v error -select_streams v:0 -show_entries stream=codec_type -of csv=p=0 "$FILE" || true)"
[ "$STREAMS" = "video" ] || die 4 "no video stream in the download (ffprobe saw '${STREAMS:-nothing}')"

DURATION="$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$FILE" || echo 0)"
awk -v d="$DURATION" 'BEGIN { exit (d > 1) ? 0 : 1 }' || die 4 "the download is ${DURATION}s long, which is not a video"

# One real frame decoded. This is the check the agent invented and it was right:
# a container can hold a video stream that does not decode.
ffmpeg -v error -i "$FILE" -frames:v 1 -f image2 "$TMP/frame.jpg" -y >/dev/null 2>&1 \
  || die 4 "the download has a video stream but no decodable frame"
[ -s "$TMP/frame.jpg" ] || die 4 "frame extraction produced nothing"

# 4. Publish. The point of the whole script: the caller gets an id and a URL that
# every surface can open, not a path inside this container.
[ -n "$TITLE" ] || TITLE="$(head -1 "$TMP/title.txt" 2>/dev/null || basename "$FILE")"
RESPONSE="$(curl -fsS -X POST "$ADAPTER/api/artifacts" \
  -H "Authorization: Bearer $AURA_API_KEY" \
  -H "x-artifact-mime: video/mp4" \
  -H "x-artifact-title: $TITLE" \
  ${TTL:+-H "x-artifact-ttl: $TTL"} \
  ${URL:+-H "x-artifact-source-url: $URL"} \
  -H "x-artifact-tool: fetch-video.sh" \
  --data-binary "@$FILE")" || die 5 "publishing to the artifact store failed"

ID="$(printf '%s' "$RESPONSE" | sed -n 's/.*"id"[[:space:]]*:[[:space:]]*"\([0-9a-f]\{64\}\)".*/\1/p')"
[ -n "$ID" ] || die 5 "the artifact store did not return an id: $RESPONSE"

# 5. One line of JSON, so a caller reads a field rather than parses prose.
printf '{"ok":true,"id":"%s","url":"%s/api/artifacts/%s","title":%s,"duration_s":%s,"quality":%s}\n' \
  "$ID" "${AURA_PUBLIC_URL:-$ADAPTER}" "$ID" \
  "$(printf '%s' "$TITLE" | sed 's/\\/\\\\/g; s/"/\\"/g; s/^/"/; s/$/"/')" \
  "$DURATION" "$QUALITY"
