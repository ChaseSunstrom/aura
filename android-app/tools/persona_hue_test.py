#!/usr/bin/env python3
"""Executable spec: the phone's reactor wears the primary persona's hue, by the contract.

The console tints its orb with the primary's hue while the room is calm
(`jarvis-web/src/lib/orb/personas.ts` `CALM_STATES`) and lets a loud state keep
its own colour, because that colour is information; the terminal console does
the same through `_LOUD_STATES`. `tests/contracts/persona.json` `hues`,
`untinted` and `calm_states` are the one table, and this reads the phone's
Kotlin back against it: the calm set, the persona -> token mapping (with JARVIS
and an unknown name falling to no tint), the threading from `HudState.persona`
to every reactor host, and the token names themselves in the generated
`JarvisTokens.kt` and their source in `design/tokens.json`. The pre-v2 frame that
applied the tint, and its golden, are gone (T-1299); this proves the shape,
without an SDK, in a second.

Run:  python3 android-app/tools/persona_hue_test.py
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "android-app/app/src/main/kotlin/ai/jarvis/app"

CONTRACT = ROOT / "tests/contracts/persona.json"
REACTOR_CONTRACT = ROOT / "tests/contracts/reactor.json"
DESIGN_TOKENS = ROOT / "design/tokens.json"
STATE = APP / "reactor/ReactorState.kt"
#: The pre-v2 animator, composable and HudReactor that carried the accent are gone (T-0779),
#: and ReactorModel's own copy, which nothing read, went with T-1305.
MODEL = APP / "glass/ReactorModel.kt"
AURA_TOKENS = APP / "ui/theme/JarvisTokens.kt"
CONVERSATION = APP / "assist/hermes/HermesConversation.kt"
UI = APP / "assist/JarvisConversation.kt"
#: Every host with a reactor, and the line that stores the persona for the rest of the phone's UI.
HOSTS = {
    APP / "JarvisAssistActivity.kt": "ai.jarvis.app.ui.theme.PersonaAccent.set(this, name)",
    APP / "assist/WakeWordService.kt": "ai.jarvis.app.ui.theme.PersonaAccent.set(this@WakeWordService, name)",
}

checks: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    checks.append((name, ok, bool(ok) and "" or detail))


def main() -> int:
    for path in (CONTRACT, REACTOR_CONTRACT, DESIGN_TOKENS, STATE, MODEL,
                 AURA_TOKENS, CONVERSATION, UI, *HOSTS):
        if not path.is_file():
            print(f"  FAIL  {path.relative_to(ROOT)} is missing")
            return 1

    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    reactor = json.loads(REACTOR_CONTRACT.read_text(encoding="utf-8"))
    design = json.loads(DESIGN_TOKENS.read_text(encoding="utf-8"))
    state_src = STATE.read_text(encoding="utf-8")
    tokens_src = AURA_TOKENS.read_text(encoding="utf-8")
    convo_src = CONVERSATION.read_text(encoding="utf-8")

    personas: list[str] = contract["personas"]
    hues: dict[str, str] = {k: v for k, v in contract["hues"].items() if not k.startswith("$")}
    untinted: str = contract["untinted"]
    calm: list[str] = contract["calm_states"]

    # 1. The contract is self-consistent: a hue per persona, the untinted one
    #    among them, every calm state a reactor state.
    check("every persona has a hue", sorted(hues) == sorted(personas), f"{sorted(hues)} vs {sorted(personas)}")
    check("the untinted persona is one of the four", untinted in personas, untinted)
    for s in calm:
        check(f"calm state `{s}` is a reactor state", s in reactor["states"], f"{sorted(reactor['states'])}")

    # 2. The phone's CALM set is the contract's calm_states.
    m = re.search(r"val CALM: Set<ReactorState> = setOf\(([^)]*)\)", state_src)
    phone_calm = sorted(n.strip().lower() for n in m.group(1).split(",") if n.strip()) if m else []
    check("ReactorState.CALM is the contract's calm_states", phone_calm == sorted(calm), f"phone {phone_calm}")

    # 3. personaHue: a branch per tinted persona naming its token, none for the
    #    untinted one, and `else -> null` so an unknown name adds no tint.
    body = state_src[state_src.index("fun personaHue("):]
    body = body[: body.index("\n}") + 2]
    branches = dict(re.findall(r'"([a-z]+)" -> JarvisTokens\.Color\.(PERSONA_[A-Z]+)', body))
    for name in personas:
        if name == untinted:
            check(f"`{name}` has no branch (no tint)", name not in branches, f"has {branches.get(name)}")
        else:
            check(f"`{name}` maps to PERSONA_{name.upper()}", branches.get(name) == f"PERSONA_{name.upper()}",
                  f"has {branches.get(name)}")
    check("an unknown name falls to null", "else -> null" in body)
    check("the name is lower-cased before the lookup", "name.lowercase()" in body)

    # 4. Each token the contract names is generated into JarvisTokens.kt from
    #    design/tokens.json, under the contract's CSS name.
    for name, css in hues.items():
        const = f"PERSONA_{name.upper()}"
        check(f"JarvisTokens.Color.{const} is `{css}`",
              re.search(rf"const val {const} = 0x[0-9A-F]{{8}}\.toInt\(\) // {re.escape(css)}$", tokens_src, re.M) is not None)
        check(f"design/tokens.json color.persona.{name} exists", name in design["color"]["persona"])
        check(f"`{css}` is the persona's own token", css == f"--jv-persona-{name}")

    # 5. The phone's core (V2Core) wears no tint, as the console's does not (ReactorView's KDoc),
    #    and the model holds none: a tint written and never read is how it would come back unseen.
    check("ReactorModel holds no tint", "accent" not in MODEL.read_text(encoding="utf-8"))

    # 6. From the wire to the hosts: the switch and the status poll both publish
    #    it, and every host with a reactor stores it in PersonaAccent.
    check("Ui has onPersona, defaulted", "fun onPersona(name: String) {}" in UI.read_text(encoding="utf-8"))
    check("a persona.switch publishes it", "if (HudState.Change.PERSONA in changed) ui.onPersona(hud.persona)" in convo_src)
    check("a late-joining phone learns it from the status",
          "hud.persona = status.persona\n                ui.onPersona(hud.persona)" in convo_src)
    for path, line in HOSTS.items():
        check(f"{path.name} stores the persona", line in path.read_text(encoding="utf-8"), line)


    failed = [c for c in checks if not c[1]]
    for name, ok, detail in checks:
        print(("  ok    " if ok else "  FAIL  ") + name + (f"\n        | {detail}" if not ok and detail else ""))
    print(f"{len(checks) - len(failed)}/{len(checks)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
