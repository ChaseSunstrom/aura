"""The emitter against the contract: the constants pin to
``tests/contracts/activity-frames.json``, the throttle drops a frame inside the
interval and an unchanged one at any interval, a picture in a result becomes a
reduced JPEG, the setting stops everything, the idle timer ends the activity,
and the route is jarvis-hud's store."""
from __future__ import annotations

import base64
import io
import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from _plugin_loader import load_plugin  # noqa: E402

plugin = load_plugin("jarvis-activity")
activity = plugin.activity

REPO = Path(__file__).resolve().parents[4]
CONTRACT = json.loads((REPO / "tests" / "contracts" / "activity-frames.json").read_text(encoding="utf-8"))


class Rec:
    def __init__(self) -> None:
        self.frames: list[tuple[str, str, dict]] = []

    def __call__(self, session_id: str, event: str, payload: dict) -> bool:
        self.frames.append((session_id, event, payload))
        return True

    def events(self) -> list[str]:
        return [e for _, e, _ in self.frames]

    def of(self, event: str) -> list[dict]:
        return [p for _, e, p in self.frames if e == event]


@pytest.fixture
def rec(monkeypatch):
    monkeypatch.setenv("ACTIVITY_ENABLED", "true")
    monkeypatch.setattr(activity, "SEND", Rec())
    activity.reset()
    yield activity.SEND
    activity.reset()


def _png(width: int = 1600, height: int = 900) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (width, height), (20, 40, 60)).save(buf, "PNG")
    return buf.getvalue()


def _navigate(sid: str, url: str = "https://example.com/", title: str = "Example", page: str = "- link \"More\" [ref=@e1]") -> None:
    activity.post_tool_call(tool_name="browser_navigate", args={"url": url},
                            result=json.dumps({"url": url, "title": title, "snapshot": page}),
                            session_id=sid, status="ok")


# --------------------------------------------------------------------------- contract
def test_the_plugin_copies_the_contract():
    assert activity.EVENTS == CONTRACT["events"]
    assert activity.SURFACES == CONTRACT["surfaces"]
    assert activity.TOOLS == CONTRACT["tools"]
    assert activity.THROTTLE_MS == CONTRACT["throttle"]["interval_ms"]
    assert CONTRACT["throttle"]["on_change_only"] is True
    assert activity.MAX_SIDE_PX == CONTRACT["capture"]["max_side_px"]
    assert activity.JPEG_QUALITY == CONTRACT["capture"]["quality"]
    assert activity.MAX_BYTES == CONTRACT["capture"]["max_bytes"]
    assert activity.TEXT_MAX_CHARS == CONTRACT["capture"]["text_max_chars"]
    assert activity.IDLE_END_S == CONTRACT["idle_end_s"]
    assert activity.MASK == CONTRACT["redaction"]["mask"]
    assert activity.SENSITIVE_LABELS == CONTRACT["redaction"]["sensitive_labels"]
    assert activity.END_STATUSES == CONTRACT["end_statuses"]


def test_the_matrix_knows_every_visual_tool():
    from _shared import _matrix

    known = _matrix.known_tools()
    for names in activity.TOOLS.values():
        for name in names:
            assert name in known, f"{name} is not in the approval matrix"


def test_every_event_carries_its_contract_fields(rec):
    sid = "s-fields"
    activity.pre_tool_call("browser_navigate", {"url": "https://example.com/"}, session_id=sid)
    _navigate(sid)
    activity._idle(sid, activity._LIVE[sid])
    for event, payload in [(e, p) for _, e, p in rec.frames]:
        wanted = set(CONTRACT["fields"][event])
        optional = set(CONTRACT["optional_fields"].get(event, []))
        assert wanted <= set(payload) <= wanted | optional, (event, payload)
    assert rec.events() == ["activity.start", "activity.step", "activity.frame", "activity.end"]


def test_the_pre_hook_never_answers_with_a_directive(rec):
    assert activity.pre_tool_call("browser_navigate", {"url": "x"}, session_id="s") is None
    assert activity.pre_tool_call("terminal", {"command": "ls"}, session_id="s") is None
    assert rec.events() == ["activity.start"]  # the terminal opened nothing


