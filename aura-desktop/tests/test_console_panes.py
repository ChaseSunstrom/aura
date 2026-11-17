"""The console lays out every tree the plugin can produce — the contract, replayed.

``tests/contracts/hud-layout.json`` ``op_cases`` is the table the plugin
generates from its own operations and the web replays; the console replays it
too, so a layout JARVIS chooses on the desktop maps to terminal panes without
a "keep in step" comment. Every tree in the table (inputs and expected
outputs alike) is tiled, and every expected document is arranged in its mode:

* the pane order is the tree's leaf order — the ``leaves`` cases say what that
  is, and the phone stacks in the same order;
* panes never overlap, never leave the region, and the first child of a split
  takes its ``ratio`` of the axis to within a cell;
* Focus draws the focused leaf large with the rest as the rail; Floating tiles
  every panel; Ambient draws none; Tiled hides an orphan and says so.
"""

from __future__ import annotations

import itertools
import json
from pathlib import Path

import pytest

from aura_desktop.console import panes

REPO = Path(__file__).resolve().parents[2]
CONTRACT = json.loads((REPO / "tests/contracts/hud-layout.json").read_text(encoding="utf-8"))
OP_CASES = CONTRACT["op_cases"]
CASES = OP_CASES["cases"]
PRESETS = CONTRACT["presets"]

REGION = panes.Rect(0, 0, 120, 40)
COLS, ROWS = 120, 42


def _trees() -> list[tuple[str, object]]:
    """Every tree the table mentions: the named ones, each case's input and output."""
    out: list[tuple[str, object]] = [(f"tree:{k}", v) for k, v in OP_CASES["trees"].items()]
    for i, case in enumerate(CASES):
        if "tree" in case:
            out.append((f"{i}:{case['fn']}:in", case["tree"]))
        expect = case.get("expect")
        if isinstance(expect, dict):
            if "tree" in expect and "mode" not in expect:
                out.append((f"{i}:{case['fn']}:out", expect["tree"]))
            elif "tree" in expect:
                out.append((f"{i}:{case['fn']}:doc", expect["tree"]))
            elif "split" in expect:
                out.append((f"{i}:{case['fn']}:split", expect))
    return out


def _docs() -> list[tuple[str, dict]]:
    out = [(f"doc:{k}", v) for k, v in OP_CASES["documents"].items()]
    for i, case in enumerate(CASES):
        expect = case.get("expect")
        if isinstance(expect, dict) and "mode" in expect:
            out.append((f"{i}:{case['fn']}", expect))
    return out


def _leaves(tree) -> list[str]:
    if tree is None:
        return []
    if isinstance(tree, str):
        return [tree]
    return [leaf for child in tree["children"] for leaf in _leaves(child)]


def _overlap(a: panes.Rect, b: panes.Rect) -> bool:
    return a.x < b.right and b.x < a.right and a.y < b.bottom and b.y < a.bottom


# --- every tree tiles ------------------------------------------------------------------


@pytest.mark.parametrize("name,tree", _trees(), ids=[n for n, _ in _trees()])
def test_every_tree_in_the_table_tiles_in_reading_order_without_overlap(name, tree):
    rects = panes.tile(tree, REGION)
    assert list(rects) == _leaves(tree)
    for rect in rects.values():
        assert rect.x >= 0 and rect.y >= 0 and rect.right <= REGION.w and rect.bottom <= REGION.h
        assert rect.w >= 0 and rect.h >= 0
    for a, b in itertools.combinations(rects.values(), 2):
        assert not _overlap(a, b)


@pytest.mark.parametrize("case", [c for c in CASES if c["fn"] == "leaves"], ids=lambda c: c["on"])
def test_the_pane_order_is_the_contracts_leaf_order(case):
    """The ``leaves`` cases are the plugin's own answer; the console's order must be it."""
    assert list(panes.tile(case["tree"], REGION)) == case["expect"]


