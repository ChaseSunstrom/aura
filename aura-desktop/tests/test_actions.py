"""The dispatcher and the built-in action table.

The dispatcher tests are the ones that matter: they check the *order* of the
gate — local tier, then the server's raise, then the user's policy, then a
human, then execute, then re-check — because every one of those steps is only
worth anything if it happens before the action runs.
"""

from __future__ import annotations

import asyncio

import pytest
from pathlib import Path

from aura_desktop.actions.base import Action, ActionResult, Status
from aura_desktop.actions.builtins import TIER_TABLE, all_actions, build_registry
from aura_desktop.consent import ApprovalVerdict
from aura_desktop.policy import ActionTier, Decision, TrustLevel, UserPolicy

from .conftest import RecordingAction, ScriptedConsent


# --- the local table --------------------------------------------------------


def test_the_flat_tier_table_matches_the_actions():
    """Two copies of the table, so an action added at the wrong tier shows up as
    a failing test rather than as a surprise."""
    built = {action.id: action.tier for action in all_actions()}
    assert built == TIER_TABLE


def test_action_ids_are_unique():
    ids = [action.id for action in all_actions()]
    assert len(ids) == len(set(ids))


def test_every_action_has_a_description_and_a_schema():
    for action in all_actions():
        assert action.id
        assert action.description.strip()
        assert isinstance(action.params_schema, dict)
        assert action.capability
        assert action.timeout_s > 0


@pytest.mark.parametrize(
    "action_id",
    ["run_command", "delete_file", "type_text", "click", "move_mouse", "key_press", "scroll", "lock_screen", "sleep"],
)
def test_the_dangerous_actions_are_tier_three(action_id):
    assert TIER_TABLE[action_id] == ActionTier.CONFIRM


@pytest.mark.parametrize(
    "action_id", ["get_system_state", "read_file", "list_dir", "list_windows", "open_url", "notify"]
)
def test_the_read_only_actions_are_tier_one(action_id):
    assert TIER_TABLE[action_id] == ActionTier.AUTO


@pytest.mark.parametrize(
    "action_id",
    ["write_file", "read_clipboard", "write_clipboard", "http_request", "screenshot", "focus_window"],
)
def test_the_recoverable_actions_are_tier_two(action_id):
    assert TIER_TABLE[action_id] == ActionTier.NOTIFY


def test_the_desktop_tiers_agree_with_the_phone_where_the_actions_overlap():
    """The user should not have to learn Jarvis's manners twice."""
    shared_with_android = {
        "read_file": ActionTier.AUTO,
        "write_file": ActionTier.NOTIFY,
        "list_dir": ActionTier.AUTO,  # `list_files` on the phone
        "delete_file": ActionTier.CONFIRM,
        "read_clipboard": ActionTier.NOTIFY,
        "write_clipboard": ActionTier.NOTIFY,
        "http_request": ActionTier.NOTIFY,
        "open_url": ActionTier.AUTO,
        "launch_app": ActionTier.AUTO,
        "set_volume": ActionTier.AUTO,
        "run_command": ActionTier.CONFIRM,  # `run_shell` on the phone
    }
    for action_id, tier in shared_with_android.items():
        assert TIER_TABLE[action_id] == tier, action_id


def test_http_request_raises_itself_to_confirm_for_writes():
    from aura_desktop.actions.net import HttpRequest

    action = HttpRequest()
    assert action.tier_for({"method": "GET"}) == ActionTier.NOTIFY
    assert action.tier_for({}) == ActionTier.NOTIFY
    assert action.tier_for({"method": "HEAD"}) == ActionTier.NOTIFY
    for method in ("POST", "PUT", "PATCH", "DELETE", "post"):
        assert action.tier_for({"method": method}) == ActionTier.CONFIRM, method


def test_the_manifest_is_serialisable(config, policy, audit):
    import json

    registry = build_registry(config, policy, audit)
    entries = registry.manifest()
    json.dumps(entries)  # must not raise
    assert len(entries) == len(TIER_TABLE)
    for entry in entries:
        assert entry["tier"] in (1, 2, 3)
        assert entry["requires_confirmation"] == (entry["tier"] == 3)


def test_capabilities_only_list_what_works(config, policy, audit):
    registry = build_registry(config, policy, audit)
    caps = registry.capabilities()
    assert "system" in caps
    assert "files" in caps
    # Input automation is off in the test config, so it is not advertised.
    assert "ui_automation" not in caps


# --- dispatch ordering ------------------------------------------------------


async def test_a_tier_one_action_runs_without_a_prompt(make_registry):
    action = RecordingAction("peek", ActionTier.AUTO)
    consent = ScriptedConsent(default=ApprovalVerdict.APPROVED)
    registry = make_registry([action], consent=consent)

    outcome = await registry.dispatch("peek", {"value": 1}, ActionTier.AUTO, "why")

    assert outcome.decision == Decision.ALLOW
    assert outcome.result.status == Status.OK
    assert action.calls == [{"value": 1}]
    assert consent.seen == []


async def test_a_tier_two_action_asks_once_then_remembers(make_registry, policy):
    action = RecordingAction("tweak", ActionTier.NOTIFY)
    consent = ScriptedConsent(answers=[ApprovalVerdict.APPROVED_ALWAYS])
    registry = make_registry([action], consent=consent)

    first = await registry.dispatch("tweak", {}, ActionTier.NOTIFY, "why")
    assert first.result.ok
    assert len(consent.seen) == 1
    assert consent.seen[0].rememberable is True
    assert policy.policy_for("tweak") == UserPolicy.ALLOW_ALWAYS

    second = await registry.dispatch("tweak", {}, ActionTier.NOTIFY, "why")
    assert second.decision == Decision.ALLOW
    assert len(consent.seen) == 1, "it asked again after being told always"
    assert len(action.calls) == 2


async def test_a_tier_three_action_asks_every_time_and_never_remembers(make_registry, policy):
    action = RecordingAction("nuke", ActionTier.CONFIRM)
    consent = ScriptedConsent(default=ApprovalVerdict.APPROVED_ALWAYS)
    registry = make_registry([action], consent=consent)

    for _ in range(3):
        outcome = await registry.dispatch("nuke", {}, ActionTier.CONFIRM, "why")
        assert outcome.result.ok

    assert len(consent.seen) == 3, "a Tier-3 action stopped asking"
    assert all(seen.rememberable is False for seen in consent.seen)
    assert policy.policy_for("nuke") == UserPolicy.ASK, "a Tier-3 answer was remembered"


