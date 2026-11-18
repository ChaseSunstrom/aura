"""The emitter against the contract: the constants pin to
``tests/contracts/activity-frames.json``, the throttle drops a frame inside the
interval and an unchanged one at any interval, a picture in a result becomes a
reduced JPEG, the setting stops everything, the idle timer ends the activity,
and the route is aura-hud's store."""
from __future__ import annotations

import base64
import io
import json
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from _plugin_loader import load_plugin  # noqa: E402

plugin = load_plugin("aura-activity")
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
    monkeypatch.setattr(activity, "PICTURE", lambda task, session="": asked.append(task) or _png(800, 450))
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


def test_a_browser_exec_step_is_photographed_on_its_own_session(rec, monkeypatch):
    """Hermes v0.23 drives the browser only through ``browser_exec`` (T-1519): a successful call
    is photographed on the session it named, so the console shows the page it left."""
    asked: list[tuple[str, str]] = []
    monkeypatch.setattr(activity, "PICTURE", lambda task, session="": asked.append((task, session)) or _png(800, 450))
    activity.post_tool_call(tool_name="browser_exec", args={"code": "new_tab('https://example.com/')",
                                                            "session": "shop"},
                            result=json.dumps({"output": "ok"}), task_id="t1", session_id="s-exec", status="ok")
    assert asked == [("t1", "shop")]
    assert rec.of("activity.frame")[-1]["image"].startswith("data:image/")


def test_the_screenshot_asks_browser_exec_for_the_sessions_viewport(monkeypatch, tmp_path):
    """v0.23 removed the private browser command the old capture used; the picture now comes
    from ``browser_exec``'s own ``capture_screenshot`` helper on the same task and session, so it
    follows the session's route (local, sidecar or cloud). Any failure is a frame with no
    picture, never a failed step."""
    import types

    calls: list[dict] = []

    def fake_exec(code, session="", timeout_s=0, task_id=None, local=False):
        calls.append({"code": code, "session": session, "task_id": task_id})
        path = code.split("capture_screenshot(", 1)[1].split(")", 1)[0].strip("'\"")
        Path(path).write_bytes(_png(40, 30))
        return json.dumps({"output": path})

    module = types.ModuleType("tools.browser_use_cli")
    module.browser_exec = fake_exec
    monkeypatch.setitem(sys.modules, "tools", types.ModuleType("tools"))
    monkeypatch.setitem(sys.modules, "tools.browser_use_cli", module)

    shot = activity._screenshot("t1", "shop")

    assert shot == _png(40, 30)
    assert calls and calls[0]["session"] == "shop" and calls[0]["task_id"] == "t1"
    assert not list(Path(tempfile.gettempdir()).glob("jarvis-activity-*.png")), "the file is removed"

    module.browser_exec = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("daemon gone"))
    assert activity._screenshot("t1", "shop") == b""


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
    """No second emit path: the frame reaches the session through aura-hud's
    store, keyed the way its panels are, and False when the store is absent."""
    hud_plugin = load_plugin("aura-hud")
    hud = hud_plugin.hud
    seen: list[tuple[str, str, dict]] = []
    monkeypatch.setattr(hud, "STORE", hud.HudStore(emitter=lambda k, e, p: seen.append((k, e, p)) or True))
    assert activity._send("sess-1", "activity.step", {"id": "a_1"}) is True
    assert seen == [("sess-1", "activity.step", {"id": "a_1"})]
    for name in [n for n in list(sys.modules) if n.endswith("aura_hud.hud") or n == "aura_hud"]:
        monkeypatch.delitem(sys.modules, name)
    assert activity._send("sess-1", "activity.step", {"id": "a_1"}) is False


# --------------------------------------------------------------------------- live frames (T-1546)
def _until(pred, timeout: float = 2.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.01)
    return pred()


class Shots:
    """A picture source: a new colour each call (so no frame is a repeat); empty for the
    first ``empty_first`` calls, and from call ``empty_after`` on."""

    def __init__(self, empty: bool = False, empty_first: int = 0, empty_after: int = 0) -> None:
        self.calls: list[str] = []
        self.empty = empty
        self.empty_first = empty_first
        self.empty_after = empty_after

    def __call__(self, task_id: str) -> bytes:
        self.calls.append(task_id)
        n = len(self.calls)
        if self.empty or n <= self.empty_first or (self.empty_after and n > self.empty_after):
            return b""
        from PIL import Image

        buf = io.BytesIO()
        Image.new("RGB", (64, 36), (len(self.calls) % 256, 40, 60)).save(buf, "PNG")
        return buf.getvalue()


