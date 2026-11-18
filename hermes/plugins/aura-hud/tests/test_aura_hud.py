"""Unit + contract tests for the aura-hud plugin (no Hermes runtime needed).

The panel store's emitter is injected, so the emitted-frame shape is asserted
without a gateway; the gateway-routing helpers (``_state_key``,
``_resolve_tui_sid``, ``_gateway_emit``) are tested against a fake
``tui_gateway.server`` module planted in ``sys.modules``. The contract test
reads ``tests/contracts/hud-panels.json`` and fails if the plugin's copy of the
kinds/events/fields drifts from it or an example stops validating.
"""
from __future__ import annotations

import json
import sys
import threading
import time
import types
from pathlib import Path

import pytest

PLUGIN_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = PLUGIN_DIR.parents[2]  # …/hermes/plugins/aura-hud -> repo root
CONTRACT = REPO_ROOT / "tests" / "contracts" / "hud-panels.json"

sys.path.insert(0, str(PLUGIN_DIR.parent))
from _plugin_loader import load_plugin  # noqa: E402

plugin = load_plugin("aura-hud")
hud = plugin.hud


class Recorder:
    """An injectable emitter that records (state_key, event, payload) frames."""

    def __init__(self, delivered: bool = True):
        self.frames: list[tuple[str, str, dict]] = []
        self.delivered = delivered
        self.dismissed = threading.Event()

    def __call__(self, state_key: str, event: str, payload: dict) -> bool:
        self.frames.append((state_key, event, payload))
        if event == "hud.panel.dismiss":
            self.dismissed.set()
        return self.delivered

    def events(self) -> list[str]:
        return [e for _, e, _ in self.frames]


def _store(delivered: bool = True) -> tuple[hud.HudStore, Recorder]:
    rec = Recorder(delivered=delivered)
    return hud.HudStore(emitter=rec), rec


# --------------------------------------------------------------------------- validation
def test_unknown_kind_is_rejected():
    store, _ = _store()
    with pytest.raises(hud.ValidationError) as exc:
        store.show("s", "hologram", "X", {"markdown": "hi"})
    assert "unknown panel kind" in str(exc.value)


def test_missing_required_field_is_rejected():
    with pytest.raises(hud.ValidationError) as exc:
        hud.validate("table", {"columns": ["A"]})  # rows missing
    assert "rows" in str(exc.value)


def test_wrong_field_type_is_rejected():
    with pytest.raises(hud.ValidationError):
        hud.validate("table", {"columns": "A,B", "rows": []})  # columns not a list


def test_payload_must_be_object():
    with pytest.raises(hud.ValidationError):
        hud.validate("text", "just a string")


@pytest.mark.parametrize("kind", ["video", "audio", "document"])
def test_a_video_panel_needs_a_url_and_a_mime(kind):
    # An artifact url is a digest with no extension, so a media panel without
    # its mime is a file the app can only sniff at; both are required, as strings.
    data = json.loads(CONTRACT.read_text())
    assert data["media_fields"] == ["url", "mime"] == hud.REQUIRED_FIELDS[kind]
    with pytest.raises(hud.ValidationError) as exc:
        hud.validate(kind, {"url": "/hermes/api/artifacts/abc"})
    assert "mime" in str(exc.value)
    with pytest.raises(hud.ValidationError) as exc:
        hud.validate(kind, {"mime": "video/mp4"})
    assert "url" in str(exc.value)
    with pytest.raises(hud.ValidationError):
        hud.validate(kind, {"url": "/hermes/api/artifacts/abc", "mime": ["video/mp4"]})
    hud.validate(kind, {"url": "/hermes/api/artifacts/abc", "mime": "video/mp4"})  # provenance optional


def test_telemetry_accepts_gpus_or_metrics_but_not_neither():
    hud.validate("telemetry", {"gpus": []})
    hud.validate("telemetry", {"metrics": [{"name": "load", "value": 1, "unit": ""}]})
    with pytest.raises(hud.ValidationError):
        hud.validate("telemetry", {"nope": 1})


def test_system_kinds_pass_through_as_objects():
    for kind in ("goal", "approval", "clarify"):
        hud.validate(kind, {"anything": True})


# --------------------------------------------------------------------------- ids / revs
def test_id_format_and_rev_start_at_one():
    store, _ = _store()
    rec, _ = store.show("s", "text", "Hi", {"markdown": "x"})
    assert hud._ID_RE.match(rec["id"]) and rec["rev"] == 1
    assert set(hud.REQUIRED_SHOW_FIELDS).issubset(rec)


def test_update_bumps_rev_and_emits_only_changes():
    store, r = _store()
    rec, _ = store.show("s", "text", "Hi", {"markdown": "one"})
    up, _ = store.update("s", rec["id"], payload={"markdown": "two"}, title="Hi2")
    assert up["rev"] == 2
    frame = [p for _, e, p in r.frames if e == "hud.panel.update"][0]
    assert frame == {"id": rec["id"], "rev": 2, "payload": {"markdown": "two"}, "title": "Hi2"}


def test_show_with_existing_id_replaces_and_bumps_rev():
    store, _ = _store()
    a, _ = store.show("s", "text", "Hi", {"markdown": "one"}, panel_id="p_deadbeef")
    b, _ = store.show("s", "text", "Bye", {"markdown": "two"}, panel_id="p_deadbeef")
    assert a["rev"] == 1 and b["rev"] == 2 and b["created_at"] == a["created_at"]
    assert len(store.list("s")) == 1


def test_update_unknown_panel_raises():
    store, _ = _store()
    with pytest.raises(hud.ValidationError):
        store.update("s", "p_00000000", title="nope")


# --------------------------------------------------------------------------- cap
def test_cap_evicts_oldest_with_a_dismiss():
    store, r = _store()
    ids = [store.show("s", "text", str(i), {"markdown": str(i)})[0]["id"]
           for i in range(hud.MAX_PANELS)]
    assert len(store.list("s")) == hud.MAX_PANELS
    r.frames.clear()
    extra, _ = store.show("s", "text", "new", {"markdown": "new"})
    live = {p["id"] for p in store.list("s")}
    assert len(live) == hud.MAX_PANELS
    assert ids[0] not in live and extra["id"] in live
    # oldest was dismissed (emitted) before the new panel's show
    assert r.frames[0] == ("s", "hud.panel.dismiss", {"id": ids[0]})
    assert r.frames[-1][1] == "hud.panel.show"


# --------------------------------------------------------------------------- ttl
def test_ttl_schedules_a_dismiss_that_fires():
    store, r = _store()
    rec, _ = store.show("s", "notice", "T", {"level": "info", "text": "bye soon"}, ttl_s=0.1)
    assert r.dismissed.wait(2.0), "ttl dismiss never fired"
    assert ("s", "hud.panel.dismiss", {"id": rec["id"]}) in r.frames
    assert store.list("s") == []


def test_dismiss_cancels_a_pending_ttl():
    store, r = _store()
    rec, _ = store.show("s", "text", "T", {"markdown": "x"}, ttl_s=5)
    store.dismiss("s", rec["id"])
    # the explicit dismiss emitted once; the cancelled timer must not emit again
    assert r.events().count("hud.panel.dismiss") == 1


