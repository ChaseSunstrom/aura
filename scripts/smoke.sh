#!/usr/bin/env bash
# scripts/smoke.sh — the integration smoke against a running JARVIS stack.
#
#   AURA_BASE=http://127.0.0.1:8643 AURA_API_KEY=… scripts/smoke.sh
#
# Checks, in order (each prints PASS/FAIL and the evidence):
#   1. /health on the adapter and on the Hermes API server
#   2. one streamed chat completion (SSE chunks, [DONE])
#   3. one forced tool call that must actually EXECUTE (terminal echo of a nonce)
#   4. a two-step /goal loop over the HUD socket: judge says continue, then done
#   5. a failing /goal gate blocks completion (refused, retried, never achieved)
#   6. Whisper→Piper round trip through the relay: speak text, transcribe it back
#   6b. the voice is male and CosyVoice runs on cuda (scripts/voice_pitch.py)
#   7. the two erase doors answer 401/403 with no key and 400 to a window of 0 (scripts/erase_doors_check.py)
# Exits non-zero on the first failure. Needs curl, jq, python3 with `websockets`.
# SMOKE_EXEC="docker exec jarvis" makes the file checks run inside the container (the agent's
# filesystem); without it the agent and this script share a filesystem (dev install).
# SMOKE_PYTHON may itself be "docker exec -i jarvis …python": then SMOKE_PY_IN_CONTAINER=1 makes the
# python side check files directly and SMOKE_WS_BASE (default $AURA_BASE) should be the adapter as
# seen from inside the container (http://127.0.0.1:8643 — the host's Tailscale address does not
# hairpin from within the container's own network namespace).
set -uo pipefail
BASE="${AURA_BASE:-http://127.0.0.1:8643}"
KEY="${AURA_API_KEY:?set AURA_API_KEY}"
API="${HERMES_API_BASE:-}"           # optional: the raw Hermes API server (host:8642)
PY="${SMOKE_PYTHON:-python3}"
TIMEOUT="${SMOKE_TIMEOUT:-900}"
H=(-H "Authorization: Bearer $KEY" -H "Content-Type: application/json")
fail() { echo "FAIL: $*" >&2; exit 1; }
pass() { echo "PASS: $*"; }

# T-0580: the run is also sent to the adapter's last-verified board. The checks print PASS and
# FAIL from bash and from Python, so the run is played once more under a tee and its output
# read by smoke-report.py (beside this script; without it, or without python3, nothing changes).
# The report never changes the exit code.
if [ -z "${SMOKE_REPORTING:-}" ] && [ -f "$(dirname "$0")/smoke-report.py" ] && command -v python3 >/dev/null; then
  smoke_log="$(mktemp)"
  SMOKE_REPORTING=1 bash "$0" "$@" 2>&1 | tee "$smoke_log"
  rc="${PIPESTATUS[0]}"
  AURA_BASE="$BASE" AURA_API_KEY="$KEY" python3 "$(dirname "$0")/smoke-report.py" "$smoke_log" || true
  rm -f "$smoke_log"
  exit "$rc"
fi

echo "== 1. health"
curl -fsS -m 10 "$BASE/health" | tee /dev/stderr | jq -e '.status=="ok"' >/dev/null || fail "adapter /health"
curl -fsS -m 10 "${H[@]}" "$BASE/api/status" | tee /dev/stderr | jq -e '.hermes.api_server.ok and .hermes.backend.ok' >/dev/null || fail "/api/status"
if [ -n "$API" ]; then curl -fsS -m 10 "$API/health" | jq -e '.status=="ok"' >/dev/null || fail "hermes /health"; fi
# T-0387: a deploy that lost the console password reopens the console to the LAN.
if [ -n "${CONSOLE_BASE:-}" ]; then
  AURA_API_KEY="$KEY" bash "$(dirname "$0")/console-gate-check.sh" || fail "console gate"
fi
pass "health"

