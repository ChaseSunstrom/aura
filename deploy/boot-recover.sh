#!/usr/bin/env bash
# Bring the stack back after a reboot, once the tailnet address exists.
#
# Every published port in docker-compose.yml is bound to ${TAILSCALE_IP} on purpose
# (tailnet-only, ADR-0014). On 2026-09-05 the LXC rebooted and dockerd came up four
# seconds before tailscaled had assigned that address, so every container that
# publishes a port failed with "failed to bind host port 100.64.0.51:8642/tcp:
# cannot assign requested address" and was left Exited(255). `restart: unless-stopped`
# does not retry a container whose restore failed at daemon start, and a later plain
# `docker compose up -d` started them WITHOUT programming the port forwarding (the
# binding config survived, NetworkSettings.Ports came back empty). Production was down
# for a day and nothing said so. This script is what a person would have done at 01:15.
#
# Installed as the deploy user's `@reboot` cron line by scripts/deploy.sh, so it reaches
# the host through the pipeline and never by hand. Safe to run at any time: when every
# service is already running it does nothing.
#
# It does NOT cover: a service that is up but unhealthy (that is the healthcheck's
# job and the compose file's), a missing image (the deploy's), or tailscaled itself
# never coming up (it gives up after the wait and says so in the log).
set -u
# The @reboot line an older deploy installed still says JARVIS_REMOTE_DIR (T-0959).
DIR="${AURA_REMOTE_DIR:-${JARVIS_REMOTE_DIR:-$(cd "$(dirname "$0")" && pwd)}}"
LOG="$DIR/logs/boot-recover.log"
WAIT_S="${BOOT_RECOVER_WAIT_S:-600}"
mkdir -p "$DIR/logs"
cd "$DIR" || exit 1

log() { printf '%s %s\n' "$(date -u +%FT%TZ)" "$*" >> "$LOG"; }

ip_from_env() { sed -n 's/^TAILSCALE_IP=//p' .env 2>/dev/null | tr -d '"' | head -1; }

# The address we bind is the one the compose file reads, not whatever tailscale0
# happens to carry: if .env names a different address the stack would fail the same
# way, and this script should wait for the right thing.
addr="$(ip_from_env)"
if [ -z "$addr" ]; then
  log "no TAILSCALE_IP in .env; nothing to wait for, running compose up as-is"
else
  waited=0
  until ip -4 addr show 2>/dev/null | grep -q "inet ${addr}/"; do
    if [ "$waited" -ge "$WAIT_S" ]; then
      log "gave up: ${addr} not on any interface after ${WAIT_S}s"
      exit 2
    fi
    sleep 5; waited=$((waited + 5))
  done
  log "${addr} present after ${waited}s"
fi

# The GitLab runner is caught by the same race, one layer along, and it fails
# silently. Docker copies the host's /etc/resolv.conf into a container when the
# container STARTS, so a runner that started before tailscaled rewrote the host
# resolver inherits the LAN nameserver and can never resolve the GitLab host on
# the tailnet. It then logs "Checking for jobs... failed … i/o timeout" once per
# poll and picks up nothing.
#
# Nothing in GitLab says so. Jobs tagged `jarvis` — every build:*,
# integration:smoke and deploy:production — sit `pending` for ever while untagged
# jobs run normally on another runner, so the pipeline merely looks slow.
# 2026-09-09: four builds waited 2244 s before anyone thought to look at the
# runner rather than at the code.
#
# The host is read from the runner's own config rather than guessed: if it cannot
# be read, this does nothing, because restarting a container on a guess is worse
# than leaving it alone. The runner is not in this compose project, which is why
# the recreate below would never have covered it.
if docker inspect gitlab-runner >/dev/null 2>&1; then
  gl_host="$(docker exec gitlab-runner sh -c 'sed -n "s|^[[:space:]]*url = \"[a-z]*://\([^/\"]*\).*|\1|p" /etc/gitlab-runner/config.toml' 2>/dev/null | head -1)"
  if [ -z "$gl_host" ]; then
    log "gitlab-runner is here but its url could not be read; leaving it alone"
  elif docker exec gitlab-runner getent hosts "$gl_host" >/dev/null 2>&1; then
    log "gitlab-runner resolves ${gl_host}"
  else
    log "gitlab-runner cannot resolve ${gl_host}; restarting it to re-copy the host resolver"
    if docker restart gitlab-runner >> "$LOG" 2>&1; then log "gitlab-runner restarted"; else log "gitlab-runner restart FAILED"; fi
  fi
fi

# The stack's SearXNG, the same race: on 2026-10-07 it came up with a resolver that could
# reach nothing, so every engine was "unresponsive", web_search returned no results for a
# day, and the model scraped pages through a browser instead. A restart re-copies the
# resolver. Judged by a public name, which is what its engines need.
search="${COMPOSE_PROJECT_NAME:-jarvis}-searxng"
if docker inspect "$search" >/dev/null 2>&1; then
  if docker exec "$search" python3 -c "import socket; socket.gethostbyname('duckduckgo.com')" >/dev/null 2>&1; then
    log "${search} resolves public names"
  else
    log "${search} cannot resolve public names; restarting it to re-copy the host resolver"
    if docker restart "$search" >> "$LOG" 2>&1; then log "${search} restarted"; else log "${search} restart FAILED"; fi
  fi
fi

# Services that are defined but not running. A service that failed its port bind at
# daemon start is Exited and must be RECREATED, not started: a plain start reuses the
# container whose network was never programmed.
# The same overlay rule as scripts/deploy.sh step 3c: the card, only when the node
# is there. A reboot that came back without the overlay would leave the voice on
# the CPU until the next deploy, and nothing would say so.
if [ -e /dev/nvidia0 ] && [ -f docker-compose.gpu.yml ]; then
  export COMPOSE_FILE=docker-compose.yml:docker-compose.gpu.yml
  if [ -e /dev/nvidia1 ] && [ -f docker-compose.gpu2.yml ]; then
    export COMPOSE_FILE=$COMPOSE_FILE:docker-compose.gpu2.yml
  fi
fi
down="$(docker compose ps --services --status exited --status created --status dead 2>/dev/null)"
missing="$(comm -23 <(docker compose config --services 2>/dev/null | sort) \
                    <(docker compose ps --services 2>/dev/null | sort))"
todo="$(printf '%s\n%s\n' "$down" "$missing" | sed '/^$/d' | sort -u)"
if [ -z "$todo" ]; then
  log "every service running; nothing to do"
  exit 0
fi
log "recreating: $(echo "$todo" | tr '\n' ' ')"
# shellcheck disable=SC2086
if docker compose up -d --force-recreate $todo >> "$LOG" 2>&1; then
  log "done: $(docker compose ps --format '{{.Service}}={{.Status}}' | tr '\n' ' ')"
else
  log "compose up failed (see above)"
  exit 3
fi