def test_zero_or_negative_ttl_is_ignored():
    store, _ = _store()
    rec, _ = store.show("s", "text", "T", {"markdown": "x"}, ttl_s=0)
    assert rec["ttl_s"] is None and (("s", rec["id"]) not in store._timers)


# --------------------------------------------------------------------------- dismiss
def test_dismiss_all_emits_wildcard_and_clears():
    store, r = _store()
    store.show("s", "text", "1", {"markdown": "1"})
    store.show("s", "text", "2", {"markdown": "2"})
    ids, delivered = store.dismiss("s", "*")
    assert len(ids) == 2 and delivered
    assert ("s", "hud.panel.dismiss", {"id": "*"}) in r.frames
    assert store.list("s") == []


def test_dismiss_unknown_id_emits_nothing():
    store, r = _store()
    ids, delivered = store.dismiss("s", "p_00000000")
    assert ids == [] and delivered is False and r.frames == []


def test_sessions_are_isolated():
    store, _ = _store()
    store.show("a", "text", "A", {"markdown": "a"})
    store.show("b", "text", "B", {"markdown": "b"})
    assert len(store.list("a")) == 1 and len(store.list("b")) == 1
    store.dismiss("a", "*")
    assert store.list("a") == [] and len(store.list("b")) == 1


# --------------------------------------------------------------------------- delivery flag
def test_delivered_false_still_keeps_state():
    store, _ = _store(delivered=False)
    rec, delivered = store.show("s", "text", "X", {"markdown": "x"})
    assert delivered is False
    assert [p["id"] for p in store.list("s")] == [rec["id"]]


# --------------------------------------------------------------------------- gateway routing
@pytest.fixture
def fake_gateway(monkeypatch):
    """Plant a fake tui_gateway.server with a _sessions table and an _emit spy."""
    emitted: list[tuple[str, str, dict]] = []
    server = types.ModuleType("tui_gateway.server")
    # short id "abc123" routes to the durable key the tools carry
    server._sessions = {"abc123": {"session_key": "20260903_hud_key", "transport": object()}}

    def _emit(event, sid, payload=None):
        emitted.append((event, sid, payload))

    server._emit = _emit
    server.emitted = emitted
    tg = types.ModuleType("tui_gateway")
    tg.server = server
    monkeypatch.setitem(sys.modules, "tui_gateway", tg)
    monkeypatch.setitem(sys.modules, "tui_gateway.server", server)
    return server


def test_state_key_maps_short_id_and_durable_key_to_the_same_key(fake_gateway):
    # the /hud command's short id and the tool's durable key both canonicalise
    assert hud._state_key("abc123") == "20260903_hud_key"
    assert hud._state_key("20260903_hud_key") == "20260903_hud_key"


def test_resolve_tui_sid_finds_short_id_from_durable_key(fake_gateway):
    assert hud._resolve_tui_sid("20260903_hud_key") == "abc123"
    assert hud._resolve_tui_sid("abc123") == "abc123"
    assert hud._resolve_tui_sid("unknown") is None


def test_gateway_emit_routes_to_short_sid(fake_gateway):
    delivered = hud._gateway_emit("20260903_hud_key", "hud.panel.show", {"id": "p_1"})
    assert delivered is True
    assert fake_gateway.emitted == [("hud.panel.show", "abc123", {"id": "p_1"})]


def test_gateway_emit_false_without_a_session(fake_gateway):
    assert hud._gateway_emit("no_such_key", "hud.panel.dismiss", {"id": "*"}) is False


# --------------------------------------------------------------------------- tool handlers
@pytest.fixture
def clean_store(monkeypatch):
    monkeypatch.setattr(hud, "STORE", hud.HudStore(emitter=lambda *a: True))


@pytest.mark.usefixtures("clean_store")
def test_tool_show_returns_record_json(fake_gateway):
    out = json.loads(hud.handle_hud_show(
        {"kind": "text", "title": "Hi", "payload": {"markdown": "it works"}},
        session_id="20260903_hud_key"))
    assert out["ok"] and out["kind"] == "text" and hud._ID_RE.match(out["id"])
    # the tool's durable session_id and the command's short id hit one state
    listing = json.loads(hud.handle_hud_list({}, session_id="20260903_hud_key"))
    assert listing["count"] == 1


@pytest.mark.usefixtures("clean_store")
def test_tool_show_rejects_bad_kind(fake_gateway):
    out = json.loads(hud.handle_hud_show(
        {"kind": "bogus", "title": "x", "payload": {}}, session_id="20260903_hud_key"))
    assert out["ok"] is False and "unknown panel kind" in out["error"]


# --------------------------------------------------------------------------- command parsing
def test_split_session():
    assert hud.split_session("@abc123 dismiss *") == ("abc123", "dismiss *")
    assert hud.split_session("list") == (None, "list")
    assert hud.split_session("@s1") == ("s1", "")


def test_command_requires_session_prefix():
    assert "@<session_id>" in hud.handle_command("list")


@pytest.mark.usefixtures("clean_store")
def test_command_list_dismiss_show_roundtrip(fake_gateway):
    j = plugin.hud.handle_command
    shown = j('@abc123 show text A greeting {"markdown": "hello"}')
    rec = json.loads(shown)
    assert rec["ok"] and rec["kind"] == "text" and rec["title"] == "A greeting"
    listing = json.loads(j("@abc123 list"))
    assert len(listing) == 1 and listing[0]["id"] == rec["id"]
    dismissed = json.loads(j(f"@abc123 dismiss {rec['id']}"))
    assert dismissed["dismissed"] == [rec["id"]]
    assert json.loads(j("@abc123 list")) == []


@pytest.mark.usefixtures("clean_store")
def test_command_show_reports_bad_json(fake_gateway):
    assert "not valid JSON" in hud.handle_command("@abc123 show text T {oops}")


@pytest.mark.usefixtures("clean_store")
def test_command_show_reports_bad_kind(fake_gateway):
    assert "hud error" in hud.handle_command('@abc123 show bogus T {"x": 1}')


# --------------------------------------------------------------------------- registration
def test_register_wires_tools_and_command():
    ctx = plugin.FakeCtx()
    plugin.register(ctx)
    assert set(ctx.tools) == {"hud_show", "hud_update", "hud_dismiss", "hud_say", "hud_list", "hud_project", "place_search", "route_eta", "weather_forecast", "car_navigate", "memory_import", "memory_propose", "memory_proposals", "map_regions", "map_region_download", "map_region_remove", "ledger_add", "ledger_list", "ledger_done", "later_add", "later_list", "later_drop", "later_done", "later_release", "loops_list", "loops_close", "calculate", "feeds", "watch_add", "watch_list", "watch_remove", "phone_media", "phone_action", "phone_island_get", "phone_island_set",
                              "routine_add", "routine_list", "routine_remove", "routine_update", "person_remember", "people_find",
                              "person_forget", "place_remember", "place_list", "place_forget", "forget_recent", "complaint_file", "lists"}
    assert all(ctx.tools[t]["toolset"] == "hud" for t in ctx.tools)
    assert "hud" in ctx.commands and "jmemory" in ctx.commands


def test_command_handler_never_raises(monkeypatch):
    ctx = plugin.FakeCtx()
    plugin.register(ctx)

    def boom(_):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(hud, "handle_command", boom)
    assert ctx.commands["hud"]("@s list").startswith("hud error:")


