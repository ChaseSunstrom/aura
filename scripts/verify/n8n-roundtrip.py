#!/usr/bin/env python3
"""Prove the n8n plugin lists and triggers a workflow, over a real socket.

The plugin's own tests drive its handlers through an ``httpx.MockTransport``, which
proves the parsing but never opens a connection: a wrong base URL, a header the
client drops, or a redirect would all pass there and fail against n8n. So this runs
the *same handlers* against a stdlib HTTP server that answers the n8n API shapes on
a real port, and then probes the real n8n at ``N8N_URL`` and says which half of the
proof is live.

Live today: ``/healthz`` on the real instance. Pending: the REST half needs
``N8N_API_KEY``, which the owner has not supplied — without it the real instance
answers 401 to ``/api/v1/workflows``, and this script says so rather than passing
quietly. The stub half is complete either way.

    python3 scripts/verify/n8n-roundtrip.py            # stub + probe the real host
    python3 scripts/verify/n8n-roundtrip.py --stub-only

Exit 0 when the stub round trip holds (the live probe is reported, never fatal),
1 when the plugin's behaviour against the stub is wrong.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "hermes" / "plugins"))
sys.path.insert(0, str(ROOT / "aura-schema"))

API_KEY = "stub-api-key-not-a-secret"

#: One workflow, shaped the way n8n's REST API returns it — nodes and all, because
#: the plugin reads the webhook path out of the node list rather than a top-level
#: field, and a stub that flattened it would not exercise that.
WORKFLOW = {
    "id": "17",
    "name": "Lights off",
    "active": True,
    "nodes": [{"type": "n8n-nodes-base.webhook", "parameters": {"path": "lights-off"}},
              {"type": "n8n-nodes-base.set", "parameters": {}}],
    "connections": {},
}


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # keep the proof's output readable
        del fmt, args

    def _json(self, payload, status=200):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _keyed(self) -> bool:
        """n8n answers 401 without the header; the stub does too, so a client that
        forgot to send it fails here rather than looking fine until production."""
        if self.headers.get("X-N8N-API-KEY") == API_KEY:
            return True
        self._json({"message": "unauthorized"}, 401)
        return False

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/healthz":
            self._json({"status": "ok"})
        elif path == "/api/v1/workflows" and self._keyed():
            self._json({"data": [WORKFLOW]})
        elif path == "/api/v1/workflows/17" and self._keyed():
            self._json(WORKFLOW)
        elif path == "/api/v1/executions" and self._keyed():
            self._json({"data": [{"id": "901", "workflowId": "17", "status": "success",
                                  "startedAt": "2026-09-03T18:00:00Z", "mode": "webhook"}]})
        elif path.startswith("/api/v1/"):
            pass  # _keyed already answered
        else:
            self._json({"message": "not found"}, 404)

    def do_POST(self):
        path = urlparse(self.path).path
        length = int(self.headers.get("Content-Length") or 0)
        payload = json.loads(self.rfile.read(length) or b"{}")
        if path == "/webhook/lights-off":
            self._json({"done": True, "received": payload})
        elif path == "/api/v1/workflows" and self._keyed():
            self._json({**WORKFLOW, "id": "18", "name": payload.get("name", "?")})
        elif path.startswith("/api/v1/"):
            pass
        else:
            self._json({"message": "not found"}, 404)

    def do_PUT(self):
        length = int(self.headers.get("Content-Length") or 0)
        payload = json.loads(self.rfile.read(length) or b"{}")
        if self._keyed():
            self._json({**WORKFLOW, **payload})


def _check(label: str, ok: bool, detail: str, failures: list[str]) -> None:
    print(f"{'OK  ' if ok else 'FAIL'} {label:<34} {detail}")
    if not ok:
        failures.append(label)


def stub_round_trip() -> int:
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    os.environ["N8N_URL"] = f"http://127.0.0.1:{server.server_address[1]}"
    os.environ["N8N_API_KEY"] = API_KEY
    os.environ["N8N_VERIFY_SSL"] = "true"

    from _plugin_loader import load_plugin  # noqa: PLC0415 — after N8N_URL is set

    plugin = load_plugin("n8n-trigger")
    failures: list[str] = []
    try:
        health = json.loads(plugin.tools.handle_n8n_health({}))
        _check("n8n_health", health.get("ok") is True and health.get("api_key_ok") is True,
               f"http={health.get('http')} api_key_ok={health.get('api_key_ok')}", failures)

        listed = json.loads(plugin.tools.handle_n8n_list({}))
        rows = listed.get("workflows", [])
        _check("n8n_list_workflows",
               listed.get("ok") is True and rows and rows[0]["name"] == "Lights off"
               and rows[0]["webhooks"] == ["lights-off"],
               f"{listed.get('count')} workflow(s): {rows}", failures)

        fired = json.loads(plugin.tools.handle_n8n_trigger(
            {"webhook": "lights-off", "payload": {"room": "kitchen"}}))
        _check("n8n_trigger",
               fired.get("ok") is True and fired.get("response", {}).get("received")
               == {"room": "kitchen"},
               f"http={fired.get('http')} response={fired.get('response')}", failures)

        runs = json.loads(plugin.tools.handle_n8n_executions({"workflow_id": "17"}))
        _check("n8n_executions", runs.get("ok") is True and runs["executions"][0]["status"]
               == "success", f"{runs.get('executions')}", failures)

        # A real node list: the plugin refuses an empty one, because n8n accepts it and
        # then has a workflow that can never fire.
        created = json.loads(plugin.tools.handle_n8n_create_workflow(
            {"name": "Made by JARVIS",
             "nodes": [{"name": "Webhook", "type": "n8n-nodes-base.webhook",
                        "parameters": {"path": "made-by-jarvis"}, "position": [0, 0]}],
             "connections": {}}))
        _check("n8n_create_workflow", created.get("ok") is True,
               f"id={created.get('id')} name={created.get('name')}", failures)

        refused = json.loads(plugin.tools.handle_n8n_create_workflow(
            {"name": "Empty", "nodes": [], "connections": {}}))
        _check("a workflow with no nodes is refused", refused.get("ok") is False,
               refused.get("error", ""), failures)

        ctx = plugin.FakeCtx()
        plugin.register(ctx)
        gate = ctx.hooks["pre_tool_call"]
        gated = [name for name in ctx.tools if gate(name, {"webhook": "x", "id": "17"}, "")]
        # Every tool but the reads the matrix lists as free is a write (trigger, create,
        # update, activate, delete, credentials): a write added later is asked by default,
        # so only a new read needs naming here (T-0849).
        reads = {"n8n_health", "n8n_list_workflows", "n8n_get_workflow", "n8n_executions",
                 "n8n_execution", "n8n_credential_schema"}
        _check("approval gate covers the writes",
               set(gated) == set(ctx.tools) - reads,
               f"gated: {sorted(gated)}", failures)

        blob = json.dumps([health, listed, fired, runs, created])
        _check("no api key in any payload", API_KEY not in blob,
               "the key never appears in a tool result", failures)
    finally:
        server.shutdown()

    print()
    if failures:
        print(f"FAILED: {', '.join(failures)}", file=sys.stderr)
        return 1
    print("Stub round trip holds: the plugin lists workflows and triggers one over a real socket.")
    return 0


def probe_live(url: str) -> None:
    """Say what the real n8n answers, without needing or printing a credential."""
    import httpx  # noqa: PLC0415

    print(f"\nLive probe of {url}:")
    try:
        with httpx.Client(base_url=url.rstrip("/"), timeout=15) as client:
            health = client.get("/healthz")
            print(f"  /healthz              -> {health.status_code} {health.text.strip()[:60]}")
            api = client.get("/api/v1/workflows", params={"limit": 1})
            print(f"  /api/v1/workflows     -> {api.status_code}")
            if api.status_code == 401:
                print("  PENDING: listing workflows on the real instance needs N8N_API_KEY, which "
                      "the owner has not supplied. The stub proof above covers the behaviour.")
            elif api.status_code == 200:
                print("  LIVE: the REST half is proven against the real instance too.")
    except Exception as exc:  # noqa: BLE001 — a probe must never fail the proof
        print(f"  unreachable: {exc.__class__.__name__}: {exc}")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stub-only", action="store_true", help="skip the live probe")
    parser.add_argument("--url", default=os.environ.get("N8N_URL", ""),
                        help="the real n8n to probe (default: $N8N_URL)")
    args = parser.parse_args(argv[1:])
    live_url = args.url
    code = stub_round_trip()
    if not args.stub_only and live_url:
        probe_live(live_url)
    elif not args.stub_only:
        print("\nNo live probe: set N8N_URL or pass --url.")
    return code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