echo "== 1b. the approval matrix classifies every tool this Hermes exposes"
# The matrix tests that compare against Hermes skip wherever Hermes is not importable (CI,
# the dev venv), so the v2026.9.24 renames went unseen: cronjob_manage, todo_list and
# process_manage fell to matrix:unknown -- every reminder asked, and process's DENY rows
# stopped applying (2026-10-06). Only the image has the real table; check it here.
if [ "${SMOKE_PY_IN_CONTAINER:-0}" = 1 ]; then
  inv="$($PY - <<'PYINV' 2>&1
import sys
sys.path[:0] = ["/opt/hermes", "/opt/data/plugins"]
import model_tools
from toolsets import resolve_toolset
from _shared import _matrix as m
names = set(resolve_toolset("hermes-api-server")) | set(resolve_toolset("hermes-cli"))
unknown = sorted(n for n in names if m.decide(n, {}).rule_key == "matrix:unknown")
renamed = sorted(f"{old}->{new}" for old, new in getattr(model_tools, "_LEGACY_TOOL_ALIASES", {}).items()
                 if old in m.known_tools() and m.HERMES_RENAMED.get(new) != old)
print(f"unknown={unknown} renamed_without_alias={renamed}")
sys.exit(1 if unknown or renamed else 0)
PYINV
)" || fail "approval matrix: $inv"
  pass "approval matrix: $inv"
fi

echo "== 2. streamed chat completion"
out="$(curl -sN -m "$TIMEOUT" "${H[@]}" -H 'X-Hermes-Session-Id: smoke-stream' "$BASE/v1/chat/completions" \
  -d '{"model":"jarvis","stream":true,"messages":[{"role":"user","content":"Reply with exactly: smoke ok"}]}')"
n="$(grep -c '^data: ' <<<"$out")"; grep -q '^data: \[DONE\]' <<<"$out" || fail "no [DONE] in SSE"
[ "$n" -ge 2 ] || fail "only $n SSE chunks"
pass "SSE chunks=$n, [DONE] seen"

echo "== 2b. Hermes's context window fits the window the model server serves"
# 2026-10-09: Hermes held 262144 while vLLM served --max-model-len 256000, and every gate
# passed; a conversation would have run past the server's window before compressing (T-0349).
# After check 2 the model is loaded, so asking vLLM (through llama-swap) loads nothing.
if [ "${SMOKE_PY_IN_CONTAINER:-0}" = 1 ]; then
  win="$($PY - <<'PYWIN' 2>&1
import json, sys, urllib.request
import yaml
model = (yaml.safe_load(open("/opt/data/config.yaml")) or {}).get("model") or {}
base = str(model.get("base_url") or "").rstrip("/").removesuffix("/v1")
req = urllib.request.Request(f"{base}/upstream/{model.get('default')}/v1/models",
                             headers={"Authorization": f"Bearer {model.get('api_key') or ''}"})
with urllib.request.urlopen(req, timeout=600) as r:
    served = max(int(m.get("max_model_len") or 0) for m in json.load(r).get("data", []))
pinned = int(model.get("context_length") or 0)
print(f"hermes={pinned} served={served}")
sys.exit(0 if served and 0 < pinned <= served else 1)
PYWIN
)" || fail "context window: $win (set LLM_CONTEXT_LENGTH in the host's .env to the model's --max-model-len)"
  pass "context window: $win"
fi

echo "== 3. forced tool call executes"
# Two tries, and the stream's tail printed when one fails: pipeline 329's
# deploy failed here on a container eleven seconds old with "no tool progress
# event" and nothing else to read, while the same request passed by hand
# against that image and against production within the minute. What the
# step proves is that a tool call runs end to end; one answer without a tool
# is the model's, and the second try says whether the stack is broken.
for try in 1 2; do
nonce="smoke-$(date +%s)-$RANDOM"
out="$(curl -sN -m "$TIMEOUT" "${H[@]}" -H "X-Hermes-Session-Id: smoke-tool-$try" "$BASE/v1/chat/completions" \
  -d "{\"model\":\"jarvis\",\"stream\":true,\"messages\":[{\"role\":\"user\",\"content\":\"Use the terminal tool to run exactly: echo $nonce ; then reply with the command's output only.\"}]}")"