# --------------------------------------------------------------------------- contract
def test_matches_the_shared_contract():
    data = json.loads(CONTRACT.read_text())
    assert hud.EVENTS == data["events"]
    assert hud.ORIGINS == data["origins"]
    assert hud.KINDS == data["kinds"]
    assert hud.REQUIRED_SHOW_FIELDS == data["required_show_fields"]
    assert hud.DISMISS_ALL_ID == data["dismiss_all_id"]
    assert hud.COMMAND_NAME == data["command"]["name"]


def test_every_contract_example_validates_and_shows():
    data = json.loads(CONTRACT.read_text())
    store, _ = _store()
    for kind, payload in data["examples"].items():
        hud.validate(kind, payload)  # must not raise
        rec, _ = store.show(f"s_{kind}", kind, kind.title(), payload)
        assert set(hud.REQUIRED_SHOW_FIELDS).issubset(rec) and rec["kind"] == kind


def test_every_contract_event_is_emitted_somewhere():
    data = json.loads(CONTRACT.read_text())
    store, r = _store()
    rec, _ = store.show("s", "text", "X", {"markdown": "x"})   # hud.panel.show
    store.update("s", rec["id"], title="Y")                     # hud.panel.update
    store.dismiss("s", rec["id"])                                # hud.panel.dismiss
    assert set(r.events()) == set(data["events"])


# --------------------------------------------------------------------------- hud_say
def test_hud_say_emits_one_spoken_line_and_refuses_an_essay(monkeypatch):
    """`hud.say` carries the words and the voice; empty, too long or an odd voice name is refused."""
    store, rec = _store()
    monkeypatch.setattr(hud, "STORE", store)
    monkeypatch.setattr(hud, "_state_key", lambda sid: sid or "s")
    out = json.loads(hud.handle_hud_say({"text": "Bitcoin is down two percent.", "voice": "jarvis"}, session_id="s"))
    assert out == {"ok": True, "delivered": True, "said": "Bitcoin is down two percent."}
    assert rec.frames[-1] == ("s", hud.SAY_EVENT, {"text": "Bitcoin is down two percent.", "voice": "jarvis"})
    # The primary's voice when none is named.
    hud.handle_hud_say({"text": "Ready."}, session_id="s")
    assert rec.frames[-1][2] == {"text": "Ready.", "voice": None}
    assert json.loads(hud.handle_hud_say({"text": "  "}, session_id="s"))["ok"] is False
    assert json.loads(hud.handle_hud_say({"text": "x" * (hud.SAY_MAX_CHARS + 1)}, session_id="s"))["ok"] is False
    assert json.loads(hud.handle_hud_say({"text": "hi", "voice": "../etc"}, session_id="s"))["ok"] is False
    assert len(rec.frames) == 2



def test_car_navigate_is_an_event_on_the_session_s_own_route(monkeypatch):
    """The registered tool emits `hud.car.navigate` through the store, keyed by the session."""
    store, rec = _store()
    monkeypatch.setattr(hud, "STORE", store)
    monkeypatch.setattr(hud, "_state_key", lambda sid: sid or "s")
    ctx = plugin.FakeCtx()
    plugin.register(ctx)
    monkeypatch.setattr(plugin.places, "NAVIGATE_WAIT_S", 0.01)
    monkeypatch.setattr(plugin.phone, "via_house", lambda *a, **k: None)
    out = json.loads(ctx.tools["car_navigate"]["handler"]({"lat": 41.88, "lon": -87.63, "label": "Home"}, session_id="s1"))
    sid, event, payload = rec.frames[-1]
    assert (sid, event) == ("s1", "hud.car.navigate") and payload.pop("id")
    assert payload == {"lat": 41.88, "lon": -87.63, "label": "Home", "app": ""}
    assert out["ok"] is False  # nothing answered and the house had no phone: not "open"


def test_a_guest_s_route_never_reaches_the_owner_s_phone_through_the_house(monkeypatch):
    """Review of T-0806: car_navigate is guest-free, and its house fallback opens the owner's
    own phone (phone_action, which reaches it too, is not guest-free)."""
    from _shared import _speaker
    store, rec = _store()
    monkeypatch.setattr(hud, "STORE", store)
    monkeypatch.setattr(hud, "_state_key", lambda sid: sid or "s")
    monkeypatch.setattr(_speaker, "_cache", {})
    monkeypatch.setattr(_speaker, "fetch", lambda sid: {"level": "guest" if sid == "g" else "owner", "label": "Sam"})
    ctx = plugin.FakeCtx()
    plugin.register(ctx)
    monkeypatch.setattr(plugin.places, "NAVIGATE_WAIT_S", 0.01)
    asked: list = []
    monkeypatch.setattr(plugin.phone, "via_house", lambda *a, **k: asked.append(a) or {"ok": True})
    to = {"lat": 41.88, "lon": -87.63, "label": "Somewhere"}
    assert json.loads(ctx.tools["car_navigate"]["handler"](dict(to), session_id="g"))["ok"] is False
    assert asked == [] and rec.frames[-1][:2] == ("g", "hud.car.navigate")  # the guest's own screens only
    assert json.loads(ctx.tools["car_navigate"]["handler"](dict(to), session_id="o"))["via"] == "house"


def test_phone_media_asks_the_phone_not_the_house(monkeypatch):
    store, rec = _store()
    monkeypatch.setattr(hud, "STORE", store)
    monkeypatch.setattr(hud, "_state_key", lambda sid: sid or "s")
    ctx = plugin.FakeCtx()
    plugin.register(ctx)
    out = json.loads(ctx.tools["phone_media"]["handler"]({"query": "Daft Punk"}, session_id="s1"))
    assert out["ok"] and rec.frames[-1] == ("s1", "hud.phone.media", {"action": "play", "query": "Daft Punk", "kind": "any"})
    assert json.loads(ctx.tools["phone_media"]["handler"]({"action": "rewind"}, session_id="s1"))["ok"] is False
    ctx.tools["phone_media"]["handler"]({"action": "pause", "query": "ignored"}, session_id="s1")
    assert rec.frames[-1][2] == {"action": "pause", "query": "", "kind": "any"}


def _phone_media(monkeypatch, here, house, delivered=True):
    """The registered phone_media with: the adapter's answer to "is this conversation the
    phone's" (`here`: True, False, or None when nobody can say), a house door that answers
    `house`, and a session that does or does not take a frame. The session named "guest" is
    a guest's and "member" a member's; any other is the owner's."""
    from _shared import _speaker
    store, rec = _store(delivered=delivered)
    monkeypatch.setattr(hud, "STORE", store)
    monkeypatch.setattr(hud, "_state_key", lambda sid: sid or "s")
    monkeypatch.setattr(hud, "_resolve_tui_sid", lambda key: key)
    monkeypatch.setattr(_speaker, "_cache", {})
    monkeypatch.setattr(_speaker, "fetch", lambda sid: {"level": sid if sid in ("guest", "member") else "owner", "label": "Sam"})
    monkeypatch.setattr(plugin.phone, "phone_in_session", lambda sid: here)
    asked: list = []
    monkeypatch.setattr(plugin.phone, "via_house", lambda action, params, reason, **kw: asked.append((action, params)) or house)
    ctx = plugin.FakeCtx()
    plugin.register(ctx)
    return (lambda args, sid="desk": json.loads(ctx.tools["phone_media"]["handler"](args, session_id=sid))), rec, asked


