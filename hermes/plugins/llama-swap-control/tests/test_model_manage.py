"""model_download / model_add / model_settings / model_delete against a fake adapter.

The console's Models screen calls ``/api/models`` on the adapter (which holds the manager's own
key); these tools send the same requests to the same routes. Nothing here reaches a real server.

Not covered: the manager's own checks (``deploy/tests/test_models_manager.py``); a refusal here is
only the tool's input check, and a manager refusal is passed through as the manager's sentence.
"""
from __future__ import annotations

import json
import secrets
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from _plugin_loader import load_plugin  # noqa: E402

plugin = load_plugin("llama-swap-control")
tools = plugin.tools
schemas = plugin.schemas

KEY = secrets.token_hex(8)


@pytest.fixture
def adapter(monkeypatch):
    """A fake adapter; ``seen`` is every (method, path+query, json body, authorization) it got."""
    class Seen(list):
        reply: dict = {}

    seen = Seen()
    reply = seen.reply = {"status": 200, "json": {"ok": True}}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        seen.append((request.method, request.url.raw_path.decode(), body, request.headers.get("authorization", "")))
        return httpx.Response(reply["status"], json=reply["json"])

    monkeypatch.setenv("AURA_ADAPTER_URL", "http://adapter.test")
    monkeypatch.setenv("AURA_ADAPTER_KEY", KEY)
    monkeypatch.setattr(tools, "_adapter_client", lambda timeout=30.0: httpx.Client(
        base_url="http://adapter.test", transport=httpx.MockTransport(handler),
        headers={"Authorization": f"Bearer {KEY}"}))
    return seen


def _run(handler, params):
    return json.loads(handler(params))


# ------------------------------------------------------------------ model_download
def test_model_download_sends_the_consoles_request(adapter):
    out = _run(tools.handle_model_download, {"repo": "owner/name"})
    assert out["ok"] is True
    assert adapter == [("POST", "/api/models/downloads", {"repo": "owner/name"}, f"Bearer {KEY}")]


def test_model_download_of_one_gguf_sends_the_file_too(adapter):
    _run(tools.handle_model_download, {"repo": "owner/name-GGUF", "file": "q4/name-q4_k_m.gguf"})
    assert adapter[0][2] == {"repo": "owner/name-GGUF", "file": "q4/name-q4_k_m.gguf"}


@pytest.mark.parametrize("params", [
    {}, {"repo": "name"}, {"repo": "https://hf.test/owner/name"}, {"repo": "../owner/name"},
    {"repo": "owner/name", "file": "../x.gguf"}, {"repo": "owner/name", "file": "model.bin"},
    {"repo": "owner/name", "file": "/etc/x.gguf"}, {"repo": ["owner/name"]},
])
def test_model_download_refuses_what_is_not_a_repo_and_sends_nothing(adapter, params):
    out = _run(tools.handle_model_download, params)
    assert out["ok"] is False and out["error"]
    assert adapter == []


def test_model_download_passes_the_managers_refusal_through(adapter):
    adapter.reply.update(status=507, json={"ok": False, "error": "Not enough disk for owner/name."})
    out = _run(tools.handle_model_download, {"repo": "owner/name"})
    assert out == {"ok": False, "http": 507, "error": "Not enough disk for owner/name."}


# ------------------------------------------------------------------ model_add
def test_model_add_sends_the_consoles_create_body(adapter):
    out = _run(tools.handle_model_add, {"id": "my-model", "repo": "owner/name", "engine": "vllm",
                                        "settings": {"max_model_len": 32768, "keep_loaded": False}})
    assert out["ok"] is True
    assert adapter == [("POST", "/api/models", {"id": "my-model", "repo": "owner/name", "engine": "vllm",
                                                "settings": {"max_model_len": 32768, "keep_loaded": False}},
                        f"Bearer {KEY}")]


def test_model_add_llama_cpp_carries_its_file_and_defaults_to_no_settings(adapter):
    _run(tools.handle_model_add, {"id": "g.1", "repo": "owner/name-GGUF", "engine": "llama.cpp", "file": "a.gguf"})
    assert adapter[0][2] == {"id": "g.1", "repo": "owner/name-GGUF", "engine": "llama.cpp",
                             "file": "a.gguf", "settings": {}}


@pytest.mark.parametrize("params", [
    {"repo": "owner/name"},                                            # no id
    {"id": "Has Space", "repo": "owner/name"},
    {"id": "a/b", "repo": "owner/name"},
    {"id": "m", "repo": "http://hf.test/o/n"},
    {"id": "m", "repo": "owner/name", "engine": "tgi"},
    {"id": "m", "repo": "owner/name", "engine": "llama.cpp"},          # llama.cpp needs its .gguf
    {"id": "m", "repo": "owner/name", "engine": "vllm", "file": "a.gguf"},
    {"id": "m", "repo": "owner/name", "settings": "big"},
    {"id": "m", "repo": "owner/name", "settings": {"cmd": "rm -rf /"}},  # a flag the manager has no setting for
])
def test_model_add_refuses_a_bad_request_and_sends_nothing(adapter, params):
    out = _run(tools.handle_model_add, params)
    assert out["ok"] is False and out["error"]
    assert adapter == []


