#!/usr/bin/env python3
"""The phone app's code moved to ai.aura.app (T-1531); what the system knows it by did not.

An installed app is updated in place only while its applicationId stays the same: a new id is
a new app beside the old one, with no pairing, no data and no updates. And the system keeps
names of its own past an update: the launcher pins an icon by the alias's component name,
PersonaIcon's enabled-state is stored against it, an intent naming the old settings screen
must still resolve, and WorkManager keeps the worker's class name in its database. These
checks pin each one, so a later "finish the rename" cannot quietly turn the update into a
reinstall.

Not covered: what the system grants by component name (the assistant role, the notification
listener, accessibility, call screening, the quick tile, widgets) follows the class to its
new name and is lost once on this update; the owner re-grants it. That is a one-time cost
the release notes name, not something a check here can hold.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ANDROID = Path(__file__).resolve().parent.parent
APP = ANDROID / "app"
GRADLE = (APP / "build.gradle.kts").read_text(encoding="utf-8")
MANIFEST = (APP / "src/main/AndroidManifest.xml").read_text(encoding="utf-8")
INSTALL_ID = "ai.jarvis.app"
CODE_PACKAGE = "ai.aura.app"


def test_the_install_identity_is_unchanged() -> None:
    ids = re.findall(r'applicationId\s*=\s*"([^"]+)"', GRADLE)
    assert ids == [INSTALL_ID], f"applicationId is {ids}: a new id installs a second app"
    assert "applicationIdSuffix" not in GRADLE, "a suffix changes the installed package too"


def test_the_code_lives_in_the_new_package() -> None:
    assert re.search(rf'namespace\s*=\s*"{re.escape(CODE_PACKAGE)}"', GRADLE), "namespace moved"
    for src in ("main", "test", "androidTest", "debug"):
        old = APP / "src" / src / "kotlin/ai/jarvis"
        # Files, not the directory: a checkout that predates the move keeps it empty.
        back = [p for p in old.rglob("*") if p.is_file()] if old.exists() else []
        assert not back, f"{old.relative_to(ANDROID)} is back: {back[:3]}"
    stray = [
        p.relative_to(ANDROID)
        for p in (APP / "src").rglob("*.kt")
        if re.search(rb"(?m)^package\s+ai\.jarvis\.app\b", p.read_bytes())
    ]
    assert not stray, f"files still declare package ai.jarvis.app: {stray[:5]}"


def test_the_launcher_aliases_keep_their_component_names() -> None:
    # A relative name would resolve against the namespace and become ai.aura.app.LauncherAura:
    # a new component, so the launcher drops the pinned icon and the enabled-state is lost.
    for alias in ("LauncherAura", "LauncherJarvis", "JarvisSettingsActivity"):
        assert f'android:name="{INSTALL_ID}.{alias}"' in MANIFEST, f"{alias} lost its old name"
        assert f'android:name=".{alias}"' not in MANIFEST, f"{alias} is relative again"
    icon = (APP / "src/main/kotlin/ai/aura/app/ui/theme/PersonaIcon.kt").read_text(encoding="utf-8")
    assert f'"{INSTALL_ID}.$' in icon, "PersonaIcon no longer toggles the aliases by their old names"


def test_periodic_work_moves_to_the_renamed_worker() -> None:
    # KEEP left the row WorkManager stored before the update, naming
    # ai.jarvis.app.update.UpdateWorker, a class the app no longer has: the hourly update
    # check would never run again. UPDATE keeps the period and re-points it.
    for rel in ("update/UpdateWorker.kt", "car/holo/MapPackSync.kt"):
        src = (APP / "src/main/kotlin/ai/aura/app" / rel).read_text(encoding="utf-8")
        assert "ExistingPeriodicWorkPolicy.UPDATE" in src, f"{rel} does not move its old work"
        assert "ExistingPeriodicWorkPolicy.KEEP" not in src, f"{rel} keeps work naming a gone class"


def test_the_self_check_still_knows_the_installed_package() -> None:
    deny = (APP / "src/main/kotlin/ai/aura/app/automation/accessibility/PackageDenylist.kt").read_text(
        encoding="utf-8"
    )
    # It compares against the package the system reports for a window, which is the install id.
    assert f'const val SELF_PACKAGE = "{INSTALL_ID}"' in deny, "the app could drive its own UI"


def main() -> int:
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    failures = 0
    for name, fn in tests:
        try:
            fn()
        except Exception as exc:  # a broken check is a failure, not an abort
            failures += 1
            print(f"FAIL {name}: {type(exc).__name__}: {exc}")
        else:
            print(f"ok   {name}")
    print(f"\n{len(tests) - failures}/{len(tests)} checks passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
