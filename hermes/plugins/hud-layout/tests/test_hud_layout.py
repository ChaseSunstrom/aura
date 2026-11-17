"""Unit + contract tests for the hud-layout plugin (no Hermes runtime needed).

The store's emitter, config and preference backend are injected, so the
emitted-frame shape, the lock, manual-wins, undo and persistence are asserted
without a gateway or a config.yaml; the channel helpers are tested against a
fake ``tui_gateway.server`` planted in ``sys.modules``. The contract tests read
``tests/contracts/hud-layout.json`` (and follow its ``$ref`` into
``hermes-settings.json``) and fail if the plugin's constants, tool argument
shapes, presets or examples drift from it, or if the referenced layout document
schema stops being the settings schema's own.
"""
from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest

PLUGIN_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = PLUGIN_DIR.parents[2]  # …/hermes/plugins/hud-layout -> repo root
CONTRACTS = REPO_ROOT / "tests" / "contracts"
CONTRACT = CONTRACTS / "hud-layout.json"

sys.path.insert(0, str(PLUGIN_DIR.parent))
from _plugin_loader import load_plugin  # noqa: E402

plugin = load_plugin("hud-layout")
layout = plugin.layout
channel = plugin.channel


class Recorder:
    """An injectable emitter that records (state_key, event, payload) frames."""

    def __init__(self, delivered: bool = True):
        self.frames: list[tuple[str, str, dict]] = []
        self.delivered = delivered

    def __call__(self, state_key: str, event: str, payload: dict) -> bool:
        self.frames.append((state_key, event, payload))
        return self.delivered

    def events(self) -> list[str]:
        return [e for _, e, _ in self.frames]

    def last(self) -> dict:
        return self.frames[-1][2]


class FakeState:
    """Hermes's PluginState surface (get/set of JSON values) over a dict."""

    def __init__(self):
        self.data: dict = {}
        self.writes = 0

    def get(self, key, default=None):
        return json.loads(json.dumps(self.data.get(key, default)))

    def set(self, key, value):
        json.dumps(value)  # must be JSON, as the real one insists
        self.data[key] = value
        self.writes += 1


def _store(*, config: dict | None = None, delivered: bool = True, state: FakeState | None = None):
    rec = Recorder(delivered=delivered)
    cfg = layout.MemoryConfig(config if config is not None else {})
    store = layout.LayoutStore(emitter=rec, config=cfg, prefs=layout.PrefStore(state))
    return store, rec, cfg


def _contract() -> dict:
    return json.loads(CONTRACT.read_text())


def _resolve(ref: str):
    """``file.json#/a/b`` -> the referenced value, from tests/contracts."""
    file, _, pointer = ref.partition("#")
    node = json.loads((CONTRACTS / file).read_text())
    for seg in pointer.strip("/").split("/"):
        node = node[seg]
    return node


# --------------------------------------------------------------------------- tree ops
def test_leaves_are_in_reading_order():
    tree = {"split": "row", "children": ["a", {"split": "column", "children": ["b", "c"]}]}
    assert layout.leaves(tree) == ["a", "b", "c"]
    assert layout.leaves(None) == [] and layout.leaves("x") == ["x"]


def test_remove_leaf_collapses_the_parent_split():
    tree = {"split": "row", "ratio": 0.6, "children": ["a", {"split": "column", "children": ["b", "c"]}]}
    out, done = layout.remove_leaf(tree, "b")
    assert done and out == {"split": "row", "ratio": 0.6, "children": ["a", "c"]}
    out, done = layout.remove_leaf(tree, "a")
    assert done and out == {"split": "column", "children": ["b", "c"]}
    assert layout.remove_leaf(tree, "zz") == (tree, False)
    assert layout.remove_leaf("a", "a") == (None, True)


def test_insert_beside_puts_the_new_panel_on_the_named_edge():
    tree = {"split": "row", "children": ["a", "b"]}
    out, done = layout.insert_beside(tree, "b", "n", "top", 0.3)
    assert done
    assert out["children"][1] == {"split": "column", "ratio": 0.3, "children": ["n", "b"]}
    out, _ = layout.insert_beside(tree, "a", "n", "right", 0.3)
    # the new panel's share is what was asked, so the existing one keeps 0.7 as first child
    assert out["children"][0] == {"split": "row", "ratio": 0.7, "children": ["a", "n"]}
    assert layout.insert_beside(tree, "zz", "n", "left", 0.5) == (tree, False)


