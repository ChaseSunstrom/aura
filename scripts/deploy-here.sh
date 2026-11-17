#!/usr/bin/env bash
# scripts/deploy-here.sh — deploy a commit to production from this host, without the pipeline.
#
#   scripts/deploy-here.sh [--allow-over-budget] [REV] [SERVICE...]
#     --allow-over-budget  ship a console that hud-size refuses (logged); without it a
#                          rebuilt jarvis-web over its size budget stops the deploy (T-0797)
#     REV      default HEAD; deployed under its 8-character short sha, like CI's tags
#     SERVICE  default: jarvis jarvis-adapter jarvis-web jarvis-extract; jarvis-cosyvoice and
#              jarvis-models only when named (built as :latest, the tag they run by)
#
# This dev box is also the production host (/opt/jarvis-host), and a pipeline deploy takes
# half an hour (the owner, 2026-09-25: "I don't want to wait for all of the pipeline
# stuff"). The hand procedure this replaces left a ~1 GB deploy.XXXX copy of the tree
# behind on every run (20 GB had piled up by 2026-10-09) and never removed an old image
# tag. Here the copy is removed on exit whatever happens, and disk_guard.py --tags runs last.
#
# Steps: git archive REV into a temp dir → buildx --load each SERVICE with the registry
# cache → a service not rebuilt is retagged from the running tag, so every image the
# compose file names exists → deploy/ synced to the host dir (never its .env,
# .previous_tag or backups) → .previous_tag recorded, AURA_TAG set → compose up the
# services, then recreate the adapter (it shares jarvis's network namespace and a
# restart orphans it) → smoke → drop old deploy tags (disk_guard.py --tags).
#
# jarvis-cosyvoice and jarvis-models run as :latest (COSYVOICE_TAG, MODELS_TAG), so an app
# rollback never rolls them back. Named, each is built here as local :latest and recreated;
# not named, the running one is left alone, never retagged. The local :latest is not pushed:
# CI's next build of that image replaces it on the next pipeline deploy.
#
# Not covered: rollback. A failed smoke is reported and the new stack stays up. To go
# back, set AURA_TAG in the host's .env to .previous_tag and compose up again.
set -euo pipefail
HERE=$(cd "$(dirname "$0")/.." && pwd)
. "$HERE/deploy/legacy-env.sh"   # a JARVIS_* set in this shell reads as AURA_* (T-0959)
HOST_DIR=${AURA_HOST_DIR:-/opt/jarvis-host}
ALLOW_OVER=0
if [ "${1:-}" = --allow-over-budget ]; then ALLOW_OVER=1; shift; fi
TAG=$(git -C "$HERE" rev-parse --short=8 "${1:-HEAD}")
[ $# -gt 0 ] && shift
SVCS=${*:-jarvis jarvis-adapter jarvis-web jarvis-extract}
ALL="jarvis jarvis-adapter jarvis-web jarvis-extract"
REG=$(grep -E '^(AURA|JARVIS)_REGISTRY=' "$HOST_DIR/.env" | head -1 | cut -d= -f2- | tr -d '\r')
# The console's GitLab origin (VITE_GITLAB_URL, T-0841): the host's, as CI passes its own.
GITLAB_ORIGIN=$(sed -n 's#^GITLAB_API_URL="\{0,1\}\(https\{0,1\}://[^/"]*\).*#\1#p' "$HOST_DIR/.env" | head -1)
[ -n "$GITLAB_ORIGIN" ] || echo "[deploy-here] warning: no GITLAB_API_URL in the host env; the console's Get the apps section will say it is offline" >&2
RUNNING=$(docker inspect --format '{{.Config.Image}}' jarvis 2>/dev/null | sed 's/.*://' || true)
log() { echo "[deploy-here] $*" >&2; }

WORK=$(mktemp -d "${TMPDIR:-/tmp}/deploy.XXXXXX")
trap 'rm -rf "$WORK"' EXIT
git -C "$HERE" archive "$TAG" | tar -x -C "$WORK"
# The Hermes base the deployed commit was written against, not the working tree's.
HERMES_TAG=$(sed -nE 's/^  HERMES_TAG: "([^"]+)"/\1/p' "$WORK/.gitlab-ci.yml")

# hud-size on the console about to ship (T-0797): it went red unread for 30 pipelines (T-0796)
# because every deploy from here goes around CI. Reads build/client out of the image just built,
# measures it against the deployed commit's own budget file, and refuses before anything is
# synced or recreated. A console it cannot measure is refused too; the flag logs and goes on.
size_gate() {
  mkdir -p "$WORK/aura-web/build"
  if command -v node >/dev/null \
    && docker run --rm --entrypoint tar "$1" -C /app/build -cf - client | tar -x -C "$WORK/aura-web/build" \
    && node "$WORK/scripts/verify/hud-size.mjs" >&2; then
    return 0
  fi
  if [ "$ALLOW_OVER" = 1 ]; then log "console over its size budget or unmeasurable; going on because of --allow-over-budget"; return 0; fi
  log "console over its size budget (the chunk is named above) or unmeasurable; nothing deployed. Fix it, or --allow-over-budget to go on anyway"
  exit 1
}

BY_NAME="jarvis-cosyvoice jarvis-models"
for svc in $ALL $BY_NAME; do
  case " $SVCS " in
    *" $svc "*)
      tag=$TAG
      case $svc in
        jarvis)         df=deploy/Dockerfile ctx=.
                        git -C "$HERE" archive --format=tar --prefix=repo/ -o "$WORK/deploy/seed-repo.tar" "$TAG" ;;
        jarvis-adapter) df=aura-adapter/Dockerfile ctx=. ;;
        jarvis-web)     df=aura-web/Dockerfile ctx=aura-web ;;
        jarvis-extract) df=deploy/extract/Dockerfile ctx=. ;;
        jarvis-cosyvoice) df=aura-voice/Dockerfile ctx=. tag=latest ;;
        jarvis-models)  df=deploy/models/Dockerfile ctx=. tag=latest ;;
      esac
      log "build $svc:$tag"
      (cd "$WORK" && docker buildx build --load -q -t "$REG/$svc:$tag" \
        --cache-from "type=registry,ref=$REG/$svc:cache" --build-arg "HERMES_TAG=$HERMES_TAG" --build-arg "VITE_GITLAB_URL=$GITLAB_ORIGIN" \
        -f "$df" "$ctx" >/dev/null)
      [ "$svc" = jarvis-web ] && size_gate "$REG/$svc:$tag"
      rm -f "$WORK/deploy/seed-repo.tar" ;;
    *)
      case " $BY_NAME " in *" $svc "*) continue ;; esac
      [ -n "$RUNNING" ] || { log "no running tag to reuse for $svc; name it"; exit 1; }
      docker tag "$REG/$svc:$RUNNING" "$REG/$svc:$TAG" ;;
  esac