async def test_an_action_can_raise_its_own_tier_from_its_params(make_registry):
    class Escalating(RecordingAction):
        def tier_for(self, params):
            return ActionTier.CONFIRM if params.get("dangerous") else ActionTier.AUTO

    action = Escalating("maybe", ActionTier.AUTO)
    consent = ScriptedConsent(default=ApprovalVerdict.DENIED)
    registry = make_registry([action], consent=consent)

    safe = await registry.dispatch("maybe", {}, ActionTier.AUTO, "why")
    assert safe.result.ok
    assert consent.seen == []

    risky = await registry.dispatch("maybe", {"dangerous": True}, ActionTier.AUTO, "why")
    assert risky.result.status == Status.DENIED
    assert len(consent.seen) == 1
    assert consent.seen[0].tier == ActionTier.CONFIRM


async def test_an_action_cannot_lower_its_own_tier(make_registry):
    class Sneaky(RecordingAction):
        def tier_for(self, params):
            return ActionTier.AUTO  # ignored: max() only goes up

    action = Sneaky("sneaky", ActionTier.CONFIRM)
    consent = ScriptedConsent(default=ApprovalVerdict.DENIED)
    registry = make_registry([action], consent=consent)

    outcome = await registry.dispatch("sneaky", {}, None, "why")
    assert action.calls == []
    assert outcome.tier == ActionTier.CONFIRM


async def test_a_tier_for_that_raises_is_treated_as_confirm(make_registry):
    class Broken(RecordingAction):
        def tier_for(self, params):
            raise RuntimeError("kaboom")

    action = Broken("broken", ActionTier.AUTO)
    consent = ScriptedConsent(default=ApprovalVerdict.DENIED)
    registry = make_registry([action], consent=consent)

    outcome = await registry.dispatch("broken", {}, None, "why")
    assert action.calls == []
    assert outcome.tier == ActionTier.CONFIRM


async def test_the_store_is_re_read_after_the_prompt_returns(make_registry, policy):
    """Panic hit while the prompt was on screen must still stop the action."""
    action = RecordingAction("slow_confirm", ActionTier.CONFIRM)

    class PanicMidPrompt(ScriptedConsent):
        async def request(self, request):
            self.seen.append(request)
            policy.panic = True  # the user slams the kill switch
            return ApprovalVerdict.APPROVED

    consent = PanicMidPrompt()
    registry = make_registry([action], consent=consent)

    outcome = await registry.dispatch("slow_confirm", {}, ActionTier.CONFIRM, "why")

    assert action.calls == [], "an approval outlived the kill switch"
    assert outcome.result.status == Status.DENIED
    assert "revoked while the prompt was up" in outcome.note


async def test_untrusted_requests_are_never_auto_allowed(make_registry):
    action = RecordingAction("peek", ActionTier.AUTO)
    consent = ScriptedConsent(default=ApprovalVerdict.DENIED)
    registry = make_registry([action], consent=consent)

    outcome = await registry.dispatch(
        "peek", {}, ActionTier.AUTO, "a web page suggested this", trust=TrustLevel.UNTRUSTED
    )

    # A Tier-1 action that would normally run silently put a prompt on screen
    # instead, because the request came from content a stranger wrote.
    assert len(consent.seen) == 1
    assert consent.seen[0].rememberable is False
    assert action.calls == []
    assert outcome.result.status == Status.DENIED


async def test_an_untrusted_approval_is_never_remembered(make_registry, policy):
    action = RecordingAction("tweak", ActionTier.NOTIFY)
    consent = ScriptedConsent(default=ApprovalVerdict.APPROVED_ALWAYS)
    registry = make_registry([action], consent=consent)

    await registry.dispatch(
        "tweak", {}, ActionTier.NOTIFY, "why", trust=TrustLevel.UNTRUSTED
    )
    assert policy.policy_for("tweak") == UserPolicy.ASK


async def test_an_unavailable_action_says_so_without_prompting(make_registry):
    class Missing(RecordingAction):
        def available(self, ctx):
            return False

        def unavailable_reason(self, ctx):
            return "install the thing first"

    action = Missing("missing", ActionTier.CONFIRM)
    consent = ScriptedConsent(default=ApprovalVerdict.APPROVED)
    registry = make_registry([action], consent=consent)

    outcome = await registry.dispatch("missing", {}, None, "why")
    assert outcome.result.status == Status.UNSUPPORTED
    assert "install the thing first" in (outcome.result.error or "")
    assert consent.seen == []


async def test_a_slow_action_is_timed_out(make_registry):
    class Slow(RecordingAction):
        def run(self, ctx, params):
            import time

            self.calls.append(params)
            time.sleep(5)
            return ActionResult.success()

    action = Slow("slow", ActionTier.AUTO)
    action.timeout_s = 0.1
    registry = make_registry([action])

    outcome = await registry.dispatch("slow", {}, None, "why")
    assert outcome.result.status == Status.ERROR
    assert "timed out" in (outcome.result.error or "")


async def test_an_async_action_is_awaited_directly(make_registry):
    class Async(Action):
        id = "async_thing"
        tier = ActionTier.AUTO
        description = "an async action"
        params_schema: dict = {}
        capability = "test"
        timeout_s = 5.0

        def __init__(self):
            self.calls = []

        async def run(self, ctx, params):
            await asyncio.sleep(0)
            self.calls.append(params)
            return ActionResult.success(async_ran=True)

    action = Async()
    registry = make_registry([action])
    outcome = await registry.dispatch("async_thing", {}, None, "why")
    assert outcome.result.ok
    assert outcome.result.data["async_ran"] is True


async def test_an_action_returning_the_wrong_type_is_an_error(make_registry):
    class Wrong(RecordingAction):
        def run(self, ctx, params):
            return "not an ActionResult"

    registry = make_registry([Wrong("wrong", ActionTier.AUTO)])
    outcome = await registry.dispatch("wrong", {}, None, "why")
    assert outcome.result.status == Status.ERROR
    assert "not an ActionResult" in (outcome.result.error or "")


# --- everything is audited --------------------------------------------------


