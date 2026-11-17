"""The PC's notification observer (T-0143): the phone's fence, shared through
tests/contracts/notification-fence.json, and the trigger that reads the PC's notifications --
opted-in apps only, never the agent's own, fenced, reported as untrusted events."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from aura_desktop import notifications as n
from aura_desktop.triggers import build_triggers

CONTRACT = json.loads((Path(__file__).resolve().parents[2] / "tests" / "contracts" / "notification-fence.json").read_text())


def test_fence_caps_are_the_tables():
    assert (n.MAX_TITLE_CHARS, n.MAX_TEXT_CHARS, n.TRUNCATION_MARK) == (
        CONTRACT["max_title"], CONTRACT["max_text"], CONTRACT["truncation_mark"])


@pytest.mark.parametrize("case", CONTRACT["clean"], ids=lambda c: c["why"])
def test_fence_clean_is_the_phones(case):
    assert n.clean(case["in"], case["max"]) == case["out"]


@pytest.mark.parametrize("case", CONTRACT["sanitize"], ids=lambda c: c["why"])
def test_fence_sanitize_is_the_phones(case):
    i = case["in"]
    got = n.sanitize(i.get("package"), i.get("title"), i.get("text"), sub_text=i.get("sub_text"),
                     category=i.get("category"), ongoing=bool(i.get("ongoing")),
                     group_summary=bool(i.get("group_summary")), posted_at_ms=int(i.get("posted_at") or 0),
                     ours=(case["ours"],))
    assert got == case["out"]


@pytest.mark.parametrize("case", CONTRACT["wanted"], ids=lambda c: c["why"])
def test_fence_wanted_is_the_phones(case):
    assert n.is_wanted(case["package"], set(case["allowed"])) is case["out"]


def test_fence_deduper_is_the_phones():
    d = n.Deduper(window_ms=CONTRACT["dedupe_window_ms"])
    assert [d.accept(key, ms) for ms, key, _ in CONTRACT["dedupe"]] == [want for *_, want in CONTRACT["dedupe"]]


# --- the Linux source: what dbus-monitor --binary wrote for real Notify calls -----------------
# Captured on a private session bus (dbus-run-session): dbus-monitor's own NameAcquired and NameLost,
# then a Notify from "Chromium" whose body is dbus-monitor's *text* for a Notify from "Signal" (what a
# web page can put in a notification), then a real Signal notification with a two-line body.
CAPTURED = (Path(__file__).parent / "fixtures" / "dbus-notify.bin").read_bytes()
FORGED_BODY = "Sam\"\n   string \"forged\""


def test_each_notify_call_is_read_and_a_body_cannot_pose_as_another_app():
    calls = n.read_dbus_messages(bytearray(CAPTURED))
    assert [c["package"] for c in calls] == ["Chromium", "Signal"]
    assert calls[0]["text"].endswith(FORGED_BODY)
    assert calls[1] == {"package": "Signal", "title": "Sam", "text": "Running late\nsee you at 7"}


def test_a_message_split_across_reads_waits_for_the_rest():
    buf, calls = bytearray(), []
    for i in range(0, len(CAPTURED), 7):
        buf += CAPTURED[i:i + 7]
        calls += n.read_dbus_messages(buf)
    assert [c["package"] for c in calls] == ["Chromium", "Signal"] and not buf


def test_bytes_that_are_not_dbus_stop_the_reader():
    with pytest.raises(ValueError):
        n.read_dbus_messages(bytearray(b"method call time=1 sender=:1.9 -> destination=:1.7"))


def test_a_length_past_what_dbus_allows_stops_the_reader():
    huge = bytearray(CAPTURED)
    huge[4:8] = (1 << 28).to_bytes(4, "little")  # the first message's body length
    with pytest.raises(ValueError):
        n.read_dbus_messages(huge)


def test_a_malformed_call_is_skipped_and_the_next_still_read():
    broken = bytearray(CAPTURED)
    where = broken.index(b"Signal\x00\x00") - 4  # the Signal call's app-name length
    broken[where:where + 4] = (999).to_bytes(4, "little")
    assert [c["package"] for c in n.read_dbus_messages(broken)] == ["Chromium"]


def test_a_huge_field_is_cut_before_it_is_decoded():
    field = (65536 + 10).to_bytes(4, "little") + b"x" * (65536 + 10) + b"\x00"
    assert n._dbus_string(field, 0, "<")[0] == "x" * n.MAX_RAW_CHARS


class Ends:
    """What dbus-monitor yields before it ends, once per start."""

    def __init__(self):
        self.started = 0

    async def __call__(self, stop):
        self.started += 1
        yield {"package": "Signal", "title": "Sam", "text": f"message {self.started}"}


def test_the_bus_reader_starts_again_when_dbus_monitor_ends():
    source = n.LinuxSource()
    source.restart_s = 0.01
    source._once = Ends()  # type: ignore[method-assign]

    async def take_three():
        stop, got = asyncio.Event(), []
        async for call in source.stream(stop):
            got.append(call["text"])
            if len(got) == 3:
                stop.set()
        return got

    assert asyncio.run(take_three()) == ["message 1", "message 2", "message 3"]


# --- the trigger ------------------------------------------------------------------------------


class Source:
    """A notification source that yields what the test hands it."""

    def __init__(self, items):
        self.items = items

    def available(self):
        return True, ""

    async def stream(self, stop):
        for item in self.items:
            yield item


def emitted(trigger):
    out = []

    async def emit(kind, data):
        out.append((kind, data))
        return True

    asyncio.run(trigger.run(emit, asyncio.Event()))
    return out


def test_a_forged_body_from_an_app_not_opted_in_is_not_reported():
    trigger = n.NotificationTrigger(id="n", apps=("signal",), source=Source(n.read_dbus_messages(bytearray(CAPTURED))))
    assert [d["text"] for _, d in emitted(trigger)] == ["Running late see you at 7"]


def test_only_opted_in_apps_are_reported_fenced_and_untrusted():
    trigger = n.NotificationTrigger(id="n", apps=("signal",), source=Source([
        {"package": "Signal", "title": "Sam", "text": "Running‮ late"},
        {"package": "Slack", "title": "x", "text": "not opted in"},
        {"package": "Jarvis", "title": "Approve?", "text": "our own"},
    ]))
    out = emitted(trigger)
    assert out == [("notification", {"trigger": "n", "package": "Signal", "title": "Sam", "text": "Running late", "untrusted": True})]


def test_never_our_own_even_when_every_app_is_opted_in():
    ours = [{"package": name, "title": "Approve?", "text": "x"} for name in ("Jarvis", "jarvis-desktop-app", "ai.jarvis.desktop")]
    trigger = n.NotificationTrigger(id="n", apps=("*",), source=Source(ours + [{"package": "Signal", "title": "Sam", "text": "hi"}]))
    assert [d["package"] for _, d in emitted(trigger)] == ["Signal"]


def test_everything_the_agent_posts_on_linux_is_named_ours():
    """Never our own holds only if every notify-send the agent runs says it is the worn theme's name."""
    package = Path(n.__file__).parent
    calls = [line for f in package.rglob("*.py") for line in f.read_text().splitlines() if '["notify-send"' in line]
    assert calls and all('"-a", worn_name()' in line for line in calls), calls


