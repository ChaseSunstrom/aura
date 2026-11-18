"""The desktop plugin against a fake house: the picture lands as a file with its
scale, image points become screen points, the device's verdicts are read, and
the matrix gates every act."""
from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from _plugin_loader import load_plugin  # noqa: E402

plugin = load_plugin("aura-desktop")
tools = plugin.tools

# A 2x2 JPEG is enough to be a picture.
JPEG = base64.b64encode(
    bytes.fromhex(
        "ffd8ffe000104a46494600010100000100010000ffdb004300080606070605080707070909080a0c140d0c0b0b0c1912130f14"
        "1d1a1f1e1d1a1c1c20242e2720222c231c1c2837292c30313434341f27393d38323c2e333432ffc0000b080002000201011100"
        "ffc40014000100000000000000000000000000000000ffc40014100100000000000000000000000000000000ffda0008010100"
        "003f00d2cf20ffd9"
    )
).decode()


@pytest.fixture
def house(monkeypatch, tmp_path):
    monkeypatch.setenv("HASS_URL", "http://core.test:8080")
    monkeypatch.setenv("HASS_TOKEN", "tok")
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    tools._scales.clear()
    seen: list[dict] = []
    state = {"devices": [
        {"id": "study-pc", "name": "Study PC", "kind": "desktop", "online": True, "actions": [{"name": "screenshot"}, {"name": "click"}]},
        {"id": "pixel", "name": "Pixel", "kind": "android", "online": True, "actions": [{"name": "torch"}]},
    ], "answer": None, "jpeg": JPEG, "screenshot": None, "tokens": {"tok-lab": "lab", "tok-study": "study-pc"}}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("authorization") == "Bearer tok"
        p = request.url.path
        if p == "/api/config/companion/list":
            return httpx.Response(200, json=state["devices"])
        if p == "/api/services/device_control/run":
            body = json.loads(request.content)
            seen.append(body)
            if not body["device_id"]:  # the house resolves a turn's token to the link holding it (T-1555)
                body = {**body, "device_id": state["tokens"].get(body.get("device_token"), "")}
            assert body["return_response"] is True
            if state["answer"] is not None:
                return httpx.Response(200, json={"changed_states": [], "service_response": state["answer"]})
            if body["action"] == "screenshot":
                # `screenshot` is a verdict for the look alone (the click before it stays ok);
                # `jpeg` is the picture the desktop sends, the 2x2 one unless a test needs a real frame.
                if state["screenshot"] is not None:
                    return httpx.Response(200, json={"changed_states": [], "service_response": {
                        "status": state["screenshot"], "device_id": body["device_id"], "action": "screenshot", "tier": 2}})
                return httpx.Response(200, json={"changed_states": [], "service_response": {
                    "status": "ok", "device_id": body["device_id"], "action": "screenshot", "tier": 2, "trust": "untrusted",
                    "note": "somebody else wrote this",
                    "result": {"path": "screenshots/x.png", "screen": [3200, 1800], "image_size": [1600, 900], "scale": 2.0,
                               "image_jpeg_base64": state["jpeg"]}}})
            return httpx.Response(200, json={"changed_states": [], "service_response": {
                "status": "ok", "device_id": body["device_id"], "action": body["action"], "tier": 3, "result": {"done": True}}})
        return httpx.Response(404, text="nope")

    monkeypatch.setattr(tools._house, "client", lambda timeout=20.0: httpx.Client(
        base_url="http://core.test:8080", headers={"Authorization": "Bearer tok"}, transport=httpx.MockTransport(handler)))
    return seen, state, tmp_path