def test_set_share_adjusts_the_parent_ratio_for_either_child():
    tree = {"split": "row", "ratio": 0.5, "children": ["a", "b"]}
    assert layout.set_share(tree, "a", 0.7)[0]["ratio"] == 0.7
    assert layout.set_share(tree, "b", 0.7)[0]["ratio"] == pytest.approx(0.3)
    assert layout.set_share("a", "a", 0.7) == ("a", False)


def test_bad_edge_and_bad_share_are_named():
    with pytest.raises(layout.LayoutError, match="unknown edge"):
        layout.split_leaf("a", "b", "diagonal", 0.5)
    with pytest.raises(layout.LayoutError, match="between 0 and 1"):
        layout._share(1.5)
    with pytest.raises(layout.LayoutError, match="between 0 and 1"):
        layout._share("wide")


# --------------------------------------------------------------------------- document ops
def _working() -> dict:
    return layout.validate_layout(layout.PRESETS["working"]["layout"])


def test_validate_layout_uses_the_schema():
    with pytest.raises(layout.LayoutError, match="invalid layout"):
        layout.validate_layout({"mode": "task", "tree": "ghost", "panels": []})
    with pytest.raises(layout.LayoutError, match="invalid layout"):
        layout.validate_layout({"mode": "grid"})
    with pytest.raises(layout.LayoutError, match="JSON object"):
        layout.validate_layout("task")
    doc = layout.validate_layout({"mode": "ambient"})
    assert doc == {"mode": "ambient", "panels": []}  # exclude_none drops the empty tree


def test_open_splits_beside_target_or_wraps_the_root():
    doc = _working()
    out = layout.op_open(doc, "image", "image", beside="chat", edge="right", share=0.4)
    assert "image" in layout.leaves(out["tree"]) and any(p["id"] == "image" for p in out["panels"])
    out = layout.op_open(doc, "image", "image", edge="bottom", share=0.25)
    assert out["tree"]["split"] == "column" and out["tree"]["children"][1] == "image"
    assert out["tree"]["ratio"] == 0.75
    with pytest.raises(layout.LayoutError, match="already open"):
        layout.op_open(doc, "chat", "chat")
    with pytest.raises(layout.LayoutError, match="no panel 'nope'"):
        layout.op_open(doc, "image", "image", beside="nope")


def test_open_in_floating_focus_and_ambient():
    floating = layout.op_open({"mode": "overview", "panels": []}, "viewer", "image")
    assert floating["panels"][0]["rect"] == layout._default_rect(0) and floating.get("tree") is None
    focus = layout.op_open({"mode": "focus", "panels": [{"id": "chat", "type": "chat"}], "tree": "chat"}, "g", "goals")
    assert focus["tree"] == "chat" and [p["id"] for p in focus["panels"]] == ["chat", "g"]
    empty_focus = layout.op_open({"mode": "focus", "panels": []}, "g", "goals")
    assert empty_focus["tree"] == "g"
    ambient = layout.op_open({"mode": "ambient", "panels": []}, "voice", "voice")
    assert ambient.get("tree") is None and ambient["panels"][0]["id"] == "voice"


def test_close_removes_from_tree_and_refuses_pinned_unless_forced():
    doc = layout.op_pin(_working(), "goals", True)
    with pytest.raises(layout.LayoutError, match="pinned by you"):
        layout.op_close(doc, "goals")
    out = layout.op_close(doc, "goals", force=True)
    assert "goals" not in layout.leaves(out["tree"]) and all(p["id"] != "goals" for p in out["panels"])
    layout.validate_layout(out)


def test_focus_move_resize_pin_set_mode():
    doc = _working()
    focused = layout.op_focus(doc, "timeline")
    assert focused["mode"] == "focus" and focused["tree"] == "timeline"
    moved = layout.op_move(doc, "goals", target="chat", edge="top", share=0.3)
    assert layout.leaves(moved["tree"])[:2] == ["goals", "chat"]
    with pytest.raises(layout.LayoutError, match="beside itself"):
        layout.op_move(doc, "chat", target="chat")
    with pytest.raises(layout.LayoutError, match="needs a target"):
        layout.op_move(doc, "chat")
    resized = layout.op_resize(doc, "chat", share=0.8)
    assert resized["tree"]["ratio"] == 0.8
    with pytest.raises(layout.LayoutError, match="nothing to resize"):
        layout.op_resize({"mode": "task", "tree": "chat", "panels": [{"id": "chat", "type": "chat"}]}, "chat", share=0.5)
    placed = layout.op_resize(doc, "chat", rect={"x": 0, "y": 0, "w": 0.5, "h": 0.5})
    assert layout.validate_layout(placed)["panels"][0]["rect"] == {"x": 0, "y": 0, "w": 0.5, "h": 0.5}
    with pytest.raises(layout.LayoutError, match="unknown mode"):
        layout.op_set_mode(doc, "grid")


