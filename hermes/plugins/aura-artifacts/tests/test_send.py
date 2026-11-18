"""The handoff and the prompt line: the two halves of "hand it to a surface that can".

`media-handoff-010` is the gate: from the terminal, "play it on the desktop"
must call `artifact_send`, not describe the file or hunt for a player. These
pin what that tool does with the adapter's answers, and that the line the
system prompt carries is the surface the client reported, narrowed, or the
poorest profile when nobody said.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from _plugin_loader import FakeCtx, load_plugin  # noqa: E402

plugin = load_plugin("aura-artifacts")
tools, deliver, surfaces = plugin.tools, plugin.deliver, plugin.surfaces

ID1 = "a" * 64
CLIP = {"id": ID1, "mime": "video/mp4", "title": "dusk", "size": 20 * surfaces.MB,
        "signed_url": "http://jarvis.tail:8643/api/artifacts/" + ID1 + "?exp=1&sig=x"}


@pytest.fixture(autouse=True)
def key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AURA_ADAPTER_KEY", "k")
    monkeypatch.setenv("AURA_PUBLIC_URL", "http://jarvis.tail:8643")


def adapter(monkeypatch, *, sessions: dict[str, list[str]], reports: dict[str, dict] | None = None,
            artifacts: list[dict] | None = None):
    """The adapter's three answers, keyed by path; every call is recorded."""
    seen: list[str] = []

    def _call(method, path, *, body=None, headers=None):
        seen.append(path)
        if path.startswith("/api/artifacts?"):
            return {"artifacts": artifacts if artifacts is not None else [CLIP]}
        if path.startswith("/api/surface/sessions?"):
            name = path.rsplit("=", 1)[1]
            return {"surface": name, "sessions": sessions.get(name, [])}
        if path.startswith("/api/surface/current?"):
            sid = path.rsplit("=", 1)[1]
            return {"session_id": sid, "surface": (reports or {}).get(sid), "at": 1.0}
        raise AssertionError(path)

    monkeypatch.setattr(tools, "_call", _call)
    # The surface record is read through the shared reader (_shared/_surface.py), not _call:
    # its one network seam answers from the same table, so `seen` stays the adapter's log.
    monkeypatch.setattr(tools._surface, "_get",
                        lambda url, key, timeout: _call("GET", url[url.index("/api/"):]))
    return seen


def shown(monkeypatch, *, live: set[str]):
    """A delivery that reaches the sessions in ``live`` and nobody else."""
    calls: list[tuple[str, dict]] = []

    def _deliver(session_id, out):
        calls.append((session_id, out))
        if session_id in live:
            return {"delivered": True, "panel_id": "p-" + session_id}
        return {"delivered": False, "reason": "no HUD client is on this session"}

    monkeypatch.setattr(deliver, "_deliver", _deliver)
    return calls


class TestSend:
    def test_it_lands_on_the_newest_live_session_of_that_surface(self, monkeypatch) -> None:
        """Newest first, and a session that reported in but has since gone is skipped."""
        adapter(monkeypatch, sessions={"desktop": ["d-new", "d-old"]})
        calls = shown(monkeypatch, live={"d-old"})
        out = json.loads(tools.handle_send({"id": ID1, "to": "Desktop"}))
        assert out["ok"] is True and out["to"] == "desktop" and out["session_id"] == "d-old"
        assert out["panel_id"] == "p-d-old" and out["say"].count(".") == 1
        assert [sid for sid, _ in calls] == ["d-new", "d-old"]
        assert calls[0][1]["signed_url"] == CLIP["signed_url"], "the panel carries the signed url"

    def test_with_nothing_of_that_kind_open_it_says_so_and_gives_the_link(self, monkeypatch) -> None:
        adapter(monkeypatch, sessions={})
        calls = shown(monkeypatch, live=set())
        out = json.loads(tools.handle_send({"id": ID1, "to": "android"}))
        assert out["ok"] is False and "no android" in out["error"]
        assert out["url"] == f"http://jarvis.tail:8643/api/artifacts/{ID1}"
        assert calls == [], "nothing was routed anywhere"

    def test_when_every_client_of_it_has_gone_the_answer_is_the_link_not_a_claim(self, monkeypatch) -> None:
        adapter(monkeypatch, sessions={"web": ["w-1"]})
        shown(monkeypatch, live=set())
        out = json.loads(tools.handle_send({"id": ID1, "to": "web"}))
        assert out["ok"] is False and out["url"].endswith(ID1) and "connected" in out["error"]

    def test_a_surface_that_cannot_show_the_thing_is_refused_before_any_lookup(self, monkeypatch) -> None:
        """A handoff to the terminal is the novelty representation by another door."""
        seen = adapter(monkeypatch, sessions={"console": ["c-1"]})
        calls = shown(monkeypatch, live={"c-1"})
        out = json.loads(tools.handle_send({"id": ID1, "to": "console"}))
        assert out["ok"] is False and "cannot show video/mp4" in out["error"] and out["url"]
        assert not any(p.startswith("/api/surface/sessions") for p in seen) and calls == []

    def test_too_big_for_the_target_is_a_link_too(self, monkeypatch) -> None:
        big = {**CLIP, "size": 900 * surfaces.MB}
        adapter(monkeypatch, sessions={"web": ["w-1"]}, artifacts=[big])
        shown(monkeypatch, live={"w-1"})
        out = json.loads(tools.handle_send({"id": ID1, "to": "web"}))
        assert out["ok"] is False and "cannot show" in out["error"]

    def test_an_unknown_surface_or_id_is_said_plainly(self, monkeypatch) -> None:
        seen = adapter(monkeypatch, sessions={})
        out = json.loads(tools.handle_send({"id": ID1, "to": "fridge"}))
        assert out["ok"] is False and "fridge" in out["error"] and "desktop" in out["error"]
        out = json.loads(tools.handle_send({"id": "../health", "to": "web"}))
        assert out["ok"] is False and "not an artifact id" in out["error"]
        assert seen == [], "neither reached the store"
        out = json.loads(tools.handle_send({"id": "b" * 64, "to": "web"}))
        assert out["ok"] is False and "no artifact" in out["error"]

    def test_it_is_free_at_the_plugin_gate_and_registered(self) -> None:
        assert tools.build_approval("artifact_send", {"id": ID1, "to": "web"}) is None
        ctx = FakeCtx()
        plugin.register(ctx)
        assert "artifact_send" in ctx.tools and ctx.tools["artifact_send"]["toolset"] == "artifacts"


