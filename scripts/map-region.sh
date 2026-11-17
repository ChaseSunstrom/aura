#!/usr/bin/env bash
# Fetch one region's map data into aura-core/maps-data/ (gitignored), idempotently.
#
#   scripts/map-region.sh --bbox WEST,SOUTH,EAST,NORTH [--pbf-url URL] [--dry-run] [--force]
#
# The region is a setting, never typed into the repo: --bbox, else MAPS_BBOX,
# else MAPS_CENTER=lat,lon with MAPS_RADIUS_KM (default 25). Without the
# Geofabrik extract (--pbf-url / MAPS_PBF_URL) there is no routing or OSM
# building data, and the plan says so. See docs/runbooks/maps.md.
#
# What it writes under $MAPS_DATA_DIR (default aura-core/maps-data):
#   region.pmtiles                 Protomaps vector tiles, by HTTP range: only the bbox is downloaded
#   valhalla/region.osm.pbf        the Geofabrik extract Valhalla builds its routing tiles from
#   osm-buildings.geojsonl         OSM footprints with height / levels, colour, roof and parts
#   osm-detail.geojsonl            trees, lamps, signals, crossings, parking, construction, water
#                                  and green, for the car's ground (T-1066)
#   osm-places.geojsonl            the region's places and addresses, for place search (T-0765)
#   osm-roads.geojsonl             every street, driveway and path, for the car's ground (T-0980)
#   overture-buildings.geojsonl    Overture footprints, which fill what OSM lacks
#   ground.jsonl                   the ground's height per pack cell, from public elevation
#                                  tiles kept under terrain/ (T-1105); without it the map is flat
#   packs/home.pack                the footprints, roads and places as one cell-indexed file, which
#                                  the house answers from and the phone keeps (T-0979)
#   region.bbox                    the bbox these were fetched for; a different bbox refetches
# Downloads resume (.part files) and a finished file is skipped, so a second
# run with the same bbox downloads nothing.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(dirname "$HERE")"
DATA="${MAPS_DATA_DIR:-$REPO/aura-core/maps-data}"
PY="${MAPS_PYTHON:-python3}"
PMTILES_VERSION=1.31.2
MAXZOOM="${MAPS_MAXZOOM:-15}"
MAX_GB="${MAPS_MAX_GB:-20}"      # refuse a Geofabrik extract bigger than this
BBOX="${MAPS_BBOX:-}"
PBF_URL="${MAPS_PBF_URL:-}"
BUILD="${MAPS_BUILD:-}"          # a Protomaps daily build, YYYYMMDD; default yesterday UTC
DRY=0
FORCE=0

die() { echo "map-region: $*" >&2; exit 2; }
while [ $# -gt 0 ]; do
  case "$1" in
    --bbox) [ $# -ge 2 ] || die "--bbox needs a value"; BBOX="$2"; shift 2 ;;
    --pbf-url) [ $# -ge 2 ] || die "--pbf-url needs a value"; PBF_URL="$2"; shift 2 ;;
    --dry-run) DRY=1; shift ;;
    --force) FORCE=1; shift ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done

if [ -z "$BBOX" ] && [ -n "${MAPS_CENTER:-}" ]; then
  BBOX="$(awk -v c="$MAPS_CENTER" -v r="${MAPS_RADIUS_KM:-25}" 'BEGIN {
    split(c, p, ","); pi = 3.14159265358979
    dlat = r / 111; dlon = r / (111 * cos(p[1] * pi / 180))
    printf "%.5f,%.5f,%.5f,%.5f", p[2] - dlon, p[1] - dlat, p[2] + dlon, p[1] + dlat }')"
fi
[ -n "$BBOX" ] || die "no region: pass --bbox W,S,E,N, or set MAPS_BBOX, or MAPS_CENTER=lat,lon"
# Four numbers, west<east, south<north, on Earth. The values reach curl and
# pmtiles as arguments, so a malformed one stops here.
echo "$BBOX" | awk -F, 'BEGIN{ok=1} {
  if (NF != 4) ok = 0
  for (i = 1; i <= 4; i++) if ($i !~ /^-?[0-9]+(\.[0-9]+)?$/) ok = 0
  if (ok && !($1 < $3 && $2 < $4 && $1 >= -180 && $3 <= 180 && $2 >= -90 && $4 <= 90)) ok = 0
  exit !ok }' || die "bad bbox '$BBOX': want WEST,SOUTH,EAST,NORTH in degrees, west<east, south<north"
IFS=, read -r W S E N <<<"$BBOX"
case "$PBF_URL" in ""|https://*) ;; *) die "--pbf-url must be https://" ;; esac
[ -z "$BUILD" ] && BUILD="$(date -u -d yesterday +%Y%m%d)"
echo "$BUILD" | grep -Eq '^[0-9]{8}$' || die "MAPS_BUILD is YYYYMMDD"

