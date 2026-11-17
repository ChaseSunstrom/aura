"""The console theme is the generated theme, painted per tier and never typed.

The packaged ``console_theme.json`` must be byte-for-byte the design tree's
``design/generated/console-theme.json`` (both are ``design/build.py`` outputs);
every palette colour paints as the tier says — the hex on truecolor, the
``xterm256`` slot on 256, the ``ansi16`` slot on 16, nothing at all on
``none`` — and the glyph ladder gives the double and single box sets to a
UTF-8 locale and the ASCII set to anything else.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from aura_desktop import reactor
from aura_desktop.console import theme as T

REPO = Path(__file__).resolve().parents[2]
PACKAGE = REPO / "aura-desktop/aura_desktop/console/console_theme.json"
DESIGN = REPO / "design/generated/console-theme.json"

TRUECOLOR = T.Caps("truecolor", unicode=True, tty=True)
C256 = T.Caps("256", unicode=True, tty=True)
C16 = T.Caps("16", unicode=True, tty=True)
NONE = T.Caps("none", unicode=True, tty=True)
ASCII = T.Caps("truecolor", unicode=False, tty=True)
TIERS = [TRUECOLOR, C256, C16, NONE]


@pytest.fixture(scope="module")
def theme() -> T.Theme:
    return T.Theme()


def test_the_packaged_theme_is_the_generated_theme():
    assert PACKAGE.read_bytes() == DESIGN.read_bytes()
    assert "@generated from design/tokens.json" in PACKAGE.read_text(encoding="utf-8")


def test_the_module_reads_the_packaged_file_not_the_design_tree(theme):
    assert theme.data == json.loads(PACKAGE.read_text(encoding="utf-8"))


def test_no_colour_is_typed_in_the_console():
    hexes = re.compile(r"#[0-9a-fA-F]{6}\b")
    for path in (REPO / "aura-desktop/aura_desktop/console").glob("*.py"):
        assert not hexes.search(path.read_text(encoding="utf-8")), path.name


@pytest.mark.parametrize("name", sorted(json.loads(DESIGN.read_text(encoding="utf-8"))["palette"]))
def test_every_palette_colour_paints_per_tier(theme, name):
    colour = theme.colour(name)
    r, g, b = colour.rgb
    assert colour.hex == f"#{r:02x}{g:02x}{b:02x}"
    assert theme.sgr(name, TRUECOLOR) == f"\x1b[38;2;{r};{g};{b}m"
    assert theme.sgr(name, C256) == f"\x1b[38;5;{colour.xterm256}m"
    slot = colour.ansi16
    assert theme.sgr(name, C16) == f"\x1b[{30 + slot if slot < 8 else 90 + slot - 8}m"
    assert theme.sgr(name, NONE) == ""
    assert theme.paint("x", name, NONE) == "x"
    assert theme.paint("x", name, TRUECOLOR).endswith("x" + T.RESET)


def test_background_and_attributes_take_their_own_codes(theme):
    assert theme.sgr("text", TRUECOLOR, bg="panel", attrs=("bold", "reverse")).startswith("\x1b[1;7;38;2;")
    assert ";48;2;" in theme.sgr("text", TRUECOLOR, bg="panel")
    assert theme.sgr("text", C256, bg="panel").endswith(f";48;5;{theme.colour('panel').xterm256}m")
    slot = theme.colour("bg").ansi16
    assert theme.sgr(None, C16, bg="bg") == f"\x1b[{40 + slot if slot < 8 else 100 + slot - 8}m"
    # No colour means no bold either: the tier is "plain text", not "plain colour".
    assert theme.sgr("text", NONE, attrs=("bold",)) == ""


def test_a_colour_no_token_defines_is_refused(theme):
    with pytest.raises(KeyError):
        theme.colour("hotpink")


def test_the_type_roles_are_colour_and_weight_only(theme):
    for role in ("display", "title", "body", "secondary", "label", "figure", "mono", "mono-faint", "control", "link"):
        colour, attrs = theme.role(role)
        assert colour in theme.palette
        assert set(attrs) <= set(T.ATTRS)
    # ADR-0031: a panel's title is Muted at the plain weight, like the web's
    # `.title` — a panel is named, not headlined.
    assert theme.role("title")[1] == ()
    assert theme.role("body")[1] == ()


@pytest.mark.parametrize("grade,expect", [("panel", "┌─│┐└┘"), ("pane", "┌─│┐└┘")])
def test_unicode_locales_get_the_box_sets(theme, grade, expect):
    box = theme.box(grade, TRUECOLOR)
    assert (box.tl, box.h, box.v, box.tr, box.bl, box.br) == tuple(expect)


@pytest.mark.parametrize("grade", ["panel", "pane"])
def test_other_locales_get_ascii_for_both_grades(theme, grade):
    box = theme.box(grade, ASCII)
    assert (box.tl, box.h, box.v, box.tr, box.bl, box.br) == ("+", "-", "|", "+", "+", "+")
    assert theme.divider(ASCII) == "-" and theme.live(ASCII) == "|"
    assert theme.divider(TRUECOLOR) == "─" and theme.live(TRUECOLOR) == "▌"


def test_reactor_colours_and_attributes_come_from_the_theme(theme):
    for state in reactor.State:
        name = theme.reactor_colour(state)
        assert name == f"reactor-{state.value}"
        assert name in theme.palette
    assert theme.state_attrs(reactor.State.WARMING) == ("dim",)
    assert theme.state_attrs(reactor.State.IDLE) == ()


def test_detection_is_the_reactors_ladder():
    """One detector: the theme re-exports the reactor's, so the tiers cannot disagree."""
    assert T.detect is reactor.detect
    fake = type("S", (), {"isatty": lambda self: True})()
    assert T.detect({"COLORTERM": "truecolor", "LANG": "C.UTF-8"}, fake).color == "truecolor"
    assert T.detect({"TERM": "xterm-256color", "LANG": "C.UTF-8"}, fake).color == "256"
    assert T.detect({"TERM": "xterm", "LANG": "C"}, fake) == T.Caps("16", False, True)
    assert T.detect({"TERM": "xterm-256color", "NO_COLOR": "1", "LANG": "C.UTF-8"}, fake).color == "none"
    assert T.detect({"TERM": "dumb"}, fake).color == "none"


def test_visible_width_ignores_escapes(theme):
    painted = theme.paint("abc", "accent", TRUECOLOR, attrs=("bold",)) + theme.paint("de", "text", C16)
    assert T.visible_width(painted) == 5
    assert T.visible_width("plain") == 5