async def test_every_outcome_lands_in_the_audit_log(make_registry, audit, policy):
    allowed = RecordingAction("peek", ActionTier.AUTO)
    blocked = RecordingAction("blocked", ActionTier.AUTO)
    confirmed = RecordingAction("nuke", ActionTier.CONFIRM)
    policy.set_policy("blocked", UserPolicy.NEVER)
    registry = make_registry(
        [allowed, blocked, confirmed], consent=ScriptedConsent(default=ApprovalVerdict.DENIED)
    )

    await registry.dispatch("peek", {}, None, "why", command_id="c-1")
    await registry.dispatch("blocked", {}, None, "why", command_id="c-2")
    await registry.dispatch("nuke", {}, None, "why", command_id="c-3")
    await registry.dispatch("ghost", {}, None, "why", command_id="c-4")

    entries = {e.action_id: e for e in audit.read()}
    assert set(entries) == {"peek", "blocked", "nuke", "ghost"}
    assert entries["peek"].status == "ok"
    assert entries["blocked"].status == "denied"
    assert entries["nuke"].status == "denied"
    assert entries["ghost"].status == "unsupported"
    assert entries["ghost"].tier == ActionTier.CONFIRM
    assert entries["nuke"].command_id == "c-3"
    assert all(e.note for e in entries.values())


async def test_the_audit_log_records_the_reason_the_tier_was_raised(make_registry, audit):
    action = RecordingAction("peek", ActionTier.AUTO)
    registry = make_registry([action], consent=ScriptedConsent(default=ApprovalVerdict.DENIED))
    await registry.dispatch("peek", {}, ActionTier.CONFIRM, "the server insisted")
    (entry,) = audit.read()
    assert "raised by server" in (entry.note or "")
    assert entry.tier == ActionTier.CONFIRM


async def test_the_audit_log_redacts_but_the_prompt_did_not(make_registry, audit):
    action = RecordingAction("nuke", ActionTier.CONFIRM)
    consent = ScriptedConsent(default=ApprovalVerdict.DENIED)
    registry = make_registry([action], consent=consent)

    await registry.dispatch("nuke", {"token": "sk-live-1", "path": "x"}, None, "why")

    assert consent.seen[0].params["token"] == "sk-live-1"  # the human sees the truth
    (entry,) = audit.read()
    assert entry.params["token"] == "[redacted]"  # the file does not
    assert entry.params["path"] == "x"


# --- registration errors ----------------------------------------------------


def test_duplicate_ids_are_refused(make_registry):
    with pytest.raises(ValueError, match="duplicate"):
        make_registry([RecordingAction("x", ActionTier.AUTO), RecordingAction("x", ActionTier.AUTO)])


def test_an_action_without_an_id_is_refused(make_registry):
    with pytest.raises(ValueError):
        make_registry([RecordingAction("", ActionTier.AUTO)])


def test_the_default_consent_gateway_denies(ctx, policy, audit):
    """A registry built without a gateway must not fall open."""
    from aura_desktop.actions.registry import ActionRegistry
    from aura_desktop.consent import DenyAllGateway

    registry = ActionRegistry(ctx, policy, audit)
    assert isinstance(registry.consent, DenyAllGateway)


# --- an action must not outlive the dispatcher's cap ------------------------
#
# Found by adversarial review. `asyncio.wait_for` around `asyncio.to_thread`
# cancels the *wrapper*; the worker thread runs on regardless. So an action
# whose own deadline sits above `timeout_s` gets reported to the server as a
# timeout while it is still doing the thing — and the command slot has already
# been freed for a second copy of it.


def test_every_action_deadline_sits_below_the_dispatchers_cap():
    """The rule `shell.py` states and three other actions used to break: the
    subprocess deadline must be under the action's own timeout."""
    from aura_desktop.actions.apps import ListWindows
    from aura_desktop.actions.system import Notify, SetVolume

    # (action, the longest deadline it hands to a subprocess)
    for action, longest_child_deadline in (
        (SetVolume(), 20.0),  # the Windows mixer nudge
        (Notify(), 15.0),  # the PowerShell toast
        (ListWindows(), 20.0),  # the PowerShell window list
    ):
        assert action.timeout_s > longest_child_deadline, action.id


def test_a_chord_is_read_as_keys_and_anything_else_is_refused():
    from aura_desktop.actions.inputauto import parse_chord

    assert parse_chord("ctrl+l") == ["ctrl", "l"]
    assert parse_chord("Ctrl+Shift+T") == ["ctrl", "shift", "t"]
    assert parse_chord("enter") == ["enter"]
    assert parse_chord("a") == ["a"]
    for bad in ("", None, "ctrl+", "ctrl+shift+alt+win+x", "launchmail", "ctrl+ctrl+ctrl+ctrl+ctrl"):
        assert isinstance(parse_chord(bad), str)


def test_key_press_and_scroll_reach_the_gui_and_stay_on_screen(ctx, monkeypatch):
    """Computer use (the owner, 2026-09-10): a chord goes to hotkey, a key to
    press, a scroll to scroll at a point that is on the screen."""
    import dataclasses

    from aura_desktop.actions import inputauto
    from aura_desktop.config import InputConfig

    calls: list[tuple] = []

    class FakeGui:
        FAILSAFE = False
        PAUSE = 0.0

        def press(self, key):
            calls.append(("press", key))

        def hotkey(self, *keys):
            calls.append(("hotkey", keys))

        def scroll(self, clicks, x=None, y=None):
            calls.append(("scroll", clicks, x, y))

        def size(self):
            return (1920, 1080)

    monkeypatch.setattr(inputauto, "_pyautogui", lambda: FakeGui())
    ctx.config = dataclasses.replace(ctx.config, input_automation=InputConfig(enabled=True))

    assert inputauto.KeyPress().run(ctx, {"keys": "ctrl+l"}).ok
    assert inputauto.KeyPress().run(ctx, {"keys": "enter", "times": 2}).ok
    assert not inputauto.KeyPress().run(ctx, {"keys": "launchmail"}).ok
    assert inputauto.Scroll().run(ctx, {"clicks": -3, "x": 100, "y": 200}).ok
    assert inputauto.Scroll().run(ctx, {"clicks": 500}).ok  # clamped
    assert not inputauto.Scroll().run(ctx, {"clicks": 0}).ok
    assert not inputauto.Scroll().run(ctx, {"clicks": 1, "x": 5000, "y": 5}).ok
    assert calls == [
        ("hotkey", ("ctrl", "l")),
        ("press", "enter"),
        ("press", "enter"),
        ("scroll", -3, 100, 200),
        ("scroll", 50, None, None),
    ]