class TestPromptLine:
    def test_the_section_is_registered_when_hermes_offers_one(self) -> None:
        class Ctx(FakeCtx):
            def __init__(self):
                super().__init__()
                self.sections = {}

            def register_system_prompt_section(self, id, content, *, position="after_memory", **_):
                self.sections[id] = (content, position)

        ctx = Ctx()
        plugin.register(ctx)
        content, position = ctx.sections["jarvis-surface"]
        assert content is tools.render_surface_section and position == "after_memory"
        # And a Hermes without sections still gets every tool.
        plain = FakeCtx()
        plugin.register(plain)
        assert "artifact_send" in plain.tools

    def test_with_no_report_the_line_is_the_poorest_profile(self, monkeypatch) -> None:
        adapter(monkeypatch, sessions={}, reports={})
        line = tools.render_surface_section({"session_id": "s-1"})
        assert line == surfaces.summary("unknown") and "no media at all" in line and "\n" not in line

    def test_the_line_is_the_surface_the_client_reported_narrowed(self, monkeypatch) -> None:
        adapter(monkeypatch, sessions={}, reports={"s-1": {"name": "web", "video": False}})
        line = tools.render_surface_section({"session_id": "s-1"})
        assert line.startswith("Active surface: web.") and "images" in line and "video" not in line

    def test_a_session_nobody_is_on_costs_no_lookup(self, monkeypatch) -> None:
        seen = adapter(monkeypatch, sessions={})
        assert tools.render_surface_section({}) == surfaces.summary("unknown")
        assert tools.render_surface_section(None) == surfaces.summary("unknown")
        assert seen == []

    def test_the_durable_id_is_mapped_to_the_live_one_through_jarvis_hud(self, monkeypatch) -> None:
        """A tool handler holds the durable id; the client reported under the live one."""
        seen = adapter(monkeypatch, sessions={}, reports={"live-7": {"name": "android"}})

        class Hud:
            @staticmethod
            def _resolve_tui_sid(state_key):
                return "live-7" if state_key == "durable-7" else None

        monkeypatch.setattr(deliver, "_hud_module", lambda: Hud)
        assert tools.reported_surface("durable-7") == {"name": "android"}
        assert seen == ["/api/surface/current?session=durable-7", "/api/surface/current?session=live-7"]

    def test_the_tool_answers_for_the_reported_surface_when_unnamed(self, monkeypatch) -> None:
        adapter(monkeypatch, sessions={}, reports={"s-1": {"name": "android", "max_bytes": 4 * surfaces.MB}})
        out = json.loads(tools.handle_surface_capabilities({}, session_id="s-1"))
        assert out["surface"] == "android" and out["reported"] is True
        assert out["max_bytes"] == 4 * surfaces.MB and out["can"]["video"] is True
        # Named, it is the table for that surface, whatever this session reported.
        out = json.loads(tools.handle_surface_capabilities({"surface": "console"}, session_id="s-1"))
        assert out["surface"] == "console" and out["reported"] is False and out["can"]["video"] is False
        # Unnamed and unreported: the conservative profile, as before.
        out = json.loads(tools.handle_surface_capabilities({}, session_id="s-none"))
        assert out["surface"] == "unknown" and out["can"]["video"] is False