TILES_SRC="https://build.protomaps.com/$BUILD.pmtiles"
PMTILES_URL="https://github.com/protomaps/go-pmtiles/releases/download/v$PMTILES_VERSION/go-pmtiles_${PMTILES_VERSION}_Linux_$(uname -m | sed 's/aarch64/arm64/').tar.gz"
PMTILES="$DATA/tools/pmtiles"
# One directory per Python version, as map_worker.py keeps its own: pyosmium's wheel is
# built for one, and a run under another died on "No module named 'osmium._osmium'".
PYLIB="$DATA/tools/py$("$PY" -c 'import sys; print(f"{sys.version_info.major}{sys.version_info.minor}")')"

# Size of a URL in MB, or "unknown" (offline, or the server does not say).
size_of() {
  curl -fsIL -m 20 "$1" 2>/dev/null | awk 'tolower($1) == "content-length:" { n = $2 + 0 } END { if (n) printf "%.0f MB", n / 1048576; else print "unknown" }'
}

fresh() { [ "$FORCE" = 0 ] && [ -s "$1" ] && [ "$(cat "$DATA/region.bbox" 2>/dev/null)" = "$BBOX" ]; }
state() { if fresh "$1"; then echo "have it, skipped"; else echo "$2"; fi; }

echo "region   bbox $BBOX   (W,S,E,N)   data $DATA"
echo "plan"
echo "  1. tools        pmtiles $PMTILES_VERSION ($(state "$PMTILES" "$(size_of "$PMTILES_URL") download")), pyosmium + overturemaps under $PYLIB"
echo "  2. tiles        region.pmtiles    $(state "$DATA/region.pmtiles" "range-read from $TILES_SRC, z0-$MAXZOOM; the planet file is $(size_of "$TILES_SRC") and only the bbox is fetched")"
if [ -n "$PBF_URL" ]; then
  echo "  3. routing      valhalla/region.osm.pbf  $(state "$DATA/valhalla/region.osm.pbf" "$(size_of "$PBF_URL") from $PBF_URL")"
  echo "  4. OSM bldgs    osm-buildings.geojsonl   $(state "$DATA/osm-buildings.geojsonl" "read out of the extract")"
  echo "     places       osm-places.geojsonl      $(state "$DATA/osm-places.geojsonl" "read out of the extract")"
  echo "     roads        osm-roads.geojsonl       $(state "$DATA/osm-roads.geojsonl" "read out of the extract")"
  echo "     detail       osm-detail.geojsonl      $(state "$DATA/osm-detail.geojsonl" "read out of the extract")"
else
  echo "  3. routing      SKIPPED: no Geofabrik extract. Set MAPS_PBF_URL (or --pbf-url) to the one covering the bbox,"
  echo "                  e.g. https://download.geofabrik.de/europe/great-britain/england/greater-london-latest.osm.pbf"
  echo "  4. OSM bldgs    SKIPPED (same extract)"
fi
echo "  5. Overture     overture-buildings.geojsonl  $(state "$DATA/overture-buildings.geojsonl" "queried from Overture's public bucket for the bbox")"
echo "  geocoding is Photon's own country index: PHOTON_REGION=<cc> docker compose --profile geocode up -d photon"
[ "$DRY" = 1 ] && { echo "dry run: nothing fetched or written"; exit 0; }

mkdir -p "$DATA/tools" "$DATA/valhalla"
if [ "$FORCE" = 1 ] || [ "$(cat "$DATA/region.bbox" 2>/dev/null)" != "$BBOX" ]; then
  # A new bbox (or --force) invalidates everything fetched for the old one. The
  # stamp is written first, and every output is renamed into place only when
  # complete, so an interrupted run resumes instead of starting over.
  rm -f "$DATA/region.pmtiles" "$DATA/osm-buildings.geojsonl" "$DATA/osm-places.geojsonl" "$DATA/osm-roads.geojsonl" "$DATA/osm-detail.geojsonl" "$DATA/overture-buildings.geojsonl" "$DATA/ground.jsonl" \
        "$DATA/valhalla/region.osm.pbf" "$DATA/valhalla/valhalla_tiles.tar" "$DATA/packs/home.pack"
  echo "$BBOX" > "$DATA/region.bbox"
fi

if [ ! -x "$PMTILES" ]; then
  echo "fetching pmtiles $PMTILES_VERSION"
  curl -fsSL -m 300 "$PMTILES_URL" | tar -xz -C "$DATA/tools" pmtiles
fi
if [ ! -s "$DATA/region.pmtiles" ]; then
  "$PMTILES" extract "$TILES_SRC" "$DATA/region.pmtiles.part" --bbox="$BBOX" --maxzoom="$MAXZOOM"
  mv "$DATA/region.pmtiles.part" "$DATA/region.pmtiles"
