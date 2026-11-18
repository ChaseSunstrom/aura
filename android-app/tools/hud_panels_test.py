#!/usr/bin/env python3
"""Executable spec: the phone renders the same HUD panel surface as the contract.

The panel surface (`hud.panel.show`/`update`/`dismiss`) is shared by
`hermes/plugins/aura-hud` (the emitter), the web console
(`aura-web/src/lib/hermes/panels.ts`) and the phone. `tests/contracts/hud-panels.json`
is what all three assert against; `docs/hermes-hud-protocol.md` § Panels is its
prose. This reads the phone's Kotlin back and checks it has not drifted from the
contract: the kinds it knows, the fields a panel carries, the three events it
folds, the command it dispatches, the reducer's rules, and the four motion
tokens (name AND value) the panels animate with on the console.

Run:  python3 android-app/tools/hud_panels_test.py
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "android-app/app/src/main/kotlin/ai/aura/app"

CONTRACT = ROOT / "tests/contracts/hud-panels.json"
DESIGN_TOKENS = ROOT / "design/tokens.json"
HUD_PANELS = APP / "assist/hermes/HudPanels.kt"
HUD_STATE = APP / "assist/hermes/HudState.kt"
HERMES_CLIENT = APP / "assist/hermes/HermesClient.kt"
AURA_TOKENS = APP / "ui/theme/JarvisTokens.kt"

checks: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    checks.append((name, ok, bool(ok) and "" or detail))


def kotlin_list(src: str, decl: str) -> list[str]:
    """`val <decl>: ... = listOf("a", "b", ...)` -> ['a', 'b']."""
    m = re.search(re.escape(decl) + r"[^=]*=\s*listOf\(([^)]*)\)", src, re.S)
    if not m:
        return []
    return [s.strip().strip('"') for s in m.group(1).split(",") if s.strip()]


def panel_fields(src: str) -> list[str]:
    """The `val` names inside `data class Panel(...)`."""
    m = re.search(r"data class Panel\((.*?)\n\)", src, re.S)
    if not m:
        return []
    return re.findall(r"\bval\s+(\w+)\b", m.group(1))


def token_int(src: str, name: str) -> int | None:
    m = re.search(rf"const val {name}\s*=\s*(\d+)", src)
    return int(m.group(1)) if m else None


def design_duration_ms(data: dict, leaf: str) -> int | None:
    node = data.get("motion", {}).get("dur", {}).get(leaf)
    if not node:
        return None
    m = re.fullmatch(r"(\d+)ms", str(node.get("$value", "")))
    return int(m.group(1)) if m else None


def main() -> int:
    for path in (CONTRACT, DESIGN_TOKENS, HUD_PANELS, HUD_STATE, HERMES_CLIENT, AURA_TOKENS):
        if not path.is_file():
            print(f"  FAIL  {path.relative_to(ROOT)} is missing")
            return 1

    contract = json.loads(CONTRACT.read_text())
    panels_src = HUD_PANELS.read_text()
    state_src = HUD_STATE.read_text()
    client_src = HERMES_CLIENT.read_text()
    tokens_src = AURA_TOKENS.read_text()
    design = json.loads(DESIGN_TOKENS.read_text())

    # 1. The kinds the phone names are the contract's, in the contract's order.
    phone_kinds = kotlin_list(panels_src, "val KINDS")
    check("the phone's KINDS are the contract's kinds", phone_kinds == contract["kinds"],
          f"phone {phone_kinds} vs contract {contract['kinds']}")

    # 2. Every required show field is a field on the Panel data class
    #    (created_at is carried as createdAt).
    fields = panel_fields(panels_src)
    rename = {"created_at": "createdAt"}
    for want in contract["required_show_fields"]:
        have = rename.get(want, want)
        check(f"Panel carries the required field `{want}`", have in fields, f"has {fields}")

    # 3. The three panel events are folded by the reducer.
    for event in contract["events"]:
        check(f"HudState folds `{event}`", f'"{event}" ->' in state_src)

    # 4. The dismiss-all id is the contract's.
    check("DISMISS_ALL is the contract's dismiss_all_id",
          f'const val DISMISS_ALL = "{contract["dismiss_all_id"]}"' in panels_src)

    # 5. The command the phone dispatches is the contract's `hud`, with the
    #    list and dismiss arg forms the contract names.
    cmd = contract["command"]
    check("the HUD command name is the contract's", f'const val HUD_COMMAND = "{cmd["name"]}"' in client_src,
          cmd["name"])
    # `@<sid> list` / `@<sid> dismiss <id|*>` written as Kotlin interpolations.
    check("the list arg is `@<sid> list`", '"@$sid list"' in client_src, cmd["list"])
    check("the dismiss arg is `@<sid> dismiss <id>`", '"@$sid dismiss $id"' in client_src, cmd["dismiss"])
    check("late-join seeding calls listPanels once a session attaches",
          "fun listPanels(" in client_src and "client?.listPanels" in (APP / "assist/hermes/HermesConversation.kt").read_text())
    check("a phone dismiss is optimistic then reconciled",
          "dismissPanelLocally" in state_src and "fun dismissPanel(" in client_src)

    # 6. The four motion tokens: name AND value, in JarvisTokens, the design
    #    source, and referenced by the surface.
    motion = contract["motion"]
    for leaf, kt, key in (
        ("panel-summon", "PANEL_SUMMON", "summon_ms"),
        ("panel-dismiss", "PANEL_DISMISS", "dismiss_ms"),
        ("panel-reorder", "PANEL_REORDER", "reorder_ms"),
        ("panel-reduced", "PANEL_REDUCED", "reduced_motion_ms"),
    ):
        want = motion[key]
        check(f"JarvisTokens.{kt} == {want}", token_int(tokens_src, kt) == want, str(token_int(tokens_src, kt)))
        check(f"design/tokens.json {leaf} == {want}", design_duration_ms(design, leaf) == want, str(design_duration_ms(design, leaf)))

    # 7. The reducer's rules, the same three the console keeps.
    check("a lower rev than held is ignored on show", "panel.rev < panels[index].rev" in panels_src)
    check("a lower rev than held is ignored on update", "revRaw < held.rev" in panels_src)
    check("an update for an unknown panel is ignored", "if (index == -1) return panels" in panels_src)
    check("the ttl fallback keeps a local expiresAt", "expiresAt" in panels_src and "fun pruneExpired(" in panels_src)

    # 8. A `hud list` seeds through applyShow. The panels draw on the console
    #    (the phone's home), so the reducer is all the phone keeps of them.
    check("a `hud list` seeds through applyShow", "fun seedFromList(" in panels_src and "applyShow(next, panel)" in panels_src)
    check("approval routes to the consent door", "raisePanelApproval" in (APP / "assist/hermes/HermesConversation.kt").read_text())
    check("clarify routes to the question prompt", "raisePanelClarify" in (APP / "assist/hermes/HermesConversation.kt").read_text())

    failed = [c for c in checks if not c[1]]
    for name, ok, detail in checks:
        print(("  ok    " if ok else "  FAIL  ") + name + (f"\n        | {detail}" if not ok and detail else ""))
    print(f"{len(checks) - len(failed)}/{len(checks)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
