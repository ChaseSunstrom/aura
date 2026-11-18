#!/usr/bin/env python3
"""Executable spec for the floating orb — the thing that appears over whatever
app you are using when you say "Hey Jarvis".

The reported symptom: *"it can speak back to me … but there's no Siri like
overlay/animation on my screen"*. The voice half worked and the visible half
did not exist. `JarvisAssistActivity` is an **Activity**, and Android 12+
silently drops a background activity start, so the conversation arrived as a
notification to tap and nothing was ever drawn over the app in front.

`AssistOverlay` is a real `TYPE_APPLICATION_OVERLAY` window, and the glass
reactor (`ReactorView`, the View host of `glass.Reactor`) is what it draws.
Four things are pinned here, each because getting it wrong produces a bug
that is invisible in review:

  1. **The reactor in a Service window.** A Compose view in a window no
     Activity owns has no lifecycle, and its first frame throws. The overlay
     has to give its window one before it is added, and the states the
     reactor shows there are the same eleven every surface shows (the table
     is `ReactorState`, pinned by `reactor_state_test.py`, not a second
     palette of this window's own).
  2. **The window flags.** An overlay that takes focus steals the keyboard from
     whatever the user was typing in. One sized to the screen swallows every
     touch on the app behind it, because FLAG_NOT_TOUCH_MODAL only passes
     through touches *outside* the window's own bounds.
  3. **The fallback.** Overlay windows are never shown above the keyguard, and
     the permission is one the user has to grant by hand. Both cases must fall
     back to the full-screen intent rather than producing nothing.
  4. **The microphone.** The overlay runs the conversation inside the same
     service that holds the wake mic, so it must give the mic up first and hand
     it back at the end — the invariant `wake_listener_test.py` already guards
     for the Activity path.

Run:  python3 android-app/tools/siri_overlay_test.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

def check_the_reactor_lives_in_the_window(android: Path) -> int:
    """The overlay draws the one reactor, and gives its window a lifecycle.

    `ComposeView` looks up `ViewTreeLifecycleOwner` on attach and throws when
    there is none; a `WindowManager.addView` from a Service provides none. So
    the overlay installs owners on its root before the add, and the owners
    follow the attach and the detach — the reactor's clock runs while the
    window is up and stops when it goes, which is the invariant the old
    `Choreographer` checks here used to pin.
    """
    overlay = android / "app/src/main/kotlin/ai/aura/app/assist/AssistOverlay.kt"
    view = android / "app/src/main/kotlin/ai/aura/app/glass/ReactorView.kt"
    for path in (overlay, view):
        if not path.is_file():
            print(f"FAIL  {path} is missing")
            return 1
    text = overlay.read_text(encoding="utf-8")
    failures = 0
    if "ReactorView(context)" not in text:
        print("FAIL  the overlay does not draw the glass reactor (ReactorView)")
        failures += 1
    # `attach(turn: Boolean = true)` since the island carries a route with no turn (T-0695).
    found = re.search(r"fun attach\([^)]*\): Boolean \{(.*?)\n    \}", text, re.S)
    attach = found.group(1) if found else ""
    if "ReactorView.attachOwners(view)" not in attach:
        print("FAIL  the overlay adds a Compose view to a window with no lifecycle; its first frame throws")
        failures += 1
    elif attach.index("ReactorView.attachOwners(view)") > attach.index("windows.addView(view, params("):
        print("FAIL  the owners are installed after the window is added, which is after the first frame")
        failures += 1
    if "orb?.setPhase(mode)" not in text or "fun setReactor(inputs: ReactorInputs)" not in text:
        print("FAIL  the overlay's reactor is not told the phase and the HUD's inputs")
        failures += 1
    for stale in ("SiriOrbView", "SiriPalette", "JarvisOrbView"):
        if stale in text:
            print(f"FAIL  the overlay still names the old orb ({stale})")
            failures += 1

    vsrc = view.read_text(encoding="utf-8")
    for needle in ("setViewTreeLifecycleOwner", "setViewTreeSavedStateRegistryOwner", "onViewDetachedFromWindow"):
        if needle not in vsrc:
            print(f"FAIL  ReactorView.attachOwners does not do: {needle}")
            failures += 1
    if "Lifecycle.State.DESTROYED" not in vsrc:
        print("FAIL  the window's lifecycle is never destroyed, so the reactor's clock outlives the window")
        failures += 1
    return failures


def check_window_flags(android: Path) -> int:
    path = android / "app/src/main/kotlin/ai/aura/app/assist/AssistOverlay.kt"
    if not path.is_file():
        print(f"FAIL  {path} is missing")
        return 1
    text = path.read_text(encoding="utf-8")
    failures = 0

    if "TYPE_APPLICATION_OVERLAY" not in text:
        print("FAIL  the overlay is not an overlay window; it cannot float over other apps")
        failures += 1
    for flag, why in (
        ("FLAG_NOT_FOCUSABLE", "an overlay that takes focus steals the keyboard"),
        ("FLAG_NOT_TOUCH_MODAL", "without it every touch on the screen goes to the orb"),
        ("FLAG_HARDWARE_ACCELERATED", "screen-blending on a software canvas drops frames"),
    ):
        if flag not in text:
            print(f"FAIL  {flag} is not set: {why}")
            failures += 1
    if "PixelFormat.TRANSLUCENT" not in text:
        print("FAIL  the overlay window is not translucent; it would paint a black box")
        failures += 1
    # Sized to the card, never to the screen: FLAG_NOT_TOUCH_MODAL passes
    # through touches OUTSIDE the window, so a MATCH_PARENT width would eat
    # every tap across the display. Only the WINDOW's own params are checked —
    # the child views inside the card are match-width on purpose.
    # `params(compact: Boolean = false)` since the badge form landed — the
    # signature carries an argument now, so match up to the closing paren.
    params = re.search(r"private fun params\([^)]*\).*?\n    \}", text, re.S)
    if not params:
        print("FAIL  AssistOverlay has no window params")
        failures += 1
    elif "MATCH_PARENT" in params.group(0):
        print("FAIL  the overlay window is screen-width; it swallows taps on the app behind")
        failures += 1
    if "canDrawOverlays" not in text:
        print("FAIL  the overlay does not check for its permission before adding a window")
        failures += 1
    # addView throws on some ROMs even with the permission; a throw must not
    # take the conversation down.
    add = re.search(r"fun attach\([^)]*\).*?\n    \}", text, re.S)
    if add and "catch" not in add.group(0):
        print("FAIL  a refused addView is not caught, so a wake word would crash the service")
        failures += 1
    if add and "return false" not in add.group(0):
        print("FAIL  attach() cannot report failure, so the caller cannot fall back")
        failures += 1
    return failures


def check_the_fallback_survives(android: Path) -> int:
    """Overlay unavailable must still put Jarvis on screen."""
    path = android / "app/src/main/kotlin/ai/aura/app/assist/WakeWordService.kt"
    text = path.read_text(encoding="utf-8")
    failures = 0

    start = re.search(r"private fun startOverlayConversation\(\).*?\n    \}", text, re.S)
    if not start:
        print("FAIL  WakeWordService never tries to show the floating orb")
        return 1
    body = start.group(0)
    if "AssistOverlay.canShow" not in body:
        print("FAIL  the overlay is attached without checking for its permission")
        failures += 1
    # The keyguard is not this function's business.
    #
    # It was, and that was a bug: `isKeyguardLocked()` is true whenever the
    # keyguard is up, which on any phone with a secure lock includes the screen
    # merely being OFF, so returning false here meant the caller could not tell
    # "no permission" from "locked" — and the fallback for those two is not the
    # same. The decision moved to `onWakeWord`, which owns both surfaces.
    if "isLocked()" in body:
        print(
            "FAIL  startOverlayConversation decides about the keyguard again. That "
            "conflates 'no permission' with 'locked', which need different answers."
        )
        failures += 1

    wake = re.search(r"override fun onWakeWord\(.*?\n    \}", text, re.S)
    if not wake:
        print("FAIL  there is no wake handler any more")
        return failures + 1
    wake_body = wake.group(0)
    if "startOverlayConversation()" not in wake_body:
        print("FAIL  the wake word does not try the floating orb first")
        failures += 1
    for fallback in ("startActivity(intent)", "showHeard(intent)"):
        if fallback not in wake_body:
            print(f"FAIL  the {fallback} fallback is gone; a locked phone gets nothing")
            failures += 1
    # The fallback must come AFTER the overlay attempt and be skipped when it
    # worked, or a successful overlay also fires a full-screen intent and the
    # user gets two Jarvises.
    if wake_body.index("startOverlayConversation()") > wake_body.index("showHeard(intent)"):
        print("FAIL  the notification fallback runs before the overlay is tried")
        failures += 1
    # ONE microphone. This is the invariant, and it is not obvious from any one
    # line of the handler.
    #
    # A `TYPE_APPLICATION_OVERLAY` window is not drawn above the keyguard — the
    # platform stopped honouring FLAG_SHOW_WHEN_LOCKED for non-Activity windows
    # long before this app's minSdk — so on a locked phone the overlay
    # conversation is one nobody can see, while the full-screen intent brings up
    # JarvisAssistActivity, which starts a conversation of its own. Both running
    # is two `AudioRecord`s on one microphone: the second startRecording wins
    # and the first goes silent, or it fails outright, and WHICH of the two is
    # deaf is a race with the activity's ACTION_PAUSE.
    #
    # So the keyguard is asked once, before anything opens a microphone, and the
    # overlay is not even attempted when it cannot be seen.
    if "val locked = isLocked()" not in wake_body:
        print(
            "FAIL  the wake handler no longer settles the keyguard question before "
            "opening a microphone"
        )
        failures += 1
    if "val showedOverlay = !locked && startOverlayConversation()" not in wake_body:
        print(
            "FAIL  the wake handler starts an overlay conversation on a locked phone. "
            "The full-screen intent starts a second one, and two AudioRecords on one "
            "microphone means one of the two Jarvises on screen is deaf."
        )
        failures += 1
    if "if (showedOverlay) {" not in wake_body:
        print(
            "FAIL  the wake handler does not stop after the overlay took the turn, so "
            "a successful overlay ALSO fires the full-screen intent"
        )
        failures += 1
    # The keyguard must be sampled before the microphone is opened, not after:
    # asked afterwards it is a check on a decision already made.
    asked = wake_body.find("val locked = isLocked()")
    opened = wake_body.find("startOverlayConversation()")
    if asked >= 0 and opened >= 0 and asked > opened:
        print("FAIL  the keyguard is checked after the overlay has already opened the mic")
        failures += 1
    if "wakeTheScreen()" not in wake_body:
        print(
            "FAIL  nothing turns the screen on. An orb drawn on a dark panel is "
            "not an orb anybody sees, and a wake word is for the phone you are "
            "not holding."
        )
        failures += 1
    return failures


def check_the_microphone_comes_back(android: Path) -> int:
    """The invariant wake_listener_test.py guards, for the new path.

    The overlay conversation runs inside the same service that holds the wake
    mic. Two `AudioRecord`s on one device is a coin toss, so the wake link has
    to be closed before the conversation opens one — and re-opened when it is
    over, or the wake word works exactly once.
    """
    path = android / "app/src/main/kotlin/ai/aura/app/assist/WakeWordService.kt"
    text = path.read_text(encoding="utf-8")
    failures = 0

    wake = re.search(r"override fun onWakeWord\(.*?\n    \}", text, re.S).group(0)
    if wake.index("pause()") > wake.index("startOverlayConversation()"):
        print("FAIL  the conversation opens the mic before the wake link gives it up")
        failures += 1

    end = re.search(r"private fun endOverlayConversation\(.*?\n    \}", text, re.S)
    if not end:
        print("FAIL  nothing tears the overlay conversation down")
        return failures + 1
    body = end.group(0)
    if "convo?.stop()" not in body or "overlay?.detach()" not in body:
        print("FAIL  ending the overlay conversation leaks the mic or the window")
        failures += 1
    if body.index("convo?.stop()") > body.index("overlay?.detach()"):
        print("FAIL  the window is removed before the conversation is stopped")
        failures += 1
    if "resume()" not in body:
        print("FAIL  the wake listener never takes the microphone back")
        failures += 1

    # Every way a conversation can end has to reach it.
    ui = re.search(r"private val overlayUi = object : JarvisConversation\.Ui \{.*?\n    \}", text, re.S)
    if not ui:
        print("FAIL  the overlay has no conversation callbacks")
        return failures + 1
    ui_body = ui.group(0)
    for hook in ("onIdle", "onError"):
        arm = re.search(rf"override fun {hook}\(.*?\n        \}}", ui_body, re.S)
        if not arm or "endOverlayConversation(giveMicBack = true)" not in arm.group(0):
            print(f"FAIL  {hook} does not give the microphone back")
            failures += 1
    # onDestroy must not: the service is going away and resume() would start a
    # link nothing will ever close.
    destroy = re.search(r"override fun onDestroy\(\).*?\n    \}", text, re.S)
    if destroy and "endOverlayConversation(giveMicBack = false)" not in destroy.group(0):
        print("FAIL  onDestroy re-opens the microphone on the way out")
        failures += 1
    return failures


def main() -> int:
    android = Path(__file__).resolve().parents[1]
    failures = (
        check_the_reactor_lives_in_the_window(android)
        + check_window_flags(android)
        + check_the_fallback_survives(android)
        + check_the_microphone_comes_back(android)
    )
    if failures:
        print(f"\n{failures} failure(s)")
        return 1
    print(
        "siri overlay: the reactor in its window, the window flags, the keyguard and "
        "permission fallbacks and the microphone hand-back all agree"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
