#!/usr/bin/env python3
"""Executable spec: the app checks for its own updates, on its own.

`release_feed_test.py` covers which release is worth offering; `updater_install
_test.py` covers what happens after the user says yes. This covers the step
before either — deciding, without being asked, that it is time to look.

`app/src/main/kotlin/ai/jarvis/app/update/AutoUpdate.kt` is a pure policy
(`isCheckDue`) wrapped in a thin SharedPreferences/connectivity layer. The
policy is the part that can be wrong in an interesting way — a check that fires
on every resume, or one that never fires because a future timestamp locked it
out — so it is written down twice: once in Kotlin, which this container cannot
compile, and once here, where it runs.

The rules, and why each exists:

  * The setting comes first. Off means off, whatever the network or the clock
    say — it is the user's decision and it beats everything.
  * Offline is never due. A check offline can only fail, and a decided check
    spends the daily slot (the stamp is written when the check is decided, not
    when it succeeds), so running one offline would waste the day on a certain
    miss.
  * At most once an hour (it was a day until ADR-0050: the pipeline publishes every
    green build of the branch production runs, and the owner wants the phone on
    the latest). onResume fires many times an hour; without a floor the
    "automatic" check would run on all of them.
  * First run is due. A fresh install should not wait a day to learn there is
    already a newer build.
  * A future last-check time is due, not a lockout. A clock set forward and
    corrected, or a stamp restored from another device, must self-heal rather
    than refuse checks until real time catches up.

It also pins the wiring, because a perfect policy nothing calls is the empty
seam `no_empty_seams_test.py` exists for: the check runs from the home screen's
onResume and from an hourly WorkManager job, and the only automatic install is
AutoUpdate.act's -- a production build, with "Install updates automatically"
on, through UpdateChecker.install(unattended = true). A test build never
installs itself, and everything else is surfaced as a notice and a notification
for INSTALL.

Run:  python3 android-app/tools/auto_update_test.py
      python3 -m pytest android-app/tools/auto_update_test.py -q
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ANDROID = Path(__file__).resolve().parents[1]
KOTLIN = ANDROID / "app/src/main/kotlin/ai/jarvis/app"

AUTO_UPDATE = KOTLIN / "update/AutoUpdate.kt"
CONFIG = KOTLIN / "config/JarvisConfig.kt"
MAIN = KOTLIN / "MainActivity.kt"
SETTINGS = KOTLIN / "SettingsActivity.kt"
UPDATE_CHECKER = KOTLIN / "update/UpdateChecker.kt"
WORKER = KOTLIN / "update/UpdateWorker.kt"
APP = KOTLIN / "JarvisApp.kt"
MANIFEST = ANDROID / "app/src/main/AndroidManifest.xml"


def read(path: Path) -> str:
    assert path.is_file(), f"missing {path}"
    return path.read_text(encoding="utf-8")


def code_only(source: str) -> str:
    """Comments are not callers — see no_empty_seams_test.py's docstring."""
    source = re.sub(r"/\*.*?\*/", " ", source, flags=re.S)
    return re.sub(r"//[^\n]*", " ", source)


# --- the policy, mirrored from AutoUpdate.isCheckDue ------------------------

CHECK_INTERVAL_MS = 60 * 60 * 1000


def is_check_due(enabled: bool, online: bool, last_check_ms: int, now_ms: int) -> bool:
    if not enabled:
        return False
    if not online:
        return False
    if last_check_ms <= 0:
        return True
    elapsed = now_ms - last_check_ms
    if elapsed < 0:
        return True
    return elapsed >= CHECK_INTERVAL_MS


NOW = 1_000_000_000_000
DAY = CHECK_INTERVAL_MS  # one period (an hour); the table's words say "a day" for the period