def test_the_screen_lands_as_a_file_with_its_scale_and_the_next_click_is_scaled(house):
    seen, _state, home = house
    shot = json.loads(tools.handle_screen({}))
    assert shot["ok"] and shot["device_id"] == "study-pc" and shot["scale"] == 2.0
    assert shot["trust"] == "untrusted" and "vision_analyze" in shot["note"]
    path = Path(shot["path"])
    assert path.is_file() and path.parent == home / "desktop" / "screens" and path.read_bytes()[:3] == b"\xff\xd8\xff"
    assert seen[-1]["params"] == {"inline": True, "max_side": 1600}

    # The 2x2 picture is too small for the after-click zoom, so the click answers plain,
    # with the look's failure beside it; the click itself is the call before that look.
    click = json.loads(tools.handle_click({"x": 100, "y": 50, "double": True}))
    assert click["ok"] and click["screen_point"] == [200, 100] and click["scale"] == 2.0
    assert seen[-2]["action"] == "click" and seen[-2]["params"] == {"x": 200, "y": 100, "button": "left", "clicks": 2}
    assert seen[-1]["action"] == "screenshot"
    scroll = json.loads(tools.handle_scroll({"clicks": -3, "x": 10, "y": 10}))
    assert scroll["ok"] and seen[-1]["params"] == {"clicks": -3, "x": 20, "y": 20}
    # A named device that has no picture yet is taken at screen pixels.
    tools.handle_click({"x": 7, "y": 7, "device_id": "other-pc"})
    assert seen[-2]["params"]["x"] == 14  # the unnamed last scale still applies
    tools._scales.clear()
    tools.handle_click({"x": 7, "y": 7, "device_id": "other-pc"})
    assert seen[-2]["params"]["x"] == 7


def test_the_other_acts_reach_the_agent_with_its_own_parameter_names(house):
    seen, _state, _home = house
    assert json.loads(tools.handle_type({"text": "hello", "press_enter": True}))["ok"]
    assert seen[-1]["action"] == "type_text" and seen[-1]["params"] == {"text": "hello", "press_enter": True}
    assert json.loads(tools.handle_key({"keys": "ctrl+l", "times": 2}))["ok"]
    assert seen[-1]["action"] == "key_press" and seen[-1]["params"] == {"keys": "ctrl+l", "times": 2}
    assert json.loads(tools.handle_focus({"window": "Firefox"}))["ok"]
    assert seen[-1]["action"] == "focus_window" and seen[-1]["params"] == {"title": "Firefox"}
    assert json.loads(tools.handle_focus({"window": "0x3a00007"}))["ok"]
    assert seen[-1]["params"] == {"window_id": "0x3a00007"}
    assert json.loads(tools.handle_open({"url": "https://example.com"}))["ok"]
    assert seen[-1]["action"] == "open_url"
    assert json.loads(tools.handle_open({"app": "firefox"}))["ok"]
    assert seen[-1]["action"] == "launch_app" and seen[-1]["params"] == {"app": "firefox"}
    assert not json.loads(tools.handle_open({}))["ok"]
    assert json.loads(tools.handle_run({"command": "ls ~", "timeout_s": 500}))["ok"]
    assert seen[-1]["action"] == "run_command" and seen[-1]["params"] == {"command": "ls ~", "timeout_s": 120}
    assert json.loads(tools.handle_windows({}))["ok"] and seen[-1]["action"] == "list_windows"


def test_the_devices_verdicts_are_read_and_a_missing_desktop_is_named(house):
    seen, state, _home = house
    for status, word in (("denied", "declined"), ("approval_required", "asking"), ("unsupported", "cannot")):
        state["answer"] = {"status": status, "device_id": "study-pc", "action": "click", "tier": 3}
        out = json.loads(tools.handle_click({"x": 1, "y": 1}))
        assert not out["ok"] and out["status"] == status and word in out["error"]
    state["answer"] = None
    state["devices"][0]["online"] = False
    out = json.loads(tools.handle_click({"x": 1, "y": 1}))
    assert not out["ok"] and "no desktop is online" in out["error"] and "Study PC" in out["error"]
    state["devices"][0]["online"] = True
    state["devices"].append({"id": "lab", "name": "Lab", "kind": "desktop", "online": True, "actions": []})
    out = json.loads(tools.handle_click({"x": 1, "y": 1}))
    assert not out["ok"] and "more than one" in out["error"] and "lab" in out["error"]
    out = json.loads(tools.handle_click({"x": 1, "y": 1, "device_id": "lab"}))
    assert out["ok"] and seen[-1]["device_id"] == "lab"
    devices = json.loads(tools.handle_devices({}))
    assert [d["device_id"] for d in devices["desktops"]] == ["study-pc", "lab"]


