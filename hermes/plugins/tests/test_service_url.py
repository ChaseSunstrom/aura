"""The one rule every service plugin applies to the server address in its setting (T-1342).

``_egress.service_url`` is the rule: https anywhere, plain http only to an address that cannot be
the public internet, and nothing with a login, a query, a fragment or a metadata address. The
table pins it literally; the per-plugin test then proves each plugin in the table (the eight and the extractor, the DAV helper and settings) calls it
before it builds a request, so a refused address answers ``ok: false`` and sends nothing.

Does not cover: DNS (a single-label name resolves wherever the host's resolver says), a redirect a
real server answers with, or the model-supplied absolute webhook URL n8n-trigger still accepts.
All hosts here are made up; no server is contacted (``httpx.Client.send`` is replaced).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _plugin_loader import load_plugin  # noqa: E402
from _shared import _egress  # noqa: E402

OK = [
    "https://svc.example.test",
    "https://svc.example.test:8443/base/",
    "http://10.0.0.9:8080",
    "http://192.168.1.20",
    "http://172.16.0.5",
    "http://172.31.255.254:9000",
    "http://127.0.0.1:8000",
    "http://localhost:8000",
    "http://[::1]:8000",
    "http://[fd00::5]:8000",
    # What deploy/.env.example documents: compose service names, and the host from a container.
    "http://n8n:5678",
    "http://jarvis-models:8000",
    "http://host.docker.internal:8188",
    "https://169.254.1.1.example.test",  # a name, not the link-local address
]

REFUSED = [
    "http://svc.example.test",
    "http://svc.example.test:8080",
    "http://8.8.8.8",
    "http://172.32.0.1",  # just outside 172.16/12
    "http://2130706433",  # a number, not a service name
    "http://svc.example.test/path",
    "https://me:pw@svc.example.test",
    "http://me@10.0.0.9",
    "https://svc.example.test?x=1",
    "https://svc.example.test/#frag",
    "https://svc.example.test:99999",
    "https://svc.example.test:abc",
    "https://svc example.test",
    "ftp://10.0.0.9",
    "10.0.0.9:8080",
    "https://",
    "",
    "   ",
    "http://169.254.169.254",
    "https://169.254.169.254/latest",
    "http://metadata.google.internal",
    "https://metadata.google.internal.",
    "http://[fd00:ec2::254]",
    "https://[fd00:ec2::254]",
    "http://[::ffff:169.254.169.254]",
    "http://0.0.0.0:8000",
]


@pytest.mark.parametrize("url", OK)
def test_service_url_accepts(url: str) -> None:
    assert _egress.service_url(url, "X_URL") == url.rstrip("/")


@pytest.mark.parametrize("url", REFUSED)
def test_service_url_refuses(url: str) -> None:
    with pytest.raises(_egress.ServiceUrlError) as err:
        _egress.service_url(url, "X_URL")
    assert str(err.value).startswith("X_URL")
    assert "pw" not in str(err.value)  # the message never echoes a password


def test_service_url_none_is_unset() -> None:
    with pytest.raises(_egress.ServiceUrlError):
        _egress.service_url(None, "X_URL")


# ------------------------------------------------------------ the plugins
BAD = "http://svc.example.test"
BAD_PASSWORD = "https://me:pw@svc.example.test"
BAD_METADATA = "http://169.254.169.254"
KEYS = {"PLEX_TOKEN": "t", "JELLYFIN_TOKEN": "t", "N8N_API_KEY": "t", "PROXMOX_TOKEN_ID": "u@pam!i",
        "PROXMOX_TOKEN_SECRET": "s", "GRAFANA_TOKEN": "t", "INFLUX_TOKEN": "t",
        "COMFYUI_CHECKPOINT": "ckpt.safetensors", "GITLAB_TOKEN": "t"}

# (plugin directory, the URL setting, a handler that must reach the network, its params)
CASES = [
    ("aura-plex", "PLEX_URL", "handle_libraries", {}),
    ("aura-plex", "PLEX_URL", "handle_search", {"query": "x"}),
    ("aura-jellyfin", "JELLYFIN_URL", "handle_libraries", {}),
    ("aura-jellyfin", "JELLYFIN_URL", "handle_sessions", {}),
    ("n8n-trigger", "N8N_URL", "handle_n8n_list", {}),
    ("n8n-trigger", "N8N_URL", "handle_n8n_health", {}),
    ("n8n-trigger", "N8N_URL", "handle_n8n_trigger", {"webhook": "my-hook"}),
    ("proxmox-control", "PROXMOX_URL", "handle_nodes", {}),
    ("proxmox-control", "PROXMOX_URL", "handle_guest_action", {"vmid": 100, "action": "stop"}),
    ("grafana-dashboards", "GRAFANA_URL", "handle_search_dashboards", {}),
    ("gpu-telemetry", "GRAFANA_URL", "handle_grafana_health", {}),
    ("gpu-telemetry", "INFLUX_URL", "handle_gpu_status", {}),
    ("comfyui-imagegen", "COMFYUI_URL", "handle_status", {}),
    ("llama-swap-control", "LLAMA_SWAP_URL", "handle_llama_swap_models", {}),
    ("llama-swap-control", "LLAMA_SWAP_URL", "handle_llama_swap_status", {}),
    # T-1374: the extract provider, the shared DAV helper and the settings plugin's GitLab check.
    ("aura-extract", "EXTRACT_URL", "extract_urls", {}),
    ("aura-contacts", "DAV_URL", "handle_search", {"query": "x"}),
    ("aura-files", "DAV_URL", "handle_list", {}),
    ("aura-files", "DAV_URL", "handle_write", {"path": "a.md", "content": "x"}),
    ("aura-settings", "GITLAB_API_URL", "expiring", {}),
]


@pytest.fixture
def sent(monkeypatch):
    """Every request that reaches an httpx client's send; the test fails the call if one does."""
    seen: list[httpx.Request] = []

    def _send(self, request, **kwargs):
        seen.append(request)
        raise httpx.ConnectError("no network in this test", request=request)

    monkeypatch.setattr(httpx.Client, "send", _send)
    for key, value in KEYS.items():
        monkeypatch.setenv(key, value)
    return seen