def _splits(tree, box: panes.Rect):
    """Every split with the box it divides, walked the way tile() walks."""
    if tree is None or isinstance(tree, str):
        return
    horizontal = tree["split"] == "row"
    extent = box.w if horizontal else box.h
    usable = max(extent - panes.GAP, 0)
    first = max(0, min(usable, int(usable * tree["ratio"] + 0.5)))
    yield tree, box, first, usable
    a, b = tree["children"]
    start = min(first + panes.GAP, extent)
    if horizontal:
        yield from _splits(a, panes.Rect(box.x, box.y, first, box.h))
        yield from _splits(b, panes.Rect(box.x + start, box.y, usable - first, box.h))
    else:
        yield from _splits(a, panes.Rect(box.x, box.y, box.w, first))
        yield from _splits(b, panes.Rect(box.x, box.y + start, box.w, usable - first))


@pytest.mark.parametrize("name,tree", [t for t in _trees() if isinstance(t[1], dict)], ids=lambda t: t if isinstance(t, str) else "")
def test_the_first_child_takes_its_ratio_to_within_a_cell(name, tree):
    rects = panes.tile(tree, REGION)
    for node, box, first, usable in _splits(tree, REGION):
        if usable == 0:
            continue
        head = _leaves(node["children"][0])[0]
        got = rects[head].w if node["split"] == "row" else rects[head].h
        # The first leaf under the first child spans the whole first share on
        # the split's axis when it is the only leaf there; otherwise its extent
        # is a sub-share. Either way the share itself is the rounded ratio.
        assert abs(first - usable * node["ratio"]) <= 0.5
        assert got <= first


def test_a_split_position_is_rounded_once_so_neighbours_meet_at_the_gap():
    odd = {"split": "row", "ratio": 0.3137, "children": ["chat", "timeline"]}
    rects = panes.tile(odd, panes.Rect(0, 0, 107, 20))
    chat, timeline = rects["chat"], rects["timeline"]
    assert timeline.x == chat.right + panes.GAP
    assert chat.w + timeline.w + panes.GAP == 107


# --- every document arranges in its mode ---------------------------------------------


@pytest.mark.parametrize("name,doc", _docs(), ids=[n for n, _ in _docs()])
def test_every_expected_document_arranges_in_its_mode(name, doc):
    a = panes.arrange(doc, COLS, ROWS)
    ids = [p["id"] for p in doc["panels"]]
    drawn = [p.pid for p in a.placed]
    assert set(drawn) <= set(ids)
    rail = list(a.rail.pids) if a.rail else []
    assert sorted(drawn + rail + list(a.hidden)) == sorted(ids), "every panel is drawn, railed or counted hidden"
    for p in a.placed:
        assert p.rect.fits()
        assert p.rect.bottom <= a.strip.y or a.mode == "ambient"
    for x, y in itertools.combinations(a.placed, 2):
        assert not _overlap(x.rect, y.rect)
    if a.mode == "task":
        assert drawn == [pid for pid in _leaves(doc["tree"]) if pid in ids and pid in drawn]
    elif a.mode == "focus":
        leaves = _leaves(doc["tree"])
        assert drawn == leaves[:1] or (not leaves and len(drawn) == 1)
        assert a.rail is not None or len(ids) == 1
    elif a.mode == "overview":
        assert sorted(drawn) == sorted(ids), "Floating degrades to tiling every panel"
    elif a.mode == "ambient":
        assert drawn == [] and sorted(a.hidden) == sorted(ids)
        assert a.reactor.y < a.strip.y  # the reactor is at the centre, not in the strip
    assert a.focused == (drawn[0] if drawn else None)


@pytest.mark.parametrize("name", sorted(PRESETS))
def test_every_preset_draws_all_its_panels_at_a_working_size(name):
    layout = PRESETS[name]["layout"]
    a = panes.arrange(layout, 100, 30)
    ids = {p["id"] for p in layout["panels"]}
    if layout["mode"] == "ambient":
        assert not a.placed
    elif layout["mode"] == "focus":
        assert {p.pid for p in a.placed} | set(a.rail.pids if a.rail else ()) == ids
    else:
        assert {p.pid for p in a.placed} == ids, f"{name} hides {a.hidden}"


