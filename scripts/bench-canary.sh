#!/usr/bin/env bash
# Nightly JCB canary: run the fast subset against the deployed stack, compare it with the
# previous night, and tell Sir in one line when two or more tasks stopped passing.
#
# Nothing measured Jarvis between hand runs (the last full JCB was 2026-09-11), so a model
# or image change that cost capability was found by the owner, not by us. bench:nightly in
# CI covers the full corpus against baseline.json but only when the GitLab runner is up and
# it tells nobody; this runs on the production host and speaks through house_tell_user —
# the same function the model uses (hermes/plugins/smart-home/tools.py tell_user), called
# inside the jarvis container so HASS_TOKEN never leaves it.
#
# Not installed automatically. To run it at 03:30 every night, add this cron line yourself:
#   30 3 * * * /opt/jarvis/scripts/bench-canary.sh >> /opt/jarvis/bench/results/canary/cron.log 2>&1
# (`bench-canary.sh --cron-line` prints it with this checkout's path.)
#
# It does NOT diagnose a failure (read <date>/*.md), notice a slow slide of one task a
# night (bench/canary.py's docstring), or run if the stack is down — a run where every task
# errors is a regression and says so, which is the point.
set -u
REPO="$(cd "$(dirname "$0")/.." && pwd)"
if [ "${1:-}" = "--cron-line" ]; then
  echo "30 3 * * * $REPO/scripts/bench-canary.sh >> $REPO/bench/results/canary/cron.log 2>&1"
  exit 0
fi
cd "$REPO" || exit 1
OUT="bench/results/canary"
NIGHT="$OUT/$(date -u +%F)"
mkdir -p "$NIGHT"
PY="$REPO/.venv/bin/python"

# The tailnet address the API server is published on (127.0.0.1:8642 does not answer).
ADDR="$(sed -n 's/^TAILSCALE_IP=//p' /opt/jarvis-host/.env 2>/dev/null | tr -d '"' | head -1)"
ADDR="${ADDR:-100.64.0.51}"
export BENCH_API_BASE="http://$ADDR:8642" BENCH_WS_BASE="http://$ADDR:8643" BENCH_MODEL=jarvis
# Read from the containers, never echoed: the API server key and the adapter's key differ
# (the adapter closes /ws with 4401 on the API server's).
BENCH_API_KEY="$(docker exec jarvis printenv API_SERVER_KEY)"
BENCH_WS_KEY="$(docker exec jarvis-adapter printenv AURA_API_KEY)"
export BENCH_API_KEY BENCH_WS_KEY
# Without this every fixture and checker runs on this host and fails like the agent did.
export BENCH_EXEC="docker exec jarvis"
export BENCH_WORK_ROOT=/opt/data/work BENCH_FILE_ROOT=/opt/data
export BENCH_FIXTURE_BASE=http://127.0.0.1:8899

# The fixture server is a separate process nothing else starts; ours lives for this run only.
FIX_PID=""
if ! curl -sf -o /dev/null http://127.0.0.1:8899/health; then
  "$PY" bench/fixtures/server.py --port 8899 --state "$NIGHT/fixtures" >> "$NIGHT/fixtures.log" 2>&1 &
  FIX_PID=$!
  trap '[ -n "$FIX_PID" ] && kill "$FIX_PID" 2>/dev/null' EXIT
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    curl -sf -o /dev/null http://127.0.0.1:8899/health && break
    sleep 1
  done
fi

# The fast subset: one or two tasks per category, the same 16 bench:mr gates on.
"$PY" -m bench run --subset fast --out "$NIGHT" > "$NIGHT/run.log" 2>&1
echo "$(date -u +%FT%TZ) bench run exit $?"
# No results file means the harness refused to start (stack down, key missing): comparing
# would re-judge last night's pair and repeat yesterday's message.
if ! ls "$NIGHT"/*.json > /dev/null 2>&1; then
  echo "JCB canary did not run on $(date -u +%F); see $NIGHT/run.log." | tee "$OUT/latest.md"
  exit 2
fi

"$PY" -m bench.canary "$OUT"
verdict=$?
if [ "$verdict" = 1 ]; then
  docker exec -e CANARY_LINE="$(cat "$OUT/latest.md")" -w /opt/data/plugins/smart-home jarvis \
    /opt/hermes/.venv/bin/python3 -c \
    'import os, sys; sys.path.insert(0, "."); import tools; r = tools.tell_user(os.environ["CANARY_LINE"], "Bench canary"); print("notified", r["ok"])'
fi
exit "$verdict"
