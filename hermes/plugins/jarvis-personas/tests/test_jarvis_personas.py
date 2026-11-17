"""The persona switch, without a Hermes runtime.

The emitter is injected, the state file lives under a tmp HERMES_HOME, and the
contract truth table (tests/contracts/persona.json) is run against the reader
the plugin uses — the adapter runs the same table against its own.
"""
from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest

PLUGIN_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = PLUGIN_DIR.parents[2]
CONTRACT = REPO_ROOT / "tests" / "contracts" / "persona.json"
sys.path.insert(0, str(PLUGIN_DIR.parent))
from _plugin_loader import load_plugin  # noqa: E402

plugin = load_plugin("jarvis-personas")
personas = plugin.personas


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    personas._rendered.clear()
    return tmp_path


class Recorder:
    def __init__(self):
        self.events: list[tuple[str, dict]] = []

    def __call__(self, event: str, payload: dict) -> int:
        self.events.append((event, payload))
        return 2


def test_the_default_is_aura_and_nothing_is_written_until_a_switch(home):
    assert personas.active() == "aura"
    assert personas.status()["pool"] == ["jarvis"]
    assert not (home / "state" / "persona.json").exists()


def test_a_switch_writes_the_record_tells_every_screen_and_is_reversible(home):
    rec = Recorder()
    out = personas.switch("Jarvis", emitter=rec)
    assert out["active"] == "jarvis" and out["display"] == "JARVIS" and out["voice"] == "jarvis"
    assert out["previous"] == "aura" and out["changed"] and out["delivered"] == 2
    assert out["pool"] == ["aura"], "the old primary joins the pool, the new one leaves it"
    data = json.loads((home / "state" / "persona.json").read_text())
    assert data["schema"] == "jarvis.persona.v1" and data["active"] == "jarvis" and data["previous"] == "aura"
    assert out["theme"] == "jarvis", "each persona wears her own pack"
    assert data["since"].endswith("Z") and len(data["since"]) == 20
    event, payload = rec.events[0]
    assert event == "persona.switch"
    contract = json.loads(CONTRACT.read_text())
    assert set(contract["event"]["fields"]) <= set(payload), "the event carries what the contract promises"
    # Back again.
    back = personas.switch("aura", emitter=rec)
    assert back["active"] == "aura" and back["previous"] == "jarvis" and back["theme"] == "aura"
    assert personas.active() == "aura"


def test_the_same_persona_again_changes_nothing_and_tells_nobody(home):
    rec = Recorder()
    personas.switch("jarvis", emitter=rec)
    again = personas.switch(" JARVIS ", emitter=rec)
    assert not again["changed"] and again["delivered"] == 0 and len(rec.events) == 1


def test_a_name_that_is_not_a_persona_is_refused_not_guessed(home):
    with pytest.raises(ValueError):
        personas.switch("Jarvises", emitter=Recorder())
    # The three the owner removed are names like any other now (T-0936).
    for gone in ("Ultron", "Ada", "Friday"):  # T-0936
        with pytest.raises(ValueError):
            personas.switch(gone, emitter=Recorder())
    assert personas.active() == "aura"
    assert json.loads(personas.HANDLERS["persona_switch"](name="hal"))["ok"] is False


def test_the_contract_truth_table(home):
    contract = json.loads(CONTRACT.read_text())
    assert contract["personas"] == list(personas.PERSONAS)
    path = home / contract["path"]
    for case in contract["cases"]:
        if path.exists():
            path.unlink()
        if case["file"] is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(case["file"])
        assert personas.active() == case["active"], case["name"]


def test_every_persona_is_her_own_prompt_section_and_the_only_identity_in_the_prompt(home):
    # AURA is the default, so a fresh house already speaks with her soul. SOUL.md once opened
    # "You are JARVIS ... Address the user as Sir" in every prompt, under AURA's section too, and
    # asked who sent a message she said JARVIS (the owner, 2026-11-10, T-0944).
    text = personas.render_section({"session_id": "s0"})
    assert text.startswith("# AURA") and "You are AURA" in text
    assert 'wake word is "aura"' in text
    assert "history" in text, "older notes that call the assistant JARVIS are named as such"
    personas.switch("jarvis", emitter=Recorder())
    jarvis = personas.render_section({"session_id": "s1"})
    assert jarvis.startswith("# JARVIS") and "You are JARVIS" in jarvis
    # The operator's own soul wins over the shipped one.
    personas.switch("aura", emitter=Recorder())
    (home / "personas").mkdir()
    (home / "personas" / "aura.md").write_text("# AURA\n\nMine.\n")
    assert personas.render_section({"session_id": "s3"}).startswith("# AURA\n\nMine.")


def test_the_house_rules_and_the_seeded_user_name_no_persona(home):
    import re
    for seed in ("SOUL.md", "USER.md"):
        text = (REPO_ROOT / "hermes" / "home" / seed).read_text()
        assert not re.search(r"You are (JARVIS|AURA)|who JARVIS works for|talks to JARVIS", text), seed
    assert not re.search(r"\bSir\b", (REPO_ROOT / "hermes" / "home" / "SOUL.md").read_text()), (
        "AURA never says Sir; it is JARVIS's manner, in his soul"
    )


