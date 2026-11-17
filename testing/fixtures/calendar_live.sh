#!/usr/bin/env bash
# The calendar, end to end, against a real CalDAV server (Radicale) on loopback.
#
#   bash testing/fixtures/calendar_live.sh
#
# What it proves (T-0147): an invitation seeded on the server is listed with its
# attendees and the owner's own answer, through Hermes's aura-calendar plugin ->
# the house REST -> jarvis-core's calendar integration -> Radicale; an RSVP is stored
# as PARTSTAT=ACCEPTED with If-Match (and a stale ETag is refused by the real server,
# not by a mock); an event the owner is not invited to is refused; and the morning
# brief lists the event. See calendar_live_driver.py for the steps.
#
# Where things live: Radicale is installed into a throwaway venv under $LIVE_SCRATCH
# (default: a temp directory removed on exit) -- never into the repo's .venv -- and
# listens on 127.0.0.1 only, on a free port, with its storage in a temp directory.
# The code under test runs with the repo's own Python. Needs network once, to fetch
# pip and Radicale; the venv is reused if $LIVE_SCRATCH already has one.
#
# Environment: ROOT (repo checkout, default: the git toplevel of the cwd),
# PY (the repo's python, default: /opt/jarvis/.venv/bin/python), LIVE_SCRATCH.
set -euo pipefail

ROOT="${ROOT:-$(git rev-parse --show-toplevel 2>/dev/null || echo /opt/jarvis)}"
PY="${PY:-/opt/jarvis/.venv/bin/python}"
[ -x "$PY" ] || PY="$ROOT/.venv/bin/python"
if [ -n "${LIVE_SCRATCH:-}" ]; then
	SCRATCH="$LIVE_SCRATCH"
	mkdir -p "$SCRATCH"
	KEEP=1
else
	SCRATCH="$(mktemp -d)"
	KEEP=0
fi
RUN="$(mktemp -d "$SCRATCH/run.XXXXXX")"
RADICALE_PID=""

cleanup() {
	if [ -n "$RADICALE_PID" ]; then
		kill "$RADICALE_PID" 2>/dev/null || true
		wait "$RADICALE_PID" 2>/dev/null || true
	fi
	rm -rf "$RUN"
	[ "$KEEP" = 1 ] || rm -rf "$SCRATCH"
}
trap cleanup EXIT

VENV="$SCRATCH/venv"
if [ ! -x "$VENV/bin/radicale" ]; then
	# No ensurepip on this host: a venv without pip, then pip from its bootstrap script.
	python3 -m venv --without-pip "$VENV"
	curl -sSf https://bootstrap.pypa.io/get-pip.py -o "$SCRATCH/get-pip.py"
	"$VENV/bin/python" "$SCRATCH/get-pip.py" -q >/dev/null
	"$VENV/bin/python" -m pip install -q radicale >/dev/null
fi

PORT="$("$VENV/bin/python" -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])')"
"$VENV/bin/python" -m radicale --config "" \
	--server-hosts "127.0.0.1:$PORT" \
	--storage-filesystem-folder "$RUN/collections" \
	--auth-type none --logging-level warning >"$RUN/radicale.log" 2>&1 &
RADICALE_PID=$!

for _ in $(seq 1 100); do
	curl -s -o /dev/null "http://127.0.0.1:$PORT/" && break
	kill -0 "$RADICALE_PID" 2>/dev/null || { cat "$RUN/radicale.log" >&2; echo "Radicale did not start" >&2; exit 1; }
	sleep 0.1
done

cd "$ROOT/aura-core"
"$PY" "$ROOT/testing/fixtures/calendar_live_driver.py" "http://127.0.0.1:$PORT"
