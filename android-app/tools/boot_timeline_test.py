#!/usr/bin/env python3
"""Executable spec for the GrapheneOS checklist and the app's startup plumbing.

`app/src/main/kotlin/ai/aura/app/compat/GrapheneCompat.kt` — the network
verdict and the requirements checklist — is Kotlin this container cannot
compile, written down a second time here where it can actually run. Every
requirement must map to a deep link and to a satisfied/unsatisfied verdict, and
the network verdict must never be optimistic when the OS has said no.

It is mirrored below and then checked against the Kotlin source, so a change to
one copy that is not made to the other fails here rather than on a phone. (The
glass boot that gave this file its name went with the native home, T-0667.)

Run:  python3 android-app/tools/boot_timeline_test.py
"""

from __future__ import annotations

import json
import re
import sys
from itertools import product
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
#: The phone's core; the pre-v2 Reactor composable it replaced is gone (T-0779).
KOTLIN_REACTOR_COMPOSABLE = ROOT / "app/src/main/kotlin/ai/aura/app/reactor/core/V2Core.kt"
KOTLIN_COMPAT = ROOT / "app/src/main/kotlin/ai/aura/app/compat/GrapheneCompat.kt"
KOTLIN_CRASH = ROOT / "app/src/main/kotlin/ai/aura/app/crash/JarvisCrashHandler.kt"
KOTLIN_APP = ROOT / "app/src/main/kotlin/ai/aura/app/JarvisApp.kt"
KOTLIN_MAIN = ROOT / "app/src/main/kotlin/ai/aura/app/MainActivity.kt"
KOTLIN_CONFIG = ROOT / "app/src/main/kotlin/ai/aura/app/config/JarvisConfig.kt"
KOTLIN_CHANNEL = ROOT / "app/src/main/kotlin/ai/aura/app/channel/JarvisChannel.kt"
KOTLIN_CRASH_UI = ROOT / "app/src/main/kotlin/ai/aura/app/ui/CrashLogActivity.kt"
KOTLIN_SETTINGS = ROOT / "app/src/main/kotlin/ai/aura/app/SettingsActivity.kt"
#: Debug source set, so it is outside KOTLIN_SRC_ROOT and the structural checks
#: below never see it.
KOTLIN_TEST_HOOKS = ROOT / "app/src/debug/kotlin/ai/aura/app/testing/TestHooks.kt"
MANIFEST = ROOT / "app/src/main/AndroidManifest.xml"
THEMES = ROOT / "app/src/main/res/values/themes.xml"
THEMES_V31 = ROOT / "app/src/main/res/values-v31/themes.xml"
ADAPTIVE_ICON = ROOT / "app/src/main/res/mipmap-anydpi-v26/ic_jarvis.xml"
KOTLIN_SRC_ROOT = ROOT / "app/src/main/kotlin"

#: Every Kotlin file this spec reads back by name. The structural checks at the
#: bottom run over the WHOLE source tree instead — there is no kotlinc in this
#: container, so a repo-wide stand-in for the compiler is worth more than one
#: scoped to the files this feature happened to touch.
TRACKED_KOTLIN = [
    KOTLIN_REACTOR_COMPOSABLE,
    KOTLIN_COMPAT,
    KOTLIN_CRASH,
    KOTLIN_CRASH_UI,
    KOTLIN_APP,
    KOTLIN_MAIN,
    KOTLIN_CONFIG,
    KOTLIN_CHANNEL,
    KOTLIN_TEST_HOOKS,
    ROOT / "app/src/main/kotlin/ai/aura/app/ui/SystemCheckActivity.kt",
]

# =========================================================================
# 2. GrapheneCompat, mirrored from GrapheneCompat.kt
# =========================================================================

SUSPECT_THRESHOLD = 3

SIG_SECURITY = "SECURITY"
SIG_HOST = "HOST"
SIG_OTHER = "OTHER"

V_GRANTED = "GRANTED"
V_DENIED = "DENIED"
V_SUSPECT = "SUSPECT"


def classify(exception_class_names) -> str:
    for name in exception_class_names:
        if name == "java.lang.SecurityException":
            return SIG_SECURITY
    for name in exception_class_names:
        if name == "java.net.UnknownHostException":
            return SIG_HOST
    return SIG_OTHER


def network_verdict(permission_granted, security_denials, host_failures, successes):
    if not permission_granted:
        return V_DENIED
    if security_denials > 0:
        return V_DENIED
    if successes > 0:
        return V_GRANTED
    if host_failures >= SUSPECT_THRESHOLD:
        return V_SUSPECT
    return V_GRANTED