def _turn_from(monkeypatch, device):
    """The adapter's record: this conversation's newest prompt came from ``device`` (T-1498)."""
    asked: list[str] = []

    def current(session, **kw):
        asked.append(session)
        return {"session_id": session, "device": device, "device_at": 10.0}
    monkeypatch.setattr(tools._surface, "current", current)
    monkeypatch.setattr(tools._surface._speaker, "sessions", lambda: {})
    return asked


def _two_desktops(state):
    state["devices"].append({"id": "lab", "name": "Lab", "kind": "desktop", "online": True, "actions": []})


def test_turn_desktop_two_online_runs_on_the_desktop_the_turn_came_from(house, monkeypatch):
    seen, state, _home = house
    _two_desktops(state)
    asked = _turn_from(monkeypatch, {"name": "Lab", "kind": "desktop", "house_token": "tok-lab"})
    out = json.loads(tools.handle_click({"x": 1, "y": 1}, session_id="s-9"))
    assert out["ok"] and out["device_id"] == "lab"
    assert seen[-2]["device_id"] == "" and seen[-2]["device_token"] == "tok-lab" and seen[-2]["action"] == "click"
    assert asked == ["s-9"]


def test_turn_desktop_a_phone_turn_still_asks_which_desktop(house, monkeypatch):
    seen, state, _home = house
    _two_desktops(state)
    _turn_from(monkeypatch, {"name": "Pixel", "kind": "phone", "house_token": "tok-pixel"})
    out = json.loads(tools.handle_click({"x": 1, "y": 1}, session_id="s-9"))
    assert not out["ok"] and "more than one" in out["error"]
    assert not seen


def test_turn_desktop_the_console_turn_still_asks_which_desktop(house, monkeypatch):
    seen, state, _home = house
    _two_desktops(state)
    _turn_from(monkeypatch, None)
    out = json.loads(tools.handle_type({"text": "hi"}, session_id="s-9"))
    assert not out["ok"] and "more than one" in out["error"] and not seen


@pytest.mark.parametrize("other, asks", [
    ({"name": "Study PC", "kind": "desktop", "house_token": "tok-study"}, True),  # a second desktop on the conversation
    (None, False),  # the console on the same conversation names no device: it cannot be acted on
])
def test_turn_desktop_a_second_screens_prompt_mid_turn_does_not_redirect(house, monkeypatch, other, asks):
    """The adapter records a prompt's device at submit, before Hermes' session lock runs it: a
    prompt sent from another desktop while this turn runs is the newest record, and must not
    move this turn's clicks there. Every desktop recorded on the conversation must agree."""
    seen, state, _home = house
    _two_desktops(state)
    records = {"live-lab": {"device": {"name": "Lab", "kind": "desktop", "house_token": "tok-lab"}, "device_at": 10.0},
               "live-other": {"device": other, "device_at": 5.0}}
    monkeypatch.setattr(tools._surface, "current", lambda session, **kw: records.get(session))
    monkeypatch.setattr(tools._surface._speaker, "sessions", lambda: {"live-lab": {"session_key": "durable"},
                                                                      "live-other": {"session_key": "durable"}})
    out = json.loads(tools.handle_click({"x": 1, "y": 1}, session_id="durable"))
    if asks:
        assert not out["ok"] and "more than one" in out["error"] and not seen
    else:
        assert out["ok"] and out["device_id"] == "lab"


def test_turn_desktop_one_online_or_a_named_one_is_not_asked_about(house, monkeypatch):
    seen, state, _home = house
    asked = _turn_from(monkeypatch, {"name": "Lab", "kind": "desktop", "house_token": "tok-lab"})
    assert json.loads(tools.handle_type({"text": "hi"}, session_id="s-9"))["ok"]
    assert seen[-1]["device_id"] == "study-pc" and "device_token" not in seen[-1]
    _two_desktops(state)
    assert json.loads(tools.handle_type({"text": "hi", "device_id": "study-pc"}, session_id="s-9"))["ok"]
    assert seen[-1]["device_id"] == "study-pc" and "device_token" not in seen[-1]
    assert asked == []