HOUSE_DID_IT = {"ok": True, "via": "house", "status": "ok"}


def test_phone_media_from_a_session_that_is_no_phones_goes_over_the_house_channel(monkeypatch):
    """T-1474: "pause my phone" at the desk console answered ok because the desk's own session
    took the frame, and nothing on a desk acts on it. The phone is reached the way
    phone_action reaches it; the media keys carry what they can, media_search the rest."""
    media, rec, asked = _phone_media(monkeypatch, here=False, house=HOUSE_DID_IT)
    for verb in ("pause", "next", "previous", "play"):
        out = media({"action": verb})
        assert out["ok"] and out["via"] == "house" and asked[-1] == ("media_control", {"command": verb})
    out = media({"query": "Daft Punk"})
    assert out["ok"] and asked[-1] == ("media_search", {"action": "play", "query": "Daft Punk", "kind": "any"})
    media({"action": "queue", "title": "Starlight"})
    assert asked[-1][0] == "media_search" and asked[-1][1]["action"] == "queue"
    # The keys cannot shuffle: a shuffled play is the search action's.
    media({"shuffle": True})
    assert asked[-1] == ("media_search", {"action": "play", "query": "", "kind": "any", "shuffle": True})
    # Once each, and the desk's own session is sent nothing it would only ignore.
    assert len(asked) == 7 and rec.frames == []


def test_phone_media_with_no_phone_on_either_route_says_so(monkeypatch):
    media, rec, asked = _phone_media(monkeypatch, here=False, house=None)
    out = media({"action": "pause"})
    assert out["ok"] is False and out["delivered"] is False and "no phone" in out["error"]
    assert len(asked) == 1 and rec.frames == []


def test_phone_media_in_the_phones_own_conversation_is_still_the_sessions_frame(monkeypatch):
    media, rec, asked = _phone_media(monkeypatch, here=True, house=HOUSE_DID_IT)
    assert media({"query": "Daft Punk"}, "phone")["ok"]
    assert rec.frames[-1][:2] == ("phone", "hud.phone.media") and asked == []
    # Nobody can say whose conversation it is (the adapter down): the session first, as before.
    media, rec, asked = _phone_media(monkeypatch, here=None, house=HOUSE_DID_IT)
    assert media({"action": "next"}, "phone")["ok"] and len(rec.frames) == 1 and asked == []
    # ... and the house when the session took nothing.
    media, rec, asked = _phone_media(monkeypatch, here=None, house=HOUSE_DID_IT, delivered=False)
    assert media({"action": "next"}, "phone")["via"] == "house" and asked == [("media_control", {"command": "next"})]


def test_phone_media_from_anyone_but_the_owner_never_reaches_the_owners_phone_over_the_house(monkeypatch):
    """The tool is free, for a guest too, and asks nobody; the house's channel opens the
    owner's own phone. Only the owner's request may take it."""
    for who in ("guest", "member"):
        media, rec, asked = _phone_media(monkeypatch, here=False, house=HOUSE_DID_IT)
        out = media({"action": "pause"}, who)
        assert out["ok"] is False and "no phone" in out["error"] and asked == [] and rec.frames == []
        # On a phone of their own, in their own conversation, it is their session's frame.
        media, rec, asked = _phone_media(monkeypatch, here=True, house=HOUSE_DID_IT)
        assert media({"action": "pause"}, who)["ok"] and rec.frames[-1][0] == who and asked == []


def test_phone_media_passes_on_what_an_older_phone_app_says_to_media_search(monkeypatch):
    refused = {"ok": False, "via": "house", "status": "unsupported", "error": "unknown action: media_search"}
    media, rec, asked = _phone_media(monkeypatch, here=False, house=refused)
    out = media({"query": "Daft Punk"})
    assert out["ok"] is False and out["delivered"] is False and "update" in out["note"]
    assert out["error"] == "unknown action: media_search"


def test_a_song_an_album_or_an_artist_goes_to_the_phone_field_by_field(monkeypatch):
    """Symfonium said "No results" to a sentence as the query (2026-09-25)."""
    store, rec = _store()
    monkeypatch.setattr(hud, "STORE", store)
    monkeypatch.setattr(hud, "_state_key", lambda sid: sid or "s")
    ctx = plugin.FakeCtx()
    plugin.register(ctx)
    media = ctx.tools["phone_media"]["handler"]
    media({"title": "Bohemian Rhapsody", "artist": "Queen"}, session_id="s1")
    assert rec.frames[-1][2] == {"action": "play", "query": "Bohemian Rhapsody", "kind": "song",
                                 "title": "Bohemian Rhapsody", "artist": "Queen"}
    media({"kind": "album", "album": "Discovery", "artist": "Daft Punk"}, session_id="s1")
    assert rec.frames[-1][2]["query"] == "Discovery" and rec.frames[-1][2]["kind"] == "album"
    media({"artist": "Queen"}, session_id="s1")
    assert rec.frames[-1][2]["kind"] == "artist" and rec.frames[-1][2]["query"] == "Queen"
    media({"action": "queue", "title": "Starlight"}, session_id="s1")
    assert rec.frames[-1][2]["action"] == "queue"
    assert json.loads(media({"action": "queue"}, session_id="s1"))["ok"] is False
    assert json.loads(media({"kind": "podcast"}, session_id="s1"))["ok"] is False
    media({"kind": "playlist", "playlist": "Liked songs", "shuffle": True}, session_id="s1")
    assert rec.frames[-1][2]["shuffle"] is True and rec.frames[-1][2]["playlist"] == "Liked songs"
    media({"shuffle": True}, session_id="s1")
    assert rec.frames[-1][2] == {"action": "play", "query": "", "kind": "any", "shuffle": True}


def test_a_phone_action_waits_for_the_phones_answer(monkeypatch):
    """The owner: "can jarvis, text, call, set alarms". The phone answers over /hud phone-result."""
    store, rec = _store()
    monkeypatch.setattr(hud, "STORE", store)
    monkeypatch.setattr(hud, "_state_key", lambda sid: sid or "s")
    # The session route; the house route is its own test (another suite may set HASS_TOKEN).
    monkeypatch.setattr(plugin.phone, "via_house", lambda *a, **k: None)
    ctx = plugin.FakeCtx()
    plugin.register(ctx)
    act = ctx.tools["phone_action"]["handler"]
    out: dict = {}
    t = threading.Thread(target=lambda: out.update(json.loads(
        act({"action": "set_alarm", "params": {"hour": 7, "minute": 30}}, session_id="s1"))))
    t.start()
    for _ in range(200):
        if rec.frames and rec.frames[-1][1] == "hud.phone.action":
            break
        threading.Event().wait(0.01)
    sid, event, payload = rec.frames[-1]
    assert (sid, event, payload["action"], payload["params"]) == ("s1", "hud.phone.action", "set_alarm", {"hour": 7, "minute": 30})
    reply = ctx.commands["hud"](f'@s1 phone-result {payload["id"]} {{"status": "ok", "result": {{"set": "07:30"}}}}')
    assert json.loads(reply)["ok"] is True, (reply, out, payload)
    t.join(5)
    assert out == {"ok": True, "status": "ok", "result": {"set": "07:30"}}
    # Late, or for nobody: said, not raised.
    assert json.loads(ctx.commands["hud"]('@s1 phone-result nobody {"status": "ok"}'))["ok"] is False
    assert json.loads(act({"action": "Send SMS!"}, session_id="s1"))["ok"] is False


