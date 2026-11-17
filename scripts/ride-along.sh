#!/usr/bin/env bash
# The nightly ride-along (T-0196): the whole car voice path against production, every night,
# so a voice regression is found here and not on the owner's next drive.
#
#   scripts/ride-along.sh            # from the deploy user's crontab (# jarvis-ride-along)
#
# car_turn_probe.py --read-only: Jarvis's own voice asks a question as car-microphone audio,
# the house transcribes it through the speaker gate, Hermes answers on a car surface, and the
# reply comes back as the car's PCM. Read-only means it changes nothing and approves nothing:
# an approval request is denied and fails the run. Each pass appends its stage timings (one
# JSON line) to bench/results/ride-along.log; a failure appends why and records a notice in
# the house's notifications (console and phone). Does not cover the car's Bluetooth, Wi-Fi,
# head unit or speakers (docs/car/drive-checklist.md).
set -uo pipefail
HOST_ENV=${AURA_HOST_ENV:-/opt/jarvis-host/.env}
LOG=${RIDE_LOG:-/opt/jarvis/bench/results/ride-along.log}
PY=${RIDE_PYTHON:-/opt/jarvis/.venv/bin/python}
cd "$(dirname "$0")/.." || exit 1
# In the probe's environment, never its arguments: /proc/<pid>/cmdline is every local
# user's to read, /proc/<pid>/environ only this user's.
AURA_API_KEY=$(grep -m1 -E '^(AURA|JARVIS)_API_KEY=' "$HOST_ENV" | cut -d= -f2-)
HASS_TOKEN=$(grep -m1 '^HASS_TOKEN=' "$HOST_ENV" | cut -d= -f2-)
export AURA_API_KEY HASS_TOKEN
# The address compose binds the adapter to (deploy/docker-compose.yml "${TAILSCALE_IP}:8643"),
# not an interface: tailscale0 went with Tailscale on 2026-10-27 and every night since dialled
# loopback, where nothing listens ([Errno 111], AUDIT V8).
ip=$(sed -n 's/^TAILSCALE_IP=//p' "$HOST_ENV" | tr -d '"' | head -1)
out=$("$PY" scripts/hermes-probes/car_turn_probe.py --base "http://${ip:-127.0.0.1}:8643" \
  --core http://127.0.0.1:8080 --read-only --timeout 300 \
  --text "${RIDE_TEXT:-Which lights are on right now?}" --expect "${RIDE_EXPECT:-light}" 2>&1)
rc=$?
if [ $rc -eq 0 ]; then
  grep '^RIDE ' <<<"$out" | sed 's/^RIDE //' >> "$LOG"
else
  why=$(grep -m1 '^FAIL' <<<"$out" || tail -1 <<<"$out")
  "$PY" - "$why" "$LOG" <<'PY'
import json, sys, time
sys.path.insert(0, "deploy")
import health_guard
why, log = sys.argv[1], sys.argv[2]
with open(log, "a") as f:
    f.write(json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "failed": why}) + "\n")
health_guard.notify(f"Tonight's car voice ride-along failed: {why[:160]}", "ride-along",
                    body="The nightly run of the whole car voice path (scripts/ride-along.sh). "
                         "bench/results/ride-along.log has each night's timings.", source="ride_along")
PY
fi
exit $rc