def test_unconfigured(monkeypatch):
    monkeypatch.setenv("HASS_TOKEN", "")
    assert "not configured" in json.loads(tools.handle_screen({}))["error"]


def test_register_and_the_matrix_gates_every_act():
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from _shared import _matrix

    ctx = plugin.FakeCtx()
    plugin.register(ctx)
    assert set(ctx.tools) == {"desktop_devices", "desktop_screen", "desktop_windows", "desktop_click", "desktop_type",
                              "desktop_key", "desktop_scroll", "desktop_focus", "desktop_open", "desktop_run",
                              "desktop_clipboard", "desktop_files", "desktop_notify", "desktop_lock", "desktop_sleep",
                              "desktop_notes", "desktop_media", "desktop_window"}
    assert set(plugin.schemas.ACTS) <= set(ctx.tools)
    assert all(name in _matrix.known_tools() for name in ctx.tools)
    for name in plugin.schemas.ACTS:
        assert _matrix.decide(name, {"x": 1, "y": 1, "text": "hi", "keys": "enter", "clicks": 1, "window": "w",
                                     "app": "firefox", "command": "ls"}).verdict == _matrix.ASK, name
    for name in ("desktop_devices", "desktop_windows", "desktop_screen"):
        assert _matrix.decide(name, {}).free, name
    # The shell's shapes travel with the command: sealed is refused, not asked.
    assert _matrix.decide("desktop_run", {"command": "rm -rf /"}).verdict == _matrix.DENY
    assert _matrix.decide("desktop_run", {"command": "git push --force origin main"}).verdict == _matrix.ASK