def test_an_inline_screenshot_is_a_bounded_jpeg_with_its_scale():
    """The model is on another machine: the picture has to cross the channel,
    bounded, and say how to map a point in it back onto the screen."""
    import base64

    from aura_desktop.actions.inputauto import _inline

    PIL = pytest.importorskip("PIL.Image")
    big = PIL.new("RGB", (3200, 1800), (10, 20, 30))
    out = _inline(big, 1600)
    assert out["image_size"] == [1600, 900] and out["scale"] == 2.0
    jpeg = base64.b64decode(out["image_jpeg_base64"])
    assert jpeg[:3] == b"\xff\xd8\xff" and len(jpeg) < 200_000
    small = _inline(PIL.new("RGB", (640, 480)), 1600)
    assert small["image_size"] == [640, 480] and small["scale"] == 1.0


def test_type_text_finishes_inside_its_budget(ctx, monkeypatch):
    """4000 characters at the maximum interval would be over an hour of typing
    under a 120s cap: the keystrokes would carry on long after the server had
    been told the action timed out."""
    from aura_desktop.actions import inputauto

    typed: dict[str, float] = {}

    class FakeGui:
        FAILSAFE = False
        PAUSE = 0.0

        def write(self, text, interval):
            typed["chars"] = len(text)
            typed["interval"] = interval

        def press(self, key):
            pass

    import dataclasses

    from aura_desktop.config import InputConfig

    monkeypatch.setattr(inputauto, "_pyautogui", lambda: FakeGui())
    ctx.config = dataclasses.replace(ctx.config, input_automation=InputConfig(enabled=True))

    action = inputauto.TypeText()
    result = action.run(ctx, {"text": "x" * 4000, "interval_s": 1.0})

    assert result.ok
    assert typed["chars"] * typed["interval"] < action.timeout_s


# --- media keys and window close/minimize: fixed argv, never a shell string ---


def _fake_os(monkeypatch, ctx, module, system, tools):
    """Pretend to be ``system`` with only ``tools`` on PATH; return the argv log."""
    import platform

    monkeypatch.setattr(platform, "system", lambda: system)
    monkeypatch.setattr(ctx, "which", lambda *names: next((n for n in names if n in tools), None))
    seen: list[list[str]] = []
    monkeypatch.setattr(module, "_run", lambda argv, timeout=8.0: (seen.append(argv), (0, "", ""))[1])
    return seen


@pytest.mark.parametrize(
    "command, verb",
    [("play_pause", "play-pause"), ("play", "play"), ("pause", "pause"), ("next", "next"),
     ("previous", "previous"), ("stop", "stop")],
)
def test_media_on_linux_drives_mpris_through_playerctl(ctx, monkeypatch, command, verb):
    from aura_desktop.actions import system

    seen = _fake_os(monkeypatch, ctx, system, "Linux", {"playerctl"})
    action = system.MediaControl()
    assert action.available(ctx)
    assert action.run(ctx, {"command": command}).ok
    assert seen == [["playerctl", verb]]


def test_media_refuses_an_unknown_command_and_says_what_to_install(ctx, monkeypatch):
    from aura_desktop.actions import system

    seen = _fake_os(monkeypatch, ctx, system, "Linux", {"playerctl"})
    assert not system.MediaControl().run(ctx, {"command": "; rm -rf ~"}).ok and seen == []
    _fake_os(monkeypatch, ctx, system, "Linux", set())
    assert not system.MediaControl().available(ctx)
    assert "playerctl" in (system.MediaControl().unavailable_reason(ctx) or "")


def test_media_on_windows_sends_the_media_virtual_keys(ctx, monkeypatch):
    """Windows has no MPRIS; the media keys are the one control every player hears.
    There is no separate play or pause key, so those two are an honest no."""
    from aura_desktop.actions import system

    seen = _fake_os(monkeypatch, ctx, system, "Windows", {"powershell"})
    assert system.MediaControl().run(ctx, {"command": "next"}).ok
    assert "[char]176" in seen[0][-1]
    assert system.MediaControl().run(ctx, {"command": "play"}).status == Status.UNSUPPORTED


def test_window_close_and_minimize_use_the_window_tools(ctx, monkeypatch):
    from aura_desktop.actions import apps

    action = apps.WindowControl()
    seen = _fake_os(monkeypatch, ctx, apps, "Linux", {"wmctrl", "xdotool"})
    assert action.run(ctx, {"command": "close", "window_id": "0x04a00007"}).ok
    assert action.run(ctx, {"command": "close", "title": "Notes"}).ok
    assert action.run(ctx, {"command": "minimize", "window_id": "0x04a00007"}).ok
    assert seen == [["wmctrl", "-i", "-c", "0x04a00007"], ["wmctrl", "-c", "Notes"],
                    ["xdotool", "windowminimize", "0x04a00007"]]
    assert not action.run(ctx, {"command": "maximize", "title": "Notes"}).ok
    assert not action.run(ctx, {"command": "close", "window_id": "1; reboot"}).ok
    # Closing can lose unsaved work; hiding a window cannot.
    assert action.tier_for({"command": "close"}) == ActionTier.CONFIRM
    assert action.tier_for({"command": "minimize"}) == ActionTier.NOTIFY


# --- media_now_playing / play_media: the phone's two media actions, per platform ---


def _canned(monkeypatch, ctx, module, system, tools, replies):
    """Like ``_fake_os`` but ``_run`` answers from ``replies`` (a tuple, or argv -> tuple)."""
    seen = _fake_os(monkeypatch, ctx, module, system, tools)
    monkeypatch.setattr(
        module, "_run",
        lambda argv, timeout=8.0: (seen.append(argv), replies(argv) if callable(replies) else replies)[1],
    )
    return seen


def test_now_playing_and_play_media_are_tier_one_like_the_phone():
    assert TIER_TABLE["media_now_playing"] == ActionTier.AUTO
    assert TIER_TABLE["play_media"] == ActionTier.AUTO


