"""Connect anything end to end, timed: one fake MCP server and one fake OpenAPI service (T-0609).

    python3 scripts/hermes-probes/connect_anything_demo.py --fake

Only local fakes (hermes/plugins/jarvis-integrations/tests/fake_services.py) are contacted and a
scratch HERMES_HOME holds the registry; no real service, secret or container is touched. For each
service it runs the three steps the model runs: add (validate, list, test call), turn on, one call.
Exits non-zero when a step fails or the whole run takes 60 s or more.
"""
from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLUGINS = ROOT / "hermes" / "plugins"
sys.path[:0] = [str(PLUGINS), str(PLUGINS / "jarvis-integrations" / "tests"), str(ROOT / "scripts")]
from _probe import brief  # noqa: E402
from _plugin_loader import load_plugin  # noqa: E402
import fake_services  # noqa: E402

LIMIT_S = 60.0


def _step(label: str, raw: str) -> dict:
    out = json.loads(raw)
    print(f"  {label}: {brief({'result': out}, 160)}")
    if not out.get("ok"):
        raise SystemExit(f"FAILED at {label}")
    return out


def main() -> int:
    if "--fake" not in sys.argv:
        print("only --fake is supported: this demo never touches a real service")
        return 2
    import os
    os.environ["HERMES_HOME"] = tempfile.mkdtemp(prefix="connect-demo-")
    connect = load_plugin("jarvis-integrations").connect
    server, url, _ = fake_services.serve()
    started = time.monotonic()
    try:
        for kind, add, tool, args in (
                ("MCP", {"url": url, "name": "demo-mcp"}, "send", {"text": "hello"}),
                ("OpenAPI", {"spec_url": url + "/openapi.json", "name": "demo-api"}, "getPet", {"id": 7})):
            t0 = time.monotonic()
            print(f"{kind}:")
            name = add["name"]
            added = _step("add", (connect.handle_mcp_add if kind == "MCP" else connect.handle_api_add)(
                {**add, "allow_private": True}))  # the fakes are on loopback
            print(f"    tools: {[t['name'] for t in added['tools']]}; test: {added['test'].get('tool')}")
            _step("turn on", connect.handle_enable({"name": name, "enabled": True}))
            _step("call", connect.handle_call({"connector": name, "tool": tool, "args": args}))
            print(f"  {kind} took {time.monotonic() - t0:.2f}s")
    finally:
        server.shutdown()
    total = time.monotonic() - started
    print(f"both services connected in {total:.2f}s (limit {LIMIT_S:.0f}s)")
    return 0 if total < LIMIT_S else 1


if __name__ == "__main__":
    sys.exit(main())
