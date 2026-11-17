"""The console reactor is the reactor: same states, same colours, same clock.

``reactor.py`` types the theme's glyphs and colours because the installed
package has no ``design/`` directory to read. This holds every typed value to
``design/generated/console-theme.json`` and the state machine to
``tests/contracts/reactor.json`` — the same truth table the web and the phone
run — so the terminal cannot show "thinking" for an approval the HUD shows as
"attention", or a cyan the tokens no longer contain.

Nothing here touches a terminal: frames are strings, capabilities are dicts.
"""

from __future__ import annotations

import io
import json
import os
from dataclasses import replace
from pathlib import Path

import pytest

from aura_desktop import reactor as r

REPO = Path(__file__).resolve().parents[2]
THEME = json.loads((REPO / "design/generated/console-theme.json").read_text(encoding="utf-8"))
CONTRACT = json.loads((REPO / "tests/contracts/reactor.json").read_text(encoding="utf-8"))

TRUECOLOR = r.Caps("truecolor", unicode=True, tty=True)
C256 = r.Caps("256", unicode=True, tty=True)
C16 = r.Caps("16", unicode=True, tty=True)
PLAIN = r.Caps("none", unicode=True, tty=True)
ASCII = r.Caps("truecolor", unicode=False, tty=True)

ANIMATED = [s for s in r.State if s not in (r.State.KILL, r.State.OFFLINE, r.State.GOAL)]


# --- the mirrors -------------------------------------------------------------


def test_the_ring_and_spinner_are_the_themes():
    assert list(r.RING) == THEME["reactor"]["ring"]
    assert list(r.SPINNER) == THEME["reactor"]["spinner"]
    assert [list(row) for row in r.CELLS] == CONTRACT["console"]["cells"]
    assert r.FPS == THEME["motion"]["fps"] == CONTRACT["console"]["fps"]


def test_every_state_glyph_colour_and_light_is_the_themes():
    states = THEME["reactor"]["states"]
    palette = THEME["palette"]
    assert {s.value for s in r.State} == set(states)
    for state in r.State:
        spec = states[state.value]
        glyph, ascii_, sgr = r.GLYPHS[state]
        assert (glyph, ascii_, list(sgr)) == (spec["glyph"], spec["ascii"], spec["sgr"]), state
        colour = palette[spec["color"]]
        assert r.COLOURS[state] == (colour["hex"], colour["xterm256"], colour["ansi16"]), state
        if "halo" in spec:
            # Only the halo survives: the rim and shadow are gone with the light layer.
            assert r.LIGHT[state] == spec["halo"], state
        else:
            assert state not in r.LIGHT, "goal has no light of its own: it overlays"
    housing = palette[THEME["reactor"]["housing"]]
    assert r.HOUSING == (housing["hex"], housing["xterm256"], housing["ansi16"])


def test_the_words_and_precedence_are_the_contracts():
    # The contract's precedence is the ten real states; the theme's lists the
    # goal overlay too, in the slot §10 gives it (before idle), and State's
    # members are declared in that fuller order.
    assert [s.value for s in r.PRECEDENCE] == CONTRACT["precedence"]
    assert [s.value for s in r.State] == THEME["reactor"]["precedence"]
    for name, spec in CONTRACT["states"].items():
        assert r.WORDS[r.State(name)] == spec["word"], name


def test_the_clock_is_the_themes():
    for name, ms in r.PERIOD_MS.items():
        assert THEME["motion"]["reactor_ms"][name] == ms, name
    for name, ms in r.DUR_MS.items():
        assert THEME["motion"]["durations_ms"][name] == ms, name
    assert r.ALERT_CYCLES == CONTRACT["states"]["alert"]["cycles"]
    assert list(r.BREATH) == CONTRACT["states"]["idle"]["luminance"]


def test_the_eased_curve_is_the_css_curve():
    """The theme carries the curve sampled at 16 points; the solver must land on them."""
    samples = THEME["motion"]["ease_standard"]
    for i, want in enumerate(samples):
        assert r.ease_standard(i / (len(samples) - 1)) == pytest.approx(want, abs=0.002), i
    assert r.ease_standard(-1) == 0.0 and r.ease_standard(2) == 1.0


# --- the state machine -------------------------------------------------------


