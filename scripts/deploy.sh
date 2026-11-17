#!/usr/bin/env bash
# scripts/deploy.sh — deploy a tag to the JARVIS host over SSH (LAN or WireGuard) with a health-gated
# rollout and automatic rollback. The pipeline's deploy job runs this same script.
#
#   AURA_HOST=10.20.30.59 AURA_SSH_PORT=22 DEPLOY_SSH_KEY=~/.ssh/jarvis_deploy \
#   AURA_TAG=abc1234 scripts/deploy.sh [--rollback]
#
# Steps (never a hand edit on the host):
#   1. rsync deploy/ → /opt/jarvis/ (compose file, seeds; the host's .env is untouched)
#   2. record the running tag in /opt/jarvis/.previous_tag
#   3. write AURA_TAG=<tag> into /opt/jarvis/.env
#   3c. probe the host for /dev/nvidia0 and add the GPU compose overlay only if it is there
#   4. docker compose pull && docker compose up -d --remove-orphans
#   5. poll /health until healthy (bounded wait that allows a model cold start)
#   6. run scripts/smoke.sh against the host
#   7. on failure: restore .previous_tag, compose up again, exit non-zero (loudly)
#   8. drop deploy tags nothing needs (deploy/disk_guard.py --tags: keeps running, previous, the newest two)
#   9. install the deploy user's @reboot recovery line (deploy/boot-recover.sh), idempotently
set -euo pipefail
# GitLab's project variables still say JARVIS_HOST and friends (T-0959): read them as AURA_*.
. "$(cd "$(dirname "$0")/.." && pwd)/deploy/legacy-env.sh"
HOST="${AURA_HOST:?AURA_HOST (LAN address or name of the JARVIS host)}"
PORT="${AURA_SSH_PORT:-22}"
USER_="${AURA_SSH_USER:-deploy}"
KEY="${DEPLOY_SSH_KEY:-$HOME/.ssh/jarvis_deploy}"
TAG="${AURA_TAG:-}"   # required for a deploy; --rollback takes it from the host
REMOTE_DIR="${AURA_REMOTE_DIR:-/opt/jarvis}"
WAIT="${DEPLOY_HEALTH_WAIT:-420}"
HERE="$(cd "$(dirname "$0")/.." && pwd)"
SSH=(ssh -i "$KEY" -p "$PORT" -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=15 "$USER_@$HOST")
# stderr, never stdout: `compose_files` is captured with `$(…)`, and the one log
# line it prints landed inside COMPOSE_FILES -- every `docker compose` call then
# read `docker compose [deploy] 3c. no /dev/nvidia0 … (TTS …)` and bash refused
# the `(` (pipeline 276: rollout AND its rollback died on the same line, leaving
# .env on the new tag with the old containers running).
log() { echo "[deploy] $*" >&2; }

remote() { "${SSH[@]}" "$@"; }

# Every published port binds TAILSCALE_IP (the LAN address since 10-27). boot-recover.sh waits
# for it at reboot; a deploy did not, so a deploy while it was down stopped the old stack and
# could not start the new one ("cannot assign requested address"), and the rollback hit the same
# wall. Runs on the host from the deploy dir (sent with declare -f), and gives the address a
# minute to come back before the deploy gives up without having changed anything.
bind_address_present() {
  local addr n=0
  addr="$(sed -n 's/^TAILSCALE_IP=//p' .env 2>/dev/null | tr -d '"\r' | head -1)"
  [ -n "$addr" ] || return 0
  until ip -4 addr show 2>/dev/null | grep -qF "inet ${addr}/"; do
    [ "$n" -ge 60 ] && { echo "bind address $addr is on no interface of this host" >&2; return 1; }
    sleep 5; n=$((n + 5))
  done
}

# Which compose files this deploy uses. The GPU overlay names /dev/nvidia0 in a
# `devices:` list, and Docker refuses to CREATE a container whose device node is
# missing -- so including it unconditionally would turn "this host lost its GPU"
# into "this host cannot start the stack", which is a crash-loop, not a
# degradation (ADR-0039 rule 4). So the host is asked first, every deploy, and a
# host without the node simply gets the CPU path.
compose_files() {
  local files="-f docker-compose.yml"
  if [ -f "$HERE/deploy/docker-compose.gpu.yml" ] && remote "test -e /dev/nvidia0" 2>/dev/null; then
    log "3c. GPU present on $HOST — adding docker-compose.gpu.yml"
    files="$files -f docker-compose.gpu.yml"
    # The second card, for Whisper, only where the node exists (docker-compose.gpu2.yml).
    if [ -f "$HERE/deploy/docker-compose.gpu2.yml" ] && remote "test -e /dev/nvidia1" 2>/dev/null; then
      log "3c. second GPU on $HOST — adding docker-compose.gpu2.yml"
      files="$files -f docker-compose.gpu2.yml"
    fi
  else
    log "3c. no /dev/nvidia0 on $HOST — CPU path (TTS degrades to CPU, stack still starts)"
  fi
  echo "$files"
}
COMPOSE_FILES="$(compose_files)"
COMPOSE="docker compose $COMPOSE_FILES"

