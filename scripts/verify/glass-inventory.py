#!/usr/bin/env python3
"""Glass HUD inventory completeness gate.

``docs/design/GLASS_INVENTORY.md`` maps every existing UI element in the repo to
its Glass HUD panel or component, with a disposition, so the migration cannot
silently leave an old screen behind (the V2 no-dead-code rule, applied to the
plan rather than to the code). This script is the ratchet that keeps that map
honest: it walks the three UI trees, collects every component file the way the
inventory defines a "UI file", and fails naming any file whose repo-relative
path does not appear verbatim in the inventory. A UI file added while the
migration is in flight therefore breaks this gate until someone gives it a
panel — which is the whole point.

Only stdlib, Python 3.11, so ``make docs-check``/CI runs it without a venv.

The three trees and what counts as a UI file in each (kept identical to the
tables in GLASS_INVENTORY.md — change one, change the other):

* **web** — every ``*.svelte`` under ``aura-web/src`` (routes, sections,
  components, ui, hud, dashboards). ``.ts`` co-modules (panelData.ts, layout.ts)
  are logic, not a rendered surface, and are out of scope by the brief's own
  wording ("each Svelte section/component/ui/hud file, each route").
* **desktop** — the desktop app's windows and views (Tauri, ADR-0051):
  ``aura-desktop-app/src-tauri/src/*.rs`` (the windows, tray, popup and their
  plumbing) and ``src-tauri/logic/src/*.rs`` (their decisions) and ``aura-desktop-app/ui/*.html`` / ``*.ts`` (its own pages: this
  computer, the consent prompt, the unreachable page),
  plus the Python device agent's modules that draw a window: any
  ``aura-desktop/aura_desktop/*.py`` that imports tkinter (the consent and
  companion dialogs) and ``theme.py`` that styles them. The shell otherwise loads
  the web console, so it has no component tree of its own. The agent is headless
  except for those dialogs, which is exactly why they are caught: a grey tk
  dialog is the easiest old surface to forget.
* **android** — a ``*.kt`` under ``android-app/app/src/main/kotlin`` is a UI file
  when it lives in the ``ui``, ``surface`` or ``car`` package, OR its name ends
  ``Activity.kt``, OR it renders something (a Compose function, a classic
  ``View``/``Drawable``/overlay, a dialog, a Car screen, a notification, or a
  quick-settings tile — the notification and the tile are HUD surfaces too).
  This deliberately catches the notification builders, the tile and the overlay
  views that live in feature packages, so the "notifications" and overlay
  surfaces are inventoried, not just the activities.

Run: ``python3 scripts/verify/glass-inventory.py`` (add ``--list`` to print the
collected set without checking, e.g. while editing the inventory).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# Repo root = two levels up from scripts/verify/glass-inventory.py, so the gate
# runs the same whether CI invokes it from the root or a hook from elsewhere.
ROOT = Path(__file__).resolve().parents[2]
INVENTORY = ROOT / "docs" / "design" / "GLASS_INVENTORY.md"

# A Kotlin file that renders a surface without living in a ui/surface/car
# package or being named *Activity.kt — a Compose function, a classic View or
# Drawable, a WindowManager overlay, an AlertDialog, a Car screen, a
# notification (RemoteViews / Notification.Builder) or a quick-settings tile
# (TileService: its icon and label are a surface). The markers are literal
# enough that a pure data/protocol/service file (HudState, HermesRpc,
# WakeStartPolicy) does not match, which is why the Android set stays the UI
# layer and not the whole app. Mirrored by the Android tables in the inventory.
_ANDROID_UI_MARKER = re.compile(
    r"@Composable"
    r"|import androidx\.compose"
    r"|import android\.widget\."
    r"|import android\.view\.(?:View|SurfaceView|TextureView|WindowManager)"
    r"|SurfaceView|TextureView|GLSurfaceView"
    r"|import android\.graphics\.drawable"
    r"|AlertDialog|RemoteViews"
    r"|NotificationCompat\.Builder|Notification\.Builder"
    r"|CarAppService|androidx\.car\.app"
    r"|TileService"
)

# The Python device agent draws exactly two windows (consent, companion ask),
# both through tkinter, and styles them from theme.py. Anything else in the
# package is protocol, policy or telemetry with no surface.
_DESKTOP_AGENT_UI_MARKER = re.compile(r"^\s*import tkinter\b|^\s*from tkinter\b", re.M)
_DESKTOP_AGENT_ALWAYS = {"theme.py"}


def _rel(path: Path) -> str:
    """Repo-relative POSIX path — the exact string the inventory must contain."""
    return path.relative_to(ROOT).as_posix()


def _web_files() -> list[str]:
    root = ROOT / "aura-web" / "src"
    # Every Svelte file under src is a section/component/ui/hud file or a route;
    # the brief scopes the web tree to exactly those.
    return [_rel(p) for p in root.rglob("*.svelte")]


def _desktop_files() -> list[str]:
    app = ROOT / "aura-desktop-app"
    files: list[Path] = []
    for rust in (app / "src-tauri" / "src", app / "src-tauri" / "logic" / "src"):
        if rust.is_dir():
            files += sorted(p for p in rust.glob("*.rs") if p.name != "lib.rs")
    pages = app / "ui"
    if pages.is_dir():
        files += sorted(pages.glob("*.html")) + sorted(pages.glob("*.ts"))
    agent = ROOT / "aura-desktop" / "aura_desktop"
    if agent.is_dir():
        for p in sorted(agent.glob("*.py")):
            if p.name in _DESKTOP_AGENT_ALWAYS:
                files.append(p)
                continue
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if _DESKTOP_AGENT_UI_MARKER.search(text):
                files.append(p)
    return [_rel(p) for p in files]


def _android_files() -> list[str]:
    root = ROOT / "android-app" / "app" / "src" / "main" / "kotlin"
    out: list[str] = []
    for p in root.rglob("*.kt"):
        rel = _rel(p)
        # Package-based: the ui/surface/car packages are wholly the view layer.
        # Slash-guarded so the segment is matched, never a substring of a name.
        if "/ui/" in rel or "/surface/" in rel or "/car/" in rel:
            out.append(rel)
            continue
        # Every Activity is a screen, wherever it lives.
        if p.name.endswith("Activity.kt"):
            out.append(rel)
            continue
        # Otherwise, only if the file actually renders something.
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if _ANDROID_UI_MARKER.search(text):
            out.append(rel)
    return out


def collect() -> list[str]:
    """The full UI-file set across the three trees, sorted and de-duplicated."""
    files = set(_web_files()) | set(_desktop_files()) | set(_android_files())
    return sorted(files)


def main(argv: list[str]) -> int:
    files = collect()

    if "--list" in argv:
        for f in files:
            print(f)
        print(f"# {len(files)} UI files across the three trees", file=sys.stderr)
        return 0

    if not INVENTORY.is_file():
        print(f"glass-inventory: {_rel(INVENTORY)} does not exist", file=sys.stderr)
        return 2

    text = INVENTORY.read_text(encoding="utf-8", errors="replace")
    # Membership is by full repo-relative path, not basename: several basenames
    # collide across trees (PanelCard.svelte / PanelCard.kt, PanelSurface.*,
    # SectionStrip.*), and a path is unambiguous.
    missing = [f for f in files if f not in text]

    if missing:
        print(
            f"glass-inventory: {len(missing)} UI file(s) not mentioned in "
            f"{_rel(INVENTORY)} — give each a panel/component and a disposition:",
            file=sys.stderr,
        )
        for f in missing:
            print(f"  {f}", file=sys.stderr)
        return 1

    print(
        f"glass-inventory: OK — all {len(files)} UI files "
        f"(web + desktop + android) are mapped in {_rel(INVENTORY)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