if grep -q 'hermes.tool.progress' <<<"$out" && grep -q "\"tool\": \"terminal\"" <<<"$out" && grep -q "$nonce" <<<"$out"; then break; fi
echo "WARN: try $try: progress=$(grep -c 'hermes.tool.progress' <<<"$out") terminal=$(grep -c '"tool": "terminal"' <<<"$out") nonce=$(grep -c "$nonce" <<<"$out"); the stream's tail:"
tail -c 1200 <<<"$out" | tr '\n' ' ' | cut -c1-1200; echo
[ "$try" = 2 ] && fail "the terminal tool did not run end to end (both tries)"
done
pass "terminal tool ran; nonce $nonce came back (try $try)"

echo "== 4. goal loop (continue then done), 5. failing gate blocks, 5b. a panel pulled up"
EXEC="${SMOKE_EXEC:-}"
if [ -n "$EXEC" ]; then
  WORK="/opt/data/work/smoke-$(date +%s)"
  # docker exec runs as root by default; the agent runs as uid 10000 (hermes) and must own the dir
  $EXEC mkdir -p "$WORK" && $EXEC chown 10000:10000 "$WORK" 2>/dev/null || true
else
  WORK="$(mktemp -d /tmp/smoke-goal-XXXX)"
fi
PY_EXEC="$EXEC"; [ "${SMOKE_PY_IN_CONTAINER:-0}" = "1" ] && PY_EXEC=""
$PY - "${SMOKE_WS_BASE:-$BASE}" "$KEY" "$TIMEOUT" "$WORK" "$PY_EXEC" <<'PY' || fail "steps 4-5d (goal loop, failing gate, HUD panel, HUD layout, approval round trip) — the last PASS line above says how far it got"
import os
import asyncio, json, os, shlex, subprocess, sys, time, websockets
base, key, timeout, work, execp = sys.argv[1], sys.argv[2], float(sys.argv[3]), sys.argv[4], sys.argv[5]
url = base.replace("http://", "ws://").replace("https://", "wss://") + f"/ws?key={key}"
# Every turn here says it is a probe, so the habit counter skips it (T-1201). The value is the
# contract's: tests/contracts/rig-session.json probe_surface, read against this text by
# deploy/tests/test_probe_mark.py (this runs in a container, without the checkout).
PROBE_SURFACE = "probe"
def exists(path):
    if execp:
        return subprocess.run(shlex.split(execp) + ["test", "-f", path]).returncode == 0
    return os.path.exists(path)
async def session(ws, events):
    """Numbered JSON-RPC calls; event frames that land mid-call go to `events`.

    The gateway writes every frame a handler produced to the socket BEFORE that
    handler's reply (tui_gateway.server.dispatch pins both to one transport), so
    an event a slash command emits synchronously arrives ahead of the reply to
    that command. Dropping non-matching frames here threw step 5c's
    hud.layout.apply away and the wait that followed the call then timed out on
    a frame that had already been read (pipeline 93).
    """
    rid = 0
    async def call(m, p):
        nonlocal rid; rid += 1
        await ws.send(json.dumps({"jsonrpc": "2.0", "id": rid, "method": m, "params": p}))
        while True:
            r = json.loads(await ws.recv())
            if r.get("id") == rid: return r
            if r.get("method") == "event": events.append(r["params"])
    return call
async def next_event(ws, events, deadline):
    """The next event's `params`: buffered ones first, then the socket. None at the deadline."""
    if events: return events.pop(0)
    while time.time() < deadline:
        try:
            m = json.loads(await asyncio.wait_for(ws.recv(), timeout=max(1, deadline - time.time())))
        except asyncio.TimeoutError:
            return None
        if m.get("method") == "event": return m["params"]
    return None
