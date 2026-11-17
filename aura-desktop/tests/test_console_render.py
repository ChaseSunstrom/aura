"""Every mode draws, on every tier, at exactly the terminal's width.

Frames are strings; nothing here opens a terminal. The invariants: a row is
``cols`` visible cells whatever the tier; the ``none`` tier carries no escape;
a non-UTF-8 locale gets no box glyph, no arc and no chrome mark; the reactor is
in the strip (or at the centre in Ambient) in the state the events put it in;
a highlight lights all four edges; and each of the four content states is a
sentence inside the panel's own border.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from aura_desktop import reactor
from aura_desktop.console import app, panes, render, state as S
from aura_desktop.console import theme as T

REPO = Path(__file__).resolve().parents[2]
LAYOUT = json.loads((REPO / "tests/contracts/hud-layout.json").read_text(encoding="utf-8"))
PANELS = json.loads((REPO / "tests/contracts/hud-panels.json").read_text(encoding="utf-8"))

TRUECOLOR = T.Caps("truecolor", True, True)
C256 = T.Caps("256", True, True)
C16 = T.Caps("16", True, True)
NONE = T.Caps("none", True, True)
ASCII = T.Caps("16", False, True)
TIERS = [TRUECOLOR, C256, C16, NONE, ASCII]
THEME = T.Theme()

BOX_GLYPHS = set("╔═║╗╚╝┌─│┐└┘▌") | set(reactor.RING)


def _text(rows) -> str:
    return "\n".join(render.rows_plain(rows))


@pytest.mark.parametrize("mode", panes.MODES)
@pytest.mark.parametrize("caps", TIERS, ids=lambda c: f"{c.color}-{'utf8' if c.unicode else 'ascii'}")
@pytest.mark.parametrize("size", [(80, 24), (120, 40), (60, 16), (30, 8)], ids=lambda s: f"{s[0]}x{s[1]}")
def test_every_mode_draws_on_every_tier_at_the_terminals_width(mode, caps, size):
    cols, rows = size
    fr = render.frame(app.demo_hud(mode, LAYOUT), cols, rows, THEME, caps, now=2.5)
    assert len(fr.rows) == rows
    assert render.width_ok(fr.rows, cols), [T.visible_width(r) for r in fr.rows]
    if caps.color == "none":
        assert all("\x1b" not in r for r in fr.rows)
    if not caps.unicode:
        chrome = _text(fr.rows)
        assert not (set(chrome) & BOX_GLYPHS), "no box glyph or arc reaches a non-UTF-8 terminal"
        assert "…" not in chrome and "—" not in chrome


def test_the_task_demo_shows_what_the_web_shows():
    hud = app.demo_hud("task", LAYOUT)
    text = _text(render.frame(hud, 100, 28, THEME, NONE, now=2.5).rows)
    for title in ("Chat", "Tool calls", "Goal", "Model"):
        # ADR-0031: the panel header is small capitals, so the border draws
        # the title upper-cased.
        # Sentence case, as the web draws it: one design system, one register.
        assert f" {title} " in text
    assert "you  Warm the study" in text
    assert "Done. The heater is on and set to 21°." in text
    assert "ha_call_service" in text and "done 412 ms" in text and "running" in text
    assert "turn 3 of 20" in text and "The study is still" in text
    assert "qwen3-30b" in text and "ready" in text and "tools 14" in text
    assert "GPUs" in text and "vram 93.6 %" in text, "content with no panel of its own shows in the chat"
    assert "Working" in text and "on a goal" in text
    assert "> _" in text, "the composer is the chat's last row"


def test_the_focus_demo_is_the_approval_with_the_rest_railed_and_highlighted():
    hud = app.demo_hud("focus", LAYOUT)
    fr = render.frame(hud, 100, 24, THEME, TRUECOLOR, now=2.5)
    text = _text(fr.rows)
    assert " Approvals " in text and " Panels " in text
    assert "ha_call_service" in text and "[o]nce  [s]ession  [a]lways  [d]eny" in text
    assert "Needs you" in text and "1 waiting on you" in text
    assert fr.arrangement.rail is not None and fr.arrangement.rail.pids == ("chat", "timeline")
    # The highlight (until t=3.6) lights all four edges in C, bold.
    rim = THEME.sgr(THEME.reactor_colour(reactor.State.ATTENTION), TRUECOLOR, attrs=THEME.focused_attrs)
    assert fr.rows[0].startswith(rim), "the top edge, which faces away from the reactor, is lit while highlighted"
    later = render.frame(hud, 100, 24, THEME, TRUECOLOR, now=9.0)
    far = THEME.sgr(THEME.data["borders"]["far"], TRUECOLOR)
    assert later.rows[0].startswith(far), "after the pulse the top edge is the shadow side again"


def test_the_floating_demo_tiles_every_panel():
    fr = render.frame(app.demo_hud("overview", LAYOUT), 100, 20, THEME, NONE, now=2.5)
    assert {p.pid for p in fr.arrangement.placed} == {"chat", "telemetry", "goals"}
    assert "overview" in _text(fr.rows)


def test_the_ambient_demo_centres_the_reactor_and_keeps_one_strip():
    fr = render.frame(app.demo_hud("ambient", LAYOUT), 100, 20, THEME, NONE, now=2.5)
    text = _text(fr.rows)
    assert fr.arrangement.placed == ()
    assert "Working" in text and "Warm the study" in text and "Done. The heater" in text
    assert "Tool calls 3" in text
    assert "╔" not in text, "no panel is drawn in Ambient; the strip is a pane"
    assert "┌" in text


def test_the_reactor_in_the_strip_is_the_reactors_own_frame():
    hud = app.demo_hud("task", LAYOUT)
    fr = render.frame(hud, 100, 28, THEME, TRUECOLOR, now=2.5)
    derived = hud.reactor()
    expected = reactor.frame(derived.state, 2.5, goal=derived.goal, segments=3, caps=TRUECOLOR)
    strip = fr.arrangement.strip
    for i, row in enumerate(expected):
        assert row in fr.rows[strip.y + i]
    assert derived.state is reactor.State.TOOL


def test_the_animator_drives_the_strip_when_given_one():
    hud = app.demo_hud("task", LAYOUT)
    animator = reactor.Animator(caps=TRUECOLOR)
    render.frame(hud, 100, 28, THEME, TRUECOLOR, animator=animator)
    assert animator.state is reactor.State.TOOL and animator.goal and animator.segments == 3


def test_facing_edges_take_the_reactors_colour_and_the_far_edges_mark():
    hud = app.demo_hud("task", LAYOUT)
    fr = render.frame(hud, 100, 28, THEME, TRUECOLOR, now=2.5, focused="chat")
    colour = THEME.reactor_colour(reactor.State.TOOL)
    lit = THEME.sgr(colour, TRUECOLOR, attrs=THEME.focused_attrs)
    unlit = THEME.sgr(colour, TRUECOLOR)
    far = THEME.sgr(THEME.data["borders"]["far"], TRUECOLOR)
    chat = fr.arrangement.rects()["chat"]
    timeline = fr.arrangement.rects()["timeline"]
    assert fr.rows[chat.bottom - 1].startswith(lit), "the focused chat's bottom faces the reactor: C, bold"
    assert fr.rows[chat.y].startswith(far), "its top faces away: Mark"
    row = fr.rows[timeline.y + 1]
    assert unlit + THEME.box("panel", TRUECOLOR).v in row, "the timeline's left edge faces the reactor: C"


@pytest.mark.parametrize("ptype", sorted(set(LAYOUT["panel_types"])))
def test_every_panel_type_has_an_empty_state_sentence(ptype):
    doc = {"mode": "task", "tree": ptype, "panels": [{"id": ptype, "type": ptype}]}
    hud = S.Hud(session_id="s1")
    hud.link(True)
    hud.layout = doc
    text = _text(render.frame(hud, 80, 12, THEME, NONE).rows)
    sentence = render.EMPTY.get(ptype, render.NOT_HERE)
    assert sentence.split(".")[0] in text.replace("\n", " ").replace("  ", " ") or sentence[:20] in text


def test_offline_error_and_loading_states_are_sentences_in_the_panel():
    doc = LAYOUT["presets"]["working"]["layout"]
    hud = S.Hud(session_id="s1")
    hud.layout = doc
    hud.link(False)
    text = _text(render.frame(hud, 100, 24, THEME, NONE).rows)
    assert text.count("Couldn't") == 4, "every panel says offline inside its own border"
    assert "Offline" in text
    hud.link(True)
    hud.begin_turn("x", 0)
    text = _text(render.frame(hud, 100, 24, THEME, NONE).rows)
    assert render.LOADING in text
    hud.apply({"type": "error", "session_id": "s1", "payload": {"message": "the model went away"}}, 1)
    text = _text(render.frame(hud, 100, 24, THEME, NONE).rows)
    assert "The turn failed: the model went away" in text and "Alert" in text


def test_a_layout_with_no_panels_says_how_to_get_one():
    hud = S.Hud(session_id="s1")
    hud.link(True)
    text = _text(render.frame(hud, 80, 10, THEME, NONE).rows)
    assert "Waiting for a layout from AURA" in text
    hud.layout = {"mode": "task", "tree": None, "panels": []}
    assert "No panels on this layout" in _text(render.frame(hud, 80, 10, THEME, NONE).rows)


@pytest.mark.parametrize("kind", PANELS["kinds"])
def test_every_content_kind_renders_its_example(kind):
    example = PANELS["examples"].get(kind)
    if example is None:
        pytest.skip(f"the contract carries no example for {kind}")
    home = S.panel_type_for(kind)
    hud = S.Hud(session_id="s1")
    hud.link(True)
    hud.layout = {"mode": "task", "tree": home, "panels": [{"id": home, "type": home}]}
    hud.apply({"type": "hud.panel.show", "session_id": "s1", "payload": {
        "id": "p", "kind": kind, "title": f"A {kind}", "payload": example, "origin": "agent",
        "created_at": "x", "rev": 1}}, 1)
    text = _text(render.frame(hud, 90, 16, THEME, NONE).rows)
    if home not in ("goals", "approvals"):
        assert f"A {kind}" in text
    assert render.EMPTY.get(home, render.NOT_HERE)[:15] not in text or home in ("goals", "approvals")


def test_the_composer_and_pinned_title_are_drawn():
    hud = app.demo_hud("task", LAYOUT)
    hud.layout["panels"][0]["pinned"] = True
    text = _text(render.frame(hud, 100, 28, THEME, NONE, composer="warm it", focused="chat").rows)
    assert "> warm it_" in text and "Chat (pinned)" in text


def test_a_panels_border_carries_the_one_value_it_says_about_itself():
    """The web's panel header puts the panel's own readout on its right (Reactor II
    wave 3, the mockup's `.panel h2 b`); on a terminal the top border is where that
    goes, and it must be the SAME value — the session, the turn budget, the model's
    state — or the two surfaces disagree about what a panel is saying.

    Terser than the web's ("3", not "3 calls"): a border is not a header row.
    """
    hud = S.Hud(session_id="sess-demo", model_state="ready")
    hud.goal = S.Goal(phase="continuing", turn=3, budget=20)
    hud.turn.tools = [S.Tool(id="a", name="terminal"), S.Tool(id="b", name="read_file")]
    assert render._readout("chat", hud) == "sess-demo"[-8:]
    assert render._readout("goals", hud) == "3/20"
    assert render._readout("model", hud) == "ready"
    assert render._readout("timeline", hud) == "2"
    # Nothing to say is said with nothing: a border full of empty values is noise.
    assert render._readout("approvals", hud) == ""
    assert render._readout("telemetry", hud) == ""
    quiet = S.Hud()
    assert render._readout("chat", quiet) == ""
    assert render._readout("goals", quiet) == ""
    assert render._readout("model", quiet) == ""


def test_the_prompt_and_the_ring_follow_the_primary():
    """The same switch changes the ring colour and the name in the prompt (V3 PART 2)."""
    hud = app.demo_hud("task", LAYOUT)
    text = _text(render.frame(hud, 100, 28, THEME, NONE, composer="hello", focused="chat").rows)
    assert "JARVIS> hello_" in text
    hud.persona = "aura"
    text = _text(render.frame(hud, 100, 28, THEME, NONE, composer="hello", focused="chat").rows)
    assert "AURA> hello_" in text and "JARVIS>" not in text
    # The ring wears AURA's hue at rest, and the state's own colour when the state speaks.
    assert render._ring_colour(THEME, reactor.State.IDLE, "aura") == "persona-aura"
    assert render._ring_colour(THEME, reactor.State.LISTENING, "aura") == THEME.reactor_colour(reactor.State.LISTENING)
    assert render._ring_colour(THEME, reactor.State.IDLE, "jarvis") == THEME.reactor_colour(reactor.State.IDLE)
    # The calm set is the contract's (the console and the phone read the same
    # table), expressed here as its complement.
    contract = json.loads((REPO / "tests/contracts/persona.json").read_text(encoding="utf-8"))
    calm = {s for s in reactor.State if s not in render._LOUD_STATES}
    assert sorted(s.value for s in calm) == sorted(contract["calm_states"])
    assert contract["untinted"] == "jarvis"