def _answer(plugin: str, setting: str, handler: str, params: dict) -> dict:
    """The handler's answer as a dict. Two rows are not tool handlers: the extract provider answers
    a list (the first result is read) and the settings plugin's GitLab expiry check is a plain
    function over an environment mapping; each is turned into ``{"ok": ..., "error": ...}``."""
    mod = load_plugin(plugin)
    if plugin == "aura-extract":
        first = getattr(mod.provider, handler)(["https://page.example.test/a"])[0]
        return {"ok": False, "error": first["error"]}
    if plugin == "aura-settings":
        from datetime import date
        env = {"GITLAB_TOKEN": "t", "GITLAB_API_URL": os.environ[setting]}
        out = getattr(mod.settings, handler)(env, "/nonexistent", template="/nonexistent",
                                             read_cert=lambda host, port: date(2099, 1, 1),
                                             resolves=lambda host: True)
        row = next(r for r in out["rows"] if r.get("env") == "GITLAB_TOKEN")
        return {"ok": not row["warn"], "error": row.get("unreadable", "")}
    return json.loads(getattr(mod.tools, handler)(params))


@pytest.mark.parametrize("bad", [BAD, BAD_PASSWORD, BAD_METADATA])
@pytest.mark.parametrize(("plugin", "setting", "handler", "params"), CASES)
def test_service_url_refused_address_sends_nothing(monkeypatch, sent, plugin, setting, handler,
                                                  params, bad) -> None:
    monkeypatch.setenv(setting, bad)
    answer = _answer(plugin, setting, handler, params)
    assert answer["ok"] is False
    assert setting in answer["error"], answer
    assert "pw" not in json.dumps(answer)
    assert sent == []


@pytest.mark.parametrize(("plugin", "setting", "handler", "params"), CASES)
def test_service_url_private_address_reaches_the_client(monkeypatch, sent, plugin, setting,
                                                        handler, params) -> None:
    """The other half: a conforming plain-http address is let through to the client."""
    monkeypatch.setenv(setting, "http://10.0.0.9:8080")
    answer = _answer(plugin, setting, handler, params)
    assert answer["ok"] is False  # the stub send fails every request ...
    assert sent, answer  # ... but one was attempted, so the rule did not refuse