if [ "${1:-}" = "--rollback" ]; then
  prev="$(remote "cat $REMOTE_DIR/.previous_tag 2>/dev/null || true")"
  [ -n "$prev" ] || { echo "no .previous_tag on the host" >&2; exit 1; }
  log "rolling back to $prev"
  TAG="$prev"
fi
# --rollback never needed AURA_TAG, yet the old ${AURA_TAG:?} check ran first and
# `make rollback` could not start (2026-09-02).
[ -n "$TAG" ] || { echo "AURA_TAG (image tag to deploy) is required" >&2; exit 1; }

log "1. rsync deploy/ → $HOST:$REMOTE_DIR"
# rsync needs itself on both ends; the JARVIS host has none (pipeline 26: "error in
# rsync protocol data stream"), so tar over ssh is the path unless both sides have it.
if command -v rsync >/dev/null && remote "command -v rsync >/dev/null"; then
  # tauri-updater.key: the desktop app's update-signing key (ADR-0051), one of the
  # host's own secrets like .env -- --delete would otherwise take it on every deploy.
  rsync -az --delete --exclude '.env' --exclude 'n8n.env' --exclude '.previous_tag' --exclude 'backups' --exclude 'smoke-last.log' \
    --exclude 'tauri-updater.key' \
    -e "ssh -i $KEY -p $PORT -o BatchMode=yes -o StrictHostKeyChecking=accept-new" "$HERE/deploy/" "$USER_@$HOST:$REMOTE_DIR/"
else
  # No rsync here: tar the same set, never the host's own .env / .previous_tag / backups.
  tar -C "$HERE/deploy" --exclude='./.env' --exclude='./n8n.env' --exclude='./.previous_tag' --exclude='./backups' --exclude='./tests' -czf - . \
    | remote "mkdir -p $REMOTE_DIR && tar -C $REMOTE_DIR -xzf -"
fi
# n8n's own secrets (T-0630): created on first deploy, only missing keys added after, never
# printed. Before any compose call, because the compose file's env_file names n8n.env.
remote "cd $REMOTE_DIR && bash n8n-env.sh n8n.env"
remote "mkdir -p $REMOTE_DIR/scripts" && scp -i "$KEY" -P "$PORT" -o BatchMode=yes -q "$HERE/scripts/smoke.sh" "$HERE/scripts/console-gate-check.sh" "$HERE/scripts/voice_pitch.py" "$HERE/scripts/erase_doors_check.py" "$HERE/scripts/smoke-report.py" "$HERE/scripts/hermes-probes/"*.py "$USER_@$HOST:$REMOTE_DIR/scripts/" 2>/dev/null || true

# An image this pipeline could not ship (T-0419): build:models and build:cosyvoice are
# allow_failure, so their failure leaves the pipeline green and `:latest` on the last image
# that passed. Each failed .buildx job leaves image-failed-<svc>.txt (its job URL) in the
# artifacts this job downloads; the owner hears of it as a house notice, before the rollout so
# a rollout that fails still says it. A notice the house cannot take does not stop the deploy.
for f in "$HERE"/image-failed-*.txt; do
  [ -e "$f" ] || continue
  svc="$(basename "$f" .txt)"; svc="${svc#image-failed-}"
  log "1b. $svc: this pipeline's image did not ship; production keeps the last one"
  remote "cd $REMOTE_DIR && python3 health_guard.py --image-failed '$svc' '$(tr -d "'" < "$f")'" \
    || log "1b. could not record the $svc notice"
done

log "1c. the bind address is on $HOST"
remote "$(declare -f bind_address_present); cd $REMOTE_DIR && bind_address_present" \
  || { echo "[deploy] stopped before changing anything: the address every port binds is missing on $HOST" >&2; exit 1; }