def test_a_phone_action_with_no_phone_or_no_answer_says_so():
    from importlib import import_module
    phone = import_module(plugin.__name__ + ".phone")
    assert "no phone" in json.loads(phone.handle_phone_action({"action": "set_timer"}, lambda e, p: False))["error"]
    assert "did not answer" in json.loads(phone.handle_phone_action({"action": "set_timer"}, lambda e, p: True, wait_s=0.05))["error"]


def test_a_phone_action_goes_over_the_house_first_and_the_session_only_when_it_cannot():
    """A text asked for at the desktop: the console ignores hud.phone.action, so only the
    house's device channel reaches the phone (2026-10-06, a 90 s wait and a failure)."""
    from importlib import import_module
    phone = import_module(plugin.__name__ + ".phone")
    emitted: list = []
    house = lambda a, p, r: {"ok": True, "via": "house", "status": "ok", "action": a}  # noqa: E731
    out = json.loads(phone.handle_phone_action({"action": "send_sms", "params": {"to": "x"}},
                                                lambda e, p: emitted.append(e) or True, house=house))
    assert out["via"] == "house" and out["action"] == "send_sms" and emitted == []
    out = json.loads(phone.handle_phone_action({"action": "send_sms"}, lambda e, p: emitted.append(e) or False,
                                                house=lambda a, p, r, wake_s=0.0: None))
    assert emitted == ["hud.phone.action"] and "asleep or offline" in out["error"]


def test_a_phone_action_skips_a_conversation_the_phone_is_not_in():
    """'Start a five minute timer' from a car-probe socket, the phone off the house: emit
    reported delivered to a listener that cannot answer, and the turn waited 90 s (2026-10-09)."""
    from importlib import import_module
    phone = import_module(plugin.__name__ + ".phone")
    emitted: list = []
    no_house = lambda a, p, r, wake_s=0.0: None  # noqa: E731
    out = json.loads(phone.handle_phone_action({"action": "set_timer"}, lambda e, p: emitted.append(e) or True,
                                                wait_s=30, house=no_house, here=lambda: False))
    assert emitted == [] and "asleep or offline" in out["error"]
    # The phone's own conversation, and one the adapter cannot place, keep the route.
    for here in (lambda: True, lambda: None):
        out = json.loads(phone.handle_phone_action({"action": "set_timer"}, lambda e, p: emitted.append(e) or True,
                                                    wait_s=0.05, house=no_house, here=here))
        assert "did not answer" in out["error"]
    assert emitted == ["hud.phone.action"] * 2


def test_the_conversation_is_the_phones_only_when_its_latest_prompt_was(monkeypatch):
    from importlib import import_module
    phone = import_module(plugin.__name__ + ".phone")
    seen: list = []

    # The shared reader's seams (_shared/_surface.py; its own answers are pinned in
    # hermes/plugins/tests/test_surface_shared.py against real listeners).
    shared = phone._surface

    def adapter(url, key, timeout):
        seen.append(url)
        return {"surface": {"name": "android"} if "phone-sid" in url else {"name": "web"}}
    monkeypatch.setattr(shared, "_get", adapter)
    monkeypatch.setattr(shared, "_adapter", lambda: ("http://adapter", "k"))
    assert phone.phone_in_session("phone-sid") is True
    assert phone.phone_in_session("console-sid") is False
    monkeypatch.setattr(shared, "_get", lambda url, key, timeout: {"surface": None})
    assert phone.phone_in_session("probe-sid") is False, "a session that never reported is no phone's"
    assert phone.phone_in_session(None) is None, "no live client: the route fails on its own"

    def down(url, key, timeout):
        raise OSError("refused")
    monkeypatch.setattr(shared, "_get", down)
    assert phone.phone_in_session("phone-sid") is None
    monkeypatch.setattr(shared, "_get", lambda url, key, timeout: ["not", "a", "record"])
    assert phone.phone_in_session("phone-sid") is None, "an answer that is no record cannot say"
    monkeypatch.setattr(shared, "_adapter", lambda: ("http://adapter", ""))
    assert phone.phone_in_session("phone-sid") is None
    assert seen[0] == "http://adapter/api/surface/current?session=phone-sid"


def test_the_house_route_steps_aside_for_a_phone_it_cannot_reach(monkeypatch):
    from importlib import import_module
    from contextlib import nullcontext
    from _shared import _house
    phone = import_module(plugin.__name__ + ".phone")
    answer: dict = {}
    monkeypatch.setattr(_house, "configured", lambda: True)
    monkeypatch.setattr(_house, "client", lambda timeout=0: nullcontext(None))
    monkeypatch.setattr(_house, "devices", lambda c: [{"device_id": "d", "kind": "desktop", "online": True},
                                                      {"device_id": "p", "kind": "android", "online": True}])
    monkeypatch.setattr(_house, "run_action", lambda c, d, a, p, r: {"ok": True, "device_id": d, "response": answer})
    answer.update(status="error", error="the device disconnected before answering")
    assert phone.via_house("send_sms", {}, "") is None, "unreached: the session may still have the phone"
    answer.update(status="denied", error="the owner said no")
    assert phone.via_house("send_sms", {}, "") == {"ok": False, "via": "house", "status": "denied", "error": "the owner said no"}
    monkeypatch.setattr(_house, "devices", lambda c: [{"device_id": "p", "kind": "android", "online": False}])
    assert phone.via_house("send_sms", {}, "") is None


def test_a_sleeping_phone_is_waited_for_when_nothing_else_can_reach_it(monkeypatch):
    """A scheduled job asks while the phone dozes: it comes back to the house in seconds,
    and the action runs instead of failing at once with "no phone is connected"."""
    from importlib import import_module
    from contextlib import nullcontext
    from _shared import _house
    phone = import_module(plugin.__name__ + ".phone")
    polls = iter([[], [{"device_id": "p", "kind": "android", "online": False}],
                  [{"device_id": "p", "kind": "android", "online": True}]])
    monkeypatch.setattr(_house, "configured", lambda: True)
    monkeypatch.setattr(_house, "client", lambda timeout=0: nullcontext(None))
    monkeypatch.setattr(_house, "devices", lambda c: next(polls))
    monkeypatch.setattr(_house, "run_action", lambda c, d, a, p, r: {"ok": True, "response": {"status": "ok", "events": []}})
    monkeypatch.setattr(phone.time, "sleep", lambda s: None)
    out = json.loads(phone.handle_phone_action({"action": "read_calendar"}, lambda e, p: False, house=phone.via_house))
    assert out == {"ok": True, "via": "house", "status": "ok", "events": []}