@pytest.mark.parametrize("case", CONTRACT["derivation"]["cases"], ids=lambda c: c["name"])
def test_the_truth_table(case):
    inputs = r.Inputs(**{**CONTRACT["derivation"]["rest"], **case["inputs"]})
    got = r.derive(inputs)
    assert (got.state.value, got.goal) == (case["state"], case["goal"]), case["name"]


def test_rest_defaults_are_the_contracts():
    for name, value in CONTRACT["derivation"]["rest"].items():
        assert getattr(r.Inputs(), name) == value, name


# --- frames ------------------------------------------------------------------


def strip(rows: list[str]) -> list[str]:
    import re

    return [re.sub(r"\x1b\[[0-9;]*m", "", row) for row in rows]


@pytest.mark.parametrize("state", list(r.State))
def test_every_state_is_two_rows_of_three_cells(state):
    for caps in (TRUECOLOR, C256, C16, PLAIN):
        for t in (0.0, 0.5, 3.0):
            rows = r.frame(state, t, caps=caps, goal=True, level=0.5, envelope=0.7, progress=0.4)
            assert len(rows) == 2, (state, caps)
            assert all(len(row) == 3 for row in strip(rows)), (state, caps, strip(rows))


#: What moves each animated state (§10): the clock for the ones with a period,
#: the input for the ones that follow something — a level, a tool, a cold start.
#: Warming has "no other motion" by design, so a fixed progress is a fixed frame.
STIMULUS = {
    r.State.IDLE: ({"t": 0.0}, {"t": 2.5}),
    r.State.SPEAKING: ({"t": 0.0}, {"t": 0.3}),
    r.State.ATTENTION: ({"t": 0.0}, {"t": 0.5}),
    r.State.ALERT: ({"t": 0.0}, {"t": 0.3}),
    r.State.THINKING: ({"t": 0.0}, {"t": 1.5}),
    r.State.LISTENING: ({"level": 0.2}, {"level": 0.9}),
    r.State.TOOL: ({"segments": 1}, {"segments": 3}),
    r.State.WARMING: ({"progress": 0.1}, {"progress": 0.9}),
}


@pytest.mark.parametrize("state", ANIMATED)
def test_an_animated_state_moves(state):
    before, after = STIMULUS[state]
    a = r.frame(state, before.pop("t", 0.0), caps=TRUECOLOR, **before)
    b = r.frame(state, after.pop("t", 0.0), caps=TRUECOLOR, **after)
    assert a != b, state


def test_warming_holds_still_at_a_fixed_progress():
    """§10: luminance ramps with the cold start and nothing else moves."""
    assert r.frame(r.State.WARMING, 0.0, caps=TRUECOLOR, progress=0.5) == r.frame(
        r.State.WARMING, 5.0, caps=TRUECOLOR, progress=0.5
    )


def test_alert_pulses_three_times_then_holds_with_the_bar():
    rows = [r.frame(r.State.ALERT, t, caps=TRUECOLOR) for t in (0.0, 0.31, 1.0, 1.86, 2.5, 3.0)]
    assert rows[0] != rows[1], "the first cycle pulses"
    assert rows[4] == rows[5], "after three cycles it holds"
    assert r.GLYPHS[r.State.ALERT][0] in strip(rows[5])[1], "the bar sits in the held frame"
    assert r.GLYPHS[r.State.ALERT][0] not in "".join(strip(rows[0])), "no bar while pulsing"


@pytest.mark.parametrize("state", [r.State.KILL, r.State.OFFLINE])
def test_kill_and_offline_do_not_move(state):
    frames = {tuple(r.frame(state, t, caps=TRUECOLOR, goal=True)) for t in (0.0, 0.5, 4.0, 40.0)}
    assert len(frames) == 1, state
    assert r.GLYPHS[r.State.GOAL][0] not in "".join(strip(r.frame(state, 1.0, caps=TRUECOLOR, goal=True))), (
        "the goal satellite does not orbit a dead reactor"
    )


def test_kill_is_one_red_cell_and_offline_is_all_dim():
    kill = r.frame(r.State.KILL, 0.0, caps=TRUECOLOR)
    assert "".join(kill).count("38;2;224;52;75") == 1
    # BUILD_BRIEF §1: offline is a dim amber ring, never a grey that reads as idle.
    offline = "".join(r.frame(r.State.OFFLINE, 0.0, caps=C16))
    assert offline.count("\x1b[33;2m") == 6