def test_set_mode_keeps_every_panel_placeable():
    doc = layout.op_focus(_working(), "goals")           # tree is one leaf, 3 orphans
    tiled = layout.op_set_mode(doc, "task")
    assert sorted(layout.leaves(tiled["tree"])) == ["chat", "goals", "model", "timeline"]
    floating = layout.op_set_mode(_working(), "overview")
    assert all(p.get("rect") for p in floating["panels"])
    back_to_focus = layout.op_set_mode(_working(), "focus")
    assert back_to_focus["tree"] == "chat"
    ambient = layout.op_set_mode(_working(), "ambient")
    assert ambient["tree"] == _working()["tree"]  # hidden, not closed
    for out in (tiled, floating, back_to_focus, ambient):
        layout.validate_layout(out)


# --------------------------------------------------------------------------- store: events, undo
def test_every_change_emits_apply_with_the_contract_fields():
    store, rec, _ = _store()
    fields = _contract()["apply_fields"]
    store.arrange("s", preset="goal-start")
    store.open("s", "image", "image", beside="chat")
    store.resize("s", "image", share=0.6)
    store.move("s", "goals", target="chat", edge="top")
    store.pin("s", "chat")
    store.focus("s", "goals")
    store.set_mode("s", "task")
    store.close("s", "image")
    assert rec.events() == ["hud.layout.apply"] * 8
    ops = [p["op"] for _, _, p in rec.frames]
    assert ops == ["arrange", "open", "resize", "move", "pin", "focus", "set_mode", "close"]
    for _, _, frame in rec.frames:
        assert set(fields).issubset(frame) and frame["animate"] is True and frame["reversible"] is True
        layout.validate_layout(frame["layout"])
    assert [p["rev"] for _, _, p in rec.frames] == list(range(1, 9))


def test_undo_reapplies_the_replaced_layout_and_records_the_decline():
    store, rec, _ = _store()
    before = store.current("s")
    store.arrange("s", preset="goal-start")
    out = store.undo("s")
    assert out["ok"] and out["undid"] == "arrange" and out["reversible"] is False
    assert rec.last()["op"] == "undo" and rec.last()["layout"] == before
    assert store.current("s") == before and out["undo_depth"] == 0
    assert store.prefs.declined("goal") == 1
    again = store.undo("s")
    assert again["ok"] is False and "nothing to undo" in again["error"]


def test_undo_restores_the_activity_and_walks_back_in_order():
    store, _, _ = _store()
    store.arrange("s", preset="goal-start")          # activity goal
    store.arrange("s", preset="image-open")          # activity image
    assert store.list_panels("s")["activity"] == "image"
    store.undo("s")
    assert store.list_panels("s")["activity"] == "goal"
    assert layout.leaves(store.current("s")["tree"]) == ["chat", "goals", "timeline"]
    store.undo("s")
    assert store.list_panels("s")["activity"] == "default"


def test_undo_stack_is_capped():
    store, _, _ = _store()
    store._max_undo = 3
    for _ in range(5):
        store.set_mode("s", "overview")
        store.set_mode("s", "task")
    assert store.list_panels("s")["undo_depth"] == 3


def test_sessions_are_isolated():
    store, _, _ = _store()
    store.arrange("a", preset="goal-start")
    assert store.list_panels("b")["activity"] == "default"
    assert store.list_panels("a")["undo_depth"] == 1 and store.list_panels("b")["undo_depth"] == 0


def test_delivered_false_still_keeps_state():
    store, _, _ = _store(delivered=False)
    out = store.arrange("s", preset="voice-ambient")
    assert out["delivered"] is False and store.current("s")["mode"] == "ambient"