def test_every_text_jarvis_sends_says_jarvis_wrote_it(monkeypatch, tmp_path):
    """The owner, 2026-10-08: "it needs to tell every message it sends that it was made by
    jarvis". Signed before the phone sees it, so the approval shows the text that goes. In
    the worn persona's name (T-0927): no state file is AURA, the default."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    phone = plugin.phone
    seen: list[tuple[str, dict]] = []

    def house(action, params, reason, **kw):
        seen.append((action, params))
        return {"ok": True}

    phone.handle_phone_action({"action": "send_sms", "params": {"to": "+15550100", "body": "Running late, 10 min."}},
                              lambda e, p: False, house=house)
    phone.handle_phone_action({"action": "notification_reply", "params": {"key": "k", "text": "On my way"}},
                              lambda e, p: False, house=house)
    phone.handle_phone_action({"action": "send_sms", "params": {"to": "+15550100", "body": "Hi, this is Aura, Casey's assistant."}},
                              lambda e, p: False, house=house)
    phone.handle_phone_action({"action": "set_timer", "params": {"seconds": 60, "label": "tea"}},
                              lambda e, p: False, house=house)
    assert seen[0][1]["body"] == "Running late, 10 min.\n- sent by Aura, an AI assistant"
    assert seen[1][1]["text"] == "On my way\n- sent by Aura, an AI assistant"
    assert seen[2][1]["body"] == "Hi, this is Aura, Casey's assistant."  # already says so: not twice
    assert seen[3][1] == {"seconds": 60, "label": "tea"}
    # Plain ASCII: an em dash in the line would put every signed text in UCS-2.
    assert all(ord(c) < 128 for c in seen[0][1]["body"])
    # JARVIS worn: his name, and a body naming him is his already.
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "persona.json").write_text('{"active": "jarvis"}')
    seen.clear()
    phone.handle_phone_action({"action": "send_sms", "params": {"to": "+15550100", "body": "Running late."}},
                              lambda e, p: False, house=house)
    phone.handle_phone_action({"action": "send_sms", "params": {"to": "+15550100", "body": "Jarvis here."}},
                              lambda e, p: False, house=house)
    assert [s[1]["body"] for s in seen] == ["Running late.\n- sent by Jarvis, an AI assistant", "Jarvis here."]


# --------------------------------------------------------------------------- kept panels
# T-0434: a card the owner asks to keep ("keep the weather up") stays as a named
# tab in every conversation and on every screen, and survives a restart.
def _kept_store(tmp_path, live=("a", "b")):
    rec = Recorder()
    return hud.HudStore(emitter=rec, kept_path=tmp_path / "kept.json", live=lambda: list(live)), rec


def test_kept_panel_is_listed_everywhere_and_survives_a_restart(tmp_path):
    store, rec = _kept_store(tmp_path)
    kept, delivered = store.show("a", "text", "Weather", {"markdown": "12°"}, ttl_s=5, keep=True)
    store.show("a", "text", "Scratch", {"markdown": "x"})
    assert delivered and kept["kept"] is True and kept["ttl_s"] is None
    # Shown on every live screen, not only the one that asked.
    assert [(k, e) for k, e, p in rec.frames if p.get("id") == kept["id"]] == [
        ("a", "hud.panel.show"), ("b", "hud.panel.show")]
    assert [p["title"] for p in store.list("b")] == ["Weather"]
    assert [p["title"] for p in store.list("a")] == ["Weather", "Scratch"]
    again, _ = _kept_store(tmp_path)  # the plugin restarted
    assert [(p["id"], p["title"], p["kept"]) for p in again.list("new-session")] == [
        (kept["id"], "Weather", True)]


def test_kept_panels_ignore_eviction_and_stop_at_twelve(tmp_path):
    store = hud.HudStore(emitter=Recorder(), max_panels=2, kept_path=tmp_path / "kept.json",
                         live=lambda: ["a"])
    kept, _ = store.show("a", "text", "Pinned", {"markdown": "k"}, keep=True)
    for i in range(4):
        store.show("a", "text", f"T{i}", {"markdown": "x"})
    assert kept["id"] in [p["id"] for p in store.list("a")]
    for i in range(hud.MAX_KEPT - 1):
        store.show("a", "text", f"K{i}", {"markdown": "k"}, keep=True)
    with pytest.raises(hud.ValidationError, match="dismiss one"):
        store.show("a", "text", "One too many", {"markdown": "k"}, keep=True)
    # Replacing a kept panel by id is not a new one.
    store.show("a", "text", "Pinned, renamed", {"markdown": "k2"}, panel_id=kept["id"], keep=True)


def test_a_shown_panel_becomes_kept_when_shown_again_with_keep(tmp_path):
    store, rec = _kept_store(tmp_path, live=("a",))
    first, _ = store.show("a", "text", "Calendar", {"markdown": "9:00"}, ttl_s=60)
    again, _ = store.show("a", "text", "Calendar", {"markdown": "9:00"}, panel_id=first["id"], keep=True)
    assert again["rev"] == 2 and again["kept"] is True
    assert [p["id"] for p in store.list("a")] == [first["id"]]  # once, not twice
    assert ("a", first["id"]) not in store._timers  # its ttl no longer applies


def test_kept_update_from_any_session_is_saved_and_reaches_every_screen(tmp_path):
    store, rec = _kept_store(tmp_path)
    kept, _ = store.show("a", "text", "Weather", {"markdown": "12°"}, keep=True)
    rec.frames.clear()
    # A cron run has a session of its own, with no screen.
    rec2, _ = store.update("cron_123", kept["id"], payload={"markdown": "14°"})
    assert rec2["rev"] == 2
    assert [(k, e) for k, e, _ in rec.frames] == [("a", "hud.panel.update"), ("b", "hud.panel.update")]
    again, _ = _kept_store(tmp_path)
    assert again.list("x")[0]["payload"] == {"markdown": "14°"}


def test_dismissing_a_kept_panel_forgets_it_and_the_wildcard_keeps_it(tmp_path):
    store, rec = _kept_store(tmp_path)
    kept, _ = store.show("a", "text", "Weather", {"markdown": "12°"}, keep=True)
    scratch, _ = store.show("a", "text", "Scratch", {"markdown": "x"})
    rec.frames.clear()
    ids, _ = store.dismiss("a", "*")
    assert [p["title"] for p in store.list("a")] == ["Weather"]
    # The screen clears everything, then gets the kept panel back; the other screen
    # loses only the session panel it was sent (T-0785).
    assert [(k, e, p["id"]) for k, e, p in rec.frames] == [
        ("a", "hud.panel.dismiss", "*"), ("b", "hud.panel.dismiss", scratch["id"]),
        ("a", "hud.panel.show", kept["id"])]
    assert kept["id"] not in ids
    rec.frames.clear()
    ids, _ = store.dismiss("b", kept["id"])  # closed from the other screen
    assert ids == [kept["id"]] and store.list("a") == []
    assert [(k, e) for k, e, _ in rec.frames] == [("a", "hud.panel.dismiss"), ("b", "hud.panel.dismiss")]
    assert _kept_store(tmp_path)[0].list("a") == []


def test_kept_panels_reach_every_live_gateway_session(fake_gateway, tmp_path):
    fake_gateway._sessions["def456"] = {"session_key": "20260903_other", "transport": object()}
    store = hud.HudStore(kept_path=tmp_path / "kept.json")
    store.show("20260903_hud_key", "text", "Weather", {"markdown": "12°"}, keep=True)
    assert sorted(sid for e, sid, _ in fake_gateway.emitted if e == "hud.panel.show") == ["abc123", "def456"]


@pytest.mark.usefixtures("clean_store")
def test_tool_show_kept_on_request(fake_gateway, tmp_path, monkeypatch):
    monkeypatch.setattr(hud, "STORE", hud.HudStore(emitter=lambda *a: True, kept_path=tmp_path / "k.json"))
    out = json.loads(hud.handle_hud_show(
        {"kind": "text", "title": "Weather", "payload": {"markdown": "12°"}, "keep": True},
        session_id="20260903_hud_key"))
    assert out["ok"] and out["kept"] is True
    assert json.loads(hud.handle_command("@def456 list"))[0]["title"] == "Weather"


def test_a_kept_panel_cron_update_in_the_other_process_reaches_the_screens(tmp_path):
    # Cron runs in the gateway process; the screens hang off the dashboard's.
    screens, rec = _kept_store(tmp_path)
    cron = hud.HudStore(emitter=Recorder(), kept_path=tmp_path / "kept.json", live=lambda: [])
    kept, _ = screens.show("a", "text", "Weather", {"markdown": "12°"}, keep=True)
    rec.frames.clear()
    cron.update("cron_1", kept["id"], payload={"markdown": "14°"})
    screens.refresh_kept()
    assert [(k, e, p["payload"]) for k, e, p in rec.frames] == [
        ("a", "hud.panel.show", {"markdown": "14°"}), ("b", "hud.panel.show", {"markdown": "14°"})]
    rec.frames.clear()
    screens.refresh_kept()  # nothing new: nothing sent
    cron.dismiss("cron_1", kept["id"])
    screens.refresh_kept()
    assert [(k, e) for k, e, _ in rec.frames] == [("a", "hud.panel.dismiss"), ("b", "hud.panel.dismiss")]


def test_a_guest_cannot_keep_change_drop_or_read_the_owner_s_kept_panels(tmp_path, monkeypatch):
    """Review of T-0434: hud_* are guest-free, and keep reaches across sessions, so a guest
    (or a fetched page talking the model into it) could pin, rewrite, drop or read the owner's tabs."""
    from _shared import _speaker
    monkeypatch.setattr(hud, "STORE", hud.HudStore(emitter=lambda *a: True, kept_path=tmp_path / "k.json"))
    monkeypatch.setattr(hud, "_state_key", lambda sid: sid or "s")
    monkeypatch.setattr(_speaker, "_cache", {})
    monkeypatch.setattr(_speaker, "fetch", lambda sid: {"level": "guest" if sid == "g" else "owner", "label": "Sam"})
    owner = json.loads(hud.handle_hud_show(
        {"kind": "text", "title": "Weather", "payload": {"markdown": "12°"}, "keep": True}, session_id="o"))
    pid = owner["panel"]["id"] if "panel" in owner else owner["id"]
    shown = json.loads(hud.handle_hud_show(
        {"kind": "text", "title": "Mine", "payload": {"markdown": "x"}, "keep": True}, session_id="g"))
    assert shown["ok"] and not shown.get("kept")  # shown to the guest's own session only
    for out in (hud.handle_hud_show({"kind": "text", "title": "Hijack", "payload": {"markdown": "y"}, "id": pid},
                                    session_id="g"),
                hud.handle_hud_update({"id": pid, "payload": {"markdown": "y"}}, session_id="g"),
                hud.handle_hud_dismiss({"id": pid}, session_id="g")):
        assert json.loads(out)["ok"] is False
    titles = [p["title"] for p in json.loads(hud.handle_hud_list({}, session_id="g"))["panels"]]
    assert titles == ["Mine"]
    assert [p["title"] for p in hud.STORE.list("o")] == ["Weather"]