def test_the_pc_parity_tools_are_free_only_where_they_read_or_play():
    """Reads and the media keys are free (as `phone_media` is); every write, lock,
    sleep and close asks, so a guest's turn is refused them. The clipboard asks even
    to read: it is where passwords sit. An action word the rows do not know asks."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from _shared import _matrix

    # A file's contents ask (commit review, 2026-10-09): free, a guest's turn or a line
    # injected into a page could read the owner's files out. Listing names stays free.
    free = [("desktop_files", {"action": "list"}),
            ("desktop_notes", {"action": "find", "query": "milk"}), ("desktop_media", {"command": "next"}),
            ("desktop_notify", {"title": "t", "message": "m"})]
    asks = [("desktop_files", {"action": "write", "path": "a", "content": "x"}),
            ("desktop_files", {"action": "delete", "path": "a"}), ("desktop_files", {"action": "Read"}),
            ("desktop_files", {"action": "read", "path": "a.txt"}),
            ("desktop_files", {}), ("desktop_notes", {"action": "save", "title": "t"}), ("desktop_notes", {}),
            ("desktop_clipboard", {"action": "read"}), ("desktop_clipboard", {"action": "write", "text": "x"}),
            ("desktop_lock", {}), ("desktop_sleep", {}),
            ("desktop_window", {"action": "close", "window": "x"}), ("desktop_window", {"action": "minimize", "window": "x"})]
    for name, args in free:
        assert _matrix.decide(name, args).free, (name, args)
    for name, args in asks:
        assert _matrix.decide(name, args).verdict == _matrix.ASK, (name, args)
    # A standing yes is per grain: one for reading must not cover a write or a delete.
    read = _matrix.decide("desktop_files", {"action": "read", "path": "a"}).rule_key
    for act in ("write", "delete", "Read"):
        assert _matrix.decide("desktop_files", {"action": act, "path": "a"}).rule_key != read, act
    # Free is not read-only: the kill switch still stops what can write or play.
    for name in ("desktop_files", "desktop_notes", "desktop_media", "desktop_notify"):
        assert name not in _matrix.read_only_tools(), name


def test_the_pc_parity_tools_reach_the_agent_with_its_own_names(house):
    seen, _state, _home = house
    calls = [
        (tools.handle_clipboard, {"action": "read"}, "read_clipboard", {}),
        (tools.handle_clipboard, {"action": "write", "text": "hi"}, "write_clipboard", {"content": "hi"}),
        (tools.handle_files, {"action": "list"}, "list_dir", {}),
        (tools.handle_files, {"action": "read", "path": "a.txt"}, "read_file", {"path": "a.txt"}),
        (tools.handle_files, {"action": "write", "path": "a.txt", "content": "x", "append": True}, "write_file",
         {"path": "a.txt", "content": "x", "append": True}),
        (tools.handle_files, {"action": "delete", "path": "d", "recursive": True}, "delete_file", {"path": "d", "recursive": True}),
        (tools.handle_notify, {"title": "Tea", "message": "ready"}, "notify", {"title": "Tea", "message": "ready"}),
        (tools.handle_lock, {}, "lock_screen", {}),
        (tools.handle_sleep, {}, "sleep", {}),
        (tools.handle_notes, {"action": "find", "query": "milk"}, "find_note", {"query": "milk"}),
        (tools.handle_notes, {"action": "save", "title": "Milk", "body": "2 l"}, "save_note", {"title": "Milk", "body": "2 l"}),
        (tools.handle_media, {"command": "play_pause"}, "media_control", {"command": "play_pause"}),
        (tools.handle_window, {"action": "close", "window": "Notes"}, "window_control", {"command": "close", "title": "Notes"}),
        (tools.handle_window, {"action": "minimize", "window": "0x3a00007"}, "window_control",
         {"command": "minimize", "window_id": "0x3a00007"}),
    ]
    for handler, args, action, params in calls:
        assert json.loads(handler(args))["ok"], args
        assert (seen[-1]["action"], seen[-1]["params"]) == (action, params), args
    # A word the matrix did not grade never reaches the agent: `Write` would have
    # passed the gate as free and then written.
    before = len(seen)
    for handler, args in ((tools.handle_files, {"action": "Write", "path": "a"}), (tools.handle_notes, {"action": "SAVE"}),
                          (tools.handle_clipboard, {"action": "wipe"}), (tools.handle_window, {"action": "kill", "window": "x"}),
                          (tools.handle_media, {"command": "eject"})):
        assert not json.loads(handler(args))["ok"], args
    assert len(seen) == before


def test_the_saved_picture_carries_a_labelled_grid_and_a_tiny_one_does_not():
    """A vision model reads coordinates off the grid lines; a picture smaller than one cell is left alone."""
    import io

    from PIL import Image

    plain = io.BytesIO()
    Image.new("RGB", (320, 240), (255, 255, 255)).save(plain, format="JPEG")
    gridded = Image.open(io.BytesIO(tools._gridded(plain.getvalue()))).convert("RGB")
    assert gridded.size == (320, 240)
    on_line, off_line = gridded.getpixel((100, 150)), gridded.getpixel((50, 150))
    # Magenta at half alpha on white: red and blue stay high, green drops.
    assert on_line[1] < 200 and off_line[1] > 240, (on_line, off_line)
    assert tools._gridded(base64.b64decode(JPEG)) == base64.b64decode(JPEG)


def test_a_zoom_enlarges_the_region_and_labels_its_grid_in_full_picture_pixels():
    """A dot at (155, 123) of the full picture lands at the enlarged crop's matching point; the labels stay absolute."""
    import io

    from PIL import Image

    full = Image.new("RGB", (320, 240), (255, 255, 255))
    for dx in range(3):
        for dy in range(3):
            full.putpixel((154 + dx, 122 + dy), (0, 0, 0))
    raw = io.BytesIO()
    full.save(raw, format="PNG")
    picture, zoom = tools._zoomed(raw.getvalue(), (100, 100, 100, 50))
    assert zoom == {"region": [100, 100, 100, 50], "factor": 4, "grid": 10}
    crop = Image.open(io.BytesIO(picture)).convert("RGB")
    assert crop.size == (400, 200)
    # (155, 123) -> ((155 - 100) * 4, (123 - 100) * 4); off the grid lines, which fall on multiples of 40.
    assert sum(crop.getpixel((222, 94))) < 300 and sum(crop.getpixel((104, 44))) > 600
    # A region past the picture is refused; one hanging off its edge is trimmed.
    assert tools._zoomed(raw.getvalue(), (310, 230, 50, 50))[1] is None
    assert tools._zoomed(raw.getvalue(), (300, 200, 50, 50))[1]["region"] == [300, 200, 20, 40]
    assert tools._region([1, 2, 3]) is None and tools._region(["a", 0, 30, 30]) is None and tools._region([0, 0, 30, 30]) == (0, 0, 30, 30)