def test_model_add_reports_a_server_error(adapter):
    adapter.reply.update(status=500, json={"ok": False, "error": "The model manager failed: OSError."})
    out = _run(tools.handle_model_add, {"id": "m", "repo": "owner/name"})
    assert out == {"ok": False, "http": 500, "error": "The model manager failed: OSError."}


# ------------------------------------------------------------------ model_settings
def test_model_settings_patches_the_one_model(adapter):
    out = _run(tools.handle_model_settings, {"model": "my-model", "settings": {"max_model_len": 8192, "keep_loaded": True}})
    assert out["ok"] is True
    assert adapter == [("PATCH", "/api/models/my-model", {"settings": {"max_model_len": 8192, "keep_loaded": True}},
                        f"Bearer {KEY}")]


@pytest.mark.parametrize("params", [
    {"settings": {"max_model_len": 1}},                  # no model
    {"model": "../x", "settings": {"max_model_len": 1}},
    {"model": "m"}, {"model": "m", "settings": {}}, {"model": "m", "settings": [1]},
    {"model": "m", "settings": {"cmd": "x"}},
])
def test_model_settings_refuses_a_bad_request_and_sends_nothing(adapter, params):
    out = _run(tools.handle_model_settings, params)
    assert out["ok"] is False and out["error"]
    assert adapter == []


def test_model_settings_passes_the_managers_refusal_through(adapter):
    adapter.reply.update(status=400, json={"ok": False, "error": "max_model_len must be a number from 1024 to 1048576."})
    out = _run(tools.handle_model_settings, {"model": "m", "settings": {"max_model_len": 5}})
    assert out["ok"] is False and out["http"] == 400 and "max_model_len" in out["error"]


# ------------------------------------------------------------------ model_delete
def test_model_delete_keeps_the_weights_unless_asked(adapter):
    _run(tools.handle_model_delete, {"model": "my-model"})
    _run(tools.handle_model_delete, {"model": "my-model", "weights": True})
    assert [(m, p) for m, p, _, _ in adapter] == [("DELETE", "/api/models/my-model"),
                                                  ("DELETE", "/api/models/my-model?weights=1")]


@pytest.mark.parametrize("params", [{}, {"model": ""}, {"model": "a/b"}, {"model": "../x"}, {"model": "m", "weights": "yes"}])
def test_model_delete_refuses_a_bad_request_and_sends_nothing(adapter, params):
    out = _run(tools.handle_model_delete, params)
    assert out["ok"] is False and out["error"]
    assert adapter == []


def test_model_delete_reports_a_manager_refusal_and_an_unreachable_adapter(adapter, monkeypatch):
    adapter.reply.update(status=409, json={"ok": False, "error": "m is in the aux group (embeddings, rerank) and stays."})
    out = _run(tools.handle_model_delete, {"model": "m"})
    assert out["http"] == 409 and "aux group" in out["error"]

    def down(timeout=30.0):
        return httpx.Client(base_url="http://127.0.0.1:9", timeout=0.2)
    monkeypatch.setattr(tools, "_adapter_client", down)
    out = _run(tools.handle_model_delete, {"model": "m"})
    assert out["ok"] is False and "ConnectError" in out["error"]


def test_the_tools_are_off_without_the_adapters_key(monkeypatch):
    monkeypatch.delenv("AURA_ADAPTER_KEY", raising=False)
    monkeypatch.delenv("JARVIS_ADAPTER_KEY", raising=False)
    out = _run(tools.handle_model_delete, {"model": "m"})
    assert out["ok"] is False and "AURA_ADAPTER_KEY" in out["error"]


# T-1350: the adapter's address goes through the same check as the other service addresses, since
# the adapter's key is sent to it. No fake client here: the real one is built, and a send is counted.
@pytest.fixture
def sent(monkeypatch):
    seen: list[httpx.Request] = []

    def _send(self, request, **kwargs):
        seen.append(request)
        raise httpx.ConnectError("no network in this test", request=request)

    monkeypatch.setattr(httpx.Client, "send", _send)
    monkeypatch.setenv("AURA_ADAPTER_KEY", KEY)
    return seen


@pytest.mark.parametrize("bad", ["http://adapter.example.test:8643", "https://me:pw@adapter.example.test",
                                 "http://169.254.169.254", "ftp://10.0.0.9", "https://adapter.example.test?x=1"])
def test_a_bad_adapter_address_sends_nothing(monkeypatch, sent, bad):
    monkeypatch.setenv("AURA_ADAPTER_URL", bad)
    out = _run(tools.handle_model_delete, {"model": "m"})
    assert out["ok"] is False and "ADAPTER_URL" in out["error"]
    assert "pw" not in json.dumps(out) and "example.test" not in json.dumps(out)
    assert tools.model_settings_change("model_settings", {"model": "m", "settings": {"max_model_len": 1}}) \
        == {"key": "settings of m", "known": False}
    assert sent == []