@pytest.fixture
def live(rec, monkeypatch):
    shots = Shots()
    monkeypatch.setattr(activity, "LIVE_PICTURE", shots)
    monkeypatch.setattr(activity, "LIVE_EVERY_S", 0.02)
    monkeypatch.setattr(activity, "THROTTLE_MS", 0)
    yield shots
    activity.reset()


def _exec_starts(sid: str = "s1", task: str = "t1") -> None:
    activity.pre_tool_call("browser_exec", {"code": "goto('https://example.com/')"}, task_id=task, session_id=sid)


def _exec_ends(sid: str = "s1", task: str = "t1") -> None:
    activity.post_tool_call(tool_name="browser_exec", args={"code": "x"}, result="{}", task_id=task,
                            session_id=sid, status="ok")


def test_live_frames_flow_while_a_browser_exec_call_runs_and_stop_when_it_returns(rec, live, monkeypatch):
    monkeypatch.setattr(activity, "PICTURE", lambda *a, **k: b"")
    _exec_starts()
    assert _until(lambda: len(rec.of("activity.frame")) >= 2)
    assert set(live.calls) == {"t1"}  # the call's own task, so the supervisor is that browser's
    assert all(f["image"].startswith("data:image/jpeg;base64,") for f in rec.of("activity.frame"))
    _exec_ends()
    calls = len(live.calls)
    time.sleep(0.15)
    assert len(live.calls) == calls  # the loop ended with the call


def test_live_frames_are_at_most_one_a_second():
    assert activity.LIVE_EVERY_S >= 1.0


def test_the_live_loop_waits_its_interval_before_the_first_picture(rec, monkeypatch):
    shots = Shots()
    monkeypatch.setattr(activity, "LIVE_PICTURE", shots)
    monkeypatch.setattr(activity, "LIVE_EVERY_S", 0.5)
    _exec_starts()
    time.sleep(0.2)
    assert shots.calls == []  # a call shorter than a second costs no live capture
    _exec_ends()


def test_no_live_picture_is_taken_for_a_session_without_a_hud_client(rec, live, monkeypatch):
    monkeypatch.setattr(activity, "SEND", lambda *a: False)
    _exec_starts()
    time.sleep(0.15)
    assert live.calls == []
    _exec_ends()


def test_the_live_loop_stops_when_a_frame_is_no_longer_delivered(rec, live, monkeypatch):
    sent: list[str] = []

    def send(session_id: str, event: str, payload: dict) -> bool:
        sent.append(event)
        return event != "activity.frame"  # the client went away after the start

    monkeypatch.setattr(activity, "SEND", send)
    _exec_starts()
    assert _until(lambda: "activity.frame" in sent)
    time.sleep(0.15)
    assert sent.count("activity.frame") == 1
    assert len(live.calls) == 1
    _exec_ends()


def test_the_live_loop_gives_up_after_repeated_capture_failures(rec, monkeypatch):
    shots = Shots(empty_after=2)
    monkeypatch.setattr(activity, "LIVE_PICTURE", shots)
    monkeypatch.setattr(activity, "LIVE_EVERY_S", 0.01)
    monkeypatch.setattr(activity, "THROTTLE_MS", 0)
    _exec_starts()
    assert _until(lambda: len(shots.calls) >= 2 + activity.LIVE_FAILS)
    time.sleep(0.1)
    assert len(shots.calls) == 2 + activity.LIVE_FAILS  # the tab closed: a dead source is let go
    _exec_ends()


def test_the_live_loop_waits_for_a_browser_still_starting(rec, monkeypatch):
    """Found live: a cold browser has no supervisor for its first seconds, and three empty
    captures ended the loop before the first picture of a seven-second call."""
    shots = Shots(empty_first=activity.LIVE_FAILS + 2)
    monkeypatch.setattr(activity, "LIVE_PICTURE", shots)
    monkeypatch.setattr(activity, "LIVE_EVERY_S", 0.01)
    monkeypatch.setattr(activity, "THROTTLE_MS", 0)
    _exec_starts()
    assert _until(lambda: len(rec.of("activity.frame")) >= 1)
    _exec_ends()


def test_the_live_loop_lets_go_of_a_source_that_never_answers(rec, monkeypatch):
    shots = Shots(empty=True)
    monkeypatch.setattr(activity, "LIVE_PICTURE", shots)
    monkeypatch.setattr(activity, "LIVE_EVERY_S", 0.01)
    monkeypatch.setattr(activity, "LIVE_START_S", 0.1)
    _exec_starts()
    time.sleep(0.3)
    calls = len(shots.calls)
    time.sleep(0.1)
    assert len(shots.calls) == calls  # no supervisor (a cloud browser): not polled for the call
    _exec_ends()