def test_an_unreadable_or_corrupt_kept_file_leaves_the_panels_up(tmp_path, monkeypatch):
    store, rec = _kept_store(tmp_path)
    store.show("a", "text", "Weather", {"markdown": "12°"}, keep=True)
    rec.frames.clear()
    real = Path.read_text
    monkeypatch.setattr(Path, "read_text", lambda self, *a, **k: (_ for _ in ()).throw(PermissionError("busy")))
    store.refresh_kept()
    monkeypatch.setattr(Path, "read_text", real)
    (tmp_path / "kept.json").write_text("{half a fi", encoding="utf-8")
    store.refresh_kept()
    assert rec.frames == [] and [p["title"] for p in store.list("a")] == ["Weather"]


def test_the_shared_json_write_uses_a_temp_file_of_its_own_per_process(tmp_path, monkeypatch):
    """Gateway and dashboard both save kept.json; one shared kept.tmp let them write into each other."""
    from _shared import _jsonstore
    seen: list = []
    real = _jsonstore.os.replace
    monkeypatch.setattr(_jsonstore.os, "replace", lambda a, b: seen.append(Path(a).name) or real(a, b))
    _jsonstore.write_json(tmp_path / "kept.json", {"panels": []})
    assert seen == [f"kept.{_jsonstore.os.getpid()}.tmp"]


def test_the_kept_watcher_brings_another_process_s_update_to_the_screens(tmp_path, monkeypatch):
    monkeypatch.setattr(hud, "KEPT_POLL_S", 0.02)
    rec = Recorder()
    screens = hud.HudStore(emitter=rec, kept_path=tmp_path / "kept.json", live=lambda: ["a"], watch=True)
    cron = hud.HudStore(emitter=Recorder(), kept_path=tmp_path / "kept.json", live=lambda: [])
    kept, _ = screens.show("a", "text", "Weather", {"markdown": "12°"}, keep=True)
    screens.list("a")  # the first list starts the watcher
    rec.frames.clear()
    cron.update("cron_1", kept["id"], payload={"markdown": "14°"})
    deadline = time.monotonic() + 2
    while not rec.frames and time.monotonic() < deadline:
        time.sleep(0.01)
    assert rec.frames and rec.frames[0][2]["payload"] == {"markdown": "14°"}


# --------------------------------------------------------------------------- the owner's screens
# T-0785: a panel Jarvis showed during a phone or car voice turn stayed in that turn's own
# session, so the phone's home and the car page (each the console, on a session of its
# own) never drew it. What the owner's session shows now reaches each of their screens.
def _screens(tmp_path):
    rec = Recorder()
    store = hud.HudStore(emitter=rec, kept_path=tmp_path / "kept.json",
                         live=lambda: ["phone", "car", "guest", "member"],
                         owner=lambda sid: sid in ("phone", "car"))
    return store, rec


def test_the_owners_panel_reaches_each_of_their_screens_and_no_one_elses(tmp_path):
    store, rec = _screens(tmp_path)
    shown, delivered = store.show("phone", "text", "Traffic", {"markdown": "12 min"})
    assert delivered
    assert [(k, e) for k, e, _ in rec.frames] == [("phone", "hud.panel.show"), ("car", "hud.panel.show")]
    rec.frames.clear()
    store.update("phone", shown["id"], payload={"markdown": "14 min"})
    store.dismiss("phone", shown["id"])
    assert [(k, e) for k, e, _ in rec.frames] == [
        ("phone", "hud.panel.update"), ("car", "hud.panel.update"),
        ("phone", "hud.panel.dismiss"), ("car", "hud.panel.dismiss")]
    # The other screens hold no copy: a reload there does not bring it back.
    assert store.list("car") == []


