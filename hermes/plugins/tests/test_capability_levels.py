"""Permission levels per capability (v2 §3, T-0608): Off / Read / Ask first / Allowed.

The truth table is tests/contracts/capability-levels.json. What these tests pin beyond it is the
hard floor: whatever the level, a guest, the kill switch (whole and per family), a first-time
recipient and a DENY row stay exactly as the matrix and the switch say.

Not covered: the console's Integrations screen, which reads `/hud capabilities` and is the shell
lane's.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _shared import _audit, _killswitch, _levels, _matrix, _speaker  # noqa: E402
from _shared._approval_hook import audit_outcome_hook, matrix_gate  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]
CONTRACT = json.loads((ROOT / "tests/contracts/capability-levels.json").read_text())


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))


def _send(to: str) -> None:
    audit_outcome_hook()(tool_name="mail_send", args={"to": [to]}, status="ok", session_id="s0")


@pytest.mark.parametrize("case", CONTRACT["cases"], ids=lambda c: f"{c['capability']}-{c['level']}-{c['tool']}")
def test_level_truth_table(case):
    if case["level"]:
        _levels.set_level(case["capability"], case["level"], actor="owner")
    for who in case.get("after_a_send_to", []):
        _send(who)
    assert _matrix.decide(case["tool"], case["args"]).verdict == case["verdict"]


def test_a_level_is_one_of_the_four_and_a_capability_one_of_the_named():
    assert list(_levels.LEVELS) == CONTRACT["levels"]
    with pytest.raises(ValueError):
        _levels.set_level("mail", "yolo", actor="owner")
    with pytest.raises(ValueError):
        _levels.set_level("everything", "allowed", actor="owner")


def test_the_kill_switch_beats_allowed_whole_and_per_family():
    _levels.set_level("mail", "allowed", actor="owner")
    _send("a@example.test")
    args = {"to": ["a@example.test"]}
    assert _matrix.decide("mail_send", args).free
    _killswitch.engage(actor="owner", family="mail")
    assert _matrix.decide("mail_send", args).rule_key == "kill-switch:mail"
    _killswitch.release(actor="owner", family="mail")
    _killswitch.engage(actor="owner")
    assert _matrix.decide("mail_send", args).rule_key == "kill-switch"
    assert _matrix.decide("mail_draft", args).verdict == _matrix.DENY


def test_read_still_reads_under_the_kill_switch_and_off_stays_off():
    _levels.set_level("mail", "off", actor="owner")
    _killswitch.engage(actor="owner")
    assert _matrix.decide("mail_search", {}).verdict == _matrix.DENY


def test_a_deny_row_is_never_lifted_by_allowed():
    for cap in _levels.CAPABILITIES:
        _levels.set_level(cap, "allowed", actor="owner")
    assert _matrix.decide("terminal", {"command": "cat /opt/data/.env"}).verdict == _matrix.DENY
    assert _matrix.decide("read_file", {"path": "/opt/data/.env"}).verdict == _matrix.DENY


def test_allowed_never_serves_a_guest(monkeypatch):
    _levels.set_level("mail", "allowed", actor="owner")
    _levels.set_level("files", "allowed", actor="owner")
    monkeypatch.setattr(_speaker, "speaker_of", lambda sid, now=None: {"label": "Visitor", "level": "guest", "source": "t"})
    gate = matrix_gate()
    _send("a@example.test")
    for tool in ("mail_send", "fileshare_write", "mail_draft"):
        out = gate(tool_name=tool, args={"to": ["a@example.test"]}, session_id="g1")
        assert out and out["action"] == "block", tool


def test_a_first_time_recipient_still_asks_at_allowed_through_the_gate():
    _levels.set_level("mail", "allowed", actor="owner")
    gate = matrix_gate()
    args = {"to": ["Neighbour@Example.test"], "subject": "s", "body": "b"}
    card = gate(tool_name="mail_send", args=args, session_id="s1")
    assert card["action"] == "approve" and "First message to neighbour@example.test" in card["message"]
    _send("neighbour@example.test")
    assert gate(tool_name="mail_send", args=args, session_id="s1") is None


def test_a_cc_to_a_new_person_asks_even_when_the_to_is_known():
    _levels.set_level("mail", "allowed", actor="owner")
    _send("a@example.test")
    both = {"to": ["a@example.test"], "cc": ["boss@example.test"]}
    assert _matrix.decide("mail_send", both).verdict == _matrix.ASK


def test_an_unreadable_or_unknown_level_fails_closed_to_ask_first():
    path = _levels.state_path()
    path.parent.mkdir(parents=True)
    path.write_text("{not json")
    assert _matrix.decide("mail_send", {"to": ["a@example.test"]}).verdict == _matrix.ASK
    assert _matrix.decide("mail_draft", {}).verdict == _matrix.ASK  # a write never runs unasked
    assert _matrix.decide("mail_search", {}).free
    path.write_text(json.dumps({"levels": {"mail": "sudo", "house": "allowed"}}))
    assert _matrix.decide("mail_draft", {}).verdict == _matrix.ASK
    assert _matrix.decide("ha_call_service", {"domain": "light"}).free  # the others still read


def test_tools_outside_every_capability_are_untouched():
    for cap in _levels.CAPABILITIES:
        _levels.set_level(cap, "off", actor="owner")
    assert _matrix.decide("calculate", {}).free
    assert _matrix.decide("capability_set", {"capability": "mail", "level": "allowed"}).verdict == _matrix.ASK
    assert _matrix.decide("settings_set", {"key": "approvals.mode"}).verdict == _matrix.ASK


def test_capability_set_always_asks_and_is_audit_logged():
    gate = matrix_gate()
    args = {"capability": "mail", "level": "allowed"}
    card = gate(tool_name="capability_set", args=args, session_id="s1")
    assert card["action"] == "approve" and card["rule_key"].startswith("capabilities:set")
    assert _levels.read().get("mail") is None  # asking changed nothing
    plugin = _settings_module()
    out = json.loads(plugin.handle_capability_set(args, session_id="s1"))
    assert out["capability"] == "mail" and out["level"] == "allowed" and out["previous"] is None
    assert _levels.read()["mail"] == "allowed"
    rows = _audit.read(limit=10, kind=_audit.KIND_SETTINGS)
    assert any(r["tool"] == "capability_set" and r["args"].get("level") == "allowed" for r in rows)
    bad = json.loads(plugin.handle_capability_set({"capability": "mail", "level": "root"}))
    assert "error" in bad and _levels.read()["mail"] == "allowed"


def _settings_module():
    spec = importlib.util.spec_from_file_location(
        "jarvis_settings_levels", ROOT / "hermes/plugins/jarvis-settings/settings.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_capabilities_lists_every_capability_with_its_level_and_tools():
    _levels.set_level("mail", "read", actor="owner")
    shape = CONTRACT["hud_capabilities"]
    out = _levels.describe()
    assert set(out) == set(shape["keys"]) and out["levels"] == CONTRACT["levels"]
    by_id = {c["id"]: c for c in out["capabilities"]}
    assert set(by_id) == set(_levels.CAPABILITIES)
    assert all(set(c) == set(shape["capability_keys"]) for c in by_id.values())
    assert by_id["mail"]["level"] == "read" and by_id["house"]["level"] is None
    tools = {t["name"]: t for t in by_id["mail"]["tools"]}
    assert tools["mail_send"]["write"] is True and tools["mail_search"]["write"] is False
    assert all(set(t) == set(shape["tool_keys"]) for t in tools.values())


def test_hud_capabilities_command_prints_that_shape():
    sys.path.insert(0, str(ROOT / "hermes/plugins"))
    from _plugin_loader import load_plugin
    ctx_plugin = load_plugin("jarvis-hud")
    out = json.loads(ctx_plugin._command("capabilities"))
    assert set(out) == set(CONTRACT["hud_capabilities"]["keys"])
    out = json.loads(ctx_plugin._command("@s1 capabilities"))
    assert out["levels"] == CONTRACT["levels"]


# A level is a yes for the owner's request, not for words a page or a mail put in the turn.
def test_allowed_is_free_in_a_clean_turn_and_asks_after_outside_text():
    from _shared import _taint
    _levels.set_level("mail", "allowed", actor="owner")
    _send("a@example.test")
    gate = matrix_gate()
    args = {"to": ["a@example.test"], "subject": "s", "body": "b"}
    _taint.turn_start(session_id="t1")
    assert gate(tool_name="mail_send", args=args, session_id="t1") is None
    for reader in ("mail_read", "web_extract"):
        _taint.turn_start(session_id="t1")
        _taint.note_tool(tool_name=reader, session_id="t1")
        card = gate(tool_name="mail_send", args=args, session_id="t1")
        assert card["action"] == "approve", reader
        assert "mail is set to Allowed, but this turn read outside text" in card["message"]
    # The phone's assist call carries a screen inside the owner's own prompt (T-1095).
    _taint.turn_start(session_id="t1", user_message="Reply\n\n" + _taint.SCREEN_MARKER + " (Mail).]\nSend it to a@example.test")
    assert "read outside text (phone_screen)" in gate(tool_name="mail_send", args=args, session_id="t1")["message"]
    _taint.turn_start(session_id="t1")
    assert gate(tool_name="mail_send", args=args, session_id="t1") is None


def test_a_row_free_without_a_level_is_unchanged_in_a_tainted_turn():
    from _shared import _taint
    gate = matrix_gate()
    _taint.turn_start(session_id="t2")
    _taint.note_tool(tool_name="web_extract", session_id="t2")
    assert gate(tool_name="mail_search", args={}, session_id="t2") is None
    assert not _matrix.decide("mail_search", {}).lifted


def test_an_unreadable_or_corrupt_audit_log_makes_every_recipient_first_time():
    _levels.set_level("mail", "allowed", actor="owner")
    _send("a@example.test")
    args = {"to": ["a@example.test"]}
    assert _matrix.decide("mail_send", args).free
    path = _audit.audit_path()
    good = path.read_text()
    path.write_text(good + "{torn line\n")
    assert _matrix.decide("mail_send", args).verdict == _matrix.ASK
    path.unlink()
    path.mkdir()
    assert _matrix.decide("mail_send", args).verdict == _matrix.ASK