def test_a_session_rendered_under_another_persona_is_told_once_on_its_next_turn(home):
    # s1's prompt was rendered under AURA, the default; then the switch.
    personas.render_section({"session_id": "s1"})
    personas.switch("jarvis", emitter=Recorder())
    first = personas.pre_llm_call(session_id="s1")
    assert first and "Persona in effect from this turn: JARVIS." in first["context"] and "# JARVIS" in first["context"]
    assert personas.pre_llm_call(session_id="s1") is None, "said once, not every turn"
    # A session rendered after the switch needs nothing.
    personas.render_section({"session_id": "s2"})
    assert personas.pre_llm_call(session_id="s2") is None
    # One from before this process (a restart) was rendered under who knows whom: told once.
    old = personas.pre_llm_call(session_id="never-seen")
    assert old and "# JARVIS" in old["context"]
    assert personas.pre_llm_call(session_id="never-seen") is None
    personas.switch("aura", emitter=Recorder())
    back = personas.pre_llm_call(session_id="s2")
    assert back and "Persona in effect from this turn: AURA." in back["context"] and "# AURA" in back["context"]
    assert "AURA" in personas.pre_llm_call(session_id="old")["context"]


def test_the_command_reads_and_switches(home):
    assert personas.handle_command("status").startswith("AURA is primary; pool: JARVIS")
    said = personas.handle_command("Jarvis")
    assert said.startswith("JARVIS is primary now (was AURA)")
    assert personas.handle_command("jarvis") == "JARVIS is already primary."
    assert "not a persona" in personas.handle_command("hal")


def test_register_wires_tools_command_section_and_hook():
    calls: dict[str, list] = {"tool": [], "command": [], "section": [], "hook": []}
    ctx = types.SimpleNamespace(
        register_tool=lambda **kw: calls["tool"].append(kw["name"]),
        register_command=lambda name, **kw: calls["command"].append(name),
        register_system_prompt_section=lambda id, content, **kw: calls["section"].append(id),
        register_hook=lambda name, _cb: calls["hook"].append(name),
    )
    plugin.register(ctx)
    assert sorted(calls["tool"]) == ["persona_status", "persona_switch", "theme_apply", "theme_draft"]
    assert calls["command"] == ["persona"] and calls["section"] == ["jarvis-persona"] and calls["hook"] == ["pre_llm_call"]


def test_a_drafted_theme_is_checked_previewed_and_worn_only_when_applied(home):
    rec = Recorder()
    sunrise = {"text.label": "#ffd2a8", "accent": "#ffb36b", "bg.void": "#120806", "bg.deep": "#1a0c08"}
    out = json.loads(personas.HANDLERS["theme_draft"](id="sunrise", name="Sunrise", base="aura", color=sunrise))
    assert out["ok"] and out["previewed"], out
    # The default broadcaster has no gateway here; the draft is on disk and nothing is worn yet.
    assert (home / "themes" / "drafts" / "sunrise.json").is_file()
    assert personas.status()["theme"] == "aura"
    applied = json.loads(personas.HANDLERS["theme_apply"](id="Sunrise"))
    assert applied["ok"] and applied["custom"], applied
    assert personas.status()["theme"] == "sunrise" and personas.active() == "aura"
    pack = json.loads((home / "themes" / "sunrise.json").read_text())
    assert pack["id"] == "sunrise" and pack["color"]["accent"] == "#ffb36b" and pack["voice"]["voiceId"] == "af_heart"
    # Back to a shipped pack by its id; and a persona switch wears her own pack.
    assert json.loads(personas.HANDLERS["theme_apply"](id="aura"))["custom"] is False
    assert personas.status()["theme"] == "aura"
    assert json.loads(personas.HANDLERS["theme_apply"](id="sunrise"))["ok"] is False, "a draft is used up when applied"
    personas.write_theme("sunrise")
    personas.switch("jarvis", emitter=rec)
    assert personas.status()["theme"] == "jarvis"


def test_a_draft_that_breaks_the_rules_is_refused_with_why(home):
    bad = json.loads(personas.HANDLERS["theme_draft"](id="dusk", name="Dusk", base="jarvis",
                                                       color={"text.label": "#202020", "glow": "red"}))
    assert not bad["ok"] and not bad["previewed"]
    assert any("'glow' is not a role" in p for p in bad["problems"]), bad
    low = json.loads(personas.HANDLERS["theme_draft"](id="dusk", name="Dusk", base="jarvis", color={"text.label": "#202020"}))
    assert not low["ok"] and any("contrast" in p for p in low["problems"])
    clash = json.loads(personas.HANDLERS["theme_draft"](id="jarvis", name="X", base="aura", color={}))
    assert not clash["ok"] and any("shipped" in p for p in clash["problems"])
    assert not (home / "themes" / "drafts").exists()
    assert json.loads(personas.HANDLERS["theme_apply"](id="nope"))["ok"] is False


def test_a_draft_changed_after_its_preview_is_not_worn(home):
    assert json.loads(personas.HANDLERS["theme_draft"](id="dusk", name="Dusk", base="aura", color={}))["ok"]
    path = home / "themes" / "drafts" / "dusk.json"
    pack = json.loads(path.read_text())
    pack["color"]["text.label"] = "#111111"
    path.write_text(json.dumps(pack))
    out = json.loads(personas.HANDLERS["theme_apply"](id="dusk"))
    assert out["ok"] is False and "no longer passes" in out["error"]
    assert personas.status()["theme"] == "aura"


def test_the_approval_names_the_theme_and_the_files_are_guarded():
    from _shared import _matrix, _messages
    assert "What: Wear the theme sunrise?" in _messages.card("theme_apply", {"id": "sunrise"}, "", "asked", home=False)
    assert _matrix.decide("write_file", {"path": "/opt/data/state/persona.json", "content": "{}"}).verdict == _matrix.DENY
    assert _matrix.decide("write_file", {"path": "/opt/data/themes/sunrise.json", "content": "{}"}).verdict == _matrix.ASK