#: (label, kwargs, expected). The same table AutoUpdateTest.kt asserts.
POLICY_CASES = [
    # The setting is the first word.
    ("off, first run", dict(enabled=False, online=True, last_check_ms=0), False),
    ("off, long overdue", dict(enabled=False, online=True, last_check_ms=NOW - DAY * 10), False),
    # Offline is never due.
    ("offline, first run", dict(enabled=True, online=False, last_check_ms=0), False),
    ("offline, long overdue", dict(enabled=True, online=False, last_check_ms=NOW - DAY * 10), False),
    # First run is due.
    ("first run", dict(enabled=True, online=True, last_check_ms=0), True),
    ("never checked (sentinel)", dict(enabled=True, online=True, last_check_ms=-1), True),
    # Within the day: not due.
    ("just checked", dict(enabled=True, online=True, last_check_ms=NOW), False),
    ("half a day ago", dict(enabled=True, online=True, last_check_ms=NOW - DAY // 2), False),
    ("one ms short of a day", dict(enabled=True, online=True, last_check_ms=NOW - (DAY - 1)), False),
    # A day or more: due.
    ("exactly a day ago", dict(enabled=True, online=True, last_check_ms=NOW - DAY), True),
    ("a day and a ms ago", dict(enabled=True, online=True, last_check_ms=NOW - (DAY + 1)), True),
    ("ten days ago", dict(enabled=True, online=True, last_check_ms=NOW - DAY * 10), True),
    # A future stamp does not lock the check out.
    ("stamp a day in the future", dict(enabled=True, online=True, last_check_ms=NOW + DAY), True),
    ("stamp a year in the future", dict(enabled=True, online=True, last_check_ms=NOW + DAY * 365), True),
]


def check_policy_table() -> int:
    failures = 0
    for label, kwargs, expected in POLICY_CASES:
        got = is_check_due(now_ms=NOW, **kwargs)
        if got != expected:
            print(f"FAIL  {label}: is_check_due -> {got}, expected {expected}")
            failures += 1
    return failures


# --- the Kotlin agrees with the mirror -------------------------------------


def check_kotlin_policy_agrees() -> int:
    text = read(AUTO_UPDATE)
    failures = 0
    if "const val CHECK_INTERVAL_MS = 60L * 60L * 1000L" not in text:
        print("FAIL  AutoUpdate.CHECK_INTERVAL_MS is no longer an hour in ms")
        failures += 1
    # The guards that make the table, in the order the mirror applies them.
    for needed, why in [
        (r"if\s*\(!enabled\)\s*return false", "the setting-off guard"),
        (r"if\s*\(!online\)\s*return false", "the offline guard"),
        (r"if\s*\(lastCheckMs\s*<=\s*0L\)\s*return true", "the first-run guard"),
        (r"if\s*\(elapsed\s*<\s*0L\)\s*return true", "the future-stamp guard"),
        (r"elapsed\s*>=\s*CHECK_INTERVAL_MS", "the 24-hour floor"),
    ]:
        if not re.search(needed, text):
            print(f"FAIL  AutoUpdate.kt lost {why} ({needed})")
            failures += 1
    return failures


def check_preference_keys() -> int:
    """The keys the wrapper reads and writes, so a rename here breaks loudly
    rather than silently forgetting every device's last-check time."""
    text = read(AUTO_UPDATE)
    failures = 0
    for const, value in [
        ("KEY_LAST_CHECK", "update.last_check"),
        ("KEY_DISMISSED_CODE", "update.dismissed_code"),
        ("KEY_OFFER", "update.offer"),
    ]:
        if not re.search(rf'{const}\s*=\s*"{re.escape(value)}"', text):
            print(f"FAIL  AutoUpdate.{const} is no longer {value!r}")
            failures += 1
    return failures


def check_setting_exists() -> int:
    """The user-facing switch: a var with a default of ON and a stable key."""
    text = read(CONFIG)
    failures = 0
    if "var autoUpdateEnabled" not in text:
        print("FAIL  JarvisConfig no longer has autoUpdateEnabled")
        failures += 1
    if not re.search(r"getBoolean\(KEY_AUTO_UPDATE,\s*true\)", text):
        print("FAIL  autoUpdateEnabled no longer defaults to ON")
        failures += 1
    if not re.search(r'KEY_AUTO_UPDATE\s*=\s*"auto_update_enabled"', text):
        print("FAIL  the auto_update_enabled preference key changed")
        failures += 1
    return failures


def check_home_screen_runs_it() -> int:
    """The policy has a caller, it is the home screen's onResume, and it runs
    off the main thread."""
    text = code_only(read(MAIN))
    failures = 0
    for needed, why in [
        ("maybeOfferUpdate()", "onResume calls the auto-update entry point"),
        ("AutoUpdate.shouldCheckNow", "the hourly gate is consulted"),
        ("AutoUpdate.pendingOffer", "a cached offer is re-shown on resume"),
        ("UpdateChecker(applicationContext).check", "the check itself"),
        ("allowPrerelease", "the prerelease preference is passed through"),
        ("AutoUpdate.act(", "the offer goes to the one next step: install or notify"),
        ("Thread {", "the check runs off the main thread"),
    ]:
        if needed not in text:
            print(f"FAIL  MainActivity lost: {why} ({needed})")
            failures += 1
    # onResume must be the caller, not some dead method.
    if not re.search(r"override fun onResume\(\)[\s\S]{0,1500}maybeOfferUpdate\(\)", text):
        print("FAIL  maybeOfferUpdate is not called from onResume")
        failures += 1
    return failures


# --- which installs are automatic (ADR-0050) ---------------------------------


def should_install_unattended(auto_install: bool, prerelease: bool) -> bool:
    return auto_install and not prerelease


#: (auto_install, prerelease, expected). AutoUpdateTest.kt asserts the same four.
UNATTENDED_CASES = [
    (True, False, True),   # the setting on, a production build: it installs itself
    (True, True, False),   # a test build never does
    (False, False, False), # the setting off: offered, not installed
    (False, True, False),
]


def check_only_production_builds_install_themselves() -> int:
    """The one automatic install, and its limits.

    The owner asked for phones that are always on the latest build (ADR-0050), so a
    production build installs itself. What stays true: a test build never does, the
    setting can turn it off, the home screen and the worker share one next step, and
    unattended is only ever a request the platform may refuse.
    """
    failures = 0
    for auto, pre, expected in UNATTENDED_CASES:
        if should_install_unattended(auto, pre) != expected:
            print(f"FAIL  should_install_unattended({auto}, {pre}) != {expected}")
            failures += 1
    auto_update = code_only(read(AUTO_UPDATE))
    if not re.search(r"fun shouldInstallUnattended\(autoInstall: Boolean, update: ReleaseFeed\.Update\): Boolean =\s*autoInstall && !update\.prerelease", auto_update):
        print("FAIL  AutoUpdate.shouldInstallUnattended is no longer `autoInstall && !update.prerelease`")
        failures += 1
    act = re.search(r"fun act\([\s\S]*?\n    }", auto_update)
    if not act:
        print("FAIL  AutoUpdate.act is gone")
        return failures + 1
    body = act.group(0)
    for needed, why in [
        ("shouldInstallUnattended(config.autoInstallUpdates, update)", "the install is gated by the setting and the channel"),
        ("install(update, unattended = true)", "the automatic install asks for unattended, nothing stronger"),
        ("UpdateNotification.post", "anything not installed is offered by notification"),
    ]:
        if needed not in body:
            print(f"FAIL  AutoUpdate.act lost: {why} ({needed})")
            failures += 1
    checker = code_only(read(UPDATE_CHECKER))
    if not re.search(r"if \(unattended && Build\.VERSION\.SDK_INT >= Build\.VERSION_CODES\.S\) \{\s*params\.setRequireUserAction\(PackageInstaller\.SessionParams\.USER_ACTION_NOT_REQUIRED\)", checker):
        print("FAIL  USER_ACTION_NOT_REQUIRED is no longer asked for only on the unattended path")
        failures += 1
    if checker.count("USER_ACTION_NOT_REQUIRED") != 1:
        print("FAIL  USER_ACTION_NOT_REQUIRED appears outside the one unattended branch")
        failures += 1
    main = code_only(read(MAIN))
    if "UpdateChecker(applicationContext).install(update)" not in main:
        print("FAIL  MainActivity's INSTALL no longer installs through UpdateChecker.install")
        failures += 1
    if not re.search(r'"Install"\s*\)\s*\{\s*_,\s*_\s*->\s*installUpdate\(update\)', main):
        print("FAIL  the INSTALL action no longer calls installUpdate")
        failures += 1
    if not re.search(r'"Later"\s*\)\s*\{\s*_,\s*_\s*->\s*dismissUpdateOffer\(update\)', main):
        print("FAIL  the LATER action no longer dismisses the offer")
        failures += 1
    worker = code_only(read(WORKER))
    for needed, why in [
        ("AutoUpdate.shouldCheckNow", "the worker shares the hourly gate"),
        ("AutoUpdate.act(", "the worker shares the one next step"),
        ("PeriodicWorkRequestBuilder<UpdateWorker>(1, TimeUnit.HOURS)", "the worker runs hourly"),
        ("ExistingPeriodicWorkPolicy.KEEP", "an app start does not reset the period"),
    ]:
        if needed not in worker:
            print(f"FAIL  UpdateWorker lost: {why} ({needed})")
            failures += 1
    if "UpdateWorker.schedule(this)" not in code_only(read(APP)):
        print("FAIL  JarvisApp no longer schedules the update worker")
        failures += 1
    manifest = read(MANIFEST)
    if "android.permission.UPDATE_PACKAGES_WITHOUT_USER_ACTION" not in manifest:
        print("FAIL  the manifest lost UPDATE_PACKAGES_WITHOUT_USER_ACTION (API 33+ needs it to skip the prompt)")
        failures += 1
    return failures


def check_settings_toggle() -> int:
    text = read(SETTINGS)
    failures = 0
    if "Check for updates automatically" not in text:
        print("FAIL  Settings has no 'Check for updates automatically' toggle")
        failures += 1
    if "config.autoUpdateEnabled = autoUpdate.isChecked" not in text:
        print("FAIL  Settings save() no longer writes autoUpdateEnabled")
        failures += 1
    if "Install updates automatically" not in text or "config.autoInstallUpdates = autoInstall.isChecked" not in text:
        print("FAIL  Settings has no working 'Install updates automatically' toggle")
        failures += 1
    return failures


def main() -> int:
    failures = (
        check_policy_table()
        + check_kotlin_policy_agrees()
        + check_preference_keys()
        + check_setting_exists()
        + check_home_screen_runs_it()
        + check_only_production_builds_install_themselves()
        + check_settings_toggle()
    )
    if failures:
        print(f"\n{failures} failure(s)")
        return 1
    print(
        f"auto update: {len(POLICY_CASES)} policy cases, the interval, the "
        "preference keys, the setting, the onResume caller, the notice and "
        "which builds install themselves, the worker and the permission all agree"
    )
    return 0


def test_auto_update():
    """pytest entry point — the same checks, as one assertion."""
    assert main() == 0


if __name__ == "__main__":
    sys.exit(main())