# id -> (essential, Settings constant name, needs package: uri)
REQUIREMENT_TABLE = [
    ("network", True, "ACTION_APPLICATION_DETAILS_SETTINGS", True),
    ("microphone", True, "ACTION_APPLICATION_DETAILS_SETTINGS", True),
    ("assistant", False, "ACTION_VOICE_INPUT_SETTINGS", False),
    ("accessibility", False, "ACTION_ACCESSIBILITY_SETTINGS", False),
    ("notifications", False, "ACTION_NOTIFICATION_LISTENER_SETTINGS", False),
    ("battery", True, "ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS", True),
    ("post_notifications", True, "ACTION_APPLICATION_DETAILS_SETTINGS", True),
    ("on_screen", True, "ACTION_MANAGE_OVERLAY_PERMISSION", True),
    ("full_screen", False, "ACTION_MANAGE_APP_USE_FULL_SCREEN_INTENT", True),
    ("overlay", False, "ACTION_MANAGE_OVERLAY_PERMISSION", True),
    ("exact_alarms", False, "ACTION_REQUEST_SCHEDULE_EXACT_ALARM", True),
    # What Jarvis may DO, as opposed to whether it runs at all. Every one of
    # these was declared in the manifest, never requested, and therefore denied
    # on every device — while this checklist said "Everything is granted",
    # because it did not list them. See tools/runtime_permissions_test.py.
    #
    # All optional: a phone where Jarvis never touches your messages is a
    # working phone. The Settings action is the app details page for all five,
    # because that is where Android puts per-app permissions — but the tap on
    # the row asks for them directly and only falls through to Settings when
    # there is nothing left to ask.
    ("people", False, "ACTION_APPLICATION_DETAILS_SETTINGS", True),
    ("calendar", False, "ACTION_APPLICATION_DETAILS_SETTINGS", True),
    ("location", False, "ACTION_APPLICATION_DETAILS_SETTINGS", True),
    ("media", False, "ACTION_APPLICATION_DETAILS_SETTINGS", True),
    ("sensors", False, "ACTION_APPLICATION_DETAILS_SETTINGS", True),
]

# The actual intent action strings, so "maps to a deep link" is checked against
# a value and not just against a symbol that could be anything.
SETTINGS_ACTIONS = {
    "ACTION_APPLICATION_DETAILS_SETTINGS": "android.settings.APPLICATION_DETAILS_SETTINGS",
    "ACTION_VOICE_INPUT_SETTINGS": "android.settings.VOICE_INPUT_SETTINGS",
    "ACTION_ACCESSIBILITY_SETTINGS": "android.settings.ACCESSIBILITY_SETTINGS",
    "ACTION_NOTIFICATION_LISTENER_SETTINGS": "android.settings.ACTION_NOTIFICATION_LISTENER_SETTINGS",
    "ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS": "android.settings.REQUEST_IGNORE_BATTERY_OPTIMIZATIONS",
    "ACTION_MANAGE_OVERLAY_PERMISSION": "android.settings.action.MANAGE_OVERLAY_PERMISSION",
    "ACTION_MANAGE_APP_USE_FULL_SCREEN_INTENT": "android.settings.MANAGE_APP_USE_FULL_SCREEN_INTENT",
    "ACTION_REQUEST_SCHEDULE_EXACT_ALARM": "android.settings.REQUEST_SCHEDULE_EXACT_ALARM",
}

STATUS_FIELDS = [
    "network",
    "microphone",
    "assistant",
    "accessibility",
    "notificationListener",
    "batteryExempt",
    "batteryRestricted",
    "canDrawOverlays",
    "postNotifications",
    "fullScreenIntents",
    "exactAlarms",
    # One flag per RuntimePermissions group: true when every dangerous
    # permission in that group is held.
    "people",
    "calendar",
    "location",
    "media",
    "sensors",
]


def evaluate(status: dict):
    """Mirror of GrapheneCompat.evaluate: Status -> ordered requirement list."""
    satisfied = {
        "network": status["network"],
        "microphone": status["microphone"],
        "assistant": status["assistant"],
        "accessibility": status["accessibility"],
        "notifications": status["notificationListener"],
        # Exempt from doze AND not background-restricted. Either one alone
        # still gets the automation service killed.
        "battery": status["batteryExempt"] and not status["batteryRestricted"],
        # The other half of "does listening survive a reboot", and the whole
        # of "does the wake word draw an orb over the app you are in".
        "overlay": status["canDrawOverlays"],
        # Runtime since Android 13 and never requested until now, which is why a
        # wake word could not put anything on screen at all.
        "post_notifications": status["postNotifications"],
        # A DISJUNCTION, and the only one on the checklist. Either grant lets a
        # wake word put something in front of you; with neither, nothing was
        # both essential and missing, so no banner appeared and the phone sat
        # broken with nothing anywhere saying so.
        "on_screen": status["canDrawOverlays"] or status["fullScreenIntents"],
        # Android 14 grants this at install only to calling and alarm apps.
        # Without it setFullScreenIntent silently becomes a heads-up, and the
        # conversation waits in the shade for a tap.
        "full_screen": status["fullScreenIntents"],
        "exact_alarms": status["exactAlarms"],
        # Straight through: each is the AND of its group's grants, computed by
        # RuntimePermissions.groupHeld before evaluate() is ever called, so the
        # pure part has nothing to decide.
        "people": status["people"],
        "calendar": status["calendar"],
        "location": status["location"],
        "media": status["media"],
        "sensors": status["sensors"],
    }
    out = []
    for rid, essential, action_const, needs_uri in REQUIREMENT_TABLE:
        out.append(
            {
                "id": rid,
                "essential": essential,
                "satisfied": satisfied[rid],
                "action": SETTINGS_ACTIONS[action_const],
                "actionConst": action_const,
                "needsPackageUri": needs_uri,
            }
        )
    return out


# =========================================================================
# Helpers for reading the Kotlin back
# =========================================================================


