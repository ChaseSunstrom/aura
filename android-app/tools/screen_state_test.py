#!/usr/bin/env python3
"""Executable spec: the phone settings screen has an answer for leaving mid-edit.

## 1. Settings threw away edits on the way out

`SettingsActivity.save()` ran from exactly one place: the SAVE pill at the
bottom of a ScrollView several screens long. The console tab strip sits across
the TOP of that same screen and went straight to `startActivity`. So editing the
server URL and then tapping a tab — the most natural thing to do on a screen
with a nav bar over it — discarded the edit, silently, with no warning and no
way back. Back did the same.

Eleven controls across four types, and none of them told anybody. The fix is one
snapshot compared against another, rather than a dirty flag on each control:
eleven flags is eleven places to forget one, and a control missing from a
snapshot is a visible omission where a missing flag is not.

The dialog's SAVE runs the same validation the pill does and only leaves if it
passed, so leaving cannot store an invalid URL by the side door.

Since T-0689 the Hermes settings live on this same screen, and the ways out are
the two console links; both go through the same question as Back.

"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ANDROID = Path(__file__).resolve().parents[1]
KOTLIN = ANDROID / "app/src/main/kotlin/ai/jarvis/app"

SETTINGS = KOTLIN / "SettingsActivity.kt"
CONFIG = KOTLIN / "config/JarvisConfig.kt"


def code(path: Path) -> str:
    src = path.read_text(encoding="utf-8")
    src = re.sub(r"/\*.*?\*/", " ", src, flags=re.S)
    return re.sub(r"//[^\n]*", " ", src)


# ---------------------------------------------------------------------------
# 1. unsaved settings
# ---------------------------------------------------------------------------


def test_every_way_off_the_settings_screen_goes_through_the_dirty_check() -> None:
    """The two exits, and both of them used to discard silently."""
    src = code(SETTINGS)
    assert "private fun leaveIfSaved(" in src, (
        "the settings screen has no dirty check, so tapping a console tab or "
        "pressing Back discards every edited field"
    )
    # The tab strip went with T-0639 and the Jarvis settings button with T-0689; the ways
    # out now are the console links, which leave this screen for the console's page.
    console = re.search(r"private fun openConsole\(.*?\n    \}", src, re.S)
    assert console, "the settings screen has lost its way to the console"
    assert "leaveIfSaved" in console.group(0), (
        "a console link goes straight to startActivity, which is the reported "
        "disappearing-settings bug exactly"
    )
    # A dispatcher callback since T-0788 (lint fails an onBackPressed override without super).
    back = re.search(r"override fun handleOnBackPressed\(\)[^\n]*", src)
    assert back, "the settings screen does not handle Back"
    assert "leaveIfSaved" in back.group(0), (
        "Back leaves without asking, so the other half of the bug is still there"
    )


def test_the_dirty_check_compares_every_control() -> None:
    """A snapshot with a control missing is a control whose edits are still
    silently discarded — the same bug, narrower."""
    src = code(SETTINGS)
    saved = re.search(r"private fun savedSnapshot\(\): String = listOf\((.*?)\n    \)", src, re.S)
    edited = re.search(r"private fun editedSnapshot\(\): String = listOf\((.*?)\n    \)", src, re.S)
    assert saved and edited, "the settings screen has no snapshot pair"
    # The controls the screen declares, from its own fields.
    controls = set(re.findall(r"private lateinit var (\w+): (?:Typed|Checked)\b", src))
    assert len(controls) >= 20, "the control pattern no longer finds the screen's controls"
    # Fields the save path writes are the population that matters.
    save = re.search(r"private fun save\(\): Boolean \{.*?\n    \}", src, re.S)
    assert save, "the settings screen has no save()"
    written = set(re.findall(r"config\.(\w+)\s*=", save.group(0)))
    missing_saved = sorted(n for n in written if n not in saved.group(1))
    assert not missing_saved, (
        "save() stores these and the dirty check does not compare them, so "
        "editing one and leaving discards it with no warning: "
        + ", ".join(missing_saved)
    )
    missing_edited = sorted(
        c for c in controls if c not in edited.group(1) and c != "urlField"
    )
    # urlField is compared through ServerUrl.normalize rather than by name.
    assert not missing_edited or "ServerUrl.normalize(urlField" in edited.group(1), (
        "a control on this screen is not in the edited snapshot: "
        + ", ".join(missing_edited)
    )


def test_leaving_by_saving_only_leaves_if_the_save_worked() -> None:
    """`save()` refuses an invalid URL and an empty token. If SAVE-then-leave
    left anyway, the side door would store nothing and the screen would still
    close — which is the original bug with an extra dialog."""
    src = code(SETTINGS)
    assert re.search(r"private fun save\(\): Boolean", src), (
        "save() no longer reports whether it stored anything, so the dirty "
        "check cannot tell a refused save from a successful one"
    )
    save = re.search(r"private fun save\(\): Boolean \{.*?\n    \}", src, re.S).group(0)
    assert save.count("return false") >= 2, (
        "save() no longer refuses an invalid URL or an empty token"
    )
    assert "return true" in save, "save() never reports success"
    leave = re.search(r"private fun leaveIfSaved\(.*?\n    \}", src, re.S).group(0)
    assert "if (save()) go()" in leave, (
        "the dialog's SAVE leaves whether or not the save was accepted"
    )
    for label in ("Discard", "Keep editing"):
        assert label in leave, f"the dialog offers no {label}"


def test_the_normalisation_matches_what_save_will_store() -> None:
    """Otherwise the screen is 'dirty' the moment it opens.

    An empty pipeline box becomes `DEFAULT_PIPELINE` on save, and `warmLink` /
    `headsetButton` read `headsetMode` in their own getters. Comparing raw
    against stored would report a change nobody made and prompt on every exit,
    which trains the user to tap DISCARD.
    """
    src = code(SETTINGS)
    edited = re.search(
        r"private fun editedSnapshot\(\): String = listOf\((.*?)\n    \)", src, re.S
    ).group(1)
    assert "DEFAULT_PIPELINE" in edited, (
        "an empty pipeline box reads as an edit against the default it is "
        "about to become"
    )
    assert "headsetMode.isChecked && headsetButton.isChecked" in edited, (
        "the two switches that read headsetMode in their getters are compared "
        "as if they did not, so the screen is dirty on open whenever headset "
        "mode is off"
    )
    # And the getters really do behave that way, or the mirroring above is
    # compensating for something that is no longer true.
    config = code(CONFIG)
    assert re.search(r"var warmLink[\s\S]{0,200}headsetMode &&", config), (
        "JarvisConfig.warmLink no longer reads headsetMode, so the dirty "
        "check is mirroring a rule that has gone"
    )


def main() -> int:
    tests = [
        (name, fn)
        for name, fn in sorted(globals().items())
        if name.startswith("test_") and callable(fn)
    ]
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
