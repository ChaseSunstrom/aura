"""Live proof of the aura-integrations plugin against a running `hermes serve`.

Run with the dev serve up on 127.0.0.1:9119 (token devservetoken123456):

    . .venv/bin/activate && python3 scripts/hermes-probes/integrations_probe.py

It opens the HUD socket, creates a session, and dispatches three read-only
``/jintegrations`` commands through ``command.dispatch`` — the same path the
console uses — printing the rows each returns:

    jintegrations @<sid> search skill weather   (first three rows)
    jintegrations @<sid> search mcp filesystem
    jintegrations @<sid> list plugin

It installs NOTHING: every command here is a browse, so nothing crosses the
approval gate and nothing is fetched.
"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from _probe import SERVE_URL, session  # noqa: E402


async def main() -> None:
    async with session(SERVE_URL) as rpc:

        async def dispatch(arg: str):
            r = await rpc.dispatch("jintegrations", arg, prefix_session=True)
            if "error" in r and "result" not in r:
                return {"ok": False, "error": r["error"]}
            res = r.get("result") or {}
            out = res.get("output") if isinstance(res, dict) else res
            try:
                data = json.loads(out) if isinstance(out, str) else out
            except (json.JSONDecodeError, TypeError):
                return {"raw": out}
            return data if isinstance(data, dict) else {"rows": data}

        print("\n--- jintegrations search skill weather (first 3 rows) ---")
        data = await dispatch("search skill weather")
        rows = data.get("rows", []) if isinstance(data, dict) else []
        print(f"ok={data.get('ok')} count={data.get('count')}")
        for row in rows[:3]:
            print("  •", row.get("id"), "|", row.get("source"), "|", row.get("trust"),
                  "|", (row.get("description") or "")[:60])

        print("\n--- jintegrations search mcp filesystem ---")
        data = await dispatch("search mcp filesystem")
        rows = data.get("rows", []) if isinstance(data, dict) else []
        print(f"ok={data.get('ok')} count={data.get('count')}")
        for row in rows[:5]:
            print("  •", row.get("id"), "|", row.get("source"), "|",
                  "installed" if row.get("installed") else "available", "|",
                  (row.get("description") or "")[:60])

        print("\n--- jintegrations list plugin ---")
        data = await dispatch("list plugin")
        rows = data.get("rows", []) if isinstance(data, dict) else []
        print(f"ok={data.get('ok')} count={data.get('count')}")
        for row in rows:
            print("  •", row.get("id"), "|", "enabled" if row.get("enabled") else "disabled",
                  "|", row.get("source"), "|", row.get("trust"))


asyncio.run(main())