def flat(path: Path) -> str:
    return re.sub(r"\s+", " ", path.read_text())


def code_only(path: Path) -> str:
    """Source with comments stripped.

    These checks are about what the code does, not about what the KDoc says.
    Without this, a doc comment that *mentions* `postDelayed` in order to say
    the design forbids it would fail the check that forbids it.
    """
    src = re.sub(r"/\*.*?\*/", " ", path.read_text(), flags=re.S)
    return re.sub(r"//[^\n]*", " ", src)


def first_statements(path: Path, signature: str) -> list[str]:
    """The statements of a function body, in order, comments and braces gone."""
    body = path.read_text().split(signature, 1)[1]
    body = body.split("\n    }", 1)[0]
    out = []
    for line in body.splitlines():
        line = line.strip()
        if not line or line.startswith("//") or line in ("{", "}"):
            continue
        out.append(line)
    return out


# =========================================================================
# Tests: GrapheneCompat network verdict
# =========================================================================


def test_revoked_permission_is_always_denied():
    for sec, host, ok in product((0, 1, 5), (0, 1, 9), (0, 1, 7)):
        assert network_verdict(False, sec, host, ok) == V_DENIED


def test_security_exception_denies_even_with_the_permission_granted():
    """This is the GrapheneOS case: INTERNET reads GRANTED, the OS says no."""
    assert network_verdict(True, 1, 0, 0) == V_DENIED


def test_one_success_outranks_mere_suspicion():
    assert network_verdict(True, 0, 99, 1) == V_GRANTED


def test_a_security_denial_outranks_an_earlier_success():
    """The Network toggle is revocable while the app is running.

    noteNetworkSuccess() clears the denial counters, so a non-zero
    securityDenials can only have been recorded AFTER the last success. Letting
    a stale success win would pin the verdict to GRANTED for the rest of the
    process the moment the user revoked Network mid-session, and the banner
    explaining the outage would never appear.
    """
    assert network_verdict(True, 1, 0, 1) == V_DENIED
    assert network_verdict(True, 1, 99, 40) == V_DENIED


def test_repeated_resolve_failures_become_suspicion_not_certainty():
    for n in range(SUSPECT_THRESHOLD):
        assert network_verdict(True, 0, n, 0) == V_GRANTED, (
            f"{n} failures should not be enough to accuse the user's settings"
        )
    assert network_verdict(True, 0, SUSPECT_THRESHOLD, 0) == V_SUSPECT
    assert network_verdict(True, 0, SUSPECT_THRESHOLD + 40, 0) == V_SUSPECT


def test_classify_reads_the_cause_chain():
    assert classify(["java.lang.SecurityException"]) == SIG_SECURITY
    assert classify(["java.net.UnknownHostException"]) == SIG_HOST
    assert classify(["java.net.ConnectException"]) == SIG_OTHER
    assert classify(["javax.net.ssl.SSLHandshakeException"]) == SIG_OTHER
    assert classify(["java.net.SocketTimeoutException"]) == SIG_OTHER
    assert classify([]) == SIG_OTHER
    # A SecurityException anywhere in the chain wins over a host failure.
    assert (
        classify(["java.io.IOException", "java.net.UnknownHostException",
                  "java.lang.SecurityException"])
        == SIG_SECURITY
    )


def test_a_server_that_is_merely_off_never_accuses_the_network_permission():
    """Connection refused / timeout must not produce a permission banner."""
    verdict = V_GRANTED
    sec = host = 0
    for _ in range(50):
        signal = classify(["java.net.ConnectException"])
        assert signal == SIG_OTHER
        verdict = network_verdict(True, sec, host, 0)
    assert verdict == V_GRANTED


# =========================================================================
# Tests: the requirements checklist
# =========================================================================


def all_status(value: bool) -> dict:
    return {f: value for f in STATUS_FIELDS}


def test_every_requirement_has_an_id_a_verdict_and_a_deep_link():
    reqs = evaluate(all_status(False))
    assert len(reqs) == len(REQUIREMENT_TABLE)
    seen = set()
    for r in reqs:
        assert r["id"], "requirement without an id"
        assert r["id"] not in seen, f"duplicate requirement id {r['id']}"
        seen.add(r["id"])
        assert isinstance(r["satisfied"], bool)
        assert r["action"].startswith("android.settings."), r["action"]
        assert isinstance(r["needsPackageUri"], bool)


def test_the_checklist_covers_exactly_the_documented_surface():
    ids = [r["id"] for r in evaluate(all_status(False))]
    assert ids == [
        "network",
        "microphone",
        "assistant",
        "accessibility",
        "notifications",
        "battery",
        "post_notifications",
        "on_screen",
        "full_screen",
        "overlay",
        "exact_alarms",
        "people",
        "calendar",
        "location",
        "media",
        "sensors",
    ], ids
    assert ids[0] == "network", "network is the most common GrapheneOS surprise; it goes first"
    # The action permissions come last on purpose: they decide what Jarvis may
    # DO, and there is no point granting any of them on a phone that has not
    # got past "does it run at all".
    assert ids[-5:] == ["people", "calendar", "location", "media", "sensors"]


