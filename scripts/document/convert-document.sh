#!/usr/bin/env bash
# scripts/document/convert-document.sh — convert a document from one format to another.
#
#   convert-document.sh --in IN --out OUT [--from FORMAT] [--to FORMAT] [--pages 1-3]
#
# A local file in, a local file out; the formats come from the extensions and
# --from / --to override them when an extension lies (pandoc's names: markdown,
# gfm, html, docx, odt, epub, rst, latex, plain …). Which engine runs is decided
# by the pair, and the JSON says which it was:
#
#   pdf  -> pdf     qpdf: --pages selects pages ("1-3", "1,4,7-z"); no pages repairs
#   pdf  -> txt     pdftotext: the text, nothing else. pandoc has no PDF reader
#   pdf  -> other   pdftotext, then pandoc from that text as commonmark
#   office -> any   libreoffice --headless (doc, ppt, pptx, xls, xlsx, odp, ods:
#                   pandoc reads none of them) -- optional in the image, exit 2 if absent
#   any  -> pdf     pandoc to docx, then libreoffice: pandoc needs a PDF engine
#                   and the image ships none -- exit 2 until libreoffice is there
#   any  -> any     pandoc --standalone
#
# A scanned PDF has no text to extract: that is exit 5, not an empty document,
# and `pdftoppm` (rendering pages to images for the vision model) is the answer.
#
# Both paths must sit under /opt/data/work or /opt/data/files, the two places the
# sandbox and the gateway share (ADR-0045). A file anywhere else is either
# invisible to whoever asked or something nobody meant a shell to rewrite, so it
# is refused rather than converted.
#
# On success it prints one JSON object on stdout; progress goes to stderr. A
# failure exits non-zero and leaves no output file behind.
#
# Exit codes: 1 usage, 2 a tool is missing, 3 a path is refused (outside the two
# shared roots, or an input that does not exist), 4 the engine failed, 5 the
# output is empty (or the PDF had no text).
set -euo pipefail

IN=""; OUT=""; FROM=""; TO=""; PAGES=""

die() { echo "convert-document: $2" >&2; exit "$1"; }

usage() {
  sed -n '2,34p' "$0" | sed 's/^# \{0,1\}//'
  exit 1
}

while [ $# -gt 0 ]; do
  case "$1" in
    --in)      IN="${2:?--in needs a value}"; shift 2 ;;
    --out)     OUT="${2:?--out needs a value}"; shift 2 ;;
    --from)    FROM="${2:?--from needs a value}"; shift 2 ;;
    --to)      TO="${2:?--to needs a value}"; shift 2 ;;
    --pages)   PAGES="${2:?--pages needs a value}"; shift 2 ;;
    -h|--help) usage ;;
    *) die 1 "unknown argument: $1" ;;
  esac
done

[ -n "$IN" ] && [ -n "$OUT" ] || die 1 "--in and --out are required"

ext() { local b="${1##*/}"; case "$b" in *.*) printf '%s' "${b##*.}" | tr '[:upper:]' '[:lower:]' ;; esac; }
IN_EXT="$(ext "$IN")"; OUT_EXT="$(ext "$OUT")"
[ -n "$OUT_EXT" ] || die 1 "--out needs an extension: that is how the format is chosen"
[ -z "$PAGES" ] || [ "$IN_EXT" = pdf ] || die 1 "--pages selects pages of a PDF; $IN is not one"

# The message names the merge request because that is the only way to fix it:
# production is immutable and a runtime install is lost on the next deploy.
need() {
  for tool in "$@"; do
    command -v "$tool" >/dev/null 2>&1 || die 2 \
      "$tool is not in this image. File a capability gap and open the merge request that adds it; do not install it at runtime."
  done
}
# libreoffice is the one optional tool (deploy/Dockerfile: ~500 MB, left out on
# purpose), so its absence is reported as what it is rather than as a regression.
need_libreoffice() {
  command -v libreoffice >/dev/null 2>&1 || die 2 \
    "$1 needs libreoffice, which is optional and not in this image (pandoc cannot do it). Say so; the capability-gap skill is the route if it is wanted."
}

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

TMP="$(mktemp -d)"
OK=0
cleanup() { rm -rf -- "$TMP"; [ "$OK" = 1 ] || rm -f -- "$OUT"; }
trap cleanup EXIT

# libreoffice names its output after the input, in --outdir; move it where asked.
libreoffice_convert() {
  libreoffice --headless --convert-to "$OUT_EXT" --outdir "$TMP" "$1" >&2 \
    || die 4 "libreoffice could not convert $1"
  local made="$TMP/$(basename "${1%.*}").$OUT_EXT"
  [ -f "$made" ] || die 4 "libreoffice returned 0 but wrote no .$OUT_EXT (it does not convert $IN_EXT to that)"
  mv -f -- "$made" "$OUT"
}

echo "convert-document: $IN -> $OUT" >&2
case "$IN_EXT:$OUT_EXT" in
  pdf:pdf)
    ENGINE=qpdf; need qpdf
    if [ -n "$PAGES" ]; then
      qpdf --empty --pages "$IN" "$PAGES" -- "$OUT" || die 4 "qpdf could not select pages $PAGES of $IN"
    else
      qpdf "$IN" "$OUT" || die 4 "qpdf could not rewrite $IN"
    fi ;;
  pdf:*)
    ENGINE=pdftotext; need pdftotext
    SRC="$IN"
    if [ -n "$PAGES" ]; then
      need qpdf
      qpdf --empty --pages "$IN" "$PAGES" -- "$TMP/pages.pdf" || die 4 "qpdf could not select pages $PAGES of $IN"
      SRC="$TMP/pages.pdf"
    fi
    pdftotext "$SRC" "$TMP/text.txt" || die 4 "pdftotext could not read $IN"
    [ -s "$TMP/text.txt" ] || die 5 "the PDF has no extractable text: scanned, most likely (pdftoppm renders its pages to images)"
    if [ "$OUT_EXT" = txt ]; then
      mv -f -- "$TMP/text.txt" "$OUT"
    else
      ENGINE=pandoc; need pandoc
      pandoc --standalone -f "${FROM:-commonmark}" ${TO:+-t "$TO"} -o "$OUT" "$TMP/text.txt" \
        || die 4 "pandoc could not write $OUT from the PDF's text"
    fi ;;
  doc:*|ppt:*|pptx:*|xls:*|xlsx:*|odp:*|ods:*)
    ENGINE=libreoffice; need_libreoffice "reading .$IN_EXT"
    libreoffice_convert "$IN" ;;
  *:pdf)
    ENGINE=libreoffice; need_libreoffice "writing a PDF"; need pandoc
    pandoc --standalone ${FROM:+-f "$FROM"} -t docx -o "$TMP/document.docx" "$IN" \
      || die 4 "pandoc could not read $IN"
    libreoffice_convert "$TMP/document.docx" ;;
  *)
    ENGINE=pandoc; need pandoc
    pandoc --standalone ${FROM:+-f "$FROM"} ${TO:+-t "$TO"} -o "$OUT" "$IN" \
      || die 4 "pandoc could not convert $IN" ;;
esac

[ -s "$OUT" ] || die 5 "$ENGINE returned 0 and left an empty file"
OK=1

json_str() { printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g; s/^/"/; s/$/"/'; }
printf '{"ok":true,"path":%s,"bytes":%s,"engine":"%s"}\n' \
  "$(json_str "$OUT")" "$(stat -c %s "$OUT")" "$ENGINE"
