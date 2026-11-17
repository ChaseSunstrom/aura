"""The op-case table, replayed against the implementation that generated it.

`tests/contracts/hud-layout.json#op_cases` is what binds this plugin to the
console's window manager (`jarvis-web/src/lib/hud/layout`): the same rows are
replayed there by `tree.contract.test.ts` and `document.contract.test.ts`.

This side has two jobs. It proves the committed table is what the *current*
implementation produces — a generated table nobody regenerates is the same trap
as a "keep in step" comment — and it proves the table is worth replaying, by
failing if it thins out or stops carrying refusals.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "hermes" / "plugins"))
sys.path.insert(0, str(ROOT / "scripts"))

from _plugin_loader import load_plugin  # noqa: E402

layout = load_plugin("hud-layout").layout

CONTRACT = json.loads((ROOT / "tests" / "contracts" / "hud-layout.json").read_text(encoding="utf-8"))
OP_CASES = CONTRACT["op_cases"]
CASES = OP_CASES["cases"]

TREE_OPS = {"leaves", "remove_leaf", "split_leaf", "insert_beside", "set_share", "share"}
DOC_OPS = {"set_mode", "close", "close_forced", "focus", "pin", "resize", "move"}


def _id(case: dict) -> str:
    bits = [case["fn"], case.get("on"), case.get("pid") or case.get("target") or case.get("mode"),
            case.get("edge"), case.get("share") if "share" in case else case.get("value")]
    return " ".join(str(b) for b in bits if b is not None)


def _run(case: dict):
    fn = case["fn"]
    if fn == "leaves":
        return layout.leaves(case["tree"])
    if fn == "remove_leaf":
        tree, done = layout.remove_leaf(case["tree"], case["pid"])
        return {"tree": tree, "done": done}
    if fn == "split_leaf":
        return layout.split_leaf(case["target"], case["pid"], case["edge"], case["share"])
    if fn == "insert_beside":
        tree, done = layout.insert_beside(
            case["tree"], case["target"], case["pid"], case["edge"], case["share"])
        return {"tree": tree, "done": done}
    if fn == "set_share":
        tree, done = layout.set_share(case["tree"], case["pid"], case["share"])
        return {"tree": tree, "done": done}
    if fn == "share":
        return layout._share(case["value"])
    if fn == "set_mode":
        return layout.op_set_mode(case["doc"], case["mode"])
    if fn == "close":
        return layout.op_close(case["doc"], case["pid"])
    if fn == "close_forced":
        return layout.op_close(case["doc"], case["pid"], force=True)
    if fn == "focus":
        return layout.op_focus(case["doc"], case["pid"])
    if fn == "pin":
        return layout.op_pin(case["doc"], case["pid"], case["pinned"])
    if fn == "resize":
        return layout.op_resize(case["doc"], case["pid"], share=case["share"])
    if fn == "move":
        return layout.op_move(case["doc"], case["pid"], target=case["target"],
                              edge=case["edge"], share=case["share"])
    raise AssertionError(f"unknown case fn {fn!r}")


@pytest.mark.parametrize("case", CASES, ids=_id)
def test_the_committed_table_is_what_this_implementation_produces(case):
    """Every row, recomputed. A change to layout.py that nobody regenerated fails here."""
    if "raises" in case:
        with pytest.raises(layout.LayoutError) as exc:
            _run(case)
        # The message is compared, not just the type: the console shows the same
        # sentence, so a reword here is a reword the HUD has to follow.
        assert str(exc.value) == case["raises"]
    else:
        assert _run(case) == case["expect"]


def test_the_table_covers_every_operation_both_sides_implement():
    seen = {c["fn"] for c in CASES}
    assert seen == TREE_OPS | DOC_OPS, f"missing: {sorted((TREE_OPS | DOC_OPS) - seen)}"


def test_the_table_carries_refusals_and_not_only_happy_paths():
    # A table of successes would let every refusal drift apart unnoticed.
    assert sum(1 for c in CASES if "raises" in c) >= 20


def test_the_table_covers_all_four_edges():
    edges = {c["edge"] for c in CASES if c["fn"] in ("split_leaf", "insert_beside")}
    assert edges == set(layout.EDGES)


def test_the_table_pins_the_collapse_of_an_asymmetric_parent():
    """The ratio a collapse discards is the whole point of the sibling rule."""
    lopsided = [c for c in CASES if c["fn"] == "remove_leaf" and c.get("on") == "lopsided"]
    assert lopsided, "no lopsided removal cases: a 0.5 tree cannot show a discarded ratio"
    collapsed = [c for c in lopsided if c["expect"]["done"] and isinstance(c["expect"]["tree"], str)]
    assert collapsed, "no case where a removal collapses the parent onto a bare sibling"


def test_the_table_reaches_deeper_than_any_preset():
    deep = [c for c in CASES if c.get("on") == "many"]
    assert len(deep) >= 10
    assert len(layout.leaves(OP_CASES["documents"]["many"]["tree"])) > max(
        len(layout.leaves(p["layout"]["tree"])) for p in CONTRACT["presets"].values()
    )


def test_the_generator_is_not_stale():
    """`--check` from the suite, so a regeneration nobody ran is a red test here
    rather than a red pipeline nobody sees until the push."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "gen_hud_layout_cases", ROOT / "scripts" / "gen-hud-layout-cases.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.main(["--check"]) == 0