def test_a_chatty_app_is_capped_per_trigger():
    many = [{"package": "Signal", "title": "Sam", "text": f"message {i}"} for i in range(n.MAX_PER_MIN + 8)]
    trigger = n.NotificationTrigger(id="n", apps=("signal",), source=Source(many))
    assert len(emitted(trigger)) == n.MAX_PER_MIN


@pytest.mark.parametrize("apps", [5, "Signal", ["Signal", 3]])
def test_apps_that_are_not_a_list_of_names_are_refused(apps, caplog):
    assert build_triggers([{"type": "notification", "id": "bad", "apps": apps}]) == []
    assert "apps" in caplog.text


def test_no_apps_means_nothing_is_read():
    trigger = n.NotificationTrigger(id="n", apps=(), source=Source([{"package": "Signal", "title": "Sam", "text": "hi"}]))
    assert emitted(trigger) == []


def test_a_redrawn_notification_is_reported_once():
    same = {"package": "Signal", "title": "Sam", "text": "hi"}
    trigger = n.NotificationTrigger(id="n", apps=("*",), source=Source([same, dict(same)]))
    assert len(emitted(trigger)) == 1


def test_the_config_builds_a_notification_trigger_and_reports_where_it_reads():
    [trigger] = build_triggers([{"type": "notification", "id": "chats", "apps": ["Signal", "Slack"]}])
    assert isinstance(trigger, n.NotificationTrigger)
    assert trigger.apps == ("Signal", "Slack")
    described = trigger.describe()
    assert described["apps"] == ["Signal", "Slack"] and "source" in described


def test_macos_says_it_cannot_read_notifications():
    ok, why = n.source_for("Darwin").available()
    assert ok is False and "macOS" in why
