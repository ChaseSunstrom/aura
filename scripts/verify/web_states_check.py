#!/usr/bin/env python3
"""Every v2 console screen handles loading, empty, error and offline.

Static half of the four-states check (the v2 Playwright specs, `e2e/v2-*.spec.ts`,
are the dynamic half). The console has one route — `src/routes/[...path]/+page.svelte`
mounts the v2 shell (`$lib/ds/shell/Shell.svelte`) — and what is checked is the shape
that keeps a state from being forgotten:

  * `src/lib/hud/activities.ts` — the manifest of every path the console serves,
    which the e2e specs and the phone's parity test walk;
  * exactly one routed page (bar `api/`, `healthz`, `gallery/` and `offline/`)
    mounts `<Shell`, so no page draws its own chrome beside the shell;
  * `$lib/ds/ui/State.svelte` says each of the four in words;
  * every screen under `$lib/ds/screens` that reads a feed draws `<State`, itself
    or through a screen part it renders — so a screen cannot forget a state by not
    writing one. The car's display is the exception: its data is the phone's
    bridge, not a feed, and its offline is the unlinked line (`car-unlinked`);
  * the feeds turn a dropped link into `offline`, not an error;
  * `src/routes/+error.svelte` (a thrown error) and `src/routes/offline` (the
    console out of reach, for the phone and the car) exist.

Does not cover: whether a state reads well. The screenshot sweep is that.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "jarvis-web" / "src"
SCREENS = WEB / "lib" / "ds" / "screens"
# `offline/` is the offline state itself: prerendered into the phone app's assets for when
# the console cannot be reached (jarvis-web/scripts/offline-asset.mjs), with no shell to mount.
SKIP = ("api/", "healthz", "gallery/", "offline/")
problems: list[str] = []

manifest = WEB / "lib" / "hud" / "activities.ts"
declared: set[str] = set()
if manifest.is_file():
    declared = set(re.findall(r"^\t'(/[^']*)':\s*\{", manifest.read_text(), re.M))
    if not declared:
        problems.append(f"{manifest}: no `'/path': {{` entries found")
else:
    problems.append(f"missing activity manifest: {manifest}")

shell_pages = 0
for page in sorted(WEB.glob("routes/**/+page.svelte")):
    rel = page.relative_to(WEB / "routes").as_posix()
    if any(s in rel for s in SKIP):
        continue
    if "<Shell" in page.read_text():
        shell_pages += 1
    else:
        problems.append(f"{page.relative_to(ROOT)}: a routed page that does not mount <Shell>")
if shell_pages != 1:
    problems.append(f"expected exactly one page mounting <Shell>, found {shell_pages}")

state = WEB / "lib" / "ds" / "ui" / "State.svelte"
words = state.read_text() if state.is_file() else ""
for s in ("loading", "empty", "error", "offline"):
    if f"'{s}'" not in words and f"status === '{s}'" not in words:
        problems.append(f"ds/ui/State.svelte does not say `{s}`")


def draws_state(path: Path, seen: frozenset[Path] = frozenset()) -> bool:
    """`<State` in the screen, or in a screen part it renders (`import X from './X.svelte'`)."""
    text = path.read_text()
    if "<State" in text:
        return True
    parts = re.findall(r"import \w+ from '\./(\w+)\.svelte'", text)
    return any(draws_state(SCREENS / f"{p}.svelte", seen | {path}) for p in parts
               if SCREENS / f"{p}.svelte" not in seen and (SCREENS / f"{p}.svelte").is_file())


screens = sorted(SCREENS.glob("*.svelte"))
for screen in screens:
    text = screen.read_text()
    if not re.search(r"\bfeeds\.\w+", text):
        continue
    if screen.name == "Car.svelte":
        if "car-unlinked" not in text:
            problems.append("ds/screens/Car.svelte has no unlinked line: the car would not say it is offline")
        continue
    if not draws_state(screen):
        problems.append(f"ds/screens/{screen.name} reads a feed and draws no <State>: a state is undefined")

feeds = WEB / "lib" / "hud" / "feeds.svelte.ts"
if not feeds.is_file() or "'offline'" not in feeds.read_text():
    problems.append("hud/feeds.svelte.ts never says `offline`: a dropped link would read as an error")

for need in (WEB / "routes" / "+error.svelte", WEB / "routes" / "offline" / "+page.svelte"):
    if not need.is_file():
        problems.append(f"missing {need.relative_to(ROOT)}")

if problems:
    print("\n".join(problems))
    print(f"\n{len(problems)} problem(s) across {len(declared)} declared path(s)")
    sys.exit(1)
print(f"{len(declared)} paths declared on the one shell route; {len(screens)} ds screens, every one that reads a feed "
      "draws <State>; +error.svelte and the offline page present")
