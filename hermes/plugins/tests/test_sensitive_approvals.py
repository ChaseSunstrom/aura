"""Sensitive approvals (T-0945): the rows whose yes needs the owner's fingerprint on the phone.

The matrix says which rows; the gate marks each such ask with a one-time key and puts it to the
adapter, which says yes only for the phone's signature over it
(``aura-adapter/aura_adapter/approval_proof.py``). What these tests pin on this side: the set
of rows, that the gate does what the adapter answered, and that with no answer to be had the ask
is Hermes' own, never remembered, never lifted by a capability level, never waved through by a plan.

Not covered: the signature itself (the adapter's tests) and the phone's prompt (Kotlin's).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _shared import _approval_hook, _levels, _matrix, _plan  # noqa: E402
from _shared._approval_hook import matrix_gate  # noqa: E402

MARK = re.compile(r":sensitive:[0-9a-f]{32}$")
#: Taken before the fixture below stands in for it.
REAL_PROOF = _approval_hook.phone_proof

#: Every call here needs the fingerprint. One per sensitive row.
SENSITIVE = [
    ("ha_call_service", {"domain": "lock", "service": "unlock", "entity_id": "lock.front"}),
    ("ha_call_service", {"domain": "alarm_control_panel", "service": "alarm_disarm"}),
    ("browser_vault_fill", {"handle": "login-1"}),
    ("browser_vault_unlock", {}),
    ("terminal", {"command": "rm -rf /opt/data/work/old"}),
    ("settings_set", {"key": "approvals.mode", "value": "off"}),
    ("capability_set", {"capability": "mail", "level": "allowed"}),
]

#: Asked about, but a tap is enough.
ROUTINE = [
    ("ha_call_service", {"domain": "light", "service": "turn_on", "entity_id": "light.desk"}),
    ("ha_call_service", {"domain": "cover", "service": "close_cover", "entity_id": "cover.blind"}),
    ("execute_code", {"code": "print(1)"}),
    ("n8n_trigger", {"workflow": "w"}),
]


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    # No phone has a key unless a test says so; and no test reaches a real adapter.
    monkeypatch.setattr(_approval_hook, "phone_proof", lambda *a: None)


UNLOCK = {"domain": "lock", "service": "unlock", "entity_id": "lock.front"}


def test_the_phones_signed_yes_lets_the_call_through_with_no_other_ask(monkeypatch):
    asked = []
    monkeypatch.setattr(_approval_hook, "phone_proof", lambda *a: asked.append(a) or "once")
    assert matrix_gate()(tool_name="ha_call_service", args=UNLOCK, session_id="s1") is None
    (session, tool, message, rule_key), = asked
    assert (session, tool) == ("s1", "ha_call_service") and MARK.search(rule_key) and "lock" in message


@pytest.mark.parametrize("answer,said", [("deny", "said no"), ("timeout", "fingerprint"), ("error", "could not be asked")])
def test_a_no_or_no_answer_from_the_phone_blocks_the_call(monkeypatch, answer, said):
    monkeypatch.setattr(_approval_hook, "phone_proof", lambda *a: answer)
    out = matrix_gate()(tool_name="ha_call_service", args=UNLOCK, session_id="s1")
    assert out["action"] == "block" and said in out["message"]


@pytest.mark.parametrize("args", [
    {"domain": "Lock", "service": "unlock"},
    {"domain": " lock ", "service": "unlock"},
    {"entity_id": "lock.front", "service": "unlock"},
    {"domain": "homeassistant", "service": "turn_off", "entity_id": ["light.desk", "lock.front"]},
    {"domain": "homeassistant", "service": "turn_off", "target": {"entity_id": "alarm_control_panel.house"}},
    {"domain": "homeassistant", "service": "toggle", "data": {"entity_id": "lock.back"}},
    {"service": "lock.unlock"},
])
def test_a_lock_is_a_lock_however_the_call_names_it(args):
    assert _matrix.sensitive("ha_call_service", args), args
    assert not _matrix.sensitive("ha_call_service", {"domain": "light", "service": "turn_on", "entity_id": "light.lock_lamp"})


def test_a_plugins_own_gate_stands_aside_for_a_sensitive_call(monkeypatch):
    """aura-settings asks about settings_set itself; that ask would be a tap."""
    own = _approval_hook.approval_gate(lambda tool, args: {"action": "approve", "message": "m", "rule_key": "settings:set"})
    assert own("settings_set", {"key": "approvals.mode", "value": "off"}, session_id="s1") is None
    assert own("ha_call_service", {"domain": "light"}, session_id="s1")["action"] == "approve"
    # A refusal is still a refusal.
    block = _approval_hook.approval_gate(lambda tool, args: {"action": "block", "message": "no"})
    assert block("settings_set", {"key": "x"}, session_id="s1")["action"] == "block"


def test_a_sensitive_check_that_cannot_say_is_treated_as_sensitive(monkeypatch):
    asked = []
    monkeypatch.setattr(_approval_hook, "phone_proof", lambda *a: asked.append(a) or "deny")
    monkeypatch.setattr(_matrix, "sensitive", lambda *a: 1 / 0)
    out = matrix_gate()(tool_name="ha_call_service", args={"domain": "light", "service": "turn_on"}, session_id="s1")
    assert asked and out["action"] == "block"


def test_a_routine_ask_never_goes_to_the_phone(monkeypatch):
    monkeypatch.setattr(_approval_hook, "phone_proof", lambda *a: pytest.fail("asked the phone"))
    matrix_gate()(tool_name="ha_call_service", args={"domain": "light", "service": "turn_on"}, session_id="s1")


def test_phone_proof_steps_down_only_when_no_phone_can_sign(monkeypatch):
    import io
    import json
    from urllib import error, request

    real = REAL_PROOF
    rule = "terminal:destructive:sensitive:" + "0" * 32
    for name in ("AURA_ADAPTER_KEY", "JARVIS_ADAPTER_KEY", "AURA_API_KEY", "JARVIS_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    assert real("s1", "terminal", "m", rule) is None  # no key here: this house has no desk
    import aura_schema
    monkeypatch.setattr(aura_schema, "from_env", lambda: 1 / 0)
    assert real("s1", "terminal", "m", rule) == "error"  # unreadable settings prove nothing

    monkeypatch.setattr(aura_schema, "from_env", lambda: type("S", (), {"adapter_url": "http://127.0.0.1:9", "adapter_key": "k"})())

    def answers(what):
        def urlopen(req, timeout):
            if isinstance(what, Exception):
                raise what
            return io.BytesIO(json.dumps(what).encode())
        monkeypatch.setattr(request, "urlopen", urlopen)

    answers({"choice": "unavailable"})
    assert real("s1", "terminal", "m", rule) is None
    answers(error.HTTPError("u", 404, "nf", None, None))
    assert real("s1", "terminal", "m", rule) is None
    # Every other fault refuses: a broken adapter must not turn the fingerprint into a tap.
    for fault in (OSError("down"), error.HTTPError("u", 500, "x", None, None), {"choice": "always"}, {}, []):
        answers(fault)
        assert real("s1", "terminal", "m", rule) == "error", fault
    answers({"choice": "once"})
    assert real("s1", "terminal", "m", rule) == "once"


def test_the_sensitive_rows_are_this_set():
    assert _matrix.SENSITIVE_GRAINS == frozenset({
        "terminal:destructive", "desktop:run:destructive", "settings:set", "capabilities:set",
        "vault:credential"})  # Hermes v0.23's one browser_vault tool, its credential actions (T-1519)
    assert _matrix.SENSITIVE_HOUSE_DOMAINS == ("lock", "alarm_control_panel")
    assert _matrix.SENSITIVE_TOOLS == frozenset({
        "browser_vault_fill", "browser_vault_save_login", "browser_vault_unlock", "browser_vault_enter_code",
        "emergency_contacts_set"})
    # Every grain named is a real ASK row: a typo here would mark nothing.
    asked = {rule.rule_key for rule in _matrix.RULES if rule.verdict == _matrix.ASK}
    assert _matrix.SENSITIVE_GRAINS <= asked


@pytest.mark.parametrize("tool,args", SENSITIVE, ids=lambda v: v if isinstance(v, str) else "")
def test_a_sensitive_call_is_marked_and_asked(tool, args):
    assert _matrix.decide(tool, args).verdict != _matrix.DENY
    assert _matrix.sensitive(tool, args)
    card = matrix_gate()(tool_name=tool, args=args, session_id="s1")
    assert card["action"] == "approve" and MARK.search(card["rule_key"]), card["rule_key"]


@pytest.mark.parametrize("tool,args", ROUTINE, ids=lambda v: v if isinstance(v, str) else "")
def test_a_routine_ask_is_not(tool, args):
    assert not _matrix.sensitive(tool, args)
    card = matrix_gate()(tool_name=tool, args=args, session_id="s1")
    assert card is None or ":sensitive:" not in card.get("rule_key", "")


def test_a_sensitive_ask_is_never_the_same_key_twice_so_no_always_covers_the_next():
    gate = matrix_gate()
    args = {"domain": "lock", "service": "unlock", "entity_id": "lock.front"}
    keys = {gate(tool_name="ha_call_service", args=args, session_id="s1")["rule_key"] for _ in range(3)}
    assert len(keys) == 3
    # ...and never a home grant: "always when I'm home" would be a remembered yes.
    assert not any(k.endswith(":at-home") for k in keys)


def test_a_capability_level_cannot_lift_a_sensitive_row():
    _levels.set_level("house", "allowed", actor="owner")
    gate = matrix_gate()
    assert gate(tool_name="ha_call_service", args={"domain": "light", "service": "turn_on"}, session_id="s1") is None
    card = gate(tool_name="ha_call_service", args={"domain": "lock", "service": "unlock"}, session_id="s1")
    assert card and card["action"] == "approve" and MARK.search(card["rule_key"])


def test_an_approved_plan_does_not_cover_a_sensitive_step(monkeypatch):
    monkeypatch.setattr(_plan, "consume", lambda *a, **k: "a plan that names this step")
    gate = matrix_gate()
    assert gate(tool_name="ha_call_service", args={"domain": "light", "service": "turn_on"}, session_id="s1") is None
    card = gate(tool_name="ha_call_service", args={"domain": "lock", "service": "unlock"}, session_id="s1")
    assert card and MARK.search(card["rule_key"])


@pytest.mark.parametrize("limit,wait", [(140, 120.0), (30, 20.0), (600, 120.0), (8, 5.0), (0, 120.0)])
def test_the_wait_for_the_phone_ends_before_hermes_abandons_the_gate(monkeypatch, limit, wait):
    """Past plugins.hook_callback_timeout Hermes blocks the call and skips the gate for a minute."""
    import types
    plugins = types.ModuleType("hermes_cli.plugins")
    plugins._resolve_hook_callback_timeout = lambda: limit
    monkeypatch.setitem(sys.modules, "hermes_cli", types.ModuleType("hermes_cli"))
    monkeypatch.setitem(sys.modules, "hermes_cli.plugins", plugins)
    assert _approval_hook.proof_wait() == wait


def test_a_shortened_wait_is_logged_once_with_the_limit_that_shortened_it(monkeypatch, caplog):
    """20 s lapses before the owner reaches the phone; the log has to say why (T-1030)."""
    monkeypatch.setitem(sys.modules, "hermes_cli.plugins", None)
    monkeypatch.setattr(_approval_hook, "_wait_logged", None)
    with caplog.at_level("WARNING"):
        assert _approval_hook.proof_wait() == _approval_hook.proof_wait() == 20.0
    said = [r.getMessage() for r in caplog.records if "fingerprint" in r.getMessage()]
    assert len(said) == 1 and "20" in said[0] and "30" in said[0] and "hook_callback_timeout" in said[0]


def test_with_no_hermes_to_ask_the_wait_fits_its_default_limit(monkeypatch):
    monkeypatch.setitem(sys.modules, "hermes_cli.plugins", None)
    assert _approval_hook.proof_wait() == _approval_hook.HOOK_LIMIT_S - _approval_hook.HOOK_MARGIN_S
