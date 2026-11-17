#!/usr/bin/env bash
# Merge the GitLab container registry (plain HTTP) into /etc/docker/daemon.json's
# "insecure-registries" WITHOUT touching anything else in that file (mtu,
# registry-mirrors, dns, …), then restart Docker. Idempotent. Needs root.
#
#   sudo scripts/setup-docker-registry.sh HOST:PORT
#   scripts/setup-docker-registry.sh --dry-run HOST:PORT   # no root: print the merge, change nothing
#
set -euo pipefail
DRY=0; [ "${1:-}" = "--dry-run" ] && { DRY=1; shift; }
# No default: the house registry (registry.home.example) is HTTPS with a valid
# certificate since 2026-10-27 and must not be listed as insecure; only a plain-HTTP
# registry needs this script.
REGISTRY="${1:?usage: setup-docker-registry.sh [--dry-run] HOST:PORT -- only for a plain-HTTP registry}"
FILE="${DAEMON_JSON:-/etc/docker/daemon.json}"

command -v jq >/dev/null || { echo "jq is required (apt install jq)" >&2; exit 1; }
[ "$DRY" = 1 ] || [ "$(id -u)" -eq 0 ] || { echo "run as root (sudo), or pass --dry-run" >&2; exit 1; }

if [ -s "$FILE" ]; then
  jq empty "$FILE" || { echo "$FILE is not valid JSON — fix it by hand first" >&2; exit 1; }
  before="$(cat "$FILE")"
else
  before='{}'
fi

after="$(jq --arg r "$REGISTRY" '
  .["insecure-registries"] = ((.["insecure-registries"] // []) + [$r] | unique)
' <<<"$before")"

if [ "$DRY" = 1 ]; then
  echo "---- $FILE now ----"; jq . <<<"$before"
  echo "---- $FILE after the merge (DRY RUN, not written) ----"; jq . <<<"$after"
  for k in mtu registry-mirrors dns; do
    b="$(jq -c --arg k "$k" '.[$k]' <<<"$before")"; a="$(jq -c --arg k "$k" '.[$k]' <<<"$after")"
    [ "$b" = "$a" ] && echo "  $k: unchanged ($a)" || { echo "  $k: CHANGED $b -> $a" >&2; exit 2; }
  done
  exit 0
fi
if [ "$before" = "$after" ]; then
  echo "$FILE already lists $REGISTRY — nothing to do"
else
  cp -a "$FILE" "$FILE.bak.$(date +%s)" 2>/dev/null || true
  printf '%s\n' "$after" > "$FILE"
  echo "merged $REGISTRY into $FILE (backup kept next to it); restarting docker"
  systemctl restart docker
fi

echo "---- $FILE after ----"
jq . "$FILE"
echo "---- preserved keys check ----"
for k in mtu registry-mirrors dns; do
  b="$(jq -c --arg k "$k" '.[$k]' <<<"$before")"; a="$(jq -c --arg k "$k" '.[$k]' "$FILE")"
  [ "$b" = "$a" ] && echo "  $k: unchanged ($a)" || { echo "  $k: CHANGED $b -> $a" >&2; exit 2; }
done
docker info --format '{{json .RegistryConfig.IndexConfigs}}' 2>/dev/null | jq -r 'to_entries[] | select(.value.Secure==false) | "  insecure: \(.key)"' || true