# --------------------------------------------------------------------------- store: the lock
def test_lock_refuses_every_change_but_not_reads_or_highlight():
    store, rec, _ = _store(config={"hud": {"lock_layout": True}})
    changes = [
        lambda: store.open("s", "image", "image"), lambda: store.close("s", "chat"),
        lambda: store.focus("s", "chat"), lambda: store.move("s", "goals", target="chat"),
        lambda: store.resize("s", "chat", share=0.6), lambda: store.pin("s", "chat"),
        lambda: store.set_mode("s", "focus"), lambda: store.arrange("s", preset="goal-start"),
        lambda: store.undo("s"),
    ]
    for change in changes:
        with pytest.raises(layout.Locked) as exc:
            change()
        assert str(exc.value) == layout.LOCK_MESSAGE
    assert rec.frames == []
    assert store.list_panels("s")["locked"] is True
    assert store.highlight("s", "chat")["delivered"] is True
    assert rec.events() == ["hud.layout.highlight"]


def test_lock_tool_result_names_the_refusal():
    store, _, _ = _store(config={"hud": {"lock_layout": True}})
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(layout, "STORE", store)
        out = json.loads(layout.handle_arrange({"preset": "goal-start"}, session_id="s"))
    assert out == {"ok": False, "refused": "lock_layout", "error": layout.LOCK_MESSAGE}


def test_lock_and_unlock_write_the_setting_through_the_schema():
    store, _, cfg = _store()
    assert store.lock("s", True) == {"ok": True, "locked": True}
    assert cfg.writes == [("hud.lock_layout", True)] and cfg.lock_layout() is True
    store.lock("s", False)
    assert cfg.lock_layout() is False


# --------------------------------------------------------------------------- store: manual wins
def test_user_report_saves_to_hud_layouts_and_clears_undo():
    store, rec, cfg = _store()
    store.arrange("s", preset="goal-start")
    mine = layout.validate_layout({"mode": "task", "tree": {"split": "row", "children": ["goals", "chat"]},
                                   "panels": [{"id": "goals", "type": "goals"}, {"id": "chat", "type": "chat"}]})
    out = store.user_layout("s", "web", "goal", mine)
    assert out["ok"] and out["saved"] == "hud.layouts.web.goal" and out["undo_depth"] == 0
    assert cfg.saved_layout("web", "goal") == mine
    # the write went through the schema's coercion: the whole hud.layouts object
    assert cfg.writes[-1][0] == "hud.layouts" and cfg.writes[-1][1]["web"]["goal"] == mine
    assert store.current("s") == mine
    assert store.undo("s")["ok"] is False  # an older JARVIS change must not clobber what the person did
    assert rec.events().count("hud.layout.apply") == 1  # the report itself is not re-emitted


def test_arrange_keeps_the_persons_saved_arrangement_unless_forced():
    store, rec, _ = _store()
    mine = layout.validate_layout({"mode": "overview", "panels": [{"id": "goals", "type": "goals",
                                                                    "rect": {"x": 0, "y": 0, "w": 0.5, "h": 0.5}}]})
    store.user_layout("s", "web", "goal", mine)
    out = store.arrange("s", preset="goal-start")
    assert out["kept"] == "user" and out["message"] == "kept your arrangement for goal"
    assert rec.last()["layout"] == mine and rec.last()["reason"] == "kept your arrangement for goal"
    forced = store.arrange("s", preset="goal-start", force=True)
    assert "kept" not in forced and forced["layout"]["mode"] == "task"


def test_arrange_leaves_a_declined_activity_alone():
    store, rec, _ = _store()
    store.arrange("s", preset="goal-start")
    store.undo("s")
    rec.frames.clear()
    out = store.arrange("s", preset="goal-start")
    assert out == {**out, "ok": True, "applied": False, "kept": "declined", "delivered": False}
    assert "you undid my last arrangement for goal" in out["message"] and rec.frames == []
    assert store.arrange("s", preset="goal-start", force=True)["op"] == "arrange"
    store.forget("goal")
    assert store.arrange("s", preset="goal-start")["op"] == "arrange"


def test_user_report_rejects_a_layout_the_schema_refuses():
    store, _, cfg = _store()
    with pytest.raises(layout.LayoutError, match="invalid layout"):
        store.user_layout("s", "web", "goal", {"mode": "task", "tree": "ghost", "panels": []})
    with pytest.raises(layout.LayoutError, match="unknown surface"):
        store.user_layout("s", "watch", "goal", {"mode": "ambient"})
    assert cfg.writes == []


def test_current_starts_from_the_persons_saved_default_layout():
    saved = layout.validate_layout({"mode": "focus", "tree": "chat", "panels": [{"id": "chat", "type": "chat"}]})
    store, _, _ = _store(config={"hud": {"layouts": {"web": {"default": saved}}}})
    assert store.current("s") == saved
    assert store.current("s", "android_phone")["mode"] == "task"  # no saved phone layout: working


