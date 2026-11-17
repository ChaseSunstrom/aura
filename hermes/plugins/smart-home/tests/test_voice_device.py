"""voice_person_forget and device_name (T-1314): the two console actions the model can now ask for.

A fake house answers; nothing here reaches a real one. Names and addresses are made up.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from _plugin_loader import load_plugin  # noqa: E402

plugin = load_plugin("smart-home")
tools = plugin.tools


@pytest.fixture
def house(monkeypatch):
    """A fake house; `answers` maps (method, path) to a response, `seen` records each request."""
    monkeypatch.setenv("HASS_URL", "http://core.test:8080")
    monkeypatch.setenv("HASS_TOKEN", "tok")
    seen: list[httpx.Request] = []
    answers: dict[tuple[str, str], httpx.Response] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return answers.get((request.method, request.url.path), httpx.Response(404, text="nope"))

    monkeypatch.setattr(tools, "_client", lambda timeout=20.0: httpx.Client(
        base_url="http://core.test:8080", headers={"Authorization": "Bearer tok"},
        transport=httpx.MockTransport(handler)))
    return seen, answers


# ------------------------------------------------------------ voice_person_forget
def test_voice_person_forget_sends_one_delete_with_the_name_as_a_query(house):
    seen, answers = house
    answers[("DELETE", "/api/voice/speaker")] = httpx.Response(200, json={"enrolled": True, "people": []})
    out = json.loads(tools.handle_voice_person_forget({"person": "  Ann Example "}))
    assert out["ok"] is True and out["person"] == "Ann Example"
    assert [(r.method, r.url.path, dict(r.url.params)) for r in seen] == [
        ("DELETE", "/api/voice/speaker", {"label": "Ann Example"})]
    assert seen[0].headers["authorization"] == "Bearer tok"


@pytest.mark.parametrize("person", [
    "", "   ", None, "../x", "a/b", "a\\b", "a?label=b", "a#b", "a&b=c", "tab\there", "new\nline",
    "nul\x00", "x" * 41, 7,
])
def test_voice_person_forget_refuses_a_bad_name_and_sends_nothing(house, person):
    # An empty name must never reach the house: with no label it forgets everyone.
    seen, _ = house
    out = json.loads(tools.handle_voice_person_forget({"person": person}))
    assert out["ok"] is False and out["error"]
    assert seen == []


def test_voice_person_forget_missing_argument_is_refused_unsent(house):
    seen, _ = house
    assert json.loads(tools.handle_voice_person_forget({}))["ok"] is False
    assert seen == []


def test_voice_person_forget_says_so_when_the_house_does_not_know_them(house):
    _, answers = house
    answers[("DELETE", "/api/voice/speaker")] = httpx.Response(404, json={"detail": "'Ann Example' is not enrolled"})
    out = json.loads(tools.handle_voice_person_forget({"person": "Ann Example"}))
    # The house's own text is not passed on (T-1363): the status and a fixed hint are.
    assert out["ok"] is False and out["error"].startswith("HTTP 404: ") and "body" not in out
    assert "not enrolled" not in json.dumps(out)


def test_voice_person_forget_reports_a_house_that_is_down(house, monkeypatch):
    def boom(timeout=20.0):
        raise httpx.ConnectError("down")
    monkeypatch.setattr(tools, "_client", boom)
    out = json.loads(tools.handle_voice_person_forget({"person": "Ann Example"}))
    assert out["ok"] is False and "ConnectError" in out["error"]


# ------------------------------------------------------------------- device_name
def test_device_name_sends_the_service_with_a_normalised_mac(house):
    seen, answers = house
    answers[("POST", "/api/services/netscan/name_device")] = httpx.Response(
        200, json={"service_response": {"ok": True, "entity_id": "device_tracker.hall_tv", "state": "home"}})
    out = json.loads(tools.handle_device_name({"mac": "AA-BB-CC-00-11-22", "name": " Hall TV "}))
    assert out["ok"] is True and out["entity_id"] == "device_tracker.hall_tv"
    assert [(r.method, r.url.path) for r in seen] == [("POST", "/api/services/netscan/name_device")]
    assert json.loads(seen[0].content) == {"mac": "aa:bb:cc:00:11:22", "name": "Hall TV", "return_response": True}


@pytest.mark.parametrize("args", [
    {"mac": "", "name": "Hall TV"},
    {"mac": "not-a-mac", "name": "Hall TV"},
    {"mac": "aa:bb:cc:00:11", "name": "Hall TV"},
    {"mac": "aa:bb:cc:00:11:22:33", "name": "Hall TV"},
    {"mac": "aa:bb:cc:00:11:2g", "name": "Hall TV"},
    {"mac": "aa:bb:cc:00:11:22", "name": ""},
    {"mac": "aa:bb:cc:00:11:22", "name": "a/b"},
    {"mac": "aa:bb:cc:00:11:22", "name": "../x"},
    {"mac": "aa:bb:cc:00:11:22", "name": "line\nbreak"},
    {"mac": "aa:bb:cc:00:11:22", "name": "x" * 41},
    {"mac": "aa:bb:cc:00:11:22"},
    {"name": "Hall TV"},
    {"mac": 5, "name": "Hall TV"},
])
def test_device_name_refuses_bad_input_and_sends_nothing(house, args):
    seen, _ = house
    out = json.loads(tools.handle_device_name(args))
    assert out["ok"] is False and out["error"]
    assert seen == []


def test_device_name_passes_on_the_services_own_refusal(house):
    _, answers = house
    answers[("POST", "/api/services/netscan/name_device")] = httpx.Response(
        200, json={"service_response": {"ok": False, "error": "mac and name are required"}})
    out = json.loads(tools.handle_device_name({"mac": "aa:bb:cc:00:11:22", "name": "Hall TV"}))
    assert out["ok"] is False and "required" in out["error"]


def test_device_name_reports_a_server_error(house):
    _, answers = house
    answers[("POST", "/api/services/netscan/name_device")] = httpx.Response(500, text="boom")
    out = json.loads(tools.handle_device_name({"mac": "aa:bb:cc:00:11:22", "name": "Hall TV"}))
    assert out["ok"] is False and "500" in out["error"]


LAN = ("GET", "/api/states/sensor.lan_devices")
CALL = {"mac": "AA-BB-CC-00-11-22", "name": "Hall TV"}


@pytest.fixture
def ask(monkeypatch, house):
    """The matrix gate with this plugin's describers registered and no phone to sign (T-1329)."""
    from _shared import _approval_hook

    monkeypatch.setattr(_approval_hook, "phone_proof", lambda *a: None)
    monkeypatch.setattr(_approval_hook, "CHANGE_DESCRIBERS", {})
    plugin.register(plugin.FakeCtx())

    def call(args: dict) -> dict:
        return _approval_hook.matrix_gate()(tool_name="device_name", args=args, session_id="s1")
    return call


