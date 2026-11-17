#!/usr/bin/env python3
"""Generate the split-tree case table the HUD's two implementations are bound by.

`hermes/plugins/hud-layout/layout.py` is the authority on what a layout operation
does — it is what JARVIS drives and what the benchmark tasks exercise. The web
window manager (`aura-web/src/lib/hud/layout/tree.ts`) has to do the same
arithmetic locally, because drag-to-split, drag-to-swap and resize are gestures a
person makes at 60 fps with no round trip to the agent.

CLAUDE.md forbids binding two implementations of one behaviour with a
"keep in step" comment, so they are bound by a table instead: the cases are
computed here by calling the Python, written into the `op_cases` block of
`tests/contracts/hud-layout.json` — one contract, not a sibling file that could
disagree with it — and replayed by BOTH suites, the plugin's
`tests/test_op_cases.py` and the console's `tree.contract.test.ts` and
`document.contract.test.ts`. A change to the Python that the TypeScript does not
follow is a red test naming the case.

Everything else in that contract is hand-authored, so the block is merged in
rather than the file being rewritten.

    python3 scripts/gen-hud-layout-cases.py           # write
    python3 scripts/gen-hud-layout-cases.py --check   # fail if stale (CI)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
CONTRACT = ROOT / "tests" / "contracts" / "hud-layout.json"

#: Trees the cases are computed over. Named so a failure says which shape broke:
#: a single leaf, a flat split, a nested one, and one deep enough that a removal
#: has to collapse a split that is not the root.
TREES: dict[str, Any] = {
    "leaf": "chat",
    "flat": {"split": "row", "ratio": 0.5, "children": ["chat", "timeline"]},
    "nested": {
        "split": "row",
        "ratio": 0.6,
        "children": ["chat", {"split": "column", "ratio": 0.4, "children": ["timeline", "goals"]}],
    },
    "deep": {
        "split": "column",
        "ratio": 0.7,
        "children": [
            {"split": "row", "ratio": 0.5, "children": ["chat", "timeline"]},
            {"split": "row", "ratio": 0.3, "children": ["goals", "model"]},
        ],
    },
    # Asymmetric on purpose: a collapse discards the parent's ratio entirely, and
    # a 0.5 tree would look identical whether that happened or not.
    "lopsided": {"split": "row", "ratio": 0.82, "children": ["chat", "timeline"]},
    "lopsided-nested": {
        "split": "column",
        "ratio": 0.25,
        "children": [
            "chat",
            {"split": "row", "ratio": 0.77, "children": ["timeline", "goals"]},
        ],
    },
    "empty": None,
}

#: A document with more panels than any preset ships. There is no panel cap in
#: the plugin, the schema or the contract (`panels` has no max_length), so this
#: is the nearest real thing: every operation exercised at a depth the shipped
#: layouts never reach, which is what a cap would have guarded.
MANY = 9


#: Whole documents the document-level cases are computed over. The window manager
#: performs these operations locally — alt-m cycles the mode, a drag is a move, a
#: resize handle is a resize, the close verb is a close — so they are bound the
#: same way the tree primitives are.
DOCS: dict[str, Any] = {
    "task": {
        "mode": "task",
        "tree": {"split": "row", "ratio": 0.6, "children": ["chat", {"split": "column", "ratio": 0.5, "children": ["timeline", "goals"]}]},
        "panels": [
            {"id": "chat", "type": "chat", "pinned": False},
            {"id": "timeline", "type": "timeline", "pinned": False},
            {"id": "goals", "type": "goals", "pinned": False},
        ],
    },
    "pinned": {
        "mode": "task",
        "tree": {"split": "row", "ratio": 0.5, "children": ["chat", "goals"]},
        "panels": [
            {"id": "chat", "type": "chat", "pinned": False},
            {"id": "goals", "type": "goals", "pinned": True},
        ],
    },
    # A panel the tree does not hold: what Floating and Ambient leave behind, and
    # what a switch back to Tiled has to re-tile rather than lose.
    "orphan": {
        "mode": "overview",
        "tree": "chat",
        "panels": [
            {"id": "chat", "type": "chat", "pinned": False},
            {"id": "image", "type": "image", "pinned": False},
        ],
    },
    "single": {
        "mode": "task",
        "tree": "chat",
        "panels": [{"id": "chat", "type": "chat", "pinned": False}],
    },
}


def _load_layout():
    sys.path.insert(0, str(ROOT / "hermes" / "plugins"))
    from _plugin_loader import load_plugin  # noqa: E402  (path set above)

    return load_plugin("hud-layout").layout


def _pair(result: tuple[Any, bool]) -> dict[str, Any]:
    tree, done = result
    return {"tree": tree, "done": done}


def build_cases(layout: Any) -> list[dict[str, Any]]:
    """Every case, in a stable order so a regeneration is a no-op diff."""
    cases: list[dict[str, Any]] = []

    for name, tree in TREES.items():
        cases.append({"fn": "leaves", "on": name, "tree": tree, "expect": layout.leaves(tree)})

    # A removal of every id present, plus one that is not: the absent case is what
    # tells a caller "nothing changed" rather than silently returning an empty tree.
    for name, tree in TREES.items():
        for pid in [*layout.leaves(tree), "absent"]:
            cases.append({
                "fn": "remove_leaf", "on": name, "tree": tree, "pid": pid,
                "expect": _pair(layout.remove_leaf(tree, pid)),
            })

    # All four edges at two shares: the ratio inversion for the edges that put the
    # new panel second is invisible until left and right of the same share differ.
    for edge in layout.EDGES:
        for share in (0.3, 0.5):
            cases.append({
                "fn": "split_leaf", "target": "chat", "pid": "image", "edge": edge, "share": share,
                "expect": layout.split_leaf("chat", "image", edge, share),
            })

    for name, tree in TREES.items():
        for target in [*layout.leaves(tree), "absent"]:
            for edge in layout.EDGES:
                cases.append({
                    "fn": "insert_beside", "on": name, "tree": tree, "target": target,
                    "pid": "image", "edge": edge, "share": 0.35,
                    "expect": _pair(layout.insert_beside(tree, target, "image", edge, 0.35)),
                })

    for name, tree in TREES.items():
        for pid in [*layout.leaves(tree), "absent"]:
            cases.append({
                "fn": "set_share", "on": name, "tree": tree, "pid": pid, "share": 0.25,
                "expect": _pair(layout.set_share(tree, pid, 0.25)),
            })

    # The shares the plugin refuses. The TypeScript must refuse the same set, or a
    # drag could write a ratio the schema then rejects on save.
    for value in (0, 1, -0.5, 1.5, "x", None):
        try:
            layout._share(value)
        except layout.LayoutError as exc:
            cases.append({"fn": "share", "value": value, "raises": str(exc)})
        else:
            raise AssertionError(f"_share({value!r}) was expected to raise")
    for value in (0.001, 0.5, 0.999):
        cases.append({"fn": "share", "value": value, "expect": layout._share(value)})

    cases.extend(_document_cases(layout))
    cases.extend(_deep_cases(layout))
    return cases


def _many_panel_document(layout: Any) -> dict[str, Any]:
    """A right-leaning spine of MANY panels, built the way op_open builds one."""
    doc: dict[str, Any] = {"mode": "task", "tree": None, "panels": []}
    for i in range(MANY):
        beside = f"p{i - 1}" if i else None
        doc = layout.op_open(doc, f"p{i}", "chat", beside=beside, edge="right", share=0.5)
    return doc


def _deep_cases(layout: Any) -> list[dict[str, Any]]:
    """Every operation against a document deeper than any preset ships.

    There is no panel cap to test — `panels` carries no max_length in the schema
    and the plugin refuses no document for being large — so this is what a cap
    would have guarded: the ops at a nesting depth the presets never reach, where
    a collapse has to walk several levels to find its parent.
    """
    doc = _many_panel_document(layout)
    cases: list[dict[str, Any]] = [
        {"fn": "leaves", "on": "many", "tree": doc["tree"], "expect": layout.leaves(doc["tree"])},
    ]
    # The ends and the middle: a collapse at the spine's root, at its deepest
    # leaf, and in between, which are the three shapes remove_leaf can meet.
    for pid in ("p0", f"p{MANY // 2}", f"p{MANY - 1}"):
        cases.append({
            "fn": "remove_leaf", "on": "many", "tree": doc["tree"], "pid": pid,
            "expect": _pair(layout.remove_leaf(doc["tree"], pid)),
        })
        cases.append({
            "fn": "set_share", "on": "many", "tree": doc["tree"], "pid": pid, "share": 0.25,
            "expect": _pair(layout.set_share(doc["tree"], pid, 0.25)),
        })
        cases.append(_doc_case(
            layout, "close", "many", doc, lambda d=doc, x=pid: layout.op_close(d, x), pid=pid,
        ))
        cases.append(_doc_case(
            layout, "resize", "many", doc,
            lambda d=doc, x=pid: layout.op_resize(d, x, share=0.3), pid=pid, share=0.3,
        ))
    for mode in layout.HUD_MODES:
        cases.append(_doc_case(
            layout, "set_mode", "many", doc,
            lambda d=doc, m=mode: layout.op_set_mode(d, m), mode=mode,
        ))
    cases.append(_doc_case(
        layout, "move", "many", doc,
        lambda d=doc: layout.op_move(d, "p0", target=f"p{MANY - 1}", edge="left", share=0.4),
        pid="p0", target=f"p{MANY - 1}", edge="left", share=0.4,
    ))
    return cases


def _doc_case(layout: Any, op: str, doc_name: str, doc: Any, call: Any, **args: Any) -> dict[str, Any]:
    """One document-level case, recording the result or the refusal by its message."""
    row: dict[str, Any] = {"fn": op, "on": doc_name, "doc": doc, **args}
    try:
        row["expect"] = call()
    except layout.LayoutError as exc:
        row["raises"] = str(exc)
    return row


def _document_cases(layout: Any) -> list[dict[str, Any]]:
    """The whole-document operations the manager performs on a gesture."""
    cases: list[dict[str, Any]] = []

    for name, doc in DOCS.items():
        for mode in layout.HUD_MODES:
            cases.append(_doc_case(
                layout, "set_mode", name, doc,
                lambda d=doc, m=mode: layout.op_set_mode(d, m), mode=mode,
            ))
        for pid in ("chat", "goals", "absent"):
            cases.append(_doc_case(
                layout, "close", name, doc,
                lambda d=doc, p=pid: layout.op_close(d, p), pid=pid,
            ))
            cases.append(_doc_case(
                layout, "close_forced", name, doc,
                lambda d=doc, p=pid: layout.op_close(d, p, force=True), pid=pid,
            ))
            cases.append(_doc_case(
                layout, "focus", name, doc,
                lambda d=doc, p=pid: layout.op_focus(d, p), pid=pid,
            ))
            cases.append(_doc_case(
                layout, "pin", name, doc,
                lambda d=doc, p=pid: layout.op_pin(d, p, True), pid=pid, pinned=True,
            ))
            cases.append(_doc_case(
                layout, "resize", name, doc,
                lambda d=doc, p=pid: layout.op_resize(d, p, share=0.3), pid=pid, share=0.3,
            ))
        for target in ("chat", "goals", "absent"):
            cases.append(_doc_case(
                layout, "move", name, doc,
                lambda d=doc, t=target: layout.op_move(d, "timeline", target=t, edge="left", share=0.4),
                pid="timeline", target=target, edge="left", share=0.4,
            ))
    return cases


#: The header the `op_cases` block carries, so a reader who opens the contract
#: knows which side is the authority without having to infer it.
OP_CASES_COMMENT = (
    "GENERATED by scripts/gen-hud-layout-cases.py — do not edit by hand. "
    "hermes/plugins/hud-layout/layout.py is the AUTHORITY on these operations: the cases "
    "are computed by calling the Python and replayed by the plugin's "
    "tests/test_op_cases.py (the console's TypeScript window manager that once followed "
    "it went with the v2 shell). Change the Python, run `make hud-cases`, and the suite "
    "goes red naming the case."
)


def build_op_cases(layout: Any) -> dict[str, Any]:
    return {
        "_comment": OP_CASES_COMMENT,
        "generated_from": "hermes/plugins/hud-layout/layout.py",
        "no_panel_cap": (
            "There is no panel cap: `panels` carries no max_length in the settings schema and "
            "the plugin refuses no document for being large. The `many` cases are what a cap "
            "would have guarded — every operation at a nesting depth no preset reaches."
        ),
        "no_envelope_in_python": (
            "The plugin has no size envelopes: a share is validated as 0 < share < 1 and "
            "nothing else, so it cannot drive a panel below a minimum it does not know about. "
            "The envelopes are §9.1's table, and clampExtent in the console's glass library is "
            "what holds a resize above them."
        ),
        "trees": TREES,
        "documents": {**DOCS, "many": _many_panel_document(layout)},
        "cases": build_cases(layout),
    }


def build_document(layout: Any) -> str:
    """The whole contract, with `op_cases` replaced and every other key untouched.

    Merged rather than rewritten: the contract is hand-authored everywhere else —
    the tools, the presets, the refusal text — and a generator that emitted the
    file wholesale would own lines it has no business owning.
    """
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    contract["op_cases"] = build_op_cases(layout)
    return json.dumps(contract, indent=2, ensure_ascii=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true", help="fail if the committed file is stale")
    args = ap.parse_args(argv)

    text = build_document(_load_layout())
    rel = CONTRACT.relative_to(ROOT)
    if args.check:
        if CONTRACT.read_text(encoding="utf-8") != text:
            print(f"STALE: {rel}#op_cases is not what hermes/plugins/hud-layout/layout.py "
                  "produces — run `make hud-cases`", file=sys.stderr)
            return 1
        print(f"ok    {rel}#op_cases")
        return 0
    changed = CONTRACT.read_text(encoding="utf-8") != text
    CONTRACT.write_text(text, encoding="utf-8")
    print(f"{'wrote' if changed else 'ok   '} {rel}#op_cases")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