def test_verdicts_follow_the_status_for_every_combination():
    for combo in product((False, True), repeat=len(STATUS_FIELDS)):
        status = dict(zip(STATUS_FIELDS, combo))
        by_id = {r["id"]: r for r in evaluate(status)}
        assert by_id["network"]["satisfied"] == status["network"]
        assert by_id["microphone"]["satisfied"] == status["microphone"]
        assert by_id["assistant"]["satisfied"] == status["assistant"]
        assert by_id["accessibility"]["satisfied"] == status["accessibility"]
        assert by_id["notifications"]["satisfied"] == status["notificationListener"]
        assert by_id["exact_alarms"]["satisfied"] == status["exactAlarms"]
        assert by_id["overlay"]["satisfied"] == status["canDrawOverlays"]
        assert by_id["post_notifications"]["satisfied"] == status["postNotifications"]
        assert by_id["on_screen"]["satisfied"] == (
            status["canDrawOverlays"] or status["fullScreenIntents"]
        )
        assert by_id["full_screen"]["satisfied"] == status["fullScreenIntents"]
        # Battery needs BOTH: exempt from doze and not background-restricted.
        assert by_id["battery"]["satisfied"] == (
            status["batteryExempt"] and not status["batteryRestricted"]
        )


def test_background_restriction_alone_fails_the_battery_check():
    status = all_status(True)  # batteryExempt True, batteryRestricted True
    by_id = {r["id"]: r for r in evaluate(status)}
    assert by_id["battery"]["satisfied"] is False, (
        "an app that is exempt from doze but background-restricted is still killed"
    )


def test_all_granted_leaves_nothing_unsatisfied():
    status = all_status(True)
    status["batteryRestricted"] = False
    reqs = evaluate(status)
    assert all(r["satisfied"] for r in reqs)
    assert [r for r in reqs if r["essential"] and not r["satisfied"]] == []


def test_nothing_granted_flags_every_essential():
    reqs = evaluate(all_status(False))
    missing = [r["id"] for r in reqs if r["essential"] and not r["satisfied"]]
    assert missing == [
        "network", "microphone", "battery", "post_notifications", "on_screen",
    ], missing


def test_essential_and_optional_are_split_the_way_the_docs_claim():
    reqs = {r["id"]: r["essential"] for r in evaluate(all_status(False))}
    assert reqs["network"] and reqs["microphone"] and reqs["battery"]
    # Essential: without it Jarvis cannot show the listening notification, the
    # wake-word alert, or a Tier-3 approval — and an approval that cannot be
    # delivered times out and is denied.
    assert reqs["post_notifications"]
    assert reqs["on_screen"]
    # NOT essential: "display over other apps" makes Jarvis draw the orb
    # directly, which needs no full-screen intent at all. Only one of the two
    # has to be granted, so neither can be the one that fails the checklist.
    assert not reqs["full_screen"]
    assert not reqs["overlay"]
    assert not reqs["assistant"]
    assert not reqs["accessibility"]
    assert not reqs["notifications"]
    assert not reqs["exact_alarms"]


def test_package_scoped_deep_links_are_the_ones_that_need_a_package():
    by_id = {r["id"]: r for r in evaluate(all_status(False))}
    # These screens are per-app and are useless without a package: URI.
    for rid in ("network", "microphone", "battery", "exact_alarms"):
        assert by_id[rid]["needsPackageUri"] is True, rid
    # These are global lists the user picks Jarvis out of.
    for rid in ("assistant", "accessibility", "notifications"):
        assert by_id[rid]["needsPackageUri"] is False, rid


# =========================================================================
# Tests: the Kotlin still says the same thing
# =========================================================================


def test_the_reactor_keeps_its_own_clock_and_stops_when_hidden():
    """A frozen reactor is what "the animation isn't looped" meant.

    The old orb View integrated everything it drew off a `ValueAnimator`,
    which ends on its first frame when the animator scale is 0 — developer
    options, or a battery saver — so it grew a `Choreographer` fallback and a
    debug hook to switch it off under instrumentation. The glass reactor runs
on Compose's frame clock (`withFrameNanos`), which the animator scale
    cannot reach, and GLASS_HUD §8.3 rule 2 says when it must NOT run: while
    the screen is not resumed, and under reduced motion (V2Core stands still
    there). Both are one `running` predicate, pinned here.
    """
    src = KOTLIN_REACTOR_COMPOSABLE.read_text()
    code = code_only(KOTLIN_REACTOR_COMPOSABLE)
    assert "withFrameNanos" in code, "the reactor has no clock of its own"
    assert "ValueAnimator" not in code and "Choreographer" not in code, (
        "the reactor must not fall back to the animator clock the scale can kill"
    )
    running = src.split("val running = ", 1)[1].split("\n\n", 1)[0]
    assert "lifecycle.isAtLeast(Lifecycle.State.RESUMED)" in running, (
        "the clock runs while the screen is not resumed (§8.3 rule 2)"
    )
    assert "!reduced" in running, "under reduced motion the clock must not run"
    assert "LaunchedEffect(running" in code, "the loop is not keyed on the running predicate"
    # The debug hook that held the old orb still is gone with the old orb; a
    # hook that names a class which no longer exists would be a compile error
    # in the debug source set that only CI sees.
    hooks = KOTLIN_TEST_HOOKS.read_text()
    assert "JarvisOrbView" not in hooks and "frameClockFallbackEnabled" not in hooks, (
        "TestHooks still reaches for the old orb's clock switch"
    )