def _frame(width: int = 320, height: int = 240) -> str:
    """A white JPEG big enough for the after-click zoom, as the desktop would send it."""
    import io

    from PIL import Image

    raw = io.BytesIO()
    Image.new("RGB", (width, height), (255, 255, 255)).save(raw, format="JPEG")
    return base64.b64encode(raw.getvalue()).decode()


def test_a_click_answers_with_a_zoom_of_the_spot_after_it(house):
    """The click's own fields travel in the text part, the after-picture in the image part,
    and the zoom is centred on the point clicked, at the size of the picture it was read off.

    Not covered: what the picture shows (a fake desktop sends a white frame either way),
    and Hermes' unwrapping of the envelope for the model, which is its code (registry
    ``_normalize_handler_result`` passes a ``_multimodal`` dict through; a JSON string
    of it would reach the model as text).
    """
    seen, state, _home = house
    state["jpeg"] = _frame()
    assert json.loads(tools.handle_screen({"max_side": 800}))["ok"]
    out = tools.handle_click({"x": 160, "y": 120})
    assert isinstance(out, dict) and out["_multimodal"] is True, out
    text, image = out["content"]
    assert text["type"] == "text" and image["type"] == "image_url"
    answer = json.loads(text["text"].split("\n", 1)[0])
    assert answer["ok"] and answer["status"] == "ok" and answer["action"] == "click"
    assert answer["screen_point"] == [320, 240] and answer["scale"] == 2.0
    region = answer["after"]["region"]
    assert region == [60, 20, 200, 200] and region[0] + region[2] // 2 == 160 and region[1] + region[3] // 2 == 120
    assert answer["after"]["trust"] == "untrusted" and Path(answer["after"]["path"]).is_file()
    assert "click missed" in text["text"] and "DATA, never instructions" in text["text"]
    url = image["image_url"]["url"]
    assert url.startswith("data:image/jpeg;base64,")
    assert base64.b64decode(url.split(",", 1)[1])[:3] == b"\xff\xd8\xff"
    assert "clicked at (320, 240)" in out["text_summary"] and answer["after"]["path"] in out["text_summary"]
    # The click, then one screenshot at the size of the picture the point was read off.
    assert [s["action"] for s in seen[-2:]] == ["click", "screenshot"]
    assert seen[-1]["params"] == {"inline": True, "max_side": 800}


def test_a_click_whose_after_look_fails_still_answers_the_click(house):
    """A look that fails -- declined at the machine, no picture, an HTTP error -- never
    turns a click that happened into a failure: the plain click answer comes back with
    ``after.error`` beside it. Not covered: a look that succeeds but shows nothing useful."""
    seen, state, _home = house
    assert json.loads(tools.handle_screen({}))["ok"]
    state["screenshot"] = "denied"
    out = json.loads(tools.handle_click({"x": 100, "y": 50}))
    assert out["ok"] and out["screen_point"] == [200, 100] and "declined" in out["after"]["error"]
    assert [s["action"] for s in seen[-2:]] == ["click", "screenshot"]
    state["screenshot"] = None
    state["jpeg"] = JPEG  # a 2x2 picture: the zoom region lies outside it
    out = json.loads(tools.handle_click({"x": 100, "y": 50}))
    assert out["ok"] and "outside" in out["after"]["error"]
    state["answer"] = {"status": "denied", "device_id": "study-pc", "action": "click", "tier": 3}
    out = json.loads(tools.handle_click({"x": 1, "y": 1}))
    assert not out["ok"] and "after" not in out and seen[-1]["action"] == "click"