# --------------------------------------------------------------------------- throttle
def test_a_frame_inside_the_interval_is_dropped_and_the_next_change_is_sent(rec, monkeypatch):
    sid = "s-throttle"
    clock = [1000.0]
    monkeypatch.setattr(activity.time, "time", lambda: clock[0])
    _navigate(sid, title="One")
    clock[0] += 0.2
    _navigate(sid, title="Two")   # changed, but 200 ms after the last: dropped
    clock[0] += 0.4
    _navigate(sid, title="Three")  # 600 ms after the last sent: sent
    assert [f["title"] for f in rec.of("activity.frame")] == ["One", "Three"]
    assert len(rec.of("activity.step")) == 3  # a step is never throttled


def test_an_unchanged_page_sends_no_second_frame(rec, monkeypatch):
    sid = "s-same"
    clock = [1000.0]
    monkeypatch.setattr(activity.time, "time", lambda: clock[0])
    _navigate(sid)
    clock[0] += 5
    activity.post_tool_call(tool_name="browser_snapshot", args={},
                            result=json.dumps({"url": "https://example.com/", "title": "Example",
                                               "snapshot": "- link \"More\" [ref=@e1]"}),
                            session_id=sid, status="ok")
    assert len(rec.of("activity.frame")) == 1


# --------------------------------------------------------------------------- pictures
def test_a_picture_in_a_multimodal_result_becomes_a_reduced_jpeg(rec):
    sid = "s-picture"
    png = _png()
    result = {"_multimodal": True, "content": [
        {"type": "text", "text": "Image loaded"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(png).decode()}},
    ], "text_summary": "attached"}
    activity.post_tool_call(tool_name="browser_vision", args={}, result=result, session_id=sid, status="ok")
    frame = rec.of("activity.frame")[0]
    assert frame["image"].startswith("data:image/jpeg;base64,")
    raw = base64.b64decode(frame["image"].split(",", 1)[1])
    assert len(raw) <= activity.MAX_BYTES
    from PIL import Image

    with Image.open(io.BytesIO(raw)) as im:
        assert max(im.size) <= activity.MAX_SIDE_PX
        assert im.size == (640, 360)