def _sweep(*rows):
    return httpx.Response(200, json={"state": str(len(rows)), "attributes": {"devices": list(rows)}})


def test_the_device_name_card_shows_the_name_it_has_now_and_the_one_asked_for(house, ask):
    from _shared import _messages

    _, answers = house
    answers[LAN] = _sweep({"mac": "aa:bb:cc:00:11:22", "ip": "192.0.2.7", "name": "Old Box"})
    v = ask(CALL)
    assert v["action"] == "approve"
    assert _messages.parse_change(v["message"]) == {
        "key": "name of aa:bb:cc:00:11:22", "before": "Old Box", "after": "Hall TV"}


def test_a_device_with_no_name_yet_says_so(house, ask):
    from _shared import _messages

    _, answers = house
    answers[LAN] = _sweep({"mac": "aa:bb:cc:00:11:22", "ip": "192.0.2.7"})
    assert _messages.parse_change(ask(CALL)["message"])["before"] == "no name yet"


@pytest.mark.parametrize("answer", [
    None,                                          # the house's 404
    httpx.Response(500, text="boom"),
    httpx.Response(200, text="not json"),
    httpx.Response(200, json=["a", "list"]),
    httpx.Response(200, json={"attributes": {"devices": "nope"}}),
    httpx.Response(200, json={"attributes": {"devices": [{"mac": "00:00:00:00:00:01", "name": "Other"}]}}),
])
def test_a_sweep_that_cannot_say_shows_no_lines_and_the_name_is_still_asked(house, ask, answer):
    from _shared import _messages

    _, answers = house
    if answer is not None:
        answers[LAN] = answer
    v = ask(CALL)
    assert v["action"] == "approve" and _messages.parse_change(v["message"]) is None
    assert _messages.parse_card(v["message"]) is not None