def test_now_playing_on_linux_reads_mpris_through_playerctl(ctx, monkeypatch):
    from aura_desktop.actions import system

    seen = _canned(monkeypatch, ctx, system, "Linux", {"playerctl"},
                   (0, "Playing\tspotify\tSo What\tMiles Davis\tKind of Blue\n", ""))
    result = system.MediaNowPlaying().run(ctx, {})
    assert result.ok and seen[0][:2] == ["playerctl", "metadata"]
    assert result.data == {"title": "So What", "artist": "Miles Davis", "album": "Kind of Blue",
                           "app": "spotify", "playing": True, "state": "playing",
                           "spoken": "playing So What — Miles Davis in spotify"}


def test_now_playing_with_no_player_is_an_answer_not_an_error(ctx, monkeypatch):
    from aura_desktop.actions import system

    _canned(monkeypatch, ctx, system, "Linux", {"playerctl"}, (1, "", "No players found\n"))
    result = system.MediaNowPlaying().run(ctx, {})
    assert result.ok and result.data["playing"] is False
    assert result.data["spoken"] == "nothing is playing"


def test_now_playing_on_windows_reads_the_system_media_session(ctx, monkeypatch):
    from aura_desktop.actions import system

    out = ('{"status":"Paused","app":"Spotify.exe","title":"Blue in Green",'
           '"artist":"Miles Davis","album":null}')
    seen = _canned(monkeypatch, ctx, system, "Windows", {"powershell"}, (0, out, ""))
    result = system.MediaNowPlaying().run(ctx, {})
    assert "GlobalSystemMediaTransportControlsSessionManager" in seen[0][-1]
    assert result.data["playing"] is False and result.data["state"] == "paused"
    assert result.data["spoken"] == "paused: Blue in Green — Miles Davis in Spotify.exe"
    _canned(monkeypatch, ctx, system, "Windows", {"powershell"}, (0, "", ""))
    assert system.MediaNowPlaying().run(ctx, {}).data["spoken"] == "nothing is playing"


def test_now_playing_on_macos_asks_spotify_then_music(ctx, monkeypatch):
    from aura_desktop.actions import system

    def replies(argv):
        if "Spotify" in argv[-1]:
            return (1, "", "Can't get application")  # not installed
        return (0, "playing\tSo What\tMiles Davis\tKind of Blue\n", "")

    seen = _canned(monkeypatch, ctx, system, "Darwin", {"osascript"}, replies)
    result = system.MediaNowPlaying().run(ctx, {})
    assert [a[0] for a in seen] == ["osascript", "osascript"]
    assert result.data["app"] == "Music" and result.data["playing"] is True


@pytest.mark.parametrize(
    "params, target",
    [
        ({"source": "https://radio.example/stream.mp3"}, "https://radio.example/stream.mp3"),
        ({"source": "spotify:track:4uLU6hMCjMI75M1A2tKUQC"}, "spotify:track:4uLU6hMCjMI75M1A2tKUQC"),
        ({"query": "miles davis; rm -rf ~"}, "spotify:search:miles%20davis%3B%20rm%20-rf%20~"),
    ],
)
def test_play_media_hands_a_checked_uri_to_the_default_player(ctx, monkeypatch, params, target):
    from aura_desktop.actions import system

    seen = _fake_os(monkeypatch, ctx, system, "Linux", {"xdg-open"})
    assert system.PlayMedia().run(ctx, params).ok
    assert seen == [["xdg-open", target]]
    seen = _fake_os(monkeypatch, ctx, system, "Darwin", {"open"})
    assert system.PlayMedia().run(ctx, params).ok
    assert seen == [["open", target]]


def test_play_media_on_windows_uses_shell_execute_not_a_shell(ctx, monkeypatch):
    import os

    from aura_desktop.actions import system

    seen = _fake_os(monkeypatch, ctx, system, "Windows", set())
    opened: list[str] = []
    monkeypatch.setattr(os, "startfile", opened.append, raising=False)
    assert system.PlayMedia().run(ctx, {"source": "spotify:album:1weenld61qoidwYuZ1GESA"}).ok
    assert opened == ["spotify:album:1weenld61qoidwYuZ1GESA"] and seen == []


def test_play_media_plays_an_audio_file_inside_the_workspace_only(ctx, monkeypatch, workspace):
    from aura_desktop.actions import system

    song = workspace / "song.mp3"
    song.write_bytes(b"ID3")
    (workspace / "evil.sh").write_text("echo hi")
    seen = _fake_os(monkeypatch, ctx, system, "Linux", {"xdg-open"})
    action = system.PlayMedia()
    assert action.run(ctx, {"source": "song.mp3"}).ok
    assert action.run(ctx, {"source": song.resolve().as_uri()}).ok
    # The opener gets a private copy of the checked bytes, never the workspace path.
    assert len(seen) == 2 and all(argv[0] == "xdg-open" for argv in seen)
    for argv in seen:
        opened = Path(argv[1])
        assert opened.name == "song.mp3" and opened != song.resolve()
        assert opened.read_bytes() == b"ID3" and oct(opened.parent.stat().st_mode)[-3:] == "700"
    # Opening a script with its default handler could run it: media files only.
    assert not action.run(ctx, {"source": "evil.sh"}).ok
    assert not action.run(ctx, {"source": "file:///etc/passwd"}).ok
    assert not action.run(ctx, {"source": "/etc/hosts"}).ok
    assert len(seen) == 2


@pytest.mark.parametrize("name,head,ok", [
    ("disguised.mp3", b"#!/bin/sh\nrm -rf ~\n", False),
    ("disguised.mp4", b"\x7fELF\x02\x01\x01\x00", False),
    ("disguised.ogg", b"MZ\x90\x00\x03\x00", False),
    ("disguised.mkv", b"[Desktop Entry]\nExec=x", False),
    ("real.flac", b"fLaC\x00\x00\x00\x22", True),
    ("real.ogg", b"OggS\x00\x02", True),
    ("real.wav", b"RIFF\x24\x00\x00\x00WAVEfmt ", True),
    ("real.m4a", b"\x00\x00\x00\x20ftypM4A ", True),
    ("real.webm", b"\x1aE\xdf\xa3\x01\x00", True),
    ("bare.mp3", b"\xff\xfb\x90\x64", True),
])
def test_play_media_checks_a_files_bytes_not_only_its_name(ctx, monkeypatch, workspace, name, head, ok):
    # xdg-open picks the handler by sniffing content: a script named like a song is refused.
    from aura_desktop.actions import system

    (workspace / name).write_bytes(head)
    _fake_os(monkeypatch, ctx, system, "Linux", {"xdg-open"})
    assert system.PlayMedia().run(ctx, {"source": name}).ok is ok