def test_kotlin_compat_requirements_match_this_table():
    assert KOTLIN_COMPAT.is_file(), f"missing {KOTLIN_COMPAT}"
    src = KOTLIN_COMPAT.read_text()
    block = src.split("fun evaluate(status: Status): List<Requirement>", 1)[1]
    # Each Requirement(...) entry, in source order.
    entries = re.findall(
        r"id = (ID_[A-Z_]+),.*?essential = (true|false),\s*"
        r"settingsAction = ([^,]+),\s*needsPackageUri = (true|false)",
        block,
        re.S,
    )
    assert len(entries) == len(REQUIREMENT_TABLE), (
        f"GrapheneCompat has {len(entries)} requirements, this spec has {len(REQUIREMENT_TABLE)}"
    )
    id_consts = dict(re.findall(r'const val (ID_[A-Z_]+) = "([a-z_]+)"', src))
    # The permission-group ids are OWNED by RuntimePermissions — that is the
    # file that decides which grants share a checklist row — and re-exported
    # here rather than copied. Follow the alias, so the row and the permissions
    # behind it cannot drift apart without one of them failing.
    runtime = KOTLIN_COMPAT.parent / "RuntimePermissions.kt"
    assert runtime.is_file(), f"missing {runtime}"
    owned = dict(re.findall(r'const val (ID_[A-Z_]+) = "([a-z_]+)"', runtime.read_text()))
    for name, target in re.findall(
        r"const val (ID_[A-Z_]+) = RuntimePermissions\.(ID_[A-Z_]+)", src
    ):
        assert target in owned, f"GrapheneCompat.{name} aliases a missing {target}"
        id_consts[name] = owned[target]
    for (id_const, essential, action, needs_uri), (
        exp_id,
        exp_essential,
        exp_action_const,
        exp_needs_uri,
    ) in zip(entries, REQUIREMENT_TABLE):
        assert id_consts.get(id_const) == exp_id, (
            f"{id_const} is {id_consts.get(id_const)}, expected {exp_id}"
        )
        assert (essential == "true") == exp_essential, f"{exp_id} essential flag drifted"
        assert (needs_uri == "true") == exp_needs_uri, f"{exp_id} package-uri flag drifted"
        assert exp_action_const in action, (
            f"{exp_id} deep-links to {action.strip()}, expected {exp_action_const}"
        )


def test_kotlin_compat_still_encodes_the_verdict_rules():
    src = flat(KOTLIN_COMPAT)
    required = [
        "if (!permissionGranted) return NetworkVerdict.DENIED",
        "if (securityDenials > 0) return NetworkVerdict.DENIED",
        "if (successes > 0) return NetworkVerdict.GRANTED",
        "if (hostFailures >= SUSPECT_THRESHOLD) return NetworkVerdict.SUSPECT",
        'if (name == "java.lang.SecurityException") return Signal.SECURITY',
        'if (name == "java.net.UnknownHostException") return Signal.HOST',
        "satisfied = status.batteryExempt && !status.batteryRestricted",
    ]
    for needle in required:
        assert re.sub(r"\s+", " ", needle) in src, f"GrapheneCompat.kt no longer contains: {needle}"
    assert f"const val SUSPECT_THRESHOLD = {SUSPECT_THRESHOLD}" in src


def test_kotlin_compat_never_throws():
    """Every public probe has to swallow its own failures."""
    src = KOTLIN_COMPAT.read_text()
    # Split into function bodies that take a Context and touch the platform.
    for fn in (
        "isRestrictedBattery",
        "isIgnoringBatteryOptimizations",
        "hasPermission",
        "hasAssistantRole",
        "hasAccessibilityService",
        "hasNotificationListener",
        "canScheduleExactAlarms",
    ):
        assert f"fun {fn}(" in src, f"GrapheneCompat is missing {fn}"
        body = src.split(f"fun {fn}(", 1)[1][:900]
        assert "catch (t: Throwable)" in body, f"{fn} does not catch Throwable"
    # And every system service is fetched through the nullable overload.
    for call in re.findall(r"getSystemService\(([^)]*)\)", src):
        assert "::class.java" in call, f"unsafe getSystemService cast: {call}"
    assert "!!" not in src, "no !! on platform types in GrapheneCompat"


def test_crash_handler_installs_first_and_always_delegates():
    assert KOTLIN_CRASH.is_file(), f"missing {KOTLIN_CRASH}"
    src = KOTLIN_CRASH.read_text()
    flat_src = flat(KOTLIN_CRASH)
    assert "Thread.setDefaultUncaughtExceptionHandler(this)" in src
    assert "previous = Thread.getDefaultUncaughtExceptionHandler()" in src
    # Record first, delegate second, and the delegate runs in a finally.
    handler = src.split("override fun uncaughtException", 1)[1]
    assert "finally" in handler, "the previous handler must run in a finally block"
    assert "next.uncaughtException(thread, throwable)" in handler
    # Every recorded field the spec asks for.
    for field in ("ts", "thread", "exception", "stack", "app_version",
                  "android_version", "device"):
        assert f'"{field}"' in src, f"crash record is missing {field}"
    # Rotation, so the log cannot grow without bound.
    assert "MAX_RECORDS" in src and "MAX_FILE_BYTES" in src
    assert "crashes.jsonl" in src

    statements = first_statements(KOTLIN_APP, "override fun onCreate()")
    assert statements[0] == "JarvisCrashHandler.install(this)", (
        f"the crash handler must be the first statement in onCreate, not {statements[0]!r}"
    )
    assert statements[1] == "super.onCreate()", (
        "the handler goes in before super.onCreate, so a throw in startup is still caught"
    )
    assert flat_src.count("catch (t: Throwable)") >= 5, (
        "the crash handler must not be able to become the crash"
    )