def test_a_guests_or_members_panel_stays_on_their_own_screen(tmp_path):
    store, rec = _screens(tmp_path)
    store.show("guest", "text", "Their notes", {"markdown": "x"})
    store.show("member", "text", "Their list", {"markdown": "y"})
    assert [(k, e) for k, e, _ in rec.frames] == [("guest", "hud.panel.show"), ("member", "hud.panel.show")]


def test_clearing_one_screen_takes_only_its_own_panels_off_the_others(tmp_path):
    store, rec = _screens(tmp_path)
    mine, _ = store.show("phone", "text", "Traffic", {"markdown": "12 min"})
    store.show("car", "text", "Fuel", {"markdown": "half"})
    rec.frames.clear()
    store.dismiss("phone", "*")
    # The wildcard clears the asking screen; the car loses only the phone's panel, not its own.
    assert [(k, e, p["id"]) for k, e, p in rec.frames] == [
        ("phone", "hud.panel.dismiss", "*"), ("car", "hud.panel.dismiss", mine["id"])]


def test_the_asker_is_judged_by_the_id_the_adapter_records(fake_gateway, tmp_path):
    # The tools hold the durable key; the adapter's speaker record is by the short id.
    fake_gateway._sessions["def456"] = {"session_key": "20260903_other", "transport": object()}
    asked: list[str] = []
    store = hud.HudStore(kept_path=tmp_path / "k.json",
                         owner=lambda sid: asked.append(sid) or sid != "abc123")
    store.show("20260903_hud_key", "text", "Their notes", {"markdown": "x"})
    assert asked[0] == "abc123"
    assert [sid for e, sid, _ in fake_gateway.emitted if e == "hud.panel.show"] == ["abc123"]


def test_say_stays_with_the_screen_that_asked(tmp_path):
    store, rec = _screens(tmp_path)
    store.emit("phone", "hud.say", {"text": "hello"})
    assert [(k, e) for k, e, _ in rec.frames] == [("phone", "hud.say")]


ISLAND = json.loads((REPO_ROOT / "tests/contracts/island-settings.json").read_text())


def test_the_island_tools_are_registered_under_the_contracts_names():
    ctx = plugin.FakeCtx()
    plugin.register(ctx)
    assert set(ISLAND["tools"].values()) <= set(ctx.tools)
    schema = ctx.tools[ISLAND["tools"]["set"]]["schema"]["parameters"]
    named = {k: p for k, p in schema["properties"].items() if k != "reason"}
    assert set(named) == set(ISLAND["settings"])
    assert all(p["type"] == "boolean" for p in named.values())
    assert plugin.phone.ISLAND_SETTINGS == tuple(ISLAND["settings"])
    assert plugin.phone.ISLAND_ACTIONS == ISLAND["actions"]


def test_the_island_read_and_change_send_the_contracts_actions_over_the_house():
    phone = plugin.phone
    seen: list = []

    def house(action, params, reason, **kw):
        seen.append((action, params, kw.get("reserved")))
        return {"ok": True, "via": "house", "status": "ok", "result": {"settings": ISLAND["defaults"]}}

    no_session = lambda e, p: pytest.fail("the house answered")  # noqa: E731
    out = json.loads(phone.handle_island("get", {}, no_session, house=house))
    assert out["ok"] and seen[-1] == (ISLAND["actions"]["get"], {}, None)
    out = json.loads(phone.handle_island("set", ISLAND["examples"]["set_command"], no_session, house=house))
    assert out["ok"] and seen[-1] == (ISLAND["actions"]["set"], ISLAND["examples"]["set_command"], True)


def test_an_island_change_the_phone_would_refuse_is_refused_before_it_is_sent():
    phone = plugin.phone
    sent: list = []
    house = lambda *a, **k: sent.append(a)  # noqa: E731
    for bad in ISLAND["examples"]["refused"] + [{"status": None}, {"cover": 1}, {"cover": "true"}]:
        out = json.loads(phone.handle_island("set", bad, lambda e, p: sent.append(e) or True, house=house))
        assert out["ok"] is False and out["error"], bad
    assert sent == []


def test_the_island_change_reaches_the_phone_over_the_session_when_the_house_cannot(monkeypatch):
    """The same two routes phone_action has; the reserved action is the tool's own on both."""
    phone = plugin.phone
    emitted: list = []
    out = json.loads(phone.handle_island(
        "set", {"status": True}, lambda e, p: emitted.append((e, p["action"], p["params"])) or False,
        house=lambda a, p, r, **k: None))
    assert emitted == [("hud.phone.action", ISLAND["actions"]["set"], {"status": True})]
    assert out["ok"] is False


def test_via_house_sends_a_reserved_action_only_when_its_tool_says_so(monkeypatch):
    from contextlib import nullcontext
    from _shared import _house
    phone = plugin.phone
    sent: list = []
    monkeypatch.setattr(_house, "configured", lambda: True)
    monkeypatch.setattr(_house, "client", lambda timeout=0: nullcontext(None))
    monkeypatch.setattr(_house, "devices", lambda c: [{"device_id": "p", "kind": "android", "online": True}])

    def run(c, d, a, p, r, reserved=False):
        sent.append((a, reserved))
        return {"ok": True, "response": {"status": "ok"}}

    monkeypatch.setattr(_house, "run_action", run)
    phone.via_house(ISLAND["actions"]["set"], {"status": True}, "", reserved=True)
    phone.via_house(ISLAND["actions"]["get"], {}, "")
    assert sent == [(ISLAND["actions"]["set"], True), (ISLAND["actions"]["get"], False)]


def test_phone_action_refuses_the_island_change_and_allows_the_read():
    """The matrix row for phone_action is free: only the setter's own tool carries the ask."""
    phone = plugin.phone
    emitted, housed = [], []
    out = json.loads(phone.handle_phone_action(
        {"action": ISLAND["actions"]["set"], "params": {"status": True}},
        lambda e, p: emitted.append(e) or True, wait_s=0.05, house=lambda *a, **k: housed.append(a)))
    assert not out["ok"] and "own tool" in out["error"] and emitted == [] and housed == []
    phone.handle_phone_action({"action": ISLAND["actions"]["get"]}, lambda e, p: True, wait_s=0.05,
                              house=lambda *a, **k: housed.append(a) or {"ok": True})
    assert [h[0] for h in housed] == [ISLAND["actions"]["get"]]


def test_phone_action_refuses_the_emergency_alert_on_both_routes():
    """The house route's refusal used to read as "not on the house" and the session route sent it."""
    from importlib import import_module
    phone = import_module(plugin.__name__ + ".phone")
    emitted, housed = [], []
    out = json.loads(phone.handle_phone_action(
        {"action": "emergency_alert", "params": {"contacts": [{"name": "Sam", "number": "+15550100"}]}},
        lambda e, p: emitted.append(e) or True, wait_s=0.05, house=lambda *a, **k: housed.append(a)))
    assert not out["ok"] and "own tool" in out["error"] and emitted == [] and housed == []


def test_the_owners_sessions_are_one_audience_and_anyone_elses_is_their_own(tmp_path):
    """T-1471: what a tool remembers per screen is remembered per audience, the same rule share uses."""
    store, _ = _screens(tmp_path)
    assert store.audience("phone") == store.audience("car") == hud.OWNER_SCREENS
    assert store.audience("member") == "member" and store.audience("guest") == "guest"
    assert hud.OWNER_SCREENS not in ("phone", "car", "member", "guest")
