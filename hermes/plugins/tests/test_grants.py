"""A standing "always" runs out and can be taken back (T-0570): the token in every rule key."""
from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

import pytest

PLUGINS = Path(__file__).resolve().parents[1]
ROOT = PLUGINS.parents[1]
sys.path.insert(0, str(PLUGINS))
from _plugin_loader import load_plugin  # noqa: E402
from _shared import _grants, _matrix, _speaker  # noqa: E402
from _shared._approval_hook import approval_gate, approval_observer, matrix_gate  # noqa: E402

guard = load_plugin("aura-guard")
DAY = 86400.0
MAIL = {"to": ["someone@example.test"], "subject": "the private subject", "body": "the private body"}


@pytest.fixture(autouse=True)
def clock(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    now = [1_000_000.0]
    monkeypatch.setattr(_grants, "_clock", lambda: now[0])
    return now


def _ask(tool="mail_send", args=MAIL, session="s1"):
    card = matrix_gate()(tool_name=tool, args=args, session_id=session)
    assert card["action"] == "approve"
    return card["rule_key"]


def _answer(key: str, choice="always", decided_by="", tool="mail_send"):
    _requested, responded = approval_observer()
    responded(command=f"<{tool}> (plugin approval rule)", pattern_key=f"{_grants.PREFIX}{key}",
              session_key="s1", choice=choice, decided_by=decided_by)


def test_standing_grant_outlives_nothing_past_the_ttl(clock):
    key = _ask()
    _answer(key)
    clock[0] += 30 * DAY - 1
    assert _ask() == key, "inside the ttl Hermes' remembered key still matches"
    clock[0] += 1
    later = _ask()
    assert later != key and _grants.parse(later)[0] == _grants.parse(key)[0], "same rule, new token"
    assert _grants.live() == []


def test_standing_grant_without_an_answer_keeps_its_key_so_a_session_answer_holds(clock):
    key = _ask()
    clock[0] += 400 * DAY
    assert _ask() == key


def test_standing_grant_revoke_moves_every_scope_at_once():
    key = _ask()
    base, _tok, _ = _grants.parse(key)
    assert _grants.record(f"{_grants.PREFIX}{key}:at-home", "mail_send")
    assert _grants.record(f"{_grants.PREFIX}{key}:after-outside", "mail_send")
    [row] = _grants.live()
    assert row["base_key"] == base and row["scopes"] == ["after-outside", "at-home"]
    assert _grants.revoke(base) is True and _grants.live() == []
    assert _grants.parse(_ask())[1] != _grants.parse(key)[1]
    assert _grants.revoke(base) is False, "nothing left to revoke"


def test_standing_grant_answered_on_a_card_revoked_while_open_records_nothing():
    key = _ask()
    _answer(key)
    _grants.revoke(_grants.parse(key)[0])
    _answer(key)
    assert _grants.live() == []


@pytest.mark.parametrize("damage", ["lost", "corrupt"])
def test_standing_grant_lost_or_corrupt_file_asks_again(tmp_path, damage):
    key = _ask()
    _answer(key)
    path = tmp_path / _grants.STATE_REL
    if damage == "lost":
        path.unlink()
    else:
        path.write_text("{not json")
    assert _ask() != key and _grants.live() == []


def test_standing_grant_given_before_the_token_existed_asks_again():
    legacy = "mail:send"  # what the gate returned before T-0570
    assert _grants.parse(legacy) is None and _grants.parse(legacy + ":at-home") is None
    assert _grants.record(f"{_grants.PREFIX}{legacy}:at-home", "mail_send") is False
    assert not _ask().startswith(legacy + ":at-home")


@pytest.mark.parametrize("choice,decided_by", [("smart_approve", "aux_llm"), ("always", "aux_llm"),
                                               ("session", ""), ("once", ""), ("deny", "")])
def test_standing_grant_only_a_humans_always_starts_a_clock(choice, decided_by):
    _answer(_ask(), choice=choice, decided_by=decided_by)
    assert _grants.live() == []


def test_standing_grant_file_holds_no_arguments(tmp_path):
    _answer(_ask())
    text = (tmp_path / _grants.STATE_REL).read_text()
    assert "private" not in text and "someone@example.test" not in text
    assert set(json.loads(text)) == {"secret", "gen", "grants"}


def test_standing_grant_every_asking_gate_carries_the_token():
    assert _grants.parse(_ask()) is not None
    plan = {"why": "w", "steps": [{"tool": "ha_call_service", "args": {"entity_id": "light.a"}}]}
    assert _grants.parse(_ask("plan_approve", plan)) is not None
    plugin = approval_gate(lambda tool, args: {"action": "approve", "message": "m", "rule_key": "p:x"})
    got = plugin(tool_name="t", args={}, session_id="s1")["rule_key"]
    assert _grants.parse(got)[0] == "p:x"


def test_standing_grant_every_plugin_gate_goes_through_the_shared_wrapper():
    """A pre_tool_call registered bare hands Hermes a key with no token: its always never ends."""
    # These only ever return None or a block, never a card.
    observers = {"aura-activity": "activity.pre_tool_call", "aura-goals": "crew.pre_tool_call"}
    for init in sorted(PLUGINS.glob("*/__init__.py")):
        for call in ast.walk(ast.parse(init.read_text())):
            if isinstance(call, ast.Call) and getattr(call.func, "attr", "") == "register_hook" \
                    and call.args and getattr(call.args[0], "value", "") == "pre_tool_call":
                hook = ast.unparse(call.args[1])
                assert re.match(r"(approval_gate|matrix_gate)\(", hook) \
                    or observers.get(init.parent.name) == hook, f"{init.parent.name}: {hook}"


def _guard(arg: str) -> str:
    return guard._command(arg)


def test_standing_grant_guard_lists_and_revokes_for_the_owner():
    key = _ask()
    _answer(key)
    rows = json.loads(_guard("@s1 grants"))
    assert [r["tool"] for r in rows] == ["mail_send"] and rows[0]["expires_at"] > rows[0]["granted_at"]
    assert "revoked" in _guard(f"@s1 revoke {rows[0]['base_key']}")
    assert json.loads(_guard("@s1 grants")) == []
    assert "no standing" in _guard("@s1 revoke nothing:here").lower()


def test_standing_grant_guest_cannot_list_or_revoke(monkeypatch):
    _answer(_ask())
    monkeypatch.setattr(_speaker, "speaker_of", lambda _s: {"level": "guest", "label": "Ted"})
    assert "owner" in _guard("@s1 grants").lower()
    base = _grants.live()[0]["base_key"]
    assert "owner" in _guard(f"@s1 revoke {base}").lower()
    monkeypatch.setattr(_speaker, "speaker_of", lambda _s: {"level": "member", "label": "Mara"})
    assert "owner" in _guard(f"@s1 revoke {base}").lower()
    assert len(_grants.live()) == 1


def test_standing_grant_commands_need_a_named_session():
    """With no @session the speaker cannot be looked up, and "the key holder" reads as owner:
    a command relayed without its session listed and revoked for whoever was speaking (T-0858)."""
    _answer(_ask())
    base = _grants.live()[0]["base_key"]
    for arg in ("grants", f"revoke {base}", "  grants"):
        assert "session" in _guard(arg).lower()
    assert len(_grants.live()) == 1


def _revoke(rule, session="s1"):
    return guard.handle_guard_revoke({"rule": rule}, session_id=session)


def test_guard_revoke_removes_the_one_grant_it_names_and_the_rule_asks_again():
    """T-1315: the console's revoke, askable. One whole key, every scope of it, nothing else."""
    mail = _ask()
    _answer(mail)
    other = _ask("house_device_action", {"action": "lock", "device": "phone-1"})
    _answer(other, tool="house_device_action")
    bases = {g["base_key"] for g in _grants.live()}
    assert len(bases) == 2
    base = _grants.parse(mail)[0]
    assert "revoked" in _revoke(base).lower()
    assert {g["base_key"] for g in _grants.live()} == bases - {base}
    assert _ask() != mail, "the rule asks again under a new key"
    assert _ask("house_device_action", {"action": "lock", "device": "phone-1"}) == other, "the other grant stands"


def test_guard_revoke_names_a_whole_key_and_a_miss_changes_nothing():
    """Not a prefix, not a pattern, not a tool name: a miss lists what could be named and leaves
    the grant file as it was (no generation moved, so nothing is re-keyed either)."""
    key = _ask()
    _answer(key)
    base = _grants.parse(key)[0]
    before = _grants._path().read_text()
    for rule in (base[:-1], base + "x", "*", "mail_send", "mail_send*", ".*", "", "  ", None, 7, [base], {"k": base}):
        out = guard.handle_guard_revoke({"rule": rule}, session_id="s1")
        assert "revoked" not in out.lower(), rule
        assert base in out, "a refusal names what is live, so the person can say which"
        assert _grants._path().read_text() == before, rule
    assert guard.handle_guard_revoke({}, session_id="s1")
    assert _ask() == key


def test_guard_revoke_with_nothing_standing_says_so():
    out = _revoke("mail_send:anything")
    assert "no standing" in out.lower() and _grants.live() == []


def test_guard_revoke_never_adds_or_widens_and_leaves_the_kill_switch_and_the_lock_alone(monkeypatch):
    """A model that can take a grant back is fine; one that can give one is not. The tool's only
    write is _grants.revoke (a generation bump); it has no path to record(), and an unknown key
    does not create a row. The kill switch and the 'always' answers' lock are not its."""
    from _shared import _killswitch
    _answer(_ask())
    base = _grants.live()[0]["base_key"]
    calls = []
    monkeypatch.setattr(_grants, "record", lambda *a, **k: calls.append(("record", a)))
    monkeypatch.setattr(_killswitch, "engage", lambda *a, **k: calls.append(("engage", a)))
    monkeypatch.setattr(_killswitch, "release", lambda *a, **k: calls.append(("release", a)), raising=False)
    _revoke("mail_send:never-granted")
    _revoke(base)
    _revoke("mail_send:never-granted")
    assert calls == [] and _grants.live() == []
    assert not _killswitch.state_path().exists()
    src = (PLUGINS / "aura-guard" / "__init__.py").read_text()
    body = src[src.index("def handle_guard_revoke"):src.index("def register")]
    assert "_grants.record" not in body and "_grants.key_for" not in body and "engage" not in body


def test_guard_revoke_is_the_owners_alone_and_needs_a_session(monkeypatch):
    """The command's rule (T-0858) holds for the tool: no session reads as the key holder, an
    owner, so a missing one is refused; a guest or a member is refused too."""
    _answer(_ask())
    base = _grants.live()[0]["base_key"]
    assert "session" in guard.handle_guard_revoke({"rule": base}).lower()
    assert "session" in guard.handle_guard_revoke({"rule": base}, session_id="").lower()
    for level in ("guest", "member"):
        monkeypatch.setattr(_speaker, "speaker_of", lambda _s, level=level: {"level": level, "label": "x"})
        assert "owner" in _revoke(base).lower()
    assert len(_grants.live()) == 1


def test_guard_revoke_is_audited():
    from _shared import _audit
    _answer(_ask())
    base = _grants.live()[0]["base_key"]
    _revoke(base)
    rows = [r for r in _audit.read(kind=_audit.KIND_APPROVAL) if r.get("decision") == "revoke"]
    assert len(rows) == 1 and rows[0]["rule_key"] == base and rows[0]["actor"] == "model"
    _revoke(base)  # a miss is not a revoke
    assert len([r for r in _audit.read(kind=_audit.KIND_APPROVAL) if r.get("decision") == "revoke"]) == 1


def test_standing_grant_doc_states_the_schema_default():
    from aura_schema import Settings
    default = Settings.model_fields["approvals_always_ttl_days"].default
    doc = (ROOT / "docs" / "hermes-hud-protocol.md").read_text()
    assert f"`approvals.always_ttl_days` (default {default} days)" in doc


def test_standing_grant_file_cannot_be_written_by_the_model():
    """A model that could write the grant file could set a generation back (a revoked key
    Hermes remembered matches again) or move ``at`` forward (a grant that never lapses), so it
    is sealed like the kill switch: no prompt in front of it, by any write tool or the shell."""
    for path in ("/opt/data/state/approval-grants.json", "/opt/data/state/approval-grants.lock"):
        for tool in ("write_file", "patch"):
            assert _matrix.decide(tool, {"path": path}).verdict == _matrix.DENY, (tool, path)
    for command in ("echo '{}' > /opt/data/state/approval-grants.json",
                    "python3 -c 'open(\"state/approval-grants.json\", \"w\")'"):
        assert _matrix.decide("terminal", {"command": command}).verdict == _matrix.DENY, command



def test_standing_grant_on_a_rule_key_with_a_newline_still_runs_out(clock):
    """A base comes from the call's arguments, so the model can put a newline in it; the key
    must still parse, or the "always" Hermes remembered would have no clock at all."""
    base = "mail_send:to:a@example.test\nsubject:x"
    key = _grants.key_for(base)
    assert _grants.parse(key)[0] == base
    _answer(key)
    assert [g["base_key"] for g in _grants.live()] == [base]
    clock[0] += 30 * DAY
    assert _grants.key_for(base) != key


def test_standing_grant_that_fails_to_record_is_voided_not_left_standing(monkeypatch):
    key = _ask()

    def broken(*_a, **_k):
        raise OSError("disk full")
    monkeypatch.setattr(_grants, "record", broken)
    _answer(key)
    assert _ask() != key, "the key Hermes now holds has no clock, so it must never match"


def test_standing_grant_gate_asks_when_the_grant_file_cannot_be_used(monkeypatch):
    """A raising gate is one Hermes fails closed on for every call; an unusable grant file
    must cost only the remembering, so the call still asks, under a key that never recurs."""
    def broken():
        raise OSError("read-only file system")
    monkeypatch.setattr(_grants, "_state", broken)
    first, second = _ask(), _ask()
    assert first != second and _grants.parse(first)[0] == _grants.parse(second)[0]


def test_standing_grant_listing_retires_what_has_lapsed(clock, tmp_path):
    _answer(_ask())
    clock[0] += 30 * DAY
    assert _grants.live() == []
    data = json.loads((tmp_path / _grants.STATE_REL).read_text())
    assert data["grants"] == {} and set(data["gen"].values()) == {1}