async def run_goal(text, gate=None, want=("continue", "achieved"), budget=timeout):
    async with websockets.connect(url, max_size=None) as ws:
        await ws.recv(); events = []; call = await session(ws, events)
        sid = (await call("session.create", {}))["result"]["session_id"]
        r = await call("command.dispatch", {"session_id": sid, "name": "goal", "arg": text})
        assert r["result"].get("type") == "send", r
        if gate:
            g = await call("command.dispatch", {"session_id": sid, "name": "jgoal", "arg": f"@{sid} gate add {gate}"})
            assert "result" in g and "Gate added" in g["result"].get("output", ""), f"jgoal gate add failed: {g}"
        await call("prompt.submit", {"session_id": sid, "text": r["result"]["message"], "surface": {"name": PROBE_SURFACE}})
        seen = []
        deadline = time.time() + budget
        while time.time() < deadline:
            p = await next_event(ws, events, deadline)
            if p is None: break
            pl = p.get("payload", {})
            # Approvals are manual in production, and a gated tool the agent
            # reaches for mid-goal (on the third try at an unsatisfiable gate it
            # went looking, pipeline 328) waits for a person until the socket's
            # budget is gone. Nobody is here: refuse, and the loop moves on.
            if p.get("type") == "approval.request" and pl.get("request_id"):
                await call("approval.respond", {"session_id": sid, "request_id": pl["request_id"], "choice": "deny"})
                print("    (denied", pl.get("command", "")[:80], ")")
                continue
            if p.get("type") == "status.update" and pl.get("kind") == "goal":
                seen.append(pl.get("text", "")); print("   ", pl.get("text", "")[:160])
                if any(k in pl.get("text", "") for k in ("✓ Goal achieved", "⏸ Goal paused")): break
                # An unsatisfiable gate is proven the moment the loop has failed it and
                # come back to fail it again: the retry after a refusal is the property,
                # exhaustion is Hermes' own bookkeeping. Waiting for the third attempt is
                # what lost deploys 311, 315, 328 and 335: told to do the impossible, the
                # model's later attempts run to the token cap and past the step's budget.
                if gate and sum("Quality gate failed" in s for s in seen) >= 2: break
        await call("command.dispatch", {"session_id": sid, "name": "goal", "arg": "clear"})
        return seen
# The judge is a language model: once (host smoke, 2026-09-02) it called the goal achieved
# after the first file because "stop after one file" read as the whole instruction. The
# text states the success criterion outright, and the loop gets a second attempt
# before the check counts as failed — the evidence wanted is achieved with both
# files present, not a single lucky verdict.
GOAL = (f"Goal: two files must exist — {work}/a.txt containing the line alpha and {work}/b.txt "
        "containing the line beta. The goal is achieved ONLY when BOTH files exist with those "
        "contents. Create them, then show the contents of each with cat.")
# What this step is for: the judge runs, and the goal is called achieved only
# once both files really exist. The text used to ask for two turns ("this turn
# create ONLY a.txt"), and that was the variance: pipelines 311 and 315 saw the
# agent make both files in one turn and the deploy roll back for the missing
# "↻ Continuing" line; 344 (2026-09-18) saw it obey, add its own gate for
# b.txt on the turn it was told not to make b.txt, fail it, and run the judge's
# follow-ups past the 900 s budget twice. The loop continuing is what step 5
# proves, so it is not asked for here; it is reported when seen.
for attempt in (1, 2):
    for f in ("a.txt", "b.txt"):
        try: os.remove(f"{work}/{f}")
        except FileNotFoundError: pass
    seen = asyncio.run(run_goal(GOAL))
    ok = (any("✓ Goal achieved" in s for s in seen)
          and exists(f"{work}/a.txt") and exists(f"{work}/b.txt"))
    if ok: break
    print(f"attempt {attempt} verdicts: {seen}")
assert ok, f"goal loop did not reach achieved with both files: {seen}"
continued = any("↻ Continuing toward goal" in s for s in seen)
print("PASS: goal loop achieved, both files exist" + (" (after a continue)" if continued else " (in one turn)"))
# The gate must be unsatisfiable: a `test -f <path>` gate was once passed by the agent simply
# creating that file, which is a legitimate gate pass. `false` cannot be gamed.
seen = asyncio.run(run_goal("Reply with the single word: done.", gate="false"))
assert any("Quality gate failed" in s for s in seen), seen
assert not any("✓ Goal achieved" in s for s in seen), seen
# What the check is for is the line above: an unsatisfiable gate never lets the
# goal be called achieved. Two refusals prove the loop retried after the first
# rather than calling it done; the pause after the last attempt is Hermes'
# bookkeeping, one more judge call away, and on pipelines 311 and 335 the step
# timed out at 900 s waiting for a third attempt that was still reasoning against
# `model.max_tokens` -- production was rolled back for it both times. On 357 the
# second refusal never came for another reason: told the gate had failed, the
# model called `goal_clear` and replied, and a cleared goal has no gate to fail.
# `goal_clear` asks now (the matrix, `goal:abandon`); nobody answers here, the
# refusal below denies it, and the loop goes on to its second refusal.
attempts = [s for s in seen if "Quality gate failed" in s]
assert any("⏸ Goal paused" in s for s in seen) or len(attempts) >= 2, seen
print("PASS: failing gate blocked completion" + (" and paused the goal" if any("⏸ Goal paused" in s for s in seen) else f" ({len(attempts)} refusals, the loop retried)"))