done

log "sync deploy/ to $HOST_DIR"
tar -C "$WORK/deploy" --exclude=./.env --exclude=./n8n.env --exclude=./.previous_tag --exclude=./backups --exclude=./tests -cf - . \
  | tar -x -C "$HOST_DIR"
mkdir -p "$HOST_DIR/scripts" && cp "$WORK/scripts/smoke.sh" "$WORK/scripts/console-gate-check.sh" "$WORK/scripts/voice_pitch.py" "$WORK/scripts/erase_doors_check.py" "$WORK/scripts/smoke-report.py" \
  "$WORK/scripts/hermes-probes/"*.py "$HOST_DIR/scripts/"

cd "$HOST_DIR"
# n8n's own secrets (T-0630): created once, only missing keys added after, never printed.
# Before any compose call, because the compose file's env_file names n8n.env.
bash ./n8n-env.sh n8n.env
[ -n "$RUNNING" ] && [ "$RUNNING" != "$TAG" ] && echo "$RUNNING" > .previous_tag
# Whichever key the host's .env already has (it may still say JARVIS_TAG): never a new line.
sed -i -E "s/^(AURA|JARVIS)_TAG=.*/\\1_TAG=$TAG/" .env
files="-f docker-compose.yml"
[ -e /dev/nvidia0 ] && files="$files -f docker-compose.gpu.yml"
[ -e /dev/nvidia1 ] && files="$files -f docker-compose.gpu2.yml"
# A recreate cuts a reply in mid-sentence: wait, at most three minutes, for no voice turn to be
# open (T-0577). Its failure is never the deploy's.
ip=$(grep -E '^TAILSCALE_IP=' .env | cut -d= -f2 | tr -d '\r')
(set -a && . ./.env && . ./legacy-env.sh && set +a && python3 wait_quiet.py "http://$ip:8643") || log "wait_quiet failed; not waiting"
log "up $SVCS (was ${RUNNING:-none})"
# shellcheck disable=SC2086
# jarvis-backup runs the jarvis image too: left out, it keeps the old tag alive.
backup=$(case " $SVCS " in *" jarvis "*) echo jarvis-backup ;; esac)
# shellcheck disable=SC2086
docker compose $files up -d --no-deps --pull never $SVCS $backup
# shellcheck disable=SC2086
docker compose $files up -d --no-deps --pull never --force-recreate jarvis-adapter
# n8n and its sandbox: started if absent, never recreated by a Jarvis deploy, and a failure
# here (a registry down, the privileged runner refused) must not fail the deploy.
# shellcheck disable=SC2086
docker compose $files up -d --no-recreate sandbox-certs sandbox-api sandbox-runner-1 n8n || log "n8n did not start (not fatal)"

