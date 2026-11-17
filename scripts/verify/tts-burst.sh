#!/usr/bin/env bash
# Every pack's voice says a burst of sentences, and the voice service did not run out of GPU
# memory doing it (T-0901). The owner saw replies on the console that were never said: the
# service held CosyVoice's model beside Kokoro under a VRAM cap measured for CosyVoice alone, and
# 14 of 20 sentences died "CUDA out of memory". A sentence that fails is a reply shown and not
# said, so one failure fails this.
#
# Runs on the host that runs the stack: it posts from inside the container, so no key and no port
# beyond the container's own loopback. Does not cover the console's own playback (the browser's
# audio element) or the adapter's route in front of the service.
set -euo pipefail

SVC="${COMPOSE_PROJECT_NAME:-jarvis}-cosyvoice"
since=$(docker inspect -f '{{.State.StartedAt}}' "$SVC")

docker exec -i "$SVC" python3 - <<'EOF'
import json
import sys
import urllib.request

LINES = [
    "Good evening.",
    "The kitchen lights are off.",
    "It is fourteen degrees outside, with a light wind from the north.",
    "I found three results; the second one looks closest to what you asked for.",
    "Your next meeting starts in twenty five minutes.",
    "Done.",
    "The front door has been locked since nine forty this morning, and nobody has opened it.",
    "I could not reach the printer, so I left the document in your downloads.",
    "Here is the route: about eleven minutes by car, most of it on the main road.",
    "The washing machine finished twenty minutes ago.",
    "Sure.",
    "Tomorrow looks dry until the afternoon, then rain from about four o'clock.",
    "I set a timer for twelve minutes.",
    "The garage sensor has not reported in for an hour; its battery may be low.",
    "Playing your evening playlist in the living room.",
    "That address is two point three kilometres from here, on the far side of the park, past the school.",
    "I have added milk, eggs and coffee to the shopping list.",
    "The upstairs thermostat is set to twenty one degrees.",
    "Nothing new since you last asked.",
    "All right, I will remind you when you get home.",
]

failed = 0
for voice in ("af_heart", "bm_george"):
    ok = 0
    for line in LINES:
        body = json.dumps({"input": line, "voice": voice, "response_format": "wav"}).encode()
        req = urllib.request.Request("http://127.0.0.1:8890/v1/audio/speech", data=body,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                ok += r.status == 200 and len(r.read()) > 1000
        except Exception as e:  # an HTTP 500 is the failure this script exists for
            print(f"{voice}: {line[:40]!r} -> {e}", file=sys.stderr)
    print(f"{voice}: {ok}/{len(LINES)} said")
    failed += len(LINES) - ok
sys.exit(1 if failed else 0)
EOF

# Not grep -q: under pipefail it exits on the first match, docker logs dies of SIGPIPE, and the
# pipeline's 141 reads as no match, so an out-of-memory line would pass.
if docker logs --since "$since" "$SVC" 2>&1 | grep -E "OutOfMemoryError|CUDA out of memory" >/dev/null; then
    echo "the voice service ran out of GPU memory since it started" >&2
    exit 1
fi
echo "no GPU out-of-memory since $since"