def test_a_screenshot_file_a_result_names_is_read_only_under_hermes_home(rec, monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    inside = tmp_path / "screens" / "one.png"
    inside.parent.mkdir()
    inside.write_bytes(_png(200, 100))
    outside = tmp_path.parent / "elsewhere.png"
    outside.write_bytes(_png(200, 100))
    sid = "s-file"
    activity.post_tool_call(tool_name="desktop_screen", args={},
                            result=json.dumps({"ok": True, "path": str(inside)}), session_id=sid, status="ok")
    assert rec.of("activity.frame")[-1]["image"] is not None
    activity.reset()
    activity.SEND.frames.clear()
    activity.post_tool_call(tool_name="desktop_screen", args={},
                            result=json.dumps({"ok": True, "path": str(outside)}), session_id=sid, status="ok")
    assert rec.of("activity.frame") == []  # nothing to show: no frame at all


def test_a_browser_step_that_moves_the_page_is_photographed(rec, monkeypatch):
    # "the browser doesn't actually show a browser, just html" (2026-09-25): a
    # navigate gave only the text snapshot; now the session's viewport too.
    asked: list[str] = []
    monkeypatch.setattr(activity, "PICTURE", lambda task: asked.append(task) or _png(800, 450))
    activity.post_tool_call(tool_name="browser_navigate", args={"url": "https://example.com/"},
                            result=json.dumps({"url": "https://example.com/", "title": "Example"}),
                            task_id="t1", session_id="s-shot", status="ok")
    assert asked == ["t1"]
    assert rec.of("activity.frame")[-1]["image"].startswith("data:image/")
    # A read does not move the page, and a failed step has nothing new to show.
    activity.post_tool_call(tool_name="browser_snapshot", args={}, result="- heading",
                            task_id="t1", session_id="s-shot", status="ok")
    activity.post_tool_call(tool_name="browser_click", args={"ref": "@e1"}, result="",
                            task_id="t1", session_id="s-shot", status="error", error_message="gone")
    assert asked == ["t1"]


def test_a_picture_that_cannot_be_made_small_is_dropped_not_sent_whole(monkeypatch):
    monkeypatch.setattr(activity, "MAX_BYTES", 10)
    assert activity.shrink(_png()) is None


# --------------------------------------------------------------------------- the setting
def test_disabling_the_setting_stops_all_capture(rec, monkeypatch):
    monkeypatch.setenv("ACTIVITY_ENABLED", "false")
    sid = "s-off"
    activity.pre_tool_call("browser_navigate", {"url": "https://example.com/"}, session_id=sid)
    _navigate(sid)
    activity.post_tool_call(tool_name="browser_vision", args={}, result={
        "_multimodal": True,
        "content": [{"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(_png()).decode()}}],
    }, session_id=sid, status="ok")
    assert rec.frames == [] and activity._LIVE == {}


def test_an_unreadable_settings_layer_fails_closed(rec, monkeypatch):
    monkeypatch.setenv("ACTIVITY_ENABLED", "not-a-bool")
    assert activity.enabled() is False


# --------------------------------------------------------------------------- the activity
def test_the_activity_ends_by_itself_after_the_idle_time(rec, monkeypatch):
    monkeypatch.setattr(activity, "IDLE_END_S", 0.05)
    sid = "s-idle"
    activity.pre_tool_call("browser_navigate", {"url": "https://example.com/"}, session_id=sid)
    _navigate(sid)
    deadline = time.time() + 2
    while "activity.end" not in rec.events() and time.time() < deadline:
        time.sleep(0.01)
    end = rec.of("activity.end")[0]
    assert end["status"] == "done" and end["steps"] == 1
    assert activity._LIVE == {}


def test_a_call_slower_than_the_idle_time_stays_one_activity(rec, monkeypatch):
    # The idle clock ran from the call's start: a vision read or a slow page past
    # IDLE_END_S ended the tile with no steps, and the result opened a second one (T-0456).
    monkeypatch.setattr(activity, "IDLE_END_S", 0.05)
    sid = "s-slow"
    activity.pre_tool_call("browser_navigate", {"url": "https://example.com/"}, session_id=sid)
    time.sleep(0.2)
    assert "activity.end" not in rec.events()
    _navigate(sid)
    deadline = time.time() + 2
    while "activity.end" not in rec.events() and time.time() < deadline:
        time.sleep(0.01)
    assert len(rec.of("activity.start")) == 1
    assert [e["steps"] for e in rec.of("activity.end")] == [1]


def test_a_failed_last_step_ends_the_activity_failed(rec):
    sid = "s-fail"
    activity.post_tool_call(tool_name="browser_click", args={"ref": "@e1"}, result="",
                            session_id=sid, status="error", error_message="no such element")
    step = rec.of("activity.step")[0]
    assert step["ok"] is False and step["error"] == "no such element"
    activity._idle(sid, activity._LIVE[sid])
    assert rec.of("activity.end")[0]["status"] == "failed"


def test_the_desktop_after_the_browser_is_a_new_activity(rec):
    sid = "s-switch"
    activity.pre_tool_call("browser_navigate", {"url": "https://example.com/"}, session_id=sid)
    activity.pre_tool_call("desktop_screen", {}, session_id=sid)
    assert rec.events() == ["activity.start", "activity.end", "activity.start"]
    starts = rec.of("activity.start")
    assert [s["surface"] for s in starts] == ["browser", "desktop"]
    assert starts[0]["intent"] == "open https://example.com/" and starts[1]["intent"] == "look at the screen"


def test_step_lines_name_the_field_by_its_snapshot_label(rec):
    sid = "s-labels"
    _navigate(sid, page="- textbox \"Search\" [ref=@e1]\n- button \"Go\" [ref=@e2]")
    activity.post_tool_call(tool_name="browser_click", args={"ref": "@e2"}, result="ok", session_id=sid, status="ok")
    activity.post_tool_call(tool_name="desktop_click", args={"x": 10, "y": 20}, result="{}", session_id=sid, status="ok")
    actions = [s["action"] for s in rec.of("activity.step")]
    assert actions[:2] == ["open https://example.com/", "click Go"]
    assert actions[2] == "click at (10, 20)"  # a new (desktop) activity, its first step


# --------------------------------------------------------------------------- the route
def test_frames_go_out_through_the_hud_stores_route(monkeypatch):
    """No second emit path: the frame reaches the session through jarvis-hud's
    store, keyed the way its panels are, and False when the store is absent."""
    hud_plugin = load_plugin("jarvis-hud")
    hud = hud_plugin.hud
    seen: list[tuple[str, str, dict]] = []
    monkeypatch.setattr(hud, "STORE", hud.HudStore(emitter=lambda k, e, p: seen.append((k, e, p)) or True))
    assert activity._send("sess-1", "activity.step", {"id": "a_1"}) is True
    assert seen == [("sess-1", "activity.step", {"id": "a_1"})]
    for name in [n for n in list(sys.modules) if n.endswith("jarvis_hud.hud") or n == "jarvis_hud"]:
        monkeypatch.delitem(sys.modules, name)
    assert activity._send("sess-1", "activity.step", {"id": "a_1"}) is False