def test_splash_screen_is_wired_with_no_white_flash():
    assert THEMES_V31.is_file(), f"missing {THEMES_V31}"
    themes = THEMES_V31.read_text()
    assert "windowSplashScreenBackground" in themes
    # The base splash is the default pack's ground (T-0885): the generated
    # theme_splash.xml colour, exactly that pack's bg.void, a near-black, so no
    # white flash; each pack's own splash is aura_theme_test.py's section 8.
    assert "@color/jv_theme_aura_ground" in themes, "the base splash must be the default pack's ground"
    splash = (ROOT / "app/src/main/res/values-v31/theme_splash.xml").read_text()
    ground = re.search(r"<color name=\"jv_theme_aura_ground\">#FF([0-9A-F]{6})</color>", splash)
    assert ground, "theme_splash.xml has no jv_theme_aura_ground"
    void = json.loads((ROOT.parent / "themes/aura/theme.json").read_text())["color"]["bg.void"]
    assert ground.group(1).lower() == void.lstrip("#").lower(), "the splash ground must be exactly the pack's void"
    assert max(int(ground.group(1)[i:i + 2], 16) for i in (0, 2, 4)) < 0x20, "the splash ground must be near-black"
    assert ADAPTIVE_ICON.is_file(), f"missing {ADAPTIVE_ICON}"
    icon = ADAPTIVE_ICON.read_text()
    assert "<background" in icon and "<foreground" in icon and "<monochrome" in icon
    mono = re.search(r"<monochrome android:drawable=\"@drawable/([a-z_]+)\"", icon)
    assert mono, "no monochrome drawable"
    assert mono.group(1) != "ic_jarvis_foreground", (
        "the monochrome layer must be its own alpha-only art; the launcher discards "
        "colour, so reusing the cyan foreground flattens into a blob"
    )
    mono_file = ROOT / f"app/src/main/res/drawable/{mono.group(1)}.xml"
    assert mono_file.is_file(), f"missing {mono_file}"
    assert "#3FD8FF" not in mono_file.read_text(), "the monochrome layer must not carry colour"
    status = ROOT / "app/src/main/res/drawable/ic_jarvis_status.xml"
    assert status.is_file(), "no notification icon"


def test_manifest_declares_the_new_screens_and_queries():
    assert MANIFEST.is_file()
    src = MANIFEST.read_text()
    for name in (".ui.SystemCheckActivity", ".ui.CrashLogActivity"):
        assert f'android:name="{name}"' in src, f"{name} is not in the manifest"
        block = src.split(f'android:name="{name}"', 1)[1][:400]
        assert 'android:exported="false"' in block, f"{name} must not be exported"
    assert "<queries>" in src, (
        "intent visibility must be declared with <queries>, not left to QUERY_ALL_PACKAGES"
    )


# =========================================================================
# Tests: the wiring the checklist depends on
# =========================================================================


def test_the_network_verdict_has_call_sites_on_the_wire():
    """SUSPECT is unreachable unless something reports what the wire did.

    Without these the verdict is only ever the permission check, which is the
    one signal GrapheneCompat's own KDoc says cannot be relied on.
    """
    channel = KOTLIN_CHANNEL.read_text()
    code = re.sub(r"/\*.*?\*/", " ", channel, flags=re.S)
    code = re.sub(r"//[^\n]*", " ", code)
    assert "GrapheneCompat.noteNetworkFailure(" in code, (
        "no failure call site: hostFailures can never reach SUSPECT_THRESHOLD"
    )
    assert "GrapheneCompat.noteNetworkSuccess()" in code, (
        "no success call site: suspicion would never clear once raised"
    )
    # The failure site has to be the socket's own failure callback.
    failure = code.split("override fun onFailure(", 1)[1][:900]
    assert "GrapheneCompat.note" in failure, "onFailure must fold into the verdict"


def test_crash_records_are_redacted_before_they_are_stored():
    """The crash screen's whole purpose is a COPY button.

    A stack trace out of OkHttp or a JSON parser routinely quotes the frame or
    the URL that failed, and channel/Redact.kt exists to keep the bearer token
    out of exactly that. It has to be applied on the way in, because the file is
    what the COPY button reads.
    """
    src = KOTLIN_CRASH.read_text()
    assert "import ai.aura.app.channel.Redact" in src
    record = src.split("internal fun record(", 1)[1].split("\n    }", 1)[0]
    assert "redact(safe { throwable.message }" in re.sub(r"\s+", " ", record), (
        "the exception message is stored verbatim"
    )
    assert "redact(sw.toString().take(CrashRecord.MAX_STACK_CHARS))" in re.sub(
        r"\s+", " ", record
    ), "the stack trace is stored verbatim"
    # The redactor itself must not be able to become the crash.
    helper = src.split("private fun redact(value: String)", 1)[1][:400]
    assert "catch (t: Throwable)" in helper

    ui = KOTLIN_CRASH_UI.read_text()
    assert "EXTRA_IS_SENSITIVE" in ui, (
        "a copied crash report must not be rendered in the system clipboard preview"
    )