log "wait for /health"
for _ in $(seq 60); do
  curl -fsS -m 5 "http://$ip:8642/health" >/dev/null 2>&1 && curl -fsS -m 5 "http://$ip:8643/health" >/dev/null 2>&1 && break
  sleep 5
done
log "smoke"
smoke=0
(set -a && . ./.env && . ./legacy-env.sh && set +a && AURA_BASE=http://$ip:8643 HERMES_API_BASE=http://$ip:8642 CONSOLE_BASE=http://$ip:8199 \
  SMOKE_PYTHON='docker exec -i jarvis /opt/hermes/.venv/bin/python' SMOKE_EXEC='docker exec jarvis' \
  SMOKE_PY_IN_CONTAINER=1 SMOKE_WS_BASE=http://127.0.0.1:8643 SMOKE_TIMEOUT=900 \
  bash -o pipefail -c 'bash scripts/smoke.sh 2>&1 | tee smoke-last.log' >/dev/null) || smoke=$?
# The golden turn (T-0580): one real turn on the console, the car's screen, the phone when
# adb sees one, each sent to the board. It never decides the deploy: a surface that fails is
# on the board and in this log. Run from the checkout, where the console's Playwright is.
if [ -d "$HERE/aura-web/node_modules/playwright" ] && command -v node >/dev/null; then
  log "golden turn"
  (set -a && . ./.env && . ./legacy-env.sh && set +a && AURA_BASE=http://$ip:8643 \
    timeout 420 node "$HERE/scripts/verify/golden-turn.mjs" "http://$ip:8199/" >&2) || log "golden turn: a surface failed or it could not run"
else
  log "golden turn skipped: no node or no aura-web/node_modules in $HERE"
fi
# Cleanup never decides the outcome: a container vanishing mid-listing (CI runs here too)
# must not turn a healthy deploy into a failed one or hide the smoke result.
python3 disk_guard.py --tags --host-dir "$HOST_DIR" || log "disk_guard --tags failed; old tags stay"
[ "$smoke" = 0 ] && log "deployed $TAG; smoke passed" || log "deployed $TAG; SMOKE FAILED (smoke-last.log)"
exit "$smoke"