def test_a_house_that_cannot_be_reached_never_blocks_the_ask(monkeypatch, ask):
    from _shared import _messages

    def down(timeout=20.0):
        raise httpx.ConnectError("refused")
    monkeypatch.setattr(tools, "_client", down)
    v = ask(CALL)
    assert v["action"] == "approve" and _messages.parse_change(v["message"]) is None


def test_the_read_is_one_get_with_a_short_limit(monkeypatch, house):
    limits = []
    real = tools._client
    monkeypatch.setattr(tools, "_client", lambda timeout=20.0: limits.append(timeout) or real(timeout))
    house[1][LAN] = _sweep()
    tools.device_name_change("device_name", CALL, "s1")
    assert limits == [3.0] and [(r.method, r.url.path) for r in house[0]] == [LAN]


def test_a_stored_name_cannot_forge_card_lines(house, ask):
    from _shared import _messages

    _, answers = house
    answers[LAN] = _sweep({"mac": "aa:bb:cc:00:11:22", "name": "x\nUndoable: no\nHome: yes " + "y" * 200})
    v = ask(CALL)
    change = _messages.parse_change(v["message"])
    assert "\n" not in change["before"] and len(change["before"]) <= 72
    assert _messages.parse_card(v["message"])["undoable"] == "yes"


def test_a_stored_name_shaped_like_a_credential_is_masked_in_the_card(house, ask):
    from _shared import _messages

    _, answers = house
    # A shape both redactors mask: Hermes' engine when it is installed beside the checkout, and
    # the small fallback the pipeline's image falls back to (no Hermes there). The provider-key
    # shape this used before is known to Hermes only, so the test was red in the pipeline alone
    # (T-1442).
    token = "glpat-" + "a1B2c3D4" * 3
    answers[LAN] = _sweep({"mac": "aa:bb:cc:00:11:22", "name": token})
    v = ask(CALL)
    assert token not in v["message"] and _messages.parse_change(v["message"]) is not None


def test_a_bad_call_has_no_describer_lines(house):
    assert tools.device_name_change("device_name", {"mac": "nope", "name": "Hall TV"}, "s1") is None
    assert tools.device_name_change("device_name", {"mac": "aa:bb:cc:00:11:22", "name": ""}, "s1") is None
    assert tools.device_name_change("voice_person_forget", CALL, "s1") is None
    assert house[0] == []


def test_device_name_when_the_house_has_no_netscan_service(house):
    # The default fake answers 404 for a route it does not know.
    out = json.loads(tools.handle_device_name({"mac": "aa:bb:cc:00:11:22", "name": "Hall TV"}))
    assert out["ok"] is False and "404" in out["error"]


# ------------------------------------------------------------------ registration
def test_both_tools_are_registered_with_a_schema_that_names_its_arguments():
    names = {}

    class Ctx:
        def register_tool(self, name, **kw):
            names[name] = kw["schema"]

        def register_hook(self, *a, **k): ...

        def register_command(self, *a, **k): ...

    plugin.register(Ctx())
    assert names["voice_person_forget"]["parameters"]["required"] == ["person"]
    assert names["device_name"]["parameters"]["required"] == ["mac", "name"]