# --------------------------------------------------------------------------- store: preferences
def test_remembered_layout_wins_over_the_preset_and_persists_in_plugin_state():
    state = FakeState()
    store, _, _ = _store(state=state)
    mine = layout.validate_layout({"mode": "task", "tree": {"split": "column", "children": ["goals", "chat"]},
                                   "panels": [{"id": "goals", "type": "goals"}, {"id": "chat", "type": "chat"}]})
    out = store.arrange("s", layout=mine, activity="goal", remember=True)
    assert out["remembered"] is True and state.data["preferences"]["layouts"]["goal"] == mine
    # a fresh store over the same state (a restarted gateway) still prefers it
    store2, rec2, _ = _store(state=state)
    assert store2.arrange("t", preset="goal-start")["layout"] == mine
    assert rec2.last()["layout"] == mine
    # an explicit layout is always what was asked
    assert store2.arrange("t", layout=layout.PRESETS["goal-start"]["layout"], activity="goal")["layout"]["mode"] == "task"
    assert store2.get("t")["preferences"] == ["goal"]
    assert store2.forget()["forgot"] == ["goal"] and state.data["preferences"] == {"layouts": {}, "declined": {}}


def test_attach_state_only_trusts_a_real_state_object():
    original = layout.STORE.prefs
    try:
        layout.attach_state(lambda *a, **k: None)   # the docs generator's stand-in ctx
        assert layout.STORE.prefs is original
        layout.attach_state(None)
        assert layout.STORE.prefs is original
        state = FakeState()
        layout.attach_state(state)
        assert layout.STORE.prefs is not original and layout.STORE.prefs._backend is state
    finally:
        layout.STORE.prefs = original


# --------------------------------------------------------------------------- presets
def test_every_preset_validates_and_names_its_activity():
    for name, preset in layout.PRESETS.items():
        doc = layout.validate_layout(preset["layout"])
        assert preset["activity"], name
        assert doc["mode"] in layout.HUD_MODES
    assert {"goal-start", "image-open", "approval-focus", "model-warming", "voice-ambient",
            "browser-task", "working"} <= set(layout.PRESETS)
    goal = layout.PRESETS["goal-start"]["layout"]
    assert {"goals", "timeline"} <= set(layout.leaves(goal["tree"]))
    assert layout.PRESETS["approval-focus"]["layout"] == {**layout.PRESETS["approval-focus"]["layout"],
                                                          "mode": "focus", "tree": "approvals"}
    assert layout.PRESETS["voice-ambient"]["layout"]["mode"] == "ambient"


# --------------------------------------------------------------------------- gateway routing
@pytest.fixture
def fake_gateway(monkeypatch):
    emitted: list[tuple[str, str, dict]] = []
    server = types.ModuleType("tui_gateway.server")
    server._sessions = {"abc123": {"session_key": "20260903_layout_key", "transport": object()}}

    def _emit(event, sid, payload=None):
        emitted.append((event, sid, payload))

    server._emit = _emit
    server.emitted = emitted
    tg = types.ModuleType("tui_gateway")
    tg.server = server
    monkeypatch.setitem(sys.modules, "tui_gateway", tg)
    monkeypatch.setitem(sys.modules, "tui_gateway.server", server)
    return server


def test_canonical_key_maps_short_id_and_durable_key_to_one_key(fake_gateway):
    assert channel.canonical_key("abc123") == "20260903_layout_key"
    assert channel.canonical_key("20260903_layout_key") == "20260903_layout_key"
    assert channel.canonical_key(None) == ""


def test_emit_routes_to_the_short_sid(fake_gateway):
    assert channel.emit("20260903_layout_key", "hud.layout.highlight", {"panel": "chat"}) is True
    assert fake_gateway.emitted == [("hud.layout.highlight", "abc123", {"panel": "chat"})]
    assert channel.emit("abc123", "hud.layout.apply", {}) is True
    assert channel.emit("no_such_key", "hud.layout.apply", {}) is False
    assert channel.emit("", "hud.layout.apply", {}) is False


def test_emit_reports_a_wedged_transport_instead_of_raising(fake_gateway):
    def boom(*a):
        raise RuntimeError("socket closed")

    fake_gateway._emit = boom
    assert channel.emit("abc123", "hud.layout.apply", {}) is False


