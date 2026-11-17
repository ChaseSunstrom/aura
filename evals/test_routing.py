"""The routing table test: every row of the policy, plus its mirror.

`routing.py` is the normative definition. What reaches the model at runtime is
the `guidance` string `get_user_context` returns; it drifts silently — nothing
crashes when a prompt loses a rule — so it is asserted here against the same
table. The second mirror, rule 4 of `aura-core/config/prompts/jarvis.txt`, was
retired with jarvis-core's agent loop, and its test with it: Hermes' persona
(`hermes/home/SOUL.md`, `hermes/skills/`) states no routing rule to check.
"""

from pathlib import Path

import pytest
import sys

from routing import CHANNELS, Ctx, decide

REPO = Path(__file__).resolve().parents[1]
CORE = REPO / "aura-core"

CASES = [
    # driving beats everything
    (Ctx(driving=True, kind="task_done", location="away"), "speak"),
    (Ctx(driving=True, channel_requested="text"), "speak"),
    (Ctx(driving=True, conversing=True), "speak"),
    # away + status/task_done → text
    (Ctx(location="away", kind="status"), "text"),
    (Ctx(location="away", kind="task_done"), "text"),
    (Ctx(location="away", kind="task_done", awake=False), "text"),
    # text in → text out
    (Ctx(channel_requested="text", kind="reply"), "text"),
    (Ctx(channel_requested="text", kind="reply", location="away"), "text"),
    # task done, no active conversation
    (Ctx(kind="task_done", conversing=False, location="home", awake=True),
     "announce_notify"),
    (Ctx(kind="task_done", conversing=False, location="home", awake=False),
     "notify_silent"),
    # live voice conversation → speak
    (Ctx(kind="reply", conversing=True, channel_requested="voice"), "speak"),
    (Ctx(kind="task_done", conversing=True, channel_requested="voice"), "speak"),
    # unsure / unsolicited → least intrusive
    (Ctx(kind="status", channel_requested="none"), "notify_silent"),
    (Ctx(kind="reply", conversing=False, channel_requested="none"),
     "notify_silent"),
]


@pytest.mark.parametrize("ctx,expected", CASES)
def test_routing_table(ctx, expected):
    assert decide(ctx) == expected


def test_all_outputs_are_known_channels():
    for ctx, expected in CASES:
        assert expected in CHANNELS


def test_the_tool_guidance_mirrors_the_table():
    """`get_user_context` is what the model consults mid-turn.

    It returns a one-line `guidance` string. If that loses a rule the table
    still passes and the running assistant still gets it wrong, so the string
    itself is asserted — in the same priority order as `decide`.
    """
    source = (CORE / "aura/tools.py").read_text()
    start = source.index('"guidance"')
    guidance = source[start : source.index("}", start)]

    for needle in ("driving: speak", "away: notify by text", "least intrusive"):
        assert needle in guidance, f"get_user_context lost routing rule: {needle}"
    # Priority order matters as much as the rules: driving beats away, and the
    # least-intrusive fallback is last.
    assert (
        guidance.index("driving: speak")
        < guidance.index("away: notify by text")
        < guidance.index("least intrusive")
    ), "the guidance no longer states the rules in priority order"


# ---------------------------------------------------------------------------
# "note that …" — which capability a spoken line is asking for
# ---------------------------------------------------------------------------
from routing import NOTE_INTENTS, note_intent  # noqa: E402


@pytest.mark.parametrize("said,expected,why", NOTE_INTENTS)
def test_a_spoken_line_asks_for_the_right_thing(said, expected, why):
    assert note_intent(said) == expected, why


def test_a_note_and_a_fact_about_the_user_are_not_the_same_thing():
    """The distinction the notes integration exists for: a document goes on
    disk and is found by searching; a fact about the user is one line and goes
    into every system prompt. Confusing them is how a four-page research report
    ended up in front of "turn the lights off"."""
    assert note_intent("note that the boiler was serviced today") == "note"
    assert note_intent("remember that I take my coffee black") == "memory"
    assert note_intent("remember to put the bins out") == "task"


def test_the_store_refuses_what_the_router_calls_a_note():
    """`MEMORY_REQUESTS` and `NOTE_REQUESTS` are the router's split, in the store.

    Every phrasing this table calls a note is a phrase `remember` refuses, and
    none of them is a memory request — otherwise "note that…" is an invitation
    to remember, which is the regression `notes-write-and-find` caught.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "aura-core"))
    from aura.integrations.memory import MEMORY_REQUESTS, NOTE_REQUESTS  # noqa: E402

    for said, expected, _why in NOTE_INTENTS:
        text = said.lower()
        if expected == "note":
            assert any(phrase in text for phrase in NOTE_REQUESTS), said
            assert not any(phrase in text for phrase in MEMORY_REQUESTS), said
        if expected == "memory":
            assert any(phrase in text for phrase in MEMORY_REQUESTS), said


#: v2 §1's three example phrases, as far as the behaviour exists (T-0607). "Use the JARVIS voice"
#: is the persona switch, never a settings write (checked below against the persona tool). "Lock
#: after 5 minutes" and "stop announcing backups" have no server-side key today: the lock timer is
#: browser-local in jarvis-web and nothing announces backups, so no row is faked for them (a
#: capability gap). What stays is the routing of a real house key's phrase.
SETTINGS_PHRASES = [
    ("use a different voice", "voice.tts_voice"),
    ("make the voice speak slower", "voice.tts.length_scale"),
    # T-1161: the car's own key is found by what the owner would say, and does not take the
    # house voice's phrase above.
    ("use the phone's voice in the car", "hud.car_voice"),
]
_HOUSE = [
    {"key": "voice.tts_voice", "label": "Voice", "type": "choice", "apply": "live", "note": "The voice the house speaks with.", "value": "amy", "security": False},
    {"key": "voice.tts.length_scale", "label": "Speaking pace", "type": "number", "apply": "live", "note": "Speaking rate for the voice: higher is slower.", "value": 1.0, "security": False},
]


def test_the_jarvis_voice_is_the_persona_switch_not_a_setting():
    sys.path.insert(0, str(REPO / "hermes" / "plugins"))
    from _plugin_loader import load_plugin  # noqa: E402

    schema = load_plugin("aura-personas").personas.SCHEMAS["persona_switch"]
    assert schema["parameters"]["properties"]["name"]["enum"] == ["AURA", "JARVIS"]
    assert "never hud_set_mode or a settings write" in schema["description"]


@pytest.mark.parametrize("phrase,key", SETTINGS_PHRASES)
def test_settings_find_routes_the_briefs_phrases(monkeypatch, phrase, key):
    sys.path.insert(0, str(REPO / "hermes" / "plugins"))
    from _plugin_loader import load_plugin  # noqa: E402

    settings = load_plugin("aura-settings").settings
    monkeypatch.setattr(settings, "_house_rows", lambda: _HOUSE)
    monkeypatch.setattr(settings, "STORE", settings.SettingsStore(
        load_resolved=lambda: {}, load_raw=lambda: {}, write_key=lambda k, v: None, environ={}, home="/nonexistent"))
    assert settings.find(phrase)[0]["key"] == key