@pytest.mark.parametrize(
    "source",
    ["javascript:alert(1)", "ftp://x/y.mp3", "smb://host/share/a.mp3", "-e evil",
     "https://x/\nrm", "spotify:track:x y", ""],
)
def test_play_media_refuses_other_schemes_and_runs_nothing(ctx, monkeypatch, source):
    from aura_desktop.actions import system

    seen = _fake_os(monkeypatch, ctx, system, "Linux", {"xdg-open"})
    assert not system.PlayMedia().run(ctx, {"source": source}).ok
    assert seen == []


def test_play_media_a_swap_after_the_check_never_reaches_the_opener(ctx, monkeypatch, workspace):
    from aura_desktop.actions import system

    song = workspace / "song.mp3"
    song.write_bytes(b"ID3\x03")
    opened: list = []

    def run(argv, **_):
        song.write_bytes(b"#!/bin/sh\nevil\n")  # the race: swapped once the check is done
        opened.append(Path(argv[1]).read_bytes())
        return 0, "", ""
    _fake_os(monkeypatch, ctx, system, "Linux", {"xdg-open"})
    monkeypatch.setattr(system, "_run", run)
    assert system.PlayMedia().run(ctx, {"source": "song.mp3"}).ok
    assert opened == [b"ID3\x03"]
# --- brightness, dnd, wallpaper, screen timeout: the phone's settings, per platform ---


def test_brightness_dnd_wallpaper_and_screen_timeout_carry_the_phones_tiers():
    assert TIER_TABLE["set_brightness"] == ActionTier.NOTIFY
    assert TIER_TABLE["toggle_dnd"] == ActionTier.NOTIFY
    assert TIER_TABLE["set_wallpaper"] == ActionTier.NOTIFY
    assert TIER_TABLE["set_screen_timeout"] == ActionTier.AUTO


@pytest.mark.parametrize("system_name, tools, argv", [
    ("Linux", {"brightnessctl", "ddcutil"}, ["brightnessctl", "set", "40%"]),
    ("Linux", {"ddcutil"}, ["ddcutil", "setvcp", "10", "40"]),
    ("Darwin", {"brightness"}, ["brightness", "0.40"]),
])
def test_brightness_builds_a_fixed_argv_per_platform(ctx, monkeypatch, system_name, tools, argv):
    from aura_desktop.actions import system

    seen = _fake_os(monkeypatch, ctx, system, system_name, tools)
    result = system.SetBrightness().run(ctx, {"level": 40})
    assert result.ok and result.data["level"] == 40
    assert seen == [argv]


def test_brightness_on_windows_goes_through_wmi_with_a_number_only(ctx, monkeypatch):
    from aura_desktop.actions import system

    seen = _fake_os(monkeypatch, ctx, system, "Windows", {"powershell"})
    assert system.SetBrightness().run(ctx, {"level": 75}).ok
    assert seen[0][:3] == ["powershell", "-NoProfile", "-Command"]
    assert "WmiSetBrightness(1,75)" in seen[0][-1]


@pytest.mark.parametrize("level", [-1, 101, "40; rm -rf ~", None])
def test_brightness_refuses_a_level_out_of_range_and_runs_nothing(ctx, monkeypatch, level):
    from aura_desktop.actions import system

    seen = _fake_os(monkeypatch, ctx, system, "Linux", {"brightnessctl"})
    params = {} if level is None else {"level": level}
    assert not system.SetBrightness().run(ctx, params).ok and seen == []


def test_brightness_without_a_command_is_unsupported(ctx, monkeypatch):
    from aura_desktop.actions import system

    for name in ("Linux", "Darwin"):
        seen = _fake_os(monkeypatch, ctx, system, name, set())
        action = system.SetBrightness()
        assert not action.available(ctx)
        assert action.run(ctx, {"level": 40}).status == Status.UNSUPPORTED and seen == []
    _fake_os(monkeypatch, ctx, system, "Linux", set())
    assert "brightnessctl" in (system.SetBrightness().unavailable_reason(ctx) or "")


@pytest.mark.parametrize("tools, on, off", [
    ({"dunstctl", "gsettings"}, ["dunstctl", "set-paused", "true"], ["dunstctl", "set-paused", "false"]),
    ({"swaync-client"}, ["swaync-client", "--dnd-on"], ["swaync-client", "--dnd-off"]),
    ({"gsettings"},
     ["gsettings", "set", "org.gnome.desktop.notifications", "show-banners", "false"],
     ["gsettings", "set", "org.gnome.desktop.notifications", "show-banners", "true"]),
])
def test_dnd_on_linux_pauses_the_notification_daemon(ctx, monkeypatch, tools, on, off):
    from aura_desktop.actions import system

    seen = _fake_os(monkeypatch, ctx, system, "Linux", tools)
    action = system.ToggleDnd()
    assert action.run(ctx, {"enabled": True}).data == {"enabled": True}
    assert action.run(ctx, {"enabled": False}).ok
    assert seen == [on, off]


def test_dnd_needs_enabled_and_is_unsupported_where_it_is_not_scriptable(ctx, monkeypatch):
    from aura_desktop.actions import system

    seen = _fake_os(monkeypatch, ctx, system, "Linux", {"gsettings"})
    assert not system.ToggleDnd().run(ctx, {}).ok and seen == []
    # Windows Focus Assist and macOS Focus have no supported command line.
    for name in ("Windows", "Darwin"):
        seen = _fake_os(monkeypatch, ctx, system, name, {"powershell", "osascript", "gsettings"})
        action = system.ToggleDnd()
        assert not action.available(ctx)
        assert action.run(ctx, {"enabled": True}).status == Status.UNSUPPORTED and seen == []


_PNG = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"