def test_redact_masks_the_shapes_a_stack_trace_carries():
    """Mirror of channel/Redact.kt's two regex families, on real trace text."""
    src = (ROOT / "app/src/main/kotlin/ai/aura/app/channel/Redact.kt").read_text()
    keys = re.search(r"SECRET_KEYS = listOf\(([^)]*)\)", src).group(1)
    keys = re.findall(r'"([a-z_]+)"', keys)
    assert "token" in keys and "access_token" in keys and "authorization" in keys

    def redact(value: str) -> str:
        out = value
        for key in keys:
            out = re.sub(
                r'"%s"\s*:\s*"[^"]*"' % re.escape(key), '"%s":"[redacted]"' % key,
                out, flags=re.I,
            )
            out = re.sub(
                r"([?&])%s=[^&\s\"]*" % re.escape(key), r"\g<1>%s=[redacted]" % key,
                out, flags=re.I,
            )
        return out

    frame = 'java.io.IOException: sending {"type":"auth","access_token":"s3cr3t-value"}'
    assert "s3cr3t-value" not in redact(frame)
    url = "java.net.UnknownHostException: GET https://box.local/api?token=abc123&x=1"
    assert "abc123" not in redact(url)
    assert "&x=1" in redact(url), "redaction must not eat the rest of the query"
    plain = "java.lang.IllegalStateException: nothing secret here"
    assert redact(plain) == plain, "redaction must leave ordinary traces alone"


def test_the_splash_theme_extends_the_base_rather_than_restating_it():
    """A same-named style in a qualified folder REPLACES the unqualified one.

    Resource qualifiers select a winner, they never merge. values-v31 must
    therefore inherit the shared items, or every item added to values/themes.xml
    silently stops applying from API 31 up — which is nearly every device.
    """
    base = THEMES.read_text()
    assert '<style name="Theme.JarvisBase"' in base, "no shared base style to inherit from"
    assert '<style name="Theme.Jarvis" parent="@style/Theme.JarvisBase"' in base

    v31 = THEMES_V31.read_text()
    assert '<style name="Theme.Jarvis" parent="@style/Theme.JarvisBase">' in v31, (
        "values-v31 redeclares Theme.Jarvis without inheriting the base"
    )
    for item in ("android:windowBackground", "android:colorAccent", "android:statusBarColor"):
        assert item not in v31, (
            f"values-v31 restates {item}; it must inherit it, or the two drift"
        )


def test_every_notification_uses_this_app_s_own_status_icon():
    """A framework drawable in the status bar reads as some other app."""
    offenders = []
    for path in sorted(KOTLIN_SRC_ROOT.rglob("*.kt")):
        for match in re.finditer(r"setSmallIcon\(([^)]*)\)", path.read_text()):
            if "android.R.drawable" in match.group(1):
                offenders.append(f"{path.name}: {match.group(1).strip()}")
    assert not offenders, "framework notification icons: " + "; ".join(offenders)


def test_settings_reaches_the_diagnostics_screens():
    """The home banner only appears when something is already wrong.

    Someone who wants the checklist before that has to be able to find it.
    """
    src = flat(KOTLIN_SETTINGS)
    assert "ai.aura.app.ui.SystemCheckActivity::class.java" in src
    assert "ai.aura.app.ui.CrashLogActivity::class.java" in src


# =========================================================================
# Tests: structural checks over the Kotlin this spec tracks
#
# No kotlinc in this container, so these stand in for the compiler on the two
# things that are cheap to check and expensive to get wrong.
# =========================================================================


def _skip_string(src: str, i: int, code: list[str] | None = None) -> int:
    """Index just past the string whose body starts at ``i``.

    A ``${...}`` template can hold whole strings of its own
    (``"at ${if (a) "x" else "y"}"``) and run over lines. Toggling on every
    quote got the parity of such a line wrong and read the rest of the file as
    a string, so a real import looked unused (car/dash/CarShell.kt:386). The
    template's expression is code -- ``"${Build.MODEL}"`` uses ``Build`` -- so
    it is appended to ``code`` when one is given.
    """
    n = len(src)
    while i < n and src[i] != '"':
        if src[i] == "\\":
            i += 2
        elif src[i:i + 2] == "${":
            start = i + 2
            i = _skip_template(src, start)
            if code is not None:
                code.append(" " + strip_kotlin_literals(src[start:i - 1]) + " ")
        else:
            i += 1
    return i + 1


def _skip_template(src: str, i: int) -> int:
    """Index just past the ``}`` closing the template expression starting at ``i``."""
    n = len(src)
    depth = 1
    while i < n and depth:
        c = src[i]
        if src[i:i + 3] == '"""':
            end = src.find('"""', i + 3)
            i = n if end < 0 else end + 3
            continue
        if c == '"':
            i = _skip_string(src, i + 1)
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
        i += 1
    return i