# 5b. The surface: the agent pulls a panel up (jarvis-hud), the event reaches the socket, the
# late-join list has it, and a dismiss-all clears it. Proves the plugin is loaded in this image
# and routes to this session — a panel that only lived in memory would pass no other check.
async def run_hud(budget=timeout):
    async with websockets.connect(url, max_size=None) as ws:
        await ws.recv(); events = []; call = await session(ws, events)
        sid = (await call("session.create", {}))["result"]["session_id"]
        before = await call("command.dispatch", {"session_id": sid, "name": "hud", "arg": f"@{sid} list"})
        # A gateway that does not know the command answers with an error object, not a
        # result: say which (pipeline 59 died on KeyError: 'result' with the plugin shipped
        # but not enabled in the volume's config).
        assert "result" in before, f"hud command not dispatched (plugin not enabled in this image's config?): {before.get('error')}"
        assert before["result"].get("type") == "plugin", f"hud command missing: {before}"
        assert json.loads(before["result"]["output"]) == [], before
        await call("prompt.submit", {"session_id": sid, "text": "Use the hud_show tool once to put a text panel titled Smoke on the screen with the markdown 'smoke ok'. Then reply with one word.", "surface": {"name": PROBE_SURFACE}})
        shown = None; deadline = time.time() + budget
        while time.time() < deadline and shown is None:
            p = await next_event(ws, events, deadline)
            if p is None: break
            if p.get("type") == "hud.panel.show" and p.get("session_id") == sid:
                shown = p["payload"]
        assert shown and shown.get("kind") == "text" and shown.get("title"), f"no hud.panel.show for the session: {shown}"
        after = json.loads((await call("command.dispatch", {"session_id": sid, "name": "hud", "arg": f"@{sid} list"}))["result"]["output"])
        assert any(x.get("id") == shown["id"] for x in after), after
        gone = await call("command.dispatch", {"session_id": sid, "name": "hud", "arg": f"@{sid} dismiss *"})
        assert gone["result"].get("type") == "plugin", gone
        assert json.loads((await call("command.dispatch", {"session_id": sid, "name": "hud", "arg": f"@{sid} list"}))["result"]["output"]) == []
        return shown
shown = asyncio.run(run_hud())
print(f"PASS: the agent pulled up panel {shown['id']} ({shown['kind']} '{shown['title']}'), listed it, dismissed all")