@pytest.mark.parametrize("state", list(r.State))
def test_reduced_motion_is_the_static_glyph(state):
    rows = r.frame(state, 0.0, caps=TRUECOLOR, reduced=True)
    assert rows == r.frame(state, 7.3, caps=TRUECOLOR, reduced=True), "reduced never moves"
    assert r.GLYPHS[state][0] in strip(rows)[0]
    assert all(len(row) == 3 for row in strip(rows))


def test_the_static_glyphs_are_distinct_enough_to_learn():
    """§8.3: the states stay learnable without motion, so no two adjacent
    states in precedence share a glyph AND a colour."""
    seen = {}
    for state in r.State:
        key = (r.GLYPHS[state][0], r.COLOURS[state][0], r.GLYPHS[state][2])
        assert key not in seen, f"{state} looks exactly like {seen[key]} at rest"
        seen[key] = state


@pytest.mark.parametrize("state", list(r.State))
def test_the_escape_matches_the_terminal(state):
    hex_, x256, a16 = r.COLOURS[state]
    rgb = tuple(int(hex_[i : i + 2], 16) for i in (1, 3, 5))
    # The full-luminance frame: warming and alert hold at 1.0 late, idle peaks
    # at 0.8 — so test the colour on the one frame every state renders lit.
    tc = "".join(r.frame(state, 100.0, caps=TRUECOLOR, level=1.0, envelope=1.0, progress=1.0, reduced=True))
    assert f"38;2;{rgb[0]};{rgb[1]};{rgb[2]}" in tc, state
    assert f"38;5;{x256}" in "".join(r.frame(state, 0.0, caps=C256, reduced=True)), state
    code = str(90 + a16 - 8) if a16 >= 8 else str(30 + a16)
    assert f"\x1b[{code}" in "".join(r.frame(state, 0.0, caps=C16, reduced=True)), state
    assert "\x1b[" not in "".join(r.frame(state, 0.0, caps=PLAIN, level=0.5)), state


def test_truecolor_dims_toward_the_housing_not_grey():
    lo = r.frame(r.State.IDLE, 0.0, caps=TRUECOLOR)  # breath at 0.6
    hi = r.frame(r.State.IDLE, 2.5, caps=TRUECOLOR)  # breath at 0.8
    assert lo != hi
    assert "38;2;31;169;201" not in "".join(lo), "at 0.6 the colour is scaled, not the raw token"


def test_sixteen_colour_says_the_breath_with_dim():
    assert "\x1b[36;2m" in "".join(r.frame(r.State.IDLE, 0.0, caps=C16))  # 0.6 < DIM_BELOW
    assert "\x1b[36m" in "".join(r.frame(r.State.IDLE, 2.5, caps=C16))  # 0.8 ≥ DIM_BELOW


def test_listening_fills_from_the_bottom_and_never_goes_dark():
    quiet = strip(r.frame(r.State.LISTENING, 0.0, caps=PLAIN, level=0.0))
    loud = strip(r.frame(r.State.LISTENING, 0.0, caps=PLAIN, level=1.0))
    assert quiet == [" " * 3, " ◡ "], quiet
    assert loud == ["◜◠◝", "◟◡◞"]


def test_thinking_sweeps_two_lit_cells_round_the_ring():
    seen = []
    for t in (0.0, 1.5, 3.0, 4.5, 6.0, 7.5):
        rows = strip(r.frame(r.State.THINKING, t, caps=PLAIN))
        seen.append("".join(rows))
        assert sum(ch != " " for ch in "".join(rows)) == 2, rows
    assert len(set(seen)) == 6, "one turn per motion.reactor.think visits every cell"


def test_tool_lights_one_cell_per_running_segment():
    for n in (1, 3, 6):
        rows = strip(r.frame(r.State.TOOL, 0.0, caps=PLAIN, segments=n))
        assert sum(ch != " " for ch in "".join(rows)) == n, (n, rows)


def test_attention_has_the_gap_at_the_top():
    rows = strip(r.frame(r.State.ATTENTION, 0.0, caps=PLAIN))
    assert rows[0][1] == " " and rows[1] == "◟◡◞", rows