def test_off_the_gateway_the_key_is_its_own_and_nothing_is_delivered(monkeypatch):
    monkeypatch.delitem(sys.modules, "tui_gateway", raising=False)
    monkeypatch.delitem(sys.modules, "tui_gateway.server", raising=False)
    monkeypatch.setattr(channel, "_gateway", lambda: None)
    assert channel.canonical_key("bench-hud-layout-001") == "bench-hud-layout-001"
    assert channel.emit("bench-hud-layout-001", "hud.layout.apply", {}) is False


# --------------------------------------------------------------------------- tool handlers
@pytest.fixture
def clean_store(monkeypatch):
    store, rec, cfg = _store()
    monkeypatch.setattr(layout, "STORE", store)
    return store, rec, cfg


def test_tool_and_command_share_one_state(clean_store, fake_gateway):
    out = json.loads(layout.handle_arrange({"preset": "goal-start"}, session_id="20260903_layout_key"))
    assert out["ok"] and out["op"] == "arrange" and out["preset"] == "goal-start"
    got = json.loads(layout.handle_command("@abc123 get"))
    assert got["activity"] == "goal" and got["undo_depth"] == 1 and got["layouts"]["web"]["mode"] == "task"
    listed = json.loads(layout.handle_list_panels({}, session_id="abc123"))
    assert listed["order"] == ["chat", "goals", "timeline"]


def test_tool_handlers_report_errors_as_json(clean_store):
    assert json.loads(layout.handle_open({"id": "x"}, session_id="s")) == {"ok": False, "error": "open needs a panel id and a type"}
    assert "needs the panel id" in json.loads(layout.handle_close({}, session_id="s"))["error"]
    assert "unknown preset" in json.loads(layout.handle_arrange({"preset": "nope"}, session_id="s"))["error"]
    assert "unknown mode" in json.loads(layout.handle_set_mode({"mode": "grid"}, session_id="s"))["error"]
    assert "unknown surface" in json.loads(layout.handle_list_panels({"surface": "watch"}, session_id="s"))["error"]
    assert json.loads(layout.handle_undo({}, session_id="s"))["ok"] is False


def test_highlight_tool_emits_the_pulse(clean_store):
    store, rec, _ = clean_store
    out = json.loads(layout.handle_highlight({"id": "chat", "reason": "look here"}, session_id="s"))
    assert out["ok"] and out["duration_ms"] == layout.HIGHLIGHT_MS
    assert rec.frames[-1][1] == "hud.layout.highlight"
    assert set(_contract()["highlight_fields"]).issubset(rec.frames[-1][2])


# --------------------------------------------------------------------------- command parsing
def test_command_requires_session_prefix():
    assert "@<session_id>" in layout.handle_command("get")


def test_command_family_roundtrip(clean_store, fake_gateway):
    store, rec, cfg = clean_store
    j = layout.handle_command
    assert json.loads(j("@abc123 presets"))["presets"] == layout.PRESETS
    arranged = json.loads(j("@abc123 arrange goal-start"))
    assert arranged["ok"] and arranged["op"] == "arrange" and arranged["reason"] == "you chose goal-start"
    undone = json.loads(j("@abc123 undo"))
    assert undone["undid"] == "arrange" and undone["origin"] == "user"
    assert json.loads(j("@abc123 lock")) == {"ok": True, "locked": True}
    refused = json.loads(j("@abc123 arrange goal-start"))
    assert refused["refused"] == "lock_layout"
    assert json.loads(j("@abc123 unlock")) == {"ok": True, "locked": False}
    assert json.loads(j("@abc123 forget goal")) == {"ok": True, "forgot": ["goal"]}
    mine = {"mode": "task", "tree": "chat", "panels": [{"id": "chat", "type": "chat"}]}
    reported = json.loads(j(f"@abc123 user web goal {json.dumps(mine)}"))
    assert reported["ok"] and reported["saved"] == "hud.layouts.web.goal"
    assert cfg.saved_layout("web", "goal") == layout.validate_layout(mine)
    assert "usage" in json.loads(j("@abc123 arrange"))["error"]
    assert "not valid JSON" in json.loads(j("@abc123 user web goal {oops"))["error"]
    assert "usage" in json.loads(j("@abc123 user web goal"))["error"]
    assert "usage" in json.loads(j("@abc123 dance"))["error"]