# 5c. The window manager: /hud_layout get answers (the hud-layout plugin is enabled in this
# image), a person's preset choice emits hud.layout.apply on this session's socket, and undo
# takes it back. Command path only — deterministic, no model turn.
async def run_hud_layout(budget=60):
    async with websockets.connect(url, max_size=None) as ws:
        await ws.recv(); events = []; call = await session(ws, events)
        sid = (await call("session.create", {}))["result"]["session_id"]
        got = await call("command.dispatch", {"session_id": sid, "name": "hud_layout", "arg": f"@{sid} get"})
        assert "result" in got and got["result"].get("type") == "plugin", f"hud_layout command not dispatched (plugin not enabled in this image's config?): {got.get('error') or got}"
        state = json.loads(got["result"]["output"])
        assert state.get("ok") and "locked" in state and "goal-start" in state.get("presets", []), state
        assert state["locked"] is False, f"Lock layout is on for this session; the smoke cannot arrange: {state}"
        arranged = json.loads((await call("command.dispatch", {"session_id": sid, "name": "hud_layout", "arg": f"@{sid} arrange goal-start"}))["result"]["output"])
        assert arranged.get("ok") and arranged.get("op") == "arrange", arranged
        # The plugin keeps the layout even when it finds no socket to send it to and says so
        # in `delivered`. Without this line that case passes the two asserts above and then
        # burns the whole budget waiting for a frame nobody sent; say which failure it is.
        assert arranged.get("delivered") is True, f"arrange reached no HUD client on this session: {arranged}"
        applied = None; deadline = time.time() + budget
        while time.time() < deadline and applied is None:
            p = await next_event(ws, events, deadline)
            if p is None: break
            if p.get("type") == "hud.layout.apply" and p.get("session_id") == sid:
                applied = p["payload"]
        assert applied and applied.get("op") == "arrange" and applied.get("activity") == "goal", f"no hud.layout.apply for the session: {applied}"
        undone = json.loads((await call("command.dispatch", {"session_id": sid, "name": "hud_layout", "arg": f"@{sid} undo"}))["result"]["output"])
        assert undone.get("ok") and undone.get("undid") == "arrange", undone
        # the undo is remembered as a decline; clear it so the smoke leaves no preference behind
        await call("command.dispatch", {"session_id": sid, "name": "hud_layout", "arg": f"@{sid} forget goal"})
        return applied
applied = asyncio.run(run_hud_layout())
print(f"PASS: hud_layout get answered, arrange goal-start emitted hud.layout.apply rev {applied['rev']}, undo took it back")

# 5d. An approval reaches the socket and the answer reaches Hermes. The goal steps above answer
# an approval when one comes but never require one, so on 2026-10-06 a Hermes upgrade that
# withdrew every approval before any app saw it (server requests, aura_adapter/
# server_requests.py) passed this smoke green. Asked for a gated call; answered deny, so
# nothing changes. Two takes: the model may answer without calling the tool once.
# The key must be one settings_set still asks about (a security key, T-0607) and the value
# its current one: hud.lock_layout became a plain key, so on 2026-11-08 the smoke set it on
# production with no approval at all. hard_stop_enabled asks (it disables a guardrail), and
# `true` is its default, so even an approval wrongly let through changes nothing.
async def run_approval(budget=timeout):
    async with websockets.connect(url, max_size=None) as ws:
        await ws.recv(); events = []; call = await session(ws, events)
        sid = (await call("session.create", {}))["result"]["session_id"]
        await call("prompt.submit", {"session_id": sid, "text": "Call settings_set once with key tool_loop_guardrails.hard_stop_enabled and value true, then reply with exactly what the tool answered.", "surface": {"name": PROBE_SURFACE}})
        asked = None; reply = None; deadline = time.time() + budget
        while time.time() < deadline and reply is None:
            p = await next_event(ws, events, deadline)
            if p is None: break
            pl = p.get("payload") or {}
            if p.get("type") == "approval.request" and pl.get("request_id") and asked is None:
                asked = pl
                await call("approval.respond", {"session_id": sid, "request_id": pl["request_id"], "choice": "deny"})
            elif p.get("type") == "message.complete" and p.get("session_id") == sid:
                reply = pl.get("text", "")
        return asked, reply
for take in (1, 2):
    asked, reply = asyncio.run(run_approval())
    if asked: break
    print(f"WARN: take {take}: no approval.request reached the socket; reply: {(reply or '')[:160]!r}")
assert asked, "no approval.request reached the socket in two takes (withdrawn upstream before any app saw it?)"
assert reply is not None and "withdrawn" not in reply.lower(), f"the deny did not reach Hermes: {reply!r}"
print(f"PASS: approval for {asked.get('command', '?')[:60]!r} reached the socket and the deny reached Hermes (take {take})")
PY

