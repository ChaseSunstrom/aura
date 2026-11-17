#!/usr/bin/env bash
# The house's own region's map data, and its router started, with nothing on the screen that
# says where the region is (T-0765).
#
#   scripts/map-region-from-house.sh [--house] [--dry-run] [--cron] [--rebuild]
#
# The region is the house's answer (GET /api/maps/region, asked from inside the jarvis-core
# container with the house's own token, so no token reaches this shell): a setting, what was
# already fetched, or the 0.1 degree cell the car is driven in most. The extract is the smallest
# Geofabrik one that covers it (scripts/geofabrik_pick.py, from Geofabrik's index). Then
# scripts/map-region.sh fetches the region and Valhalla is started on it. map-region.sh's own
# output names the bbox, so it goes to maps-data/map-region.log (gitignored); this prints its
# steps with every coordinate and URL masked.
#
#   --house    take the house coordinates as the region; they may still be the shipped
#              placeholder (GAPS G17), so they are not taken by default
#   --dry-run  map-region.sh's plan only; nothing is downloaded or started
#   --cron     quiet when there is nothing to do (no region yet, or already fetched)
#   --rebuild  run map-region.sh on the region already fetched, downloading nothing: its own
#              rule re-reads a list older than the script that writes it, which "fetched
#              already" below would never let it apply (T-1169)
#
# Does not cover: the one-time downloads themselves, which tell Geofabrik, Protomaps and
# Overture the region (docs/runbooks/maps.md, "What leaves the house").
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(dirname "$HERE")"
DATA="${MAPS_DATA_DIR:-$REPO/aura-core/maps-data}"
INDEX_URL="https://download.geofabrik.de/index-v1.json"
HOUSE=0
DRY=0
CRON=0
REBUILD=0
for arg in "$@"; do
  case "$arg" in
    --house) HOUSE=1 ;;
    --dry-run) DRY=1 ;;
    --cron) CRON=1 ;;
    --rebuild) REBUILD=1 ;;
    -h|--help) sed -n '2,24p' "$0"; exit 0 ;;
    *) echo "map-region-from-house: unknown argument: $arg" >&2; exit 2 ;;
  esac
done
say() { [ "$CRON" = 1 ] || echo "map-region-from-house: $*"; }
mask() { sed -E 's#https?://[^ ]+#<url>#g; s/-?[0-9]+\.[0-9]{2,}/<n>/g'; }

if [ "$REBUILD" = 1 ]; then
  bbox="$(cat "$DATA/region.bbox" 2>/dev/null || true)"
  [ -n "$bbox" ] && [ -s "$DATA/valhalla/region.osm.pbf" ] || {
    echo "map-region-from-house: no fetched region to rebuild; run without --rebuild first" >&2; exit 1; }
  flags=()
  [ "$DRY" = 1 ] && flags+=(--dry-run)
  # No extract URL: with the extract on disk map-region.sh downloads nothing.
  if ! MAPS_BBOX="$bbox" "$HERE/map-region.sh" "${flags[@]}" > "$DATA/map-region.log" 2>&1; then
    echo "map-region-from-house: map-region.sh failed (its log, which names the region: $DATA/map-region.log)" >&2
    tail -5 "$DATA/map-region.log" | mask >&2
    exit 1
  fi
  mask < "$DATA/map-region.log" | grep -vE '^region ' || true
  say "rebuilt from the extract already here"
  exit 0
fi

answer="$(docker exec -i jarvis-core python3 - <<'EOF'
import json, os, urllib.error, urllib.request
req = urllib.request.Request("http://127.0.0.1:8080/api/maps/region",
                             headers={"Authorization": "Bearer " + os.environ.get("AURA_TOKEN", "")})
try:
    print(json.dumps(json.load(urllib.request.urlopen(req, timeout=10))))
except urllib.error.HTTPError as err:
    print(json.dumps({"source": "none", "status": err.code}))
except OSError:
    print(json.dumps({"source": "down"}))
EOF
)"
source="$(jq -r '.source // "none"' <<<"$answer")"
case "$source" in
  none) say "the house has no region yet: set MAPS_BBOX, or drive with the car page open"; exit 0 ;;
  down) echo "map-region-from-house: the house (jarvis-core) is not answering" >&2; exit 1 ;;
  fetched)
    say "the house's region is fetched already"
    [ "$DRY" = 1 ] || [ ! -s "$DATA/valhalla/region.osm.pbf" ] || \
      docker compose -f "$REPO/aura-core/docker-compose.yml" --profile maps up -d valhalla
    exit 0 ;;
  house)
    [ "$HOUSE" = 1 ] || { say "the region would be the house coordinates; pass --house once they are real (GAPS G17)"; exit 0; } ;;
  setting|drives) ;;
  *) echo "map-region-from-house: the house answered a region source this does not know" >&2; exit 1 ;;
esac
bbox="$(jq -r '.bbox | map(tostring) | join(",")' <<<"$answer")"
# A setting stays the source after its fetch, so the daily run would otherwise ask Geofabrik's
# index every night for a region that is already here.
if [ "$(cat "$DATA/region.bbox" 2>/dev/null)" = "$bbox" ] && [ -s "$DATA/valhalla/region.osm.pbf" ]; then
  say "the house's region is fetched already"
  [ "$DRY" = 1 ] || docker compose -f "$REPO/aura-core/docker-compose.yml" --profile maps up -d valhalla
  exit 0
fi
say "region from the house ($source)"

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
curl -fsSL --retry 3 -o "$tmp/index-v1.json" "$INDEX_URL"
pbf="$(python3 -I "$HERE/geofabrik_pick.py" "$tmp/index-v1.json" "$bbox")" || {
  echo "map-region-from-house: no Geofabrik extract covers the region" >&2; exit 1; }

flags=()
[ "$DRY" = 1 ] && flags+=(--dry-run)
mkdir -p "$DATA"
if ! MAPS_BBOX="$bbox" MAPS_PBF_URL="$pbf" "$HERE/map-region.sh" "${flags[@]}" > "$DATA/map-region.log" 2>&1; then
  echo "map-region-from-house: map-region.sh failed (its log, which names the region: $DATA/map-region.log)" >&2
  tail -5 "$DATA/map-region.log" | mask >&2
  exit 1
fi
mask < "$DATA/map-region.log" | grep -vE '^region ' || true
[ "$DRY" = 1 ] && exit 0
# A new extract has no routing tiles yet: a fresh container builds them on its first start.
if [ ! -s "$DATA/valhalla/valhalla_tiles.tar" ]; then
  docker compose -f "$REPO/aura-core/docker-compose.yml" --profile maps up -d --force-recreate valhalla
else
  docker compose -f "$REPO/aura-core/docker-compose.yml" --profile maps up -d valhalla
fi
say "Valhalla is building or serving the region; its first build takes minutes for a state"