def test_the_goal_satellite_orbits_on_iris_b():
    a = "".join(strip(r.frame(r.State.IDLE, 0.0, caps=PLAIN, goal=True)))
    b = "".join(strip(r.frame(r.State.IDLE, 41.0 / 3, caps=PLAIN, goal=True)))
    dot = r.GLYPHS[r.State.GOAL][0]
    assert a.count(dot) == 1 and b.count(dot) == 1
    assert a.index(dot) != b.index(dot), "a third of an orbit moves the dot"


def test_warming_ramps_with_progress_and_falls_back_to_the_clock():
    cold = "".join(r.frame(r.State.WARMING, 0.0, caps=TRUECOLOR, progress=0.0))
    warm = "".join(r.frame(r.State.WARMING, 0.0, caps=TRUECOLOR, progress=1.0))
    assert cold != warm
    assert "38;2;255;158;44" in warm
    later = "".join(r.frame(r.State.WARMING, 90.0, caps=TRUECOLOR))
    assert later != "".join(r.frame(r.State.WARMING, 0.0, caps=TRUECOLOR)), "no figure: the clock ramps it"


# --- no unicode ----------------------------------------------------------------


@pytest.mark.parametrize("state", list(r.State))
def test_no_unicode_is_ascii_only(state):
    for reduced in (False, True):
        rows = r.frame(state, 0.4, caps=ASCII, reduced=reduced, goal=True)
        text = "".join(strip(rows))
        assert text.isascii(), (state, reduced, text)
        assert all(len(row) == 3 for row in strip(rows))


def test_no_unicode_spins_an_animated_state_and_stills_a_dead_one():
    spins = {strip(r.frame(r.State.THINKING, t, caps=ASCII))[0] for t in (0.0, 1 / 30, 2 / 30, 3 / 30)}
    assert len(spins) == 4
    assert strip(r.frame(r.State.OFFLINE, 0.0, caps=ASCII)) == [" . ", "   "]
    assert strip(r.frame(r.State.KILL, 5.0, caps=ASCII)) == [" X ", "   "]


# --- the badge and the light -------------------------------------------------


def test_the_badge_is_the_glyph_and_the_word():
    assert r.badge(r.State.ATTENTION, PLAIN) == "◡ Needs you"
    assert r.badge(r.State.ATTENTION, r.Caps("none", unicode=False)) == "n Needs you"
    assert r.badge(r.State.ATTENTION, TRUECOLOR).startswith("\x1b[38;2;255;181;71m◡\x1b[0m Needs you")


def test_the_light_is_the_reactors_own_core():
    idle = r.light(r.State.IDLE, goal=True)
    assert (idle.rgb, idle.halo, idle.neutral, idle.goal) == ((44, 143, 163), 0.1, False, True)
    assert r.light(r.State.SPEAKING, envelope=1.0).halo == pytest.approx(0.24)
    assert r.light(r.State.WARMING, progress=0.5).halo == pytest.approx(0.14)
    off = r.light(r.State.OFFLINE, goal=True)
    assert off.neutral and not off.goal and off.halo == 0.0


# --- capabilities ------------------------------------------------------------


class Tty(io.StringIO):
    def isatty(self):
        return True


@pytest.mark.parametrize(
    ("env", "want"),
    [
        ({"COLORTERM": "truecolor", "TERM": "xterm-256color", "LANG": "en_GB.UTF-8"}, ("truecolor", True)),
        ({"COLORTERM": "24bit", "TERM": "xterm", "LC_ALL": "C.utf8"}, ("truecolor", True)),
        ({"TERM": "xterm-256color", "LANG": "C"}, ("256", False)),
        ({"TERM": "xterm", "LANG": "en_US.UTF-8"}, ("16", True)),
        ({"TERM": "dumb", "LANG": "en_US.UTF-8"}, ("none", True)),
        ({}, ("none", False)),
        ({"COLORTERM": "truecolor", "NO_COLOR": "1", "LANG": "en_US.UTF-8"}, ("none", True)),
        ({"COLORTERM": "truecolor", "NO_COLOR": "", "LANG": "en_US.UTF-8"}, ("none", True)),
    ],
)
def test_detect_reads_the_environment(env, want):
    caps = r.detect(env, Tty())
    assert (caps.color, caps.unicode) == want and caps.tty