def test_a_private_adapter_address_is_still_asked(monkeypatch, sent):
    monkeypatch.setenv("AURA_ADAPTER_URL", "http://10.0.0.9:8643/")
    out = _run(tools.handle_model_delete, {"model": "m"})
    assert out["ok"] is False and "ConnectError" in out["error"]  # the stub send fails, but it was attempted
    assert [(r.url.host, r.url.port, r.headers["authorization"]) for r in sent] == [("10.0.0.9", 8643, f"Bearer {KEY}")]


def test_the_default_adapter_address_is_the_loopback_one(monkeypatch, sent):
    monkeypatch.delenv("AURA_ADAPTER_URL", raising=False)
    monkeypatch.delenv("JARVIS_ADAPTER_URL", raising=False)
    _run(tools.handle_model_delete, {"model": "m"})
    assert [(r.url.host, r.url.port) for r in sent] == [("127.0.0.1", 8643)]


LISTED = {"models": [
    {"id": "my-model", "settings": {"engine": "vllm", "max_model_len": 4096, "keep_loaded": False,
                                    "kv_cache_dtype": None, "idle_unload_s": 300}},
    {"id": "other", "settings": {"max_model_len": 1}},
]}


@pytest.fixture
def ask(monkeypatch, adapter):
    """The matrix gate with this plugin's describers registered and no phone to sign (T-1329)."""
    from _shared import _approval_hook

    monkeypatch.setattr(_approval_hook, "phone_proof", lambda *a: None)
    monkeypatch.setattr(_approval_hook, "CHANGE_DESCRIBERS", {})
    plugin.register(plugin.FakeCtx())

    def call(args: dict) -> dict:
        return _approval_hook.matrix_gate()(tool_name="model_settings", args=args, session_id="s1")
    return call


def test_the_settings_card_shows_each_changed_setting_now_and_after(adapter, ask):
    from _shared import _messages

    adapter.reply["json"] = LISTED
    v = ask({"model": "my-model", "settings": {"max_model_len": 8192, "keep_loaded": True, "kv_cache_dtype": "fp8"}})
    assert v["action"] == "approve"
    assert _messages.parse_change(v["message"]) == {
        "key": "settings of my-model",
        "before": "keep_loaded off, kv_cache_dtype default, max_model_len 4096",
        "after": "keep_loaded on, kv_cache_dtype fp8, max_model_len 8192"}
    assert [(m, p) for m, p, *_ in adapter] == [("GET", "/api/models")]  # one read; nothing written


@pytest.mark.parametrize("reply", [
    {"status": 500, "json": {"error": "boom"}},
    {"status": 200, "json": {"models": []}},                  # the model is not listed
    {"status": 200, "json": {"models": [{"id": "my-model"}]}},  # no settings
    {"status": 200, "json": ["not", "a", "dict"]},
])
def test_an_adapter_that_cannot_say_shows_no_lines_and_the_change_is_still_asked(adapter, ask, reply):
    from _shared import _messages

    adapter.reply.update(reply)
    v = ask({"model": "my-model", "settings": {"max_model_len": 8192}})
    assert v["action"] == "approve" and _messages.parse_change(v["message"]) is None
    assert _messages.parse_card(v["message"]) is not None


def test_an_adapter_that_is_down_never_blocks_the_ask(monkeypatch, ask):
    from _shared import _messages

    def down(timeout=30.0):
        raise httpx.ConnectError("refused")
    monkeypatch.setattr(tools, "_adapter_client", down)
    v = ask({"model": "my-model", "settings": {"max_model_len": 8192}})
    assert v["action"] == "approve" and _messages.parse_change(v["message"]) is None


def test_the_read_has_a_short_limit_and_a_value_is_clipped_to_a_line(monkeypatch, adapter):
    limits = []
    real = tools._adapter_client
    monkeypatch.setattr(tools, "_adapter_client", lambda timeout=30.0: limits.append(timeout) or real(timeout))
    adapter.reply["json"] = {"models": [{"id": "m", "settings": {"quantization": "x\nUndoable: no\n" + "y" * 200}}]}
    change = tools.model_settings_change("model_settings", {"model": "m", "settings": {"quantization": "awq"}}, "s1")
    assert limits == [3.0] and "\n" not in change["before"] and len(change["before"]) <= len("quantization ") + 40


@pytest.mark.parametrize("args", [
    {"model": "../x", "settings": {"max_model_len": 1}},
    {"model": "m", "settings": {"api_key": "hunter2"}},   # not a launch option: nothing is read or shown
    {"model": "m", "settings": {}},
    {"model": "m", "settings": "max_model_len"},
    {"settings": {"max_model_len": 1}},
])
def test_a_bad_request_has_no_describer_lines_and_reads_nothing(adapter, args):
    assert tools.model_settings_change("model_settings", args, "s1") is None
    assert tools.model_settings_change("model_delete", {"model": "m"}, "s1") is None
    assert list(adapter) == []


def test_the_four_tools_are_registered_beside_the_swap_tools():
    ctx = plugin.FakeCtx()
    plugin.register(ctx)
    for name in ("model_download", "model_add", "model_settings", "model_delete"):
        assert ctx.tools[name]["schema"]["name"] == name and ctx.tools[name]["toolset"] == "llama_swap"