def test_arrange_command_emits_through_the_channel_before_it_answers(monkeypatch, fake_gateway):
    """The apply frame is on the wire before the command's reply, and the reply says so.

    The gateway writes a handler's events and then its response to one transport
    in order, so a client that waits for the event only *after* the dispatch call
    returns has already had it — smoke step 5c read for its own request id, threw
    the frame away and timed out (pipeline 93). This pins both halves the smoke
    now relies on: the emit happens inside the call (hence ahead of the reply),
    and `delivered` distinguishes a routed frame from a silently dropped one.
    Unlike the rest of the command tests this store keeps the real channel
    emitter, so the fake `_sessions` table below is the routing under test.
    """
    store = layout.LayoutStore(config=layout.MemoryConfig({}), prefs=layout.PrefStore(None))
    monkeypatch.setattr(layout, "STORE", store)

    reply = json.loads(layout.handle_command("@abc123 arrange goal-start"))
    assert fake_gateway.emitted, "the command answered without emitting hud.layout.apply"
    event, sid, payload = fake_gateway.emitted[-1]
    # routed by the short id _emit indexes _sessions with, not the durable key
    assert (event, sid) == ("hud.layout.apply", "abc123")
    assert reply["delivered"] is True and payload["rev"] == reply["rev"]

    # No session to route to: the layout is still kept, and the reply admits the drop
    # rather than reporting a success the client would then wait forever on.
    reply = json.loads(layout.handle_command("@no_such_session arrange goal-start"))
    assert reply["ok"] and reply["op"] == "arrange" and reply["delivered"] is False
    assert len(fake_gateway.emitted) == 1
    assert store.current("no_such_session")["mode"] == "task"


# --------------------------------------------------------------------------- registration
def test_register_wires_tools_and_command():
    ctx = plugin.FakeCtx()
    plugin.register(ctx)
    assert list(ctx.tools) == layout.TOOLS
    assert all(ctx.tools[t]["toolset"] == layout.TOOLSET for t in ctx.tools)
    assert all(ctx.tools[t]["schema"]["name"] == t for t in ctx.tools)
    assert layout.COMMAND_NAME in ctx.commands


def test_command_handler_never_raises(monkeypatch):
    ctx = plugin.FakeCtx()
    plugin.register(ctx)

    def boom(_):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(layout, "handle_command", boom)
    assert ctx.commands[layout.COMMAND_NAME]("@s get").startswith("hud_layout error:")


def test_plugin_yaml_lists_every_tool():
    import yaml

    manifest = yaml.safe_load((PLUGIN_DIR / "plugin.yaml").read_text())
    assert manifest["name"] == "hud-layout"
    assert manifest["provides_tools"] == layout.TOOLS


# --------------------------------------------------------------------------- contract
def test_matches_the_shared_contract():
    data = _contract()
    assert layout.EVENTS == data["events"]
    assert layout.OPS == data["ops"]
    assert layout.EDGES == data["edges"]
    assert layout.PANEL_TYPES == data["panel_types"]
    assert layout.TOOLS == list(data["tools"])
    assert layout.TOOLSET == data["toolset"]
    assert layout.COMMAND_NAME == data["command"]["name"]
    assert layout.HIGHLIGHT_MS == data["highlight_ms"]
    assert layout.MAX_UNDO == data["max_undo"]
    assert layout.DEFAULT_SURFACE == data["default_surface"]
    assert layout.LOCK_MESSAGE == data["refusal"]["lock_layout"]
    assert {"layouts": layout.LAYOUTS_KEY, "lock": layout.LOCK_KEY, "mode": layout.MODE_KEY} == data["settings"]
    assert layout.PRESETS == data["presets"]


def test_contract_tool_argument_shapes_match_the_registered_schemas():
    data = _contract()
    for name, shape in data["tools"].items():
        params = plugin.SCHEMAS[name]["parameters"]
        assert list(params["required"]) == shape["required"], name
        assert [k for k in params["properties"] if k not in params["required"]] == shape["optional"], name


def test_contract_layout_document_is_the_settings_schemas_own():
    data = _contract()
    referenced = _resolve(data["layout_document"]["$ref"])
    assert referenced == layout.LayoutDocument.model_json_schema()
    assert tuple(_resolve(data["surfaces"]["$ref"])) == layout.SURFACES
    assert tuple(_resolve(data["modes"]["$ref"])) == layout.HUD_MODES
    examples = _resolve(data["layout_examples"]["$ref"])
    for doc in examples["valid"]:
        layout.validate_layout(doc)
    for doc in examples["invalid"]:
        with pytest.raises(layout.LayoutError):
            layout.validate_layout(doc)


