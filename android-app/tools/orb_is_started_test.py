#!/usr/bin/env python3
"""Executable spec: a JarvisOrbView nobody starts draws NOTHING.

## The bug this exists to make impossible

`VoiceIdentityActivity` built a `JarvisOrbView`, gave it 160dp of the screen,
set its mode, fed it the live mic level on every audio callback and set its
caption to LISTENING. It drew nothing. Not a still orb — a hole.

Two facts in `JarvisOrbView` combine into that:

  * The master alpha every layer is drawn through is `entranceProgress`, and it
    is initialised to `0f`::

        val a = boot?.coreAlpha ?: entranceProgress          // master fade

  * The frame clock — which integrates the breathing, the ring rotation, the
    blob drift and the smoothed amplitude, and issues the single `invalidate()`
    per frame — is started only by `startClock()`, and the only callers of that
    are `startEntrance()`, `beginBoot()` and `endBoot()`.

So a view that is never handed to one of those three is stuck at alpha 0 with a
clock that never ticks. `setAmplitude` deliberately does not invalidate (the
clock is meant to), `setMode` invalidates a few times through its colour
animator, and every one of those redraws paints the whole reactor at zero
opacity.

Nothing else in the fast lane can see this. It compiles. It lays out. It has no
null to dereference and throws nothing. `onDraw` runs. The JVM tests do not
start an Activity, and the instrumented suite does not look at this screen.
The only report it can produce is a person saying *"there's no animation or ANY
indicator"* — which is what happened.

`onAttachedToWindow` looks like it would save this, and does not::

    if (wasRunning) startClock()

`wasRunning` is set in `onDetachedFromWindow` from `clockRunning`. On a view
that has never run, it is false, and attaching changes nothing.

## What is checked

The old orb View is gone (the glass reactor, `glass.Reactor`, draws from its
first frame and has no entrance to forget — see `reactor/Reactor.kt`), so
the half of this spec that pinned its clock starters is retired with it.
What stays is the rule the bug taught, now against the reactor's View host:
every shipping file that builds a `ReactorView` also tells it what to show,
so a host cannot leave the reactor at rest while the screen says it is
listening.

Plus the enrolment screen's own half of the report: tap to record rather than
hold, a capture that ends itself, and a caption that does not depend on the
custom view drawing at all.

Run:  python3 android-app/tools/orb_is_started_test.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "app" / "src" / "main" / "kotlin"
#: The glass reactor's View-shaped host, for the surfaces still built from Views.
ORB = MAIN / "ai" / "aura" / "app" / "glass" / "ReactorView.kt"
#: The phone's core since v2; the pre-v2 Reactor composable is gone (T-0779).
REACTOR = MAIN / "ai" / "aura" / "app" / "reactor" / "core" / "V2Core.kt"
ENROLMENT = MAIN / "ai" / "aura" / "app" / "VoiceIdentityActivity.kt"

#: The ways a host tells the reactor what it is doing. A host that builds a
#: `ReactorView` and calls none of these shows a reactor at rest whatever the
#: screen is doing, which is the modern form of the dead-orb bug.
EXPECTED_FEEDERS = {"setPhase", "setInputs", "onReactor"}


def kotlin_files() -> list[Path]:
    return sorted(MAIN.rglob("*.kt"))


def code_of(path: Path) -> str:
    """A file's Kotlin with every comment removed.

    Not tidiness — correctness of this file. The check below asks whether a
    host calls `startEntrance()`, and the host that was BROKEN now carries a
    long comment explaining why it must. Scanned raw, that comment satisfies
    the search: deleting the actual call left this spec passing 8/8, which is
    the precise failure `dispatch_spec_test._registry_code` was written for
    ("a comment quoting one of these needles moves the anchor").
    """
    src = path.read_text(encoding="utf-8")
    src = re.sub(r"/\*.*?\*/", " ", src, flags=re.S)
    return re.sub(r"//[^\n]*", " ", src)


#: A member function declaration, at EXACTLY the class's own indent.
#:
#: Any run of modifiers, `fun`, an optional generic parameter list, the name.
#: `override fun` and `private fun <T> x(` both have to match — miss either and
#: the function BEFORE it appears to own the body of the one that was skipped,
#: which is how the first draft of this file decided `applyBlend` starts the
#: clock (it does not; `onAttachedToWindow` two declarations later does).
#:
#: The four-space anchor is the other half. Match at any indent and a `fun`
#: inside an anonymous object — `object : AnimatorListenerAdapter() { override
#: fun onAnimationEnd(...)` — becomes a boundary, and the rest of the ENCLOSING
#: function is attributed to it. That is not hypothetical either: it is exactly
#: how the second draft lost `startEntrance`, whose `startClock()` sits after
#: such a listener.
_FUN = re.compile(r"\n {4}(?:[\w@]+ +)*fun +(?:<[^>]*> *)?(\w+) *\(")


def test_the_reactor_draws_from_its_first_frame() -> None:
    """The premise of the retired half of this spec, inverted: the old View
    drew everything through a master alpha that started at 0 and only three
    methods moved. The glass reactor has no such gate — it steps
    the core on every draw and its clock starts with the lifecycle — so an
    un-started reactor cannot exist. If either of these stops being true,
    re-derive what an idle reactor looks like before relaxing anything here.
    """
    src = code_of(REACTOR)
    assert "entranceProgress" not in src and "startEntrance" not in src, (
        "the reactor grew an entrance gate again; a host can forget to open it"
    )
    assert "LaunchedEffect(running" in src, "the reactor's clock no longer starts with the lifecycle"
    assert "CoreEngine.step(" in src and "renderer.draw(" in src, (
        "the core no longer steps and draws on every draw"
    )


def test_every_reactor_view_is_fed_by_the_code_that_builds_it() -> None:
    """The check the bug asked for, in its glass form.

    Per FILE, not per construction. Whether a particular `ReactorView(...)`
    is the one a particular `setPhase` is called on needs a Kotlin parser and
    a resolver, and this repository has neither in its fast lane. What a
    file-scoped check does catch is the bug that actually happened — a host
    that feeds NO reactor, anywhere, ever — and it cannot produce a false
    failure, which is what makes it worth having.
    """
    unfed: list[str] = []
    for path in kotlin_files():
        if path == ORB:
            continue
        src = code_of(path)
        match = re.search(r"ReactorView\(", src)
        if not match:
            continue
        if not any(f"{name}(" in src for name in EXPECTED_FEEDERS):
            line = src[: match.start()].count("\n") + 1
            unfed.append(f"{path.relative_to(ROOT)}:{line}")
    assert not unfed, (
        "these build a ReactorView and never tell it what the screen is doing, so "
        f"the reactor sits at rest while the screen says it is listening: {unfed}"
    )


# ---------------------------------------------------------------------------
# The enrolment screen specifically, which is where this was found.
# ---------------------------------------------------------------------------
def test_a_small_reactor_draws_no_chrome() -> None:
    """Reported as *"in the teach Jarvis voice, Jarvis is overlayed by text of
    Jarvis"*: the old orb's wordmark was anchored to a screen-sized surface
    and landed on the orb in a 200 dp slot, so every small host had to
    remember `chromeEnabled = false`. The glass reactor has no chrome at all
    (GLASS_HUD §13 retires the brackets, the wordmark and the vignette), so
    the flag is gone and the mistake cannot be made. Pinned so a flag of that
    shape does not come back on the View host.
    """
    src = code_of(ORB)
    for flag in ("chromeEnabled", "scrimEnabled", "wordmark"):
        assert flag not in src, f"ReactorView grew a {flag} switch a host can forget"


def test_enrolment_records_on_a_tap_rather_than_a_hold() -> None:
    """Reported alongside the dead orb: *"I shouldn't have to hold it down,
    just press for it to listen"*.

    Hold-to-talk has no feedback of its own — the finger is on the button, and
    the button is the same button. It was survivable only while the orb worked,
    and the orb never worked. Both halves are fixed together, and a touch
    listener coming back would restore half the original complaint.
    """
    src = code_of(ENROLMENT)
    assert "setOnTouchListener" not in src, (
        "the enrolment button is back to push-to-talk"
    )
    assert "HOLD TO RECORD" not in src
    assert "fun toggleRecording()" in src, (
        "nothing turns the capture on and off from one control"
    )


def test_a_capture_cannot_run_forever() -> None:
    """Tap-to-stop means the user can walk away mid-capture. Something has to
    end it, and it must not be the byte cap — a sample truncated at 25s is
    still submitted, and a truncated ENROLMENT sample is written into the
    profile and skews it."""
    src = code_of(ENROLMENT)
    assert "ENROL_WINDOW_MS" in src and "TEST_WINDOW_MS" in src
    assert "main.postDelayed(autoStop" in src, "no capture ever ends by itself"
    enrol = int(re.search(r"ENROL_WINDOW_MS = ([\d_]+)L", src).group(1).replace("_", ""))
    cap_bytes = 16000 * 2 * 25
    assert enrol * 16000 * 2 // 1000 < cap_bytes, (
        "the auto-stop window outlasts MAX_SAMPLE_BYTES, so the byte cap is "
        "what truncates a forgotten capture"
    )


def test_stopping_a_capture_does_not_drop_the_servers_reply() -> None:
    """`removeCallbacksAndMessages(null)` clears EVERY message on the main
    handler, and `offMainThread` hands its result back through exactly such a
    message — the one that clears `busy` and re-enables the buttons. Losing it
    leaves the screen permanently inert with nothing logged."""
    src = code_of(ENROLMENT)
    assert "removeCallbacksAndMessages" not in src, (
        "stopCapture can again cancel the message carrying a server response"
    )
    assert "main.removeCallbacks(autoStop)" in src
    assert "main.removeCallbacks(countdown)" in src


def test_the_screen_says_it_is_listening_in_words_as_well() -> None:
    """The orb is the indicator, and the orb is what failed. A caption and a
    status line cost nothing and do not depend on a custom view drawing."""
    src = code_of(ENROLMENT)
    # NOT the orb's caption: that is chrome, and this screen turns chrome off
    # because at 200dp the wordmark lands on the orb. Words in the status line
    # do not depend on a custom view drawing at all, which is the whole lesson
    # of the dead-orb bug above.
    assert "Listening — say the line" in src, "the screen never says it is listening"
    assert "listeningPrompt" in src and "}s\"" in src, (
        "the countdown is gone, or still being written to a caption nothing draws"
    )
    assert "Phase.THINKING" in src, (
        "the reactor goes still while the server answers, which is what a broken "
        "screen looks like"
    )


def main() -> int:
    tests = [
        (n, f) for n, f in sorted(globals().items())
        if n.startswith("test_") and callable(f)
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
    print(
        f"\n{len(tests) - failures}/{len(tests)} checks passed "
        f"({len(kotlin_files())} shipping Kotlin files scanned)"
    )
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
