#!/usr/bin/env python3
"""Executable spec: every notification the phone posts wears the reactor's colour.

The brief's line is "notification icons and colours match the reactor states".
The icon was already the reactor's (`ic_jarvis_status`, from `scripts/gen-icons.py`);
the colour was `JarvisUi.ACCENT` on two builders and nothing on the other twelve, so
an approval waiting on the person and a plain "update available" were the same cyan
in the shade while the HUD showed one in amber and the other at rest.

`ui/Notify.kt` is the table — a level names the reactor state whose colour it
carries and its word; the `notify.*` templates each carry a level — and it is the
same table as `tests/contracts/notify.json`, which the web (`bodies/notices.ts`) and
the chat templates (`_shared/_messages.py`) read too. This checks:

* every level in the contract is in the Kotlin with the same state, the same colour
  token (resolved through the generated `JarvisTokens.kt` to the same hex the token
  file carries) and the same word, and nothing extra;
* every template in the contract is in the Kotlin at the same level, and vice versa;
* the aliases (`warn`, `warning`, `error`, `success`) fold the way the contract says;
* every `Notification.Builder` / `NotificationCompat.Builder` chain in the app sets its
  colour through `Notify.color(...)`, and no builder sets a colour by hand any more.

Run:  python3 android-app/tools/notify_test.py
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "android-app/app/src/main/kotlin/ai/aura/app"
NOTIFY = APP / "ui/Notify.kt"
TOKENS_KT = APP / "ui/theme/JarvisTokens.kt"
CONTRACT = ROOT / "tests/contracts/notify.json"
DESIGN = ROOT / "design/tokens.json"

checks: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    checks.append((name, ok, "" if ok else detail))


def resolve(tokens: dict, value: str) -> str:
    """`{color.warn}` -> the hex it names, following aliases."""
    while isinstance(value, str) and value.startswith("{"):
        node = tokens
        for part in value.strip("{}").split("."):
            node = node[part]
        value = node["$value"]
    return str(value)


def token_hex(tokens: dict, path: str) -> str:
    node = tokens
    for part in path.split("."):
        node = node[part]
    return resolve(tokens, node["$value"]).lower()


def kotlin_colour(src: str, const: str) -> str | None:
    """`const val REACTOR_ALERT = 0xFFFF6B5C.toInt()` -> `#ff6b5c`."""
    m = re.search(rf"const val {const} = 0x[0-9A-Fa-f]{{2}}([0-9A-Fa-f]{{6}})\.toInt\(\)", src)
    return f"#{m.group(1).lower()}" if m else None


def kotlin_levels(src: str) -> dict[str, dict[str, str]]:
    body = re.search(r"enum class Level\(.*?\)\s*\{(.*?)\n    \}", src, re.S)
    out: dict[str, dict[str, str]] = {}
    for m in re.finditer(
        r'(\w+)\("([a-z]+)",\s*ReactorState\.(\w+),\s*JarvisTokens\.Color\.(\w+),\s*"([^"]*)"\)',
        body.group(1) if body else "",
    ):
        out[m.group(2)] = {"state": m.group(3).lower(), "const": m.group(4), "word": m.group(5)}
    return out


def kotlin_templates(src: str) -> dict[str, str]:
    body = re.search(r"enum class Template\(.*?\)\s*\{(.*?)\n    \}", src, re.S)
    return {
        m.group(1): m.group(2).lower()
        for m in re.finditer(r'\("([a-z]+)",\s*Level\.(\w+)\)', body.group(1) if body else "")
    }


def kotlin_aliases(src: str) -> dict[str, str]:
    body = re.search(r"fun level\(key: String\?\): Level = when \(key\) \{(.*?)\n    \}", src, re.S)
    out: dict[str, str] = {}
    for m in re.finditer(r'((?:"[a-z]+",?\s*)+)->\s*Level\.(\w+)', body.group(1) if body else ""):
        for key in re.findall(r'"([a-z]+)"', m.group(1)):
            out[key] = m.group(2).lower()
    return out


def builder_chains(src: str) -> list[str]:
    """Each `Notification(Compat).Builder(...)` and the chained calls that follow it."""
    chains = []
    for m in re.finditer(r"Notification(?:Compat)?\.Builder\(", src):
        # The chain runs while the next non-blank line starts with `.` or is a comment.
        lines = src[m.start():].split("\n")
        chain = [lines[0]]
        for line in lines[1:]:
            s = line.strip()
            if s.startswith(".") or s.startswith("//") or s.startswith("/*") or s.startswith("*") or s == "":
                chain.append(line)
            else:
                break
        chains.append("\n".join(chain))
    return chains


def main() -> int:
    for path in (NOTIFY, TOKENS_KT, CONTRACT, DESIGN):
        if not path.is_file():
            print(f"  FAIL  {path.relative_to(ROOT)} is missing")
            return 1
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    tokens = json.loads(DESIGN.read_text(encoding="utf-8"))
    notify = NOTIFY.read_text(encoding="utf-8")
    tokens_kt = TOKENS_KT.read_text(encoding="utf-8")

    levels = {k: v for k, v in contract["levels"].items() if not k.startswith("$")}
    kt_levels = kotlin_levels(notify)
    check("the Kotlin carries exactly the contract's levels", set(kt_levels) == set(levels),
          f"kotlin {sorted(kt_levels)} vs contract {sorted(levels)}")
    for key, row in levels.items():
        kt = kt_levels.get(key)
        if not kt:
            continue
        check(f"level {key}: state", kt["state"] == row["state"], f"{kt['state']} vs {row['state']}")
        check(f"level {key}: word", kt["word"] == row["word"], f"{kt['word']!r} vs {row['word']!r}")
        want = token_hex(tokens, row["color"])
        have = kotlin_colour(tokens_kt, kt["const"])
        check(f"level {key}: colour {row['color']} is JarvisTokens.Color.{kt['const']}",
              have == want, f"{have} vs {want}")

    templates = {k: v["level"] for k, v in contract["templates"].items() if not k.startswith("$")}
    kt_templates = kotlin_templates(notify)
    check("the Kotlin carries exactly the contract's templates", set(kt_templates) == set(templates),
          f"kotlin-only {sorted(set(kt_templates) - set(templates))}, "
          f"contract-only {sorted(set(templates) - set(kt_templates))}")
    for key, level in templates.items():
        if key in kt_templates:
            check(f"template {key}: level", kt_templates[key] == level, f"{kt_templates[key]} vs {level}")

    aliases = {k: v for k, v in contract["aliases"].items() if not k.startswith("$")}
    kt_aliases = kotlin_aliases(notify)
    check("the aliases fold the way the contract says", kt_aliases == aliases, f"{kt_aliases} vs {aliases}")

    # Every builder in the app, coloured through the table and nowhere else.
    for path in sorted(APP.rglob("*.kt")):
        src = path.read_text(encoding="utf-8")
        if "Notification.Builder(" not in src and "NotificationCompat.Builder(" not in src:
            continue
        rel = path.relative_to(ROOT)
        for i, chain in enumerate(builder_chains(src), 1):
            # Either the call is in the chain, or the chain takes a `color` local
            # the file computed from the table (one builder and its redacted
            # public twin share one).
            through_table = "Notify.color(" in chain or (".setColor(color)" in chain and "= Notify.color(" in src)
            check(f"{rel} builder {i} sets its colour through Notify", through_table,
                  chain.split("\n")[0].strip())
            check(f"{rel} builder {i} sets no colour by hand",
                  not re.search(r"setColor\((?!Notify\.color|color\))", chain), "a setColor(...) not from the table")
        check(f"{rel} imports no JarvisUi.ACCENT for a notification",
              "JarvisUi.ACCENT" not in src or "setColor(JarvisUi" not in src, "setColor(JarvisUi.ACCENT)")

    failed = [c for c in checks if not c[1]]
    for name, ok, detail in checks:
        if not ok:
            print(f"  FAIL  {name}: {detail}")
    print(f"notify: {len(checks) - len(failed)} checks passed, {len(failed)} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