fi

if [ -n "$PBF_URL" ] && [ ! -s "$DATA/valhalla/region.osm.pbf" ]; then
  MB="$(size_of "$PBF_URL" | awk '{ print ($1 == "unknown") ? 0 : $1 }')"
  [ "$MB" -le $((MAX_GB * 1024)) ] || die "the extract is $MB MB, over MAPS_MAX_GB=$MAX_GB; pick a smaller one or raise it"
  curl -fsSL -C - -o "$DATA/valhalla/region.osm.pbf.part" "$PBF_URL"
  mv "$DATA/valhalla/region.osm.pbf.part" "$DATA/valhalla/region.osm.pbf"
fi

if [ ! -d "$PYLIB/osmium" ] || [ ! -d "$PYLIB/overturemaps" ]; then
  "$PY" -m pip install --quiet --target "$PYLIB" osmium overturemaps
fi
# A list older than the script that writes it is read again: a script that learns a tag
# (T-1066: colours, lanes) must not be answered by yesterday's list.
stale() { [ ! -s "$2" ] || [ "$HERE/$1" -nt "$2" ]; }
if [ -s "$DATA/valhalla/region.osm.pbf" ] && stale osm_buildings.py "$DATA/osm-buildings.geojsonl"; then
  PYTHONPATH="$PYLIB" "$PY" "$HERE/osm_buildings.py" "$DATA/valhalla/region.osm.pbf" \
    "$DATA/osm-buildings.geojsonl" "$W" "$S" "$E" "$N"
fi
if [ -s "$DATA/valhalla/region.osm.pbf" ] && [ ! -s "$DATA/osm-places.geojsonl" ]; then
  PYTHONPATH="$PYLIB" "$PY" "$HERE/osm_places.py" "$DATA/valhalla/region.osm.pbf" \
    "$DATA/osm-places.geojsonl" "$W" "$S" "$E" "$N"
fi
if [ -s "$DATA/valhalla/region.osm.pbf" ] && stale osm_roads.py "$DATA/osm-roads.geojsonl"; then
  PYTHONPATH="$PYLIB" "$PY" "$HERE/osm_roads.py" "$DATA/valhalla/region.osm.pbf" \
    "$DATA/osm-roads.geojsonl" "$W" "$S" "$E" "$N"
fi
if [ -s "$DATA/valhalla/region.osm.pbf" ] && stale osm_detail.py "$DATA/osm-detail.geojsonl"; then
  PYTHONPATH="$PYLIB" "$PY" "$HERE/osm_detail.py" "$DATA/valhalla/region.osm.pbf" \
    "$DATA/osm-detail.geojsonl" "$W" "$S" "$E" "$N"
fi
if [ ! -s "$DATA/overture-buildings.geojsonl" ]; then
  PYTHONPATH="$PYLIB" "$PY" -m overturemaps download --bbox="$BBOX" -f geojsonseq -t building \
    -o "$DATA/overture-buildings.geojsonl.part"
  mv "$DATA/overture-buildings.geojsonl.part" "$DATA/overture-buildings.geojsonl"
  rm -f "$DATA/overture-buildings.geojsonl.part.state"  # the CLI's resume marker
fi

# The ground's heights: one line per pack cell. A run that cannot fetch its tiles writes
# nothing and says so, and the pack is built flat; the next run tries again.
if stale osm_ground.py "$DATA/ground.jsonl"; then
  "$PY" "$HERE/osm_ground.py" "$DATA/ground.jsonl" "$W" "$S" "$E" "$N" "$DATA/terrain" || true
fi

# The home region's pack, rebuilt when any list is newer than it. With it on disk the house
# stops holding the lists in memory (182 MiB for a 50 km box).
PACK="$DATA/packs/home.pack"
if [ ! -s "$PACK" ] || [ -n "$(find "$DATA" -maxdepth 1 \( -name '*.geojsonl' -o -name 'ground.jsonl' \) -newer "$PACK" -print -quit)" ]; then
  mkdir -p "$DATA/packs"
  "$PY" "$REPO/aura-core/aura/integrations/maps/pack.py" "$PACK" Home "$BBOX" \
    --osm "$DATA/osm-buildings.geojsonl" --overture "$DATA/overture-buildings.geojsonl" \
    --roads "$DATA/osm-roads.geojsonl" --places "$DATA/osm-places.geojsonl" --detail "$DATA/osm-detail.geojsonl" --ground "$DATA/ground.jsonl"
fi

du -sh "$DATA"/* 2>/dev/null | grep -v tools || true
echo "done. Start routing with: docker compose -f aura-core/docker-compose.yml --profile maps up -d valhalla"