def test_wallpaper_on_linux_uses_plasma_then_gnome(ctx, monkeypatch, workspace):
    from aura_desktop.actions import system

    img = workspace / "my sky's.png"
    img.write_bytes(_PNG)
    path = str(img.resolve())
    seen = _fake_os(monkeypatch, ctx, system, "Linux", {"plasma-apply-wallpaperimage", "gsettings"})
    assert system.SetWallpaper().run(ctx, {"path": "my sky's.png"}).ok
    assert seen == [["plasma-apply-wallpaperimage", path]]
    seen = _fake_os(monkeypatch, ctx, system, "Linux", {"gsettings"})
    assert system.SetWallpaper().run(ctx, {"path": "my sky's.png"}).data["path"] == path
    uri = img.resolve().as_uri()
    assert "'" not in uri and " " not in uri  # percent-encoded: a safe GVariant literal
    assert seen == [
        ["gsettings", "set", "org.gnome.desktop.background", "picture-uri", f"'{uri}'"],
        ["gsettings", "set", "org.gnome.desktop.background", "picture-uri-dark", f"'{uri}'"],
    ]


def test_wallpaper_on_macos_passes_the_path_as_an_argument_not_script_text(ctx, monkeypatch, workspace):
    from aura_desktop.actions import system

    img = workspace / 'a" & do shell script "x.jpg'
    img.write_bytes(b"\xff\xd8\xff\xe0\x00\x10JFIF")
    seen = _fake_os(monkeypatch, ctx, system, "Darwin", {"osascript"})
    assert system.SetWallpaper().run(ctx, {"path": img.name}).ok
    assert seen[0][0] == "osascript" and seen[0][-1] == str(img.resolve())
    assert all(img.name not in part for part in seen[0][:-1])


def test_wallpaper_on_windows_calls_system_parameters_info_directly(ctx, monkeypatch, workspace):
    from aura_desktop.actions import system

    img = workspace / "w.webp"
    img.write_bytes(b"RIFF\x24\x00\x00\x00WEBPVP8 ")
    seen = _fake_os(monkeypatch, ctx, system, "Windows", {"powershell"})
    called: list[str] = []
    monkeypatch.setattr(system, "_windows_set_wallpaper", lambda p: (called.append(p), True)[1])
    assert system.SetWallpaper().run(ctx, {"path": "w.webp"}).ok
    assert called == [str(img.resolve())] and seen == []


@pytest.mark.parametrize("name, head", [
    ("evil.png", b"#!/bin/sh\nrm -rf ~\n"),
    ("evil.jpg", b"\x7fELF\x02\x01\x01\x00"),
    ("evil.webp", b"RIFF\x24\x00\x00\x00WAVEfmt "),
    ("notes.txt", _PNG),
])
def test_wallpaper_checks_the_name_and_the_bytes(ctx, monkeypatch, workspace, name, head):
    from aura_desktop.actions import system

    (workspace / name).write_bytes(head)
    seen = _fake_os(monkeypatch, ctx, system, "Linux", {"gsettings"})
    assert not system.SetWallpaper().run(ctx, {"path": name}).ok and seen == []


@pytest.mark.parametrize("path", ["/etc/hosts", "../outside.png", "", "missing.png"])
def test_wallpaper_takes_workspace_files_only(ctx, monkeypatch, path):
    from aura_desktop.actions import system

    seen = _fake_os(monkeypatch, ctx, system, "Linux", {"gsettings"})
    assert not system.SetWallpaper().run(ctx, {"path": path}).ok and seen == []


def test_wallpaper_without_a_command_is_unsupported(ctx, monkeypatch, workspace):
    from aura_desktop.actions import system

    (workspace / "a.png").write_bytes(_PNG)
    seen = _fake_os(monkeypatch, ctx, system, "Linux", set())
    assert not system.SetWallpaper().available(ctx)
    assert system.SetWallpaper().run(ctx, {"path": "a.png"}).status == Status.UNSUPPORTED
    assert seen == []


@pytest.mark.parametrize("system_name, tools, argvs", [
    ("Linux", {"gsettings"},
     [["gsettings", "set", "org.gnome.desktop.session", "idle-delay", "uint32 300"]]),
    ("Windows", {"powercfg"},
     [["powercfg", "/change", "monitor-timeout-ac", "5"],
      ["powercfg", "/change", "monitor-timeout-dc", "5"]]),
])
def test_screen_timeout_builds_a_fixed_argv_per_platform(ctx, monkeypatch, system_name, tools, argvs):
    from aura_desktop.actions import system

    seen = _fake_os(monkeypatch, ctx, system, system_name, tools)
    result = system.SetScreenTimeout().run(ctx, {"seconds": 300})
    assert result.ok and result.data["seconds"] == 300
    assert seen == argvs


def test_screen_timeout_on_windows_rounds_up_to_whole_minutes(ctx, monkeypatch):
    from aura_desktop.actions import system

    seen = _fake_os(monkeypatch, ctx, system, "Windows", {"powercfg"})
    result = system.SetScreenTimeout().run(ctx, {"seconds": 15})
    assert result.ok and seen[0][-1] == "1" and result.data["seconds"] == 60


@pytest.mark.parametrize("seconds", [14, 1801, -5, "300; reboot", None])
def test_screen_timeout_refuses_a_value_outside_the_phones_range(ctx, monkeypatch, seconds):
    from aura_desktop.actions import system

    seen = _fake_os(monkeypatch, ctx, system, "Linux", {"gsettings"})
    params = {} if seconds is None else {"seconds": seconds}
    assert not system.SetScreenTimeout().run(ctx, params).ok and seen == []


def test_screen_timeout_without_a_command_is_unsupported(ctx, monkeypatch):
    from aura_desktop.actions import system

    for name in ("Linux", "Darwin"):
        seen = _fake_os(monkeypatch, ctx, system, name, set())
        assert not system.SetScreenTimeout().available(ctx)
        assert system.SetScreenTimeout().run(ctx, {"seconds": 300}).status == Status.UNSUPPORTED
        assert seen == []


# --- sleep: the suspend command per platform, with the system call faked (T-1373) ---


def _fake_suspend_tools(monkeypatch, ctx, system, system_name, tools):
    """``_fake_os`` plus ``shutil.which``, which Sleep.run calls directly for its argv[0]."""
    seen = _fake_os(monkeypatch, ctx, system, system_name, tools)
    monkeypatch.setattr(system.shutil, "which", lambda name: name if name in tools else None)

    def no_real_command(argv, *args, **kwargs):
        # If Sleep ever bypasses the faked _run, fail here rather than suspend the host.
        raise AssertionError(f"a sleep test reached subprocess.run with {argv!r}")

    monkeypatch.setattr(system.subprocess, "run", no_real_command)
    return seen