def test_the_live_start_window_outlasts_a_cold_browser():
    assert activity.LIVE_START_S >= 15.0


def test_the_live_loop_ends_with_the_activity(rec, live, monkeypatch):
    _exec_starts()
    assert _until(lambda: len(live.calls) >= 1)
    activity.reset()  # the activity is gone (idle end, a new surface, the test's reset)
    calls = len(live.calls)
    time.sleep(0.15)
    assert len(live.calls) <= calls + 1  # at most the capture already in flight


def test_only_browser_exec_runs_a_live_loop(rec, live):
    activity.pre_tool_call("browser_navigate", {"url": "https://example.com/"}, task_id="t1", session_id="s1")
    activity.pre_tool_call("desktop_click", {"x": 1, "y": 2}, task_id="t1", session_id="s2")
    time.sleep(0.15)
    assert live.calls == []


def test_disabling_the_setting_stops_the_live_loop_too(rec, live, monkeypatch):
    monkeypatch.setenv("ACTIVITY_ENABLED", "false")
    _exec_starts()
    time.sleep(0.15)
    assert live.calls == []


class _Cdp:
    def __init__(self, fail: str = "") -> None:
        self.page_session_id = "page-1"
        self.sent: list[tuple[str, dict, Any]] = []
        self.fail = fail

    def call(self, method, params=None, *, session_id=None, timeout=10.0):
        self.sent.append((method, dict(params or {}), session_id))
        assert timeout is not None and timeout <= 5  # never an unbounded wait from the loop
        if method == self.fail:
            raise RuntimeError("cdp error")
        if method == "Target.attachToTarget":
            return {"id": 1, "result": {"sessionId": "tab-1"}}
        if method == "Page.captureScreenshot":
            return {"id": 2, "result": {"data": base64.b64encode(b"JPEGBYTES").decode()}}
        return {"id": 3, "result": {}}


class _Registry:
    def __init__(self, sup, cdp) -> None:
        self.sup, self.cdp, self.asked = sup, cdp, []

    def get(self, task_id):
        self.asked.append(task_id)
        return self.sup

    def capture(self, task_id, *, timeout=10.0):
        return self.cdp


def _supervisor(monkeypatch, sup, cdp):
    import types

    mod = types.ModuleType("tools.browser_supervisor")
    mod.SUPERVISOR_REGISTRY = _Registry(sup, cdp)
    pkg = sys.modules.get("tools") or types.ModuleType("tools")
    monkeypatch.setitem(sys.modules, "tools", pkg)
    monkeypatch.setitem(sys.modules, "tools.browser_supervisor", mod)
    return mod.SUPERVISOR_REGISTRY


def test_the_live_picture_is_the_driven_tab_through_the_tasks_supervisor(monkeypatch):
    sup = type("Sup", (), {"_driven_target_id": "T-driven"})()
    cdp = _Cdp()
    reg = _supervisor(monkeypatch, sup, cdp)
    assert activity._live_picture("t1") == b"JPEGBYTES"
    assert reg.asked == ["t1"]
    methods = [m for m, _, _ in cdp.sent]
    assert methods == ["Target.attachToTarget", "Page.captureScreenshot", "Target.detachFromTarget"]
    assert cdp.sent[0][1] == {"targetId": "T-driven", "flatten": True}
    assert cdp.sent[1][2] == "tab-1"
    assert cdp.sent[1][1]["format"] == "jpeg"
    assert cdp.sent[2][1] == {"sessionId": "tab-1"}


def test_the_live_picture_uses_the_page_session_when_no_tab_was_driven(monkeypatch):
    cdp = _Cdp()
    _supervisor(monkeypatch, type("Sup", (), {"_driven_target_id": None})(), cdp)
    assert activity._live_picture("") == b"JPEGBYTES"
    assert [(m, s) for m, _, s in cdp.sent] == [("Page.captureScreenshot", "page-1")]


def test_the_live_picture_is_empty_without_a_supervisor_and_detaches_after_a_failed_shot(monkeypatch):
    _supervisor(monkeypatch, None, _Cdp())
    assert activity._live_picture("t1") == b""
    cdp = _Cdp(fail="Page.captureScreenshot")
    _supervisor(monkeypatch, type("Sup", (), {"_driven_target_id": "T-driven"})(), cdp)
    assert activity._live_picture("t1") == b""
    assert cdp.sent[-1][0] == "Target.detachFromTarget"  # a failed shot never leaks an attached session
