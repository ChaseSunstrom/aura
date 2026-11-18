#!/usr/bin/env python3
"""Executable spec: the phone's settings are one native screen, and no console.

The owner, 2026-09-25: "the console displays in the android app and the
tabs/settings/everything is weird, it should only be the phone with the phone
settings, with all of the console settings". Until then the phone showed the
console itself in a WebView (`ManagementActivity`) behind a strip of the
console's own section names. It replaced `console_parity_test.py`, which held
the phone to that console nav.

What is pinned, from the source:

  * no WebView in the shipping app but the console's own HUD page (ConsolePage),
    which the car display (CarHud's /car, whose bridge must write what the page
    reads) and the phone's home screen host -- the settings stay native;
  * the phone's connection and Jarvis's short list are one Compose screen
    (T-0689): the old Jarvis settings activity is a manifest alias of it, so a
    shortcut or notification that names it still lands somewhere;
  * Jarvis's settings are drawn from the schema's contract, which the build
    copies from `tests/contracts/` -- one description of a setting, not two --
    and written through the settings plugin, applying its proposal on the tap;
  * what only the console can show is a link to the console's page, not a copy.

Run:  python3 android-app/tools/settings_screens_test.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ANDROID = Path(__file__).resolve().parents[1]
KOTLIN = ANDROID / "app/src/main/kotlin/ai/aura/app"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


#: The one web view the app keeps: the console's HUD page, which the car display hosts
#: at /car (2026-09-25, "Console's HUD on the car") and the phone's home screen at /
#: (2026-09-27: "the mobile app UI ... doesn't really match the look/feel of the
#: console"). CarHud keeps its bridge; the phone's settings screens stay native.
CAR_HUD = "car/dash/CarHud.kt"
CONSOLE_PAGE = "ui/ConsolePage.kt"


def test_there_is_no_webview_in_the_app() -> None:
    offenders = [
        str(p.relative_to(KOTLIN))
        for p in KOTLIN.rglob("*.kt")
        # An import or a construction, not the word in a comment about what went.
        if re.search(r"import android\.webkit\.|\bWebView\((?:this|context|ctx|activity|app)\b", read(p))
        and str(p.relative_to(KOTLIN)) not in (CAR_HUD, CONSOLE_PAGE)
    ]
    assert not offenders, f"a WebView is back in the shipping app: {offenders}"
    assert not (KOTLIN / "ManagementActivity.kt").exists(), "the console WebView activity is back"


def test_the_settings_are_one_screen_and_the_old_name_still_opens_it() -> None:
    # The console frame's tab strip is gone (T-0639), and the second screen with it (T-0689).
    assert not (KOTLIN / "ui/ConsoleTab.kt").exists(), "the console frame's tab strip is back"
    assert not (KOTLIN / "JarvisSettingsActivity.kt").exists(), "the second settings screen is back"
    manifest = read(ANDROID / "app/src/main/AndroidManifest.xml")
    alias = re.search(r"<activity-alias\b[^>]*?android:name=\"ai\.jarvis\.app\.JarvisSettingsActivity\"[^>]*>", manifest, re.S)
    assert alias, "an intent naming the old Jarvis settings screen no longer resolves"
    assert 'android:targetActivity=".SettingsActivity"' in alias.group(0), "the old name opens something else"
    assert 'android:exported="false"' in alias.group(0), "the alias exports what the screen never did"
    assert manifest.index('android:name=".SettingsActivity"') < alias.start(), (
        "an activity-alias must follow its target in the manifest"
    )
    settings = read(KOTLIN / "SettingsActivity.kt")
    assert "setContent" in settings and "import android.widget." not in settings, "the screen is Views again"
    console = read(KOTLIN / CONSOLE_PAGE)
    assert '"phone" -> ai.aura.app.SettingsActivity::class.java' in console, (
        "the console's Settings → This phone does not open the native screen"
    )


def test_the_contract_is_bundled_not_copied_by_hand() -> None:
    gradle = read(ANDROID / "app/build.gradle.kts")
    assert 'from(rootProject.file("../tests/contracts/hermes-settings.json"))' in gradle, (
        "the build no longer copies the settings contract into the app's assets"
    )
    assert 'tasks.named("preBuild") { dependsOn(copySettingsContract) }' in gradle
    assert not list((ANDROID / "app/src/main/assets").rglob("hermes-settings.json")), (
        "a hand copy of the contract is checked in; it would drift from the schema"
    )


def test_a_write_applies_its_proposal_on_the_same_tap() -> None:
    screen = read(KOTLIN / "SettingsActivity.kt")
    assert 'hermesCommand("set ${spec.key} $json")' in screen
    assert 'hermesCommand("apply ${written.id}")' in screen, (
        "a proposed write is left waiting: the tap that made it is the person's yes"
    )


#: The v2 brief's short list (docs/design/v2/PROMPT.md: "account and sign-in, people and
#: voices, the persona and voice, privacy and approvals, and devices. Everything else is
#: automatic or learned."). The phone shows the two it can set itself; the rest are links.
SHORT_LIST_KEYS = {"approvals.mode"}


def test_the_phone_shows_only_the_short_list_and_links_the_rest() -> None:
    src = read(KOTLIN / "SettingsActivity.kt")
    shown = set(re.findall(r'^\s*private val SHORT_LIST = listOf\(([^)]*)\)', src, re.M))
    keys = set(re.findall(r'"([a-z_]+\.[a-z_.]+)"', " ".join(shown)))
    assert keys == SHORT_LIST_KEYS, f"the phone's Jarvis settings drifted from the short list: {sorted(keys)}"
    assert "PERSONA_COMMAND" in src and "dispatchCommand(PERSONA_COMMAND" in src, "the persona is not chosen here"
    # A row only the console can show opens the console at that page, through the unsaved check.
    for path in ("/settings", "/devices"):
        assert f'openConsole("{path}")' in src, f"no link to the console's {path}"
    link = re.search(r"private fun openConsole\(.*?\n    \}", src, re.S)
    assert link and "leaveIfSaved" in link.group(0) and "MainActivity.EXTRA_CONSOLE_PATH" in link.group(0)
    main = read(KOTLIN / "MainActivity.kt")
    assert "const val EXTRA_CONSOLE_PATH" in main and "override fun onNewIntent" in main, (
        "the console does not open at the page the settings link names"
    )
    routes = read(ANDROID.parent / "aura-web/src/lib/ds/shell/routes.ts")
    for path in ("/settings", "/devices"):
        assert f"path: '{path}'" in routes, f"the console has no {path} screen to link to"


def test_an_approval_on_the_overlay_never_passes_the_lock_screen() -> None:
    """The wake overlay answers an approval in place (2026-09-25: "I can't click it
    without closing Jarvis"), but only while the phone is unlocked: over the
    keyguard the full-screen consent door, which gates on it, still decides."""
    wake = read(KOTLIN / "assist/WakeWordService.kt")
    block = wake[wake.index("override fun onApproval("):]
    block = block[: block.index("return true")]
    assert "isKeyguardLocked != false) return false" in block, (
        "the overlay answers approvals without first refusing a locked phone"
    )
    hermes = read(KOTLIN / "assist/hermes/HermesConversation.kt")
    assert "ui.onApproval(approval)" in hermes and "if (inline) return" in hermes, (
        "an approval no longer reaches the surface before the consent door"
    )


def test_the_settings_headings_are_headings_for_a_listener() -> None:
    """The section strip went with T-0639; TalkBack's heading navigation is how a
    listener jumps between the sections it used to jump to."""
    src = read(KOTLIN / "SettingsActivity.kt")
    # Each section is a kit Fold (T-0639), whose header is the heading.
    assert "Fold(title, summary" in src
    fold = re.search(r"\nfun Fold\(.*?\n\}", read(KOTLIN / "ui/v2/V2.kt"), re.S)
    assert fold and "heading()" in fold.group(0)
    assert "SectionStrip" not in src


def test_the_car_bridge_writes_what_the_page_reads() -> None:
    """CarHud's JSON and `car.svelte.ts` `readCarState` are one shape: every
    top-level key the page reads is one the phone writes, and every action the
    page sends is one the phone performs."""
    hud = read(KOTLIN / CAR_HUD)
    page = read(ANDROID.parent / "aura-web/src/lib/hud/car.svelte.ts")
    read_keys = set(re.findall(r"\bo\.(\w+)", page))
    written = set(re.findall(r'put\("(\w+)"', hud[hud.index("internal fun state("):]))
    assert read_keys and read_keys <= written, f"the page reads {sorted(read_keys - written)} the phone never writes"
    acts = set(re.findall(r"kind: '(\w+)'", page))
    performed = set(re.findall(r'^\s+"(\w+)" ->', hud[hud.index("private fun perform("):], re.M))
    assert acts and acts <= performed, f"the page asks for {sorted(acts - performed)} the phone ignores"
    assert 'addJavascriptInterface(Bridge(), "JarvisCar")' in hud


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