@pytest.mark.parametrize("system_name, tools, argv", [
    ("Linux", {"systemctl", "loginctl", "pm-suspend"}, ["systemctl", "suspend"]),
    ("Linux", {"loginctl", "pm-suspend"}, ["loginctl", "suspend"]),
    ("Linux", {"pm-suspend"}, ["pm-suspend"]),
    ("Darwin", {"pmset"}, ["pmset", "sleepnow"]),
    ("Windows", {"rundll32.exe"}, ["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"]),
])
def test_sleep_runs_the_platform_suspend_command(ctx, monkeypatch, system_name, tools, argv):
    from aura_desktop.actions import system

    seen = _fake_suspend_tools(monkeypatch, ctx, system, system_name, tools)
    result = system.Sleep().run(ctx, {})
    assert result.ok and result.data["suspended_with"] == argv[0]
    assert seen == [argv]


def test_sleep_moves_on_to_the_next_command_when_one_refuses(ctx, monkeypatch):
    from aura_desktop.actions import system

    tools = {"systemctl", "loginctl"}
    seen = _fake_suspend_tools(monkeypatch, ctx, system, "Linux", tools)

    def refuse_systemctl(argv, timeout=8.0):
        seen.append(argv)
        return (1 if argv[0] == "systemctl" else 0), "", ""

    monkeypatch.setattr(system, "_run", refuse_systemctl)
    result = system.Sleep().run(ctx, {})
    assert result.ok and result.data["suspended_with"] == "loginctl"
    assert seen == [["systemctl", "suspend"], ["loginctl", "suspend"]]


def test_sleep_does_not_suspend_twice_when_the_first_command_timed_out(ctx, monkeypatch):
    # A suspend call that has not returned is the normal case while the machine sleeps: a
    # second command would put it straight back to sleep on wake (T-1375).
    from aura_desktop.actions import system

    seen = _fake_suspend_tools(monkeypatch, ctx, system, "Linux", {"systemctl", "loginctl"})

    def time_out(argv, timeout=8.0):
        seen.append(argv)
        return 124, "", f"{argv[0]}: timed out"

    monkeypatch.setattr(system, "_run", time_out)
    result = system.Sleep().run(ctx, {})
    assert seen == [["systemctl", "suspend"]]
    assert result.ok and result.data == {"suspended_with": "systemctl", "confirmed": False}


@pytest.mark.parametrize("system_name, tools, argv", [
    ("Linux", {"loginctl", "swaylock", "i3lock"}, ["loginctl", "lock-session"]),
    ("Linux", {"swaylock", "i3lock"}, ["swaylock", "-f"]),
    ("Linux", {"i3lock"}, ["i3lock"]),
    ("Linux", {"gnome-screensaver-command"}, ["gnome-screensaver-command", "--lock"]),
    ("Linux", {"xdg-screensaver"}, ["xdg-screensaver", "lock"]),
    ("Darwin", {"pmset"}, ["pmset", "displaysleepnow"]),
    ("Windows", {"rundll32.exe"}, ["rundll32.exe", "user32.dll,LockWorkStation"]),
])
def test_lock_runs_the_platform_lock_command(ctx, monkeypatch, system_name, tools, argv):
    from aura_desktop.actions import system

    seen = _fake_suspend_tools(monkeypatch, ctx, system, system_name, tools)
    result = system.LockScreen().run(ctx, {})
    assert result.ok and result.data["locked_with"] == argv[0]
    assert seen == [argv]


def test_lock_without_a_locker_runs_nothing_and_fails(ctx, monkeypatch):
    from aura_desktop.actions import system

    seen = _fake_suspend_tools(monkeypatch, ctx, system, "Linux", set())
    assert not system.LockScreen().run(ctx, {}).ok and seen == []


def test_sleep_without_a_suspend_command_is_unavailable_and_runs_nothing(ctx, monkeypatch):
    from aura_desktop.actions import system

    seen = _fake_suspend_tools(monkeypatch, ctx, system, "Linux", set())
    assert not system.Sleep().available(ctx)
    assert not system.Sleep().run(ctx, {}).ok and seen == []


def test_play_media_copies_are_bounded_while_copying_and_pruned(ctx, monkeypatch, workspace, tmp_path):
    from aura_desktop.actions import system

    monkeypatch.setattr(system.tempfile if hasattr(system, "tempfile") else __import__("tempfile"),
                        "tempdir", str(tmp_path))
    monkeypatch.setattr(system, "MAX_MEDIA_COPY", 8)
    big = workspace / "big.mp3"
    big.write_bytes(b"ID3" + b"\x00" * 20)  # over the cap
    _fake_os(monkeypatch, ctx, system, "Linux", {"xdg-open"})
    assert not system.PlayMedia().run(ctx, {"source": "big.mp3"}).ok
    assert not list(tmp_path.glob(system.COPY_PREFIX + "*"))  # the partial copy is gone
    monkeypatch.setattr(system, "MAX_MEDIA_COPY", 1 << 20)
    song = workspace / "song.mp3"
    song.write_bytes(b"ID3\x03")
    for _ in range(system.KEEP_COPIES + 3):
        assert system.PlayMedia().run(ctx, {"source": "song.mp3"}).ok
    assert len(list(tmp_path.glob(system.COPY_PREFIX + "*"))) <= system.KEEP_COPIES + 1


def test_play_media_knows_common_audio_without_a_system_mime_table(ctx, monkeypatch, workspace):
    # CI's slim image has no /etc/mime.types: .flac/.ogg/.m4a guessed as nothing were refused.
    import mimetypes
    monkeypatch.setattr(mimetypes, "guess_type", lambda *a, **k: (None, None))
    test_play_media_checks_a_files_bytes_not_only_its_name(ctx, monkeypatch, workspace, "real.flac", b"fLaC\x00\x00\x00\x22", True)


def test_wallpaper_knows_webp_without_a_system_mime_table(ctx, monkeypatch, workspace):
    # Python's built-in table has no .webp; CI's slim image (and a bare desktop) refused it.
    import mimetypes
    monkeypatch.setattr(mimetypes, "guess_type", lambda *a, **k: (None, None))
    test_wallpaper_on_windows_calls_system_parameters_info_directly(ctx, monkeypatch, workspace)