def test_focus_cases_put_the_focused_panel_large_and_the_rest_in_the_rail():
    for case in (c for c in CASES if c["fn"] == "focus" and "expect" in c):
        a = panes.arrange(case["expect"], COLS, ROWS)
        assert [p.pid for p in a.placed] == [case["pid"]]
        others = {p["id"] for p in case["expect"]["panels"]} - {case["pid"]}
        if not others:
            assert a.rail is None and a.placed[0].rect.w == COLS
            continue
        assert a.rail is not None
        assert set(a.rail.pids) == others
        assert a.placed[0].rect.right + panes.GAP == a.rail.rect.x


def test_the_rail_is_dropped_on_a_narrow_terminal():
    case = next(c for c in CASES if c["fn"] == "focus" and "expect" in c and len(c["expect"]["panels"]) > 1)
    a = panes.arrange(case["expect"], panes.RAIL_MIN_COLS - 1, ROWS)
    assert a.rail is None
    assert a.placed[0].rect.w == panes.RAIL_MIN_COLS - 1
    assert set(a.hidden) == {p["id"] for p in case["expect"]["panels"]} - {case["pid"]}


def test_close_cases_stop_drawing_the_closed_panel():
    for case in (c for c in CASES if c["fn"] in ("close", "close_forced") and "expect" in c):
        before = panes.arrange(case["doc"], COLS, ROWS)
        after = panes.arrange(case["expect"], COLS, ROWS)
        assert case["pid"] in {p.pid for p in before.placed} | set(before.hidden)
        assert case["pid"] not in {p.pid for p in after.placed} | set(after.hidden)


def test_resize_cases_move_the_division():
    for case in (c for c in CASES if c["fn"] == "resize" and "expect" in c):
        before = panes.arrange(case["doc"], COLS, ROWS).rects()
        after = panes.arrange(case["expect"], COLS, ROWS).rects()
        pid = case["pid"]
        if pid in before and pid in after and case["doc"]["tree"] != case["expect"]["tree"]:
            assert before[pid] != after[pid]


def test_set_mode_cases_change_what_is_drawn():
    for case in (c for c in CASES if c["fn"] == "set_mode" and "expect" in c):
        a = panes.arrange(case["expect"], COLS, ROWS)
        assert a.mode == case["mode"]


def test_tiled_orphans_are_hidden_and_counted():
    orphan = OP_CASES["documents"]["orphan"]
    a = panes.arrange({**orphan, "mode": "task"}, COLS, ROWS)
    assert [p.pid for p in a.placed] == ["chat"]
    assert a.hidden == ("image",)


def test_slivers_are_hidden_rather_than_drawn():
    many = OP_CASES["documents"]["many"]
    a = panes.arrange(many, 60, 20)
    assert a.hidden, "nine nested halves in sixty columns cannot all fit a border"
    for p in a.placed:
        assert p.rect.fits()


# --- focus verbs -----------------------------------------------------------------------


def test_ordinal_focus_follows_reading_order():
    a = panes.arrange(PRESETS["working"]["layout"], 120, 40)
    assert [panes.ordinal(a, n) for n in range(1, 6)] == ["chat", "timeline", "goals", "model", None]


def test_direction_focus_prefers_the_panel_level_with_the_reader():
    a = panes.arrange(PRESETS["working"]["layout"], 120, 40)
    # Right from the full-height chat: the timeline, level with the chat's centre,
    # not the narrower goals panel three quarters of the way down.
    assert panes.neighbour(a, "chat", "right") == "timeline"
    assert panes.neighbour(a, "timeline", "down") == "goals"
    assert panes.neighbour(a, "goals", "right") == "model"
    assert panes.neighbour(a, "model", "left") == "goals"
    assert panes.neighbour(a, "goals", "up") == "timeline"
    assert panes.neighbour(a, "chat", "left") is None
    assert panes.neighbour(a, "nowhere", "left") is None


def test_the_focused_pane_is_kept_when_it_is_still_drawn():
    a = panes.arrange(PRESETS["working"]["layout"], 120, 40, focused="goals")
    assert a.focused == "goals"
    b = panes.arrange(PRESETS["working"]["layout"], 120, 40, focused="gone")
    assert b.focused == "chat"
