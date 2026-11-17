"""Live proof of the activity view (M5) against a running stack: a real browser run
emits activity.* on the session's socket, and no typed secret leaves the container.

    set -a && . /opt/jarvis-host/.env && set +a
    AURA_ADAPTER_WS="ws://$TAILSCALE_IP:8643/ws?key=$AURA_API_KEY" \\
        .venv/bin/python scripts/hermes-probes/activity_probe.py

It creates a session through the adapter (the path the console takes), asks the
agent to open a page with the browser, and records every ``activity.*`` event
until the run ends. It passes when a start, at least one step and a frame with
an image arrive, every event carries its contract fields
(``tests/contracts/activity-frames.json``), the end follows, and the turn's
tool calls arrive as ``tool.start``/``tool.complete`` (what the console's step
timeline is drawn from, T-0732). ``--save PATH`` writes every ``tool.*`` and
``activity.*`` payload (images cut to their first bytes) so the console's
derivation is written against Hermes's real shapes. What it does not prove: the
console's drawing of them (``e2e/activity.spec.ts``) or the redaction of a
password field (``test_redaction.py``) -- this page has none.
"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from _probe import ADAPTER_URL, session  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
CONTRACT = json.loads((ROOT / "tests/contracts/activity-frames.json").read_text())
PROMPT = ("Use the browser to open https://example.com and tell me the page's title "
          "in one sentence. Use the browser tools, not a web search.")


def _cut(payload: dict) -> dict:
    return {k: (v[:80] + "…" if k == "image" and isinstance(v, str) else v) for k, v in payload.items()}


async def main() -> int:
    at = sys.argv.index("--save") + 1 if "--save" in sys.argv else 0
    save = Path(sys.argv[at]) if 0 < at < len(sys.argv) else None
    seen: list[dict] = []
    tools: list[dict] = []
    async with session(ADAPTER_URL) as rpc:
        await rpc.call("prompt.submit", {"session_id": rpc.sid, "text": PROMPT})
        print("prompt submitted; watching for activity.* …", flush=True)
        done_at = None
        async for p in rpc.events(420):
            typ = p.get("type") or ""
            if typ.startswith("tool."):
                tools.append({"type": typ, **_cut(p.get("payload") or {})})
                print(f"  {typ} {(p.get('payload') or {}).get('name', '')}", flush=True)
            if typ.startswith("activity."):
                payload = p.get("payload") or {}
                seen.append({"type": typ, **payload})
                shown = {k: (v[:60] + "…" if isinstance(v, str) and len(v) > 60 else v)
                         for k, v in payload.items() if k != "image"}
                print(f"  {typ} {json.dumps(shown)}{' +image' if payload.get('image') else ''}", flush=True)
                if typ == "activity.end":
                    break
            elif typ == "message.complete" and done_at is None:
                done_at = True
                print("  [message.complete]", json.dumps(p.get("payload") or {})[:160], flush=True)
                # The end follows the last visual call by idle_end_s; keep listening.
    types = [e["type"] for e in seen]
    missing = [(e["type"], f) for e in seen for f in CONTRACT["fields"].get(e["type"], []) if f not in e]
    pictured = any(e["type"] == "activity.frame" and e.get("image") for e in seen)
    tool_types = {t["type"] for t in tools}
    ok = ("activity.start" in types and "activity.step" in types and pictured
          and "activity.end" in types and not missing
          and {"tool.start", "tool.complete"} <= tool_types)
    if save:
        save.write_text(json.dumps({"activity": [_cut(e) for e in seen], "tools": tools}, indent=1))
    print(json.dumps({"events": types, "tools": [f"{t['type']} {t.get('name', '')}" for t in tools],
                      "missing_fields": missing, "ok": ok}))
    return 0 if ok else 1


sys.exit(asyncio.run(main()))