log "2. record the running tag"
# The tag the jarvis container actually runs, not what .env says: a failed rollback once
# left .env at a rehearsal tag while the containers ran the real one.
remote "cd $REMOTE_DIR && (docker inspect --format '{{.Config.Image}}' jarvis 2>/dev/null | sed 's/.*://' > .previous_tag.new || true) && ( [ -s .previous_tag.new ] || grep -E '^(AURA|JARVIS)_TAG=' .env | head -1 | cut -d= -f2 | tr -d '\r' > .previous_tag.new || true ) && ( [ -s .previous_tag.new ] && mv .previous_tag.new .previous_tag || rm -f .previous_tag.new ); cat .previous_tag 2>/dev/null || echo '(none)'"

log "3. AURA_TAG=$TAG"

# The stack does not survive a reboot on its own: every port is bound to the tailnet
# address and dockerd races tailscaled for it (2026-09-05: production down for a day,
# every publishing container Exited(255) on "cannot assign requested address"). The
# recovery script ships in deploy/ and the deploy user's crontab gets ONE @reboot line
# for it, replaced rather than appended so redeploys do not stack copies. Cron rather
# than a systemd unit because the deploy user has no root and no linger.
log "3b. @reboot recovery line in the deploy user's crontab"
remote "cd $REMOTE_DIR && chmod +x boot-recover.sh && { crontab -l 2>/dev/null | grep -v 'jarvis-boot-recover' ; echo \"@reboot AURA_REMOTE_DIR=$REMOTE_DIR $REMOTE_DIR/boot-recover.sh # jarvis-boot-recover\"; } | crontab - && crontab -l | grep -c jarvis-boot-recover"
remote "cd $REMOTE_DIR && test -f .env && (grep -qE '^(AURA|JARVIS)_TAG=' .env && sed -i -E 's/^(AURA|JARVIS)_TAG=.*/\\1_TAG=$TAG/' .env || echo 'AURA_TAG=$TAG' >> .env)"