def strip_kotlin_literals(src: str) -> str:
    """Source with comments, strings and char literals blanked out."""
    out = []
    i = 0
    n = len(src)
    while i < n:
        two = src[i:i + 2]
        if two == "//":
            i = src.find("\n", i)
            if i < 0:
                break
            continue
        if two == "/*":
            depth = 1  # Kotlin block comments nest.
            i += 2
            while i < n and depth:
                if src[i:i + 2] == "/*":
                    depth += 1
                    i += 2
                elif src[i:i + 2] == "*/":
                    depth -= 1
                    i += 2
                else:
                    i += 1
            continue
        if src[i:i + 3] == '"""':
            end = src.find('"""', i + 3)
            i = n if end < 0 else end + 3
            continue
        if src[i] == '"':
            i = _skip_string(src, i + 1, out)
            continue
        if src[i] == "'":
            i += 1
            while i < n and src[i] != "'":
                i += 2 if src[i] == "\\" else 1
            i += 1
            continue
        out.append(src[i])
        i += 1
    return "".join(out)


def all_kotlin() -> list[Path]:
    files = sorted(KOTLIN_SRC_ROOT.rglob("*.kt"))
    assert len(files) > 50, f"only {len(files)} Kotlin files found; wrong root?"
    return files


def test_tracked_kotlin_is_bracket_balanced():
    for path in TRACKED_KOTLIN:
        assert path.is_file(), f"missing {path}"
    for path in all_kotlin():
        code = strip_kotlin_literals(path.read_text())
        for open_ch, close_ch in (("{", "}"), ("(", ")"), ("[", "]")):
            assert code.count(open_ch) == code.count(close_ch), (
                f"{path.name}: {open_ch}{close_ch} unbalanced "
                f"({code.count(open_ch)} vs {code.count(close_ch)})"
            )


def test_no_kdoc_block_is_orphaned_from_its_declaration():
    """Two KDoc blocks in a row means one of them documents nothing.

    Kotlin accepts it silently — the first block just stops being anybody's
    documentation. It is what you get when a new function is pasted in between
    an existing KDoc and the declaration it belonged to, which is easy to do and
    invisible in a diff that only shows the added lines.
    """
    # A file-header banner sitting above the first declaration's own KDoc is the
    # same shape and is deliberate, so only look past the first declaration.
    first_decl = re.compile(
        r"^(?:@\w[\w.]*\s*)*"
        r"(?:public |internal |private |protected |abstract |open |sealed |data |"
        r"enum |annotation |value |inline |)*"
        r"(?:class|object|interface|fun|typealias)\b",
        re.M,
    )
    orphans = []
    for path in all_kotlin():
        src = path.read_text()
        start = first_decl.search(src)
        if start is None:
            continue
        # `*/` then only blank lines and annotations before the next `/**`.
        for match in re.finditer(r"\*/\s*(?:@[\w.]+(?:\([^)]*\))?\s*)*/\*\*", src):
            if match.start() < start.start():
                continue
            orphans.append(f"{path.relative_to(KOTLIN_SRC_ROOT)}:{src[: match.start()].count(chr(10)) + 1}")
    assert not orphans, "KDoc with no declaration after it: " + "; ".join(orphans)


#: Imports a property delegate needs but never names.
#:
#: `var x by remember { mutableStateOf(…) }` compiles to calls on the
#: `getValue`/`setValue` operators, so Kotlin requires the imports — and the
#: text of the file never contains either word. A grep-based check calls them
#: unused and is wrong; it only started being wrong when Compose arrived, since
#: `by` was not used anywhere in a tree of Views.
DELEGATE_IMPORTS = {
    "androidx.compose.runtime.getValue",
    "androidx.compose.runtime.setValue",
    "kotlin.properties.Delegates",
}


def test_tracked_kotlin_has_no_unused_imports():
    """Cheap stand-in for the compiler warning, since there is no kotlinc here.

    A name counts as used if it appears in the code OR in a KDoc `[Link]`, which
    is what the Kotlin compiler itself accepts — or if it is a delegate operator
    the file needs for a `by` and can never name (:data:`DELEGATE_IMPORTS`).
    """
    unused = []
    for path in all_kotlin():
        src = path.read_text()
        body = "\n".join(l for l in src.splitlines() if not l.startswith("import "))
        code = strip_kotlin_literals(body)
        links = set(re.findall(r"\[([A-Za-z_][A-Za-z0-9_.]*)", body))
        for line in src.splitlines():
            if not line.startswith("import "):
                continue
            spec = line[len("import "):].strip()
            if spec.endswith("*"):
                continue
            name = spec.split(" as ")[-1].strip() if " as " in spec else spec.split(".")[-1]
            if spec in DELEGATE_IMPORTS and re.search(r"\bby\s", code):
                continue
            if re.search(r"\b%s\b" % re.escape(name), code):
                continue
            if any(link == name or link.startswith(name + ".") for link in links):
                continue
            unused.append(f"{path.relative_to(KOTLIN_SRC_ROOT)}: {spec}")
    assert not unused, "unused imports: " + "; ".join(unused)


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

    combos = 2 ** len(STATUS_FIELDS)
    print(
        f"\n{len(tests) - failures}/{len(tests)} checks passed "
        f"({combos} requirement status combinations)"
    )
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