echo "== 6. voice round trip (the voice → Whisper)"
# Two takes before it counts as failed: the voice is a sampled model and a take
# can come out slurred -- "The kitchen later on, sir." (2026-09-10, by hand on
# a healthy stack) -- and what this step proves is the round trip, not one
# take's diction. A second take is a second synthesis, not a second listen.
for take in 1 2; do
t0=$(date +%s%N)
spk_headers="$(mktemp)"
spk="$(curl -fsS -m "$TIMEOUT" -D "$spk_headers" "${H[@]}" "$BASE/api/audio/speak" -d '{"text":"The kitchen lights are on, Sir."}')" || fail "speak"
t1=$(date +%s%N)
# Which engine spoke: the shim (CosyVoice, the default) answers `engine` in the
# body; a fallback to hermes serve is said in a header with its reason. Either
# is a spoken line, so neither fails the smoke, but the fallback is printed:
# a deploy that always fell back would otherwise look like one that never did.
engine="$(jq -r '.engine // empty' <<<"$spk")"
fallback="$(grep -i '^x-jarvis-tts-fallback:' "$spk_headers" | cut -d: -f2- | tr -d '\r' | sed 's/^ //')"
rm -f "$spk_headers"
[ -n "$fallback" ] && echo "WARN: speak fell back to hermes serve: $fallback"
du="$(jq -r '.data_url' <<<"$spk")"; [ -n "$du" ] && [ "$du" != "null" ] || fail "speak returned no data_url"
mime="$(sed -E 's#^data:([^;]+);.*#\1#' <<<"$du")"
tr="$(curl -fsS -m "$TIMEOUT" "${H[@]}" "$BASE/api/audio/transcribe" -d "{\"data_url\":\"$du\",\"mime_type\":\"$mime\"}")" || fail "transcribe"
t2=$(date +%s%N)
text="$(jq -r '.transcript' <<<"$tr")"
# An enrolled house refuses every voice it does not know -- Jarvis's own included --
# and blanks the words (the adapter's speaker gate). That refusal is right; the
# words are then read from hermes serve itself, behind the gate.
if [ "$(jq -r '.speaker.dropped // false' <<<"$tr")" = true ]; then
  echo "    the speaker gate refused the synthetic voice ($(jq -r '.speaker.reason' <<<"$tr")); STT read behind it"
  text="$(${EXEC/exec /exec -i } sh -c "curl -fsS -m $TIMEOUT -H \"X-Hermes-Session-Token: \$HERMES_SERVE_TOKEN\" -H 'content-type: application/json' http://127.0.0.1:9119/api/audio/transcribe -d @-" <<<"{\"data_url\":\"$du\",\"mime_type\":\"$mime\"}" | jq -r '.transcript')"
fi
if grep -qi 'kitchen lights' <<<"$text"; then break; fi
[ "$take" = 2 ] && fail "transcript was: $text (both takes)"
echo "WARN: take $take transcribed as: $text — a second take"
done
pass "TTS $(( (t1-t0)/1000000 )) ms (${engine:-hermes}), STT $(( (t2-t1)/1000000 )) ms, transcript: $text (take $take)"

echo "== 6b. the voice is the persona's: male, from CosyVoice on the GPU"
# The round trip above passes for any voice that says the words; the owner heard
# "a girl speaking instead of jarvis" (2026-10). Host python3: the check is stdlib
# and asks the voice's own loopback port. The deploys copy it beside this script.
voice="$(AURA_BASE="$BASE" AURA_API_KEY="$KEY" SMOKE_TIMEOUT="$TIMEOUT" python3 "$(dirname "$0")/voice_pitch.py" 2>&1)" || fail "voice: $voice"
pass "voice: $voice"

echo "== 7. the two erase doors are deployed, guarded, and nothing was erased to find out"
# T-1412: the house's forget_window and the adapter's artifact forget_window had only run
# against test files. Each is asked with no key (must be 401/403: a 404 is a door the image
# does not have) and with the key and a window of 0 minutes (must be 400). Never a window from
# 1 to 1440: that is an erase, and this runs against the live house. Run through $PY so that, in
# the container, the house's HASS_URL and HASS_TOKEN are the ones the Hermes tool uses; the
# house half is left out (and the PASS line says so) where the smoke has no HASS_URL.
doors="$($PY - "${SMOKE_WS_BASE:-$BASE}" "$KEY" < "$(dirname "$0")/erase_doors_check.py" 2>&1)" || fail "erase doors: $doors"
pass "erase doors guarded: $doors"

echo "ALL SMOKE CHECKS PASSED"