rollout() {
  log "4. compose pull + up"
  # A recreate cuts a reply in mid-sentence: wait, at most three minutes, for no voice turn to be
  # open (T-0577, deploy-here.sh's wait). Runs in the deploy dir (deploy/ is shipped there); its
  # failure is never the deploy's. The key and the address are read on the host, from its .env.
  remote "cd $REMOTE_DIR && (set -a && . ./.env && . ./legacy-env.sh && set +a && python3 wait_quiet.py \"http://\$TAILSCALE_IP:8643\")" || log "wait_quiet failed; not waiting"
  # --ignore-pull-failures: a tag scripts/deploy-here.sh built is on this host and never in the
  # registry, so a strict pull failed `make rollback` to it (T-0381). compose up still pulls an
  # image the host lacks and fails if it cannot, and 4b checks the tag that runs.
  remote "cd $REMOTE_DIR && $COMPOSE pull --quiet --ignore-pull-failures && $COMPOSE up -d --remove-orphans"
  log "4b. the jarvis container must run the requested tag"
  local running; running="$(remote "cd $REMOTE_DIR && docker inspect --format '{{.Config.Image}}' jarvis 2>/dev/null || true")"
  case "$running" in *":$TAG") log "   running $running" ;; *) echo "[deploy] jarvis runs '$running', not tag $TAG" >&2; return 1 ;; esac
  log "5. wait for /health (up to ${WAIT}s)"
  local ip; ip="$(remote "cd $REMOTE_DIR && grep -E '^TAILSCALE_IP=' .env | cut -d= -f2 | tr -d '\r'")"
  local n=0
  until remote "curl -fsS -m 5 http://$ip:8642/health >/dev/null && curl -fsS -m 5 http://$ip:8643/health >/dev/null"; do
    n=$((n + 10)); [ "$n" -ge "$WAIT" ] && return 1; sleep 10
  done
  # If this deploy claimed the GPU, prove it reaches the container with a real
  # allocation rather than trusting that the device node mounted. A device node
  # and a working nvidia-smi both survive a driver/userspace mismatch; the first
  # cuMemAlloc does not. Non-fatal on purpose: the TTS degrades to CPU (ADR-0039
  # rule 4), so a failed probe is a loud warning, not a reason to roll back a
  # stack that is otherwise healthy.
  case "$COMPOSE_FILES" in
    *docker-compose.gpu.yml*)
      log "5b. GPU allocation probe inside jarvis-cosyvoice"
      if remote "cd $REMOTE_DIR && docker compose $COMPOSE_FILES exec -T jarvis-cosyvoice python3 /opt/verify/gpu-allocation.py"; then
        log "   GPU probe PASSED"
      else
        echo "[deploy] WARNING: GPU probe FAILED — voice is on the CPU fallback" >&2
      fi
      ;;
  esac
  # The voice loads ~9 GB of weights and warms after every start; a smoke run
  # into that gets the shim's 503 and hermes serve's fallback, which is not
  # what the deploy is checking. Wait for `warm` (the model loads in ~2 min
  # from a cold page cache, the warm-up takes ~30 s), and say so if it never
  # comes -- degraded, not failed: the voice is not the stack (ADR-0039 rule 4).
  # Up to ten minutes: a first start on a new image compiles its kernels (189 s
  # of warm-up measured after the torch 2.6 move, 30 s on every start after).
  log "5c. wait for the voice to warm (up to 600s)"
  local w=0 voice
  while :; do
    voice="$(remote "curl -fsS -m 5 http://127.0.0.1:8890/health 2>/dev/null" || true)"
    case "$voice" in *'"warm":true'*) log "   voice warm after ${w}s ($(printf '%s' "$voice" | sed -E 's/.*"device":"([a-z]+)".*/\1/'))"; break ;; esac
    w=$((w + 15)); if [ "$w" -ge 600 ]; then echo "[deploy] WARNING: voice not warm after ${w}s: ${voice:-no answer} -- speech falls back to hermes serve" >&2; break; fi
    sleep 15
  done
  log "6. smoke on the host"
  # Teed to smoke-last.log beside the compose file: the CI job's trace needs an
  # owner PAT to read, and two deploys (292, 293) failed in this step after
  # twenty minutes with nothing on the host to say which check hung, while the
  # same smoke passed by hand each time. SMOKE_TIMEOUT bounds one hung check.
  # 600 s held until 2026-09-10 (pipeline 328): the failing-gate goal's third
  # try reasoned for ten minutes and the budget ran out with production fine.
  # `model.max_tokens` now ends such a turn at about five minutes; 900 s holds
  # two quick tries and one that runs to the cap, with the judge after it.
  remote "cd $REMOTE_DIR && set -a && . ./.env && . ./legacy-env.sh && set +a && AURA_BASE=http://\$TAILSCALE_IP:8643 HERMES_API_BASE=http://\$TAILSCALE_IP:8642 CONSOLE_BASE=http://\$TAILSCALE_IP:8199 SMOKE_PYTHON='docker exec -i jarvis /opt/hermes/.venv/bin/python' SMOKE_EXEC='docker exec jarvis' SMOKE_PY_IN_CONTAINER=1 SMOKE_WS_BASE=http://127.0.0.1:8643 SMOKE_TIMEOUT=900 bash -o pipefail -c 'bash scripts/smoke.sh 2>&1 | tee smoke-last.log'"
}

if rollout; then
  log "deployed $TAG"
  remote "cd $REMOTE_DIR && $COMPOSE ps && { python3 disk_guard.py --tags --host-dir $REMOTE_DIR || echo '[deploy] disk_guard --tags failed; old tags stay' >&2; } && $COMPOSE images jarvis"
  exit 0
fi

echo "[deploy] ROLLOUT OF $TAG FAILED — ROLLING BACK" >&2
prev="$(remote "cat $REMOTE_DIR/.previous_tag 2>/dev/null || true")"
# Only a tag the host or the registry still has can be restored; otherwise the new
# (health-passing) stack stays up rather than being replaced by a pull error. The host is
# asked first: a hand deploy's tag (scripts/deploy-here.sh) is never pushed (T-0381).
if [ -n "$prev" ] && [ "$prev" != "$TAG" ] && remote "cd $REMOTE_DIR && set -a && . ./.env && . ./legacy-env.sh && set +a && { docker image inspect \$AURA_REGISTRY/jarvis:$prev >/dev/null 2>&1 || docker manifest inspect \$AURA_REGISTRY/jarvis:$prev >/dev/null 2>&1; }"; then
  remote "cd $REMOTE_DIR && sed -i -E 's/^(AURA|JARVIS)_TAG=.*/\\1_TAG=$prev/' .env && $COMPOSE up -d --remove-orphans"
  echo "[deploy] restored $prev; host state:" >&2
  remote "cd $REMOTE_DIR && $COMPOSE ps" >&2
else
  echo "[deploy] no restorable previous tag (${prev:-none}); leaving $TAG running" >&2
  remote "cd $REMOTE_DIR && $COMPOSE ps" >&2
fi
exit 1