def test_contract_examples_validate_and_tool_calls_behave():
    data = _contract()
    ex = data["examples"]
    assert set(data["apply_fields"]).issubset(ex["apply"]) and ex["apply"]["op"] in layout.OPS
    layout.validate_layout(ex["apply"]["layout"])
    assert set(data["highlight_fields"]).issubset(ex["highlight"])
    assert set(data["user_report_fields"]).issubset(ex["user"])
    assert ex["refused"]["error"] == layout.LOCK_MESSAGE
    store, _, _ = _store()
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(layout, "STORE", store)
        for call in ex["tool_calls"]["valid"]:
            out = json.loads(layout.HANDLERS[call["tool"]](call["args"], session_id="contract"))
            assert out["ok"] is True, (call, out)
        for call in ex["tool_calls"]["invalid"]:
            out = json.loads(layout.HANDLERS[call["tool"]](call["args"], session_id="contract"))
            assert out["ok"] is False and out["error"], (call, out)


# --------------------------------------------------------------------------- #
# v3: the map's own verbs (HUD.md §6.4)
# --------------------------------------------------------------------------- #

@pytest.fixture
def v3():
    """A store and the config dict behind it, for the v3 verbs."""
    store, _rec, cfg = _store()
    return store, cfg.data


def test_band_moves_the_named_panels_and_leaves_the_rest(v3):
    store, _settings = v3
    key = "s-band"
    for pid in ("a-chat", "a-goals", "a-audit"):
        store.open(key, pid, pid.split("-")[1])
    out = store.band(key, ["a-goals", "a-audit"], "outer", reason="that work is finished")
    by = {p["id"]: p for p in out["layout"]["panels"]}
    assert by["a-goals"]["band"] == "outer" and by["a-audit"]["band"] == "outer"
    # A panel nobody named keeps its type's own band, which is what absent means.
    assert by["a-chat"].get("band") is None
    assert out["op"] == "band"


def test_band_refuses_an_unknown_band_an_unknown_panel_and_an_empty_list(v3):
    store, _settings = v3
    key = "s-band2"
    store.open(key, "b-chat", "chat")
    for bad in (
        lambda: store.band(key, ["b-chat"], "sideways"),
        lambda: store.band(key, ["nope"], "inner"),
        lambda: store.band(key, [], "inner"),
    ):
        with pytest.raises(layout.LayoutError):
            bad()


def test_band_is_a_layout_change_and_the_lock_refuses_it(v3):
    store, _settings = v3
    key = "s-band3"
    store.open(key, "c-chat", "chat")
    store.config.set_lock(True)
    with pytest.raises(layout.Locked):
        store.band(key, ["c-chat"], "outer")


def test_the_camera_moves_without_changing_the_document(v3):
    store, _settings = v3
    key = "s-cam"
    store.open(key, "d-chat", "chat")
    before = store.current(key, layout.DEFAULT_SURFACE)
    for call, op in (
        (lambda: store.camera(key, "fly", panel="d-chat"), "fly"),
        (lambda: store.camera(key, "fit"), "fit"),
        (lambda: store.camera(key, "reactor"), "reactor"),
    ):
        out = call()
        assert out["ok"] is True and out["op"] == op
    assert store.current(key, layout.DEFAULT_SURFACE) == before


def test_the_lock_does_not_stop_the_camera(v3):
    """Lock layout means "do not rearrange my panels". Looking at one is not
    rearranging it, and the way back has to be available whatever else is on."""
    store, _settings = v3
    key = "s-cam2"
    store.open(key, "e-chat", "chat")
    store.config.set_lock(True)
    assert store.camera(key, "fly", panel="e-chat")["ok"] is True
    assert store.camera(key, "reactor")["ok"] is True


def test_the_camera_is_not_on_the_undo_stack(v3):
    store, _settings = v3
    key = "s-cam3"
    store.open(key, "f-chat", "chat")
    store.camera(key, "fit")
    # The open is undoable; the camera move left nothing behind it.
    assert store.undo(key).get("ok") is not False
    assert store.undo(key)["ok"] is False


def test_the_camera_refuses_an_unknown_op_and_a_panel_that_is_not_there(v3):
    store, _settings = v3
    key = "s-cam4"
    for bad in (
        lambda: store.camera(key, "orbit"),
        lambda: store.camera(key, "fly"),
        lambda: store.camera(key, "fly", panel="nope"),
    ):
        with pytest.raises(layout.LayoutError):
            bad()