def test_detect_without_a_tty_is_plain():
    caps = r.detect({"COLORTERM": "truecolor", "LANG": "en_US.UTF-8"}, io.StringIO())
    assert caps == r.Caps("none", unicode=True, tty=False)
    assert r.detect({"COLORTERM": "truecolor"}, None).color in ("truecolor", "none")


# --- the animator ------------------------------------------------------------


def test_the_animator_ticks_and_pauses():
    a = r.Animator(caps=TRUECOLOR)
    a.tick()
    assert a.t == pytest.approx(1 / 30)
    a.paused = True
    a.tick()
    assert a.t == pytest.approx(1 / 30), "a paused clock does not move"
    a.paused = False
    a.set_state(r.State.LISTENING, goal=True)
    assert a.state is r.State.LISTENING and a.goal


def test_the_animator_crossfades_in_truecolor_and_switches_elsewhere():
    a = r.Animator(caps=TRUECOLOR)
    a.set_state(r.State.ATTENTION)
    start = "".join(a.frame_at(0.0))
    mid = "".join(a.frame_at(0.13))
    # Sampled on attention's hold (after its 620 ms rise), where the luminance
    # is 1.0 and the colour is the raw token — earlier, the fade being over is
    # hidden under the rise still scaling the colour.
    done = "".join(a.frame_at(1.0))
    assert "38;2;255;181;71" not in start, "the fade starts on the old colour"
    assert start != mid != done
    assert "38;2;255;181;71" in done, "after dur.base the new colour is exact"

    b = r.Animator(caps=C16)
    b.set_state(r.State.ATTENTION)
    # The brief's curve (.2,.8,.2,1) is past half by about 50 ms, so the old
    # colour is sampled just after the change.
    assert "".join(b.frame_at(0.01)) == "".join(r.frame(r.State.IDLE, 0.01, caps=C16))
    assert "".join(b.frame_at(0.2)) == "".join(r.frame(r.State.ATTENTION, 0.2, caps=C16))


def test_the_animator_honours_reduced_motion_through_a_change():
    a = r.Animator(caps=TRUECOLOR, reduced=True)
    a.set_state(r.State.ALERT)
    assert a.frame_at(0.1) == r.frame(r.State.ALERT, 0.0, caps=TRUECOLOR, reduced=True)


# --- the reactor on the status command ----------------------------------------


def test_status_opens_with_the_reactor_and_its_word():
    """`status` leads with the mark, not with a paragraph.

    The point of §10 is that the same instrument is on every surface; a terminal
    that described the state in prose while the HUD drew it would be the fourth
    vocabulary this rebuild exists to remove.
    """
    from aura_desktop import status as status_module

    text = status_module.render(None)
    rows = text.splitlines()
    assert rows[0].startswith("".join(r.RING[i] for i in r.CELLS[0])), rows[0]
    assert rows[0].endswith(r.WORDS[r.State.OFFLINE])
    assert rows[1] == "".join(r.RING[i] for i in r.CELLS[1])


def test_status_reads_only_what_a_heartbeat_file_can_tell_it():
    """No agent, a dead one and a stale one are all offline; a live one is idle.

    This surface sees a heartbeat, not the HUD's event stream, so it answers
    "is it there and is it well" and nothing finer. A status command that
    invented "thinking" out of a stale file would be worse than one that said
    "Ready".
    """
    from aura_desktop import status as status_module

    now = 1_000_000.0
    assert status_module.reactor_state(None, now) is r.State.OFFLINE

    live = status_module.StatusSnapshot(
        version="1", device_id="d", device_name="n", server_url="s",
        pid=os.getpid(), started_at=now - 60, updated_at=now, connected=True,
    )
    assert status_module.reactor_state(live, now) is r.State.IDLE

    # Connected but nothing has updated the file for a long time: the agent is
    # wedged or gone, and "Ready" would be a lie.
    stale = replace(live, updated_at=now - 3600)
    assert status_module.reactor_state(stale, now) is r.State.OFFLINE

    # Running and refusing everything: that is the alert state, not a fourth one.
    assert status_module.reactor_state(live, now, panic=True) is r.State.ALERT
    assert status_module.reactor_state(live, now, automation_enabled=False) is r.State.ALERT

    # Alive, but the link to the server is down.
    assert status_module.reactor_state(replace(live, connected=False), now) is r.State.OFFLINE
