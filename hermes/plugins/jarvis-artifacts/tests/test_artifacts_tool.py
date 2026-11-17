"""The artifact toolset, with the adapter faked.

These cover what the tool has to get right on its own: refusing before it reads
a huge file into memory, turning the store's error text into something the model
can act on, and gating deletion but nothing else.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Real-shaped ids: the tool refuses anything that is not a SHA-256 digest before
# it becomes part of a URL, so a fixture id has to look like one.
ID1 = "a" * 64
ID2 = "b" * 64

# `_plugin_loader`, not a sys.path insert of the plugin directory: every plugin
# has a module called `tools`, so putting one on sys.path makes `import tools`
# resolve to it for the whole session and shadows the others. That is not
# hypothetical -- doing it here broke two jarvis-guard tests, which passed alone
# and failed in the full run.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from _plugin_loader import load_plugin  # noqa: E402

tools = load_plugin("jarvis-artifacts").tools


_REAL_DIRS = tools._HOME_OUTPUT_DIRS


@pytest.fixture(autouse=True)
def key(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AURA_API_KEY", "k")
    monkeypatch.setenv("AURA_PUBLIC_URL", "http://jarvis.tail:8643")
    # The tests that are not about the folder rule write straight into tmp_path: here it is an
    # output folder. The folder-rule tests put the real list back (`folders`).
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(tools, "_HOME_OUTPUT_DIRS", (*_REAL_DIRS, "."))


def fake(monkeypatch: pytest.MonkeyPatch, reply: dict, seen: dict | None = None):
    def _call(method, path, *, body=None, headers=None):
        if seen is not None:
            seen.update({"method": method, "path": path, "body": body, "headers": headers or {}})
        return reply

    monkeypatch.setattr(tools, "_call", _call)


def test_publishing_returns_a_url_a_phone_can_open(tmp_path: Path, monkeypatch) -> None:
    f = tmp_path / "clip.mp4"
    f.write_bytes(b"video")
    fake(monkeypatch, {"id": ID1, "size": 5})
    out = json.loads(tools.handle_publish({"path": str(f), "mime": "video/mp4"}))
    assert out["url"] == f"http://jarvis.tail:8643/api/artifacts/{ID1}"


OFFICE_MIMES = {
    "report.docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "sheet.xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "deck.pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}


@pytest.mark.parametrize("name", sorted(OFFICE_MIMES))
def test_an_office_file_publishes_with_its_mime_and_is_a_document_to_every_surface(
        name: str, tmp_path: Path, monkeypatch) -> None:
    """T-0826: the making-documents skill publishes .docx/.xlsx/.pptx through this tool. The
    mime rides through unchanged (the adapter stores what it is told and serves it inline, not
    as the executable download), and the surfaces table calls it a document, so the console,
    desktop and phone offer to open it. Does not cover what each client does with the bytes."""
    f = tmp_path / name
    f.write_bytes(b"PK\x03\x04")
    seen: dict = {}
    fake(monkeypatch, {"id": ID1, "size": 4, "mime": OFFICE_MIMES[name]}, seen)
    out = json.loads(tools.handle_publish({"path": str(f), "mime": OFFICE_MIMES[name]}))
    assert seen["headers"]["x-artifact-mime"] == OFFICE_MIMES[name]
    assert seen["headers"]["x-artifact-title"] == name
    assert out["url"] == f"http://jarvis.tail:8643/api/artifacts/{ID1}"
    surfaces = load_plugin("jarvis-artifacts").surfaces
    assert surfaces.capability_for(OFFICE_MIMES[name]) == "documents"
    assert surfaces.REGISTRY["web"].can("documents") and surfaces.REGISTRY["android"].can("documents")


def test_the_title_defaults_to_the_filename(tmp_path: Path, monkeypatch) -> None:
    f = tmp_path / "report.pdf"
    f.write_bytes(b"pdf")
    seen: dict = {}
    fake(monkeypatch, {"id": "x"}, seen)
    tools.handle_publish({"path": str(f)})
    assert seen["headers"]["x-artifact-title"] == "report.pdf"


def test_a_ttl_of_never_is_passed_through(tmp_path: Path, monkeypatch) -> None:
    f = tmp_path / "keep.png"
    f.write_bytes(b"p")
    seen: dict = {}
    fake(monkeypatch, {"id": "x"}, seen)
    tools.handle_publish({"path": str(f), "ttl": "never"})
    assert seen["headers"]["x-artifact-ttl"] == "never"


def test_a_missing_file_is_said_plainly_and_nothing_is_sent(tmp_path: Path, monkeypatch) -> None:
    called = {"n": 0}
    monkeypatch.setattr(tools, "_call", lambda *a, **k: called.update(n=called["n"] + 1) or {})
    out = json.loads(tools.handle_publish({"path": str(tmp_path / "nope")}))
    assert out["ok"] is False and "cannot read" in out["error"]
    assert called["n"] == 0


def test_a_file_over_the_tool_limit_is_refused_before_it_is_read(tmp_path: Path, monkeypatch) -> None:
    """The bytes pass through this process, so the limit is about memory here."""
    f = tmp_path / "huge.bin"
    f.write_bytes(b"0" * 128)
    monkeypatch.setattr(tools, "MAX_PUBLISH_BYTES", 10)
    called = {"n": 0}
    monkeypatch.setattr(tools, "_call", lambda *a, **k: called.update(n=called["n"] + 1) or {})
    out = json.loads(tools.handle_publish({"path": str(f)}))
    assert out["ok"] is False and "tool limit" in out["error"]
    assert called["n"] == 0


def test_a_listing_carries_a_url_per_row(monkeypatch) -> None:
    fake(monkeypatch, {"artifacts": [{"id": ID1}, {"id": ID2}]})
    out = json.loads(tools.handle_list({}))
    assert [r["url"] for r in out["artifacts"]] == [
        f"http://jarvis.tail:8643/api/artifacts/{ID1}",
        f"http://jarvis.tail:8643/api/artifacts/{ID2}",
    ]
    assert out["count"] == 2


def test_an_unknown_id_is_said_plainly(monkeypatch) -> None:
    fake(monkeypatch, {"artifacts": [{"id": ID2}]})
    out = json.loads(tools.handle_get({"id": ID1}))
    assert out["ok"] is False and ID1 in out["error"]


def test_without_a_key_the_tool_says_so_rather_than_hanging(monkeypatch) -> None:
    monkeypatch.delenv("AURA_API_KEY", raising=False)
    out = json.loads(tools.handle_list({}))
    assert out["ok"] is False and "AURA_API_KEY" in out["error"]


class TestApproval:
    def test_deleting_needs_approval_and_names_the_artifact(self) -> None:
        """The directive shape, not just the message.

        An earlier version returned the message as a bare string. The hook reads
        `.get("action")` off what it is handed, so every delete would have gone
        straight through — a gate that type-checked nowhere and enforced nothing.
        mypy caught it; this keeps it caught.
        """
        d = tools.build_approval("artifact_delete", {"id": ID1})
        assert isinstance(d, dict), "the hook consumes a directive, not a string"
        assert d["action"] == "approve"
        assert ID1 in d["message"]
        assert d["rule_key"] == f"artifacts:delete:{ID1}", "per artifact, not per store"

    @pytest.mark.parametrize("name", ["artifact_publish", "artifact_list", "artifact_get", "artifact_pin"])
    def test_everything_else_is_free(self, name: str) -> None:
        assert tools.build_approval(name, {"id": ID1}) is None


class TestHardening:
    """The three findings from the security review of 1b9f8967, each with a case."""

    @pytest.mark.parametrize(
        "bad",
        ["../../health", "abc", "a" * 63, "A" * 64, ID1 + "?pinned=true", ID1 + "/../../x", ""],
    )
    def test_an_id_is_validated_before_it_becomes_a_url(self, bad: str, monkeypatch) -> None:
        """`url-injection` + `sibling-guard-parity`.

        The adapter validates too, but an id from a tool argument is interpolated
        into a URL *before* it gets there, so `abc/../../health?x=` would build a
        request to somewhere else entirely. Each side of the boundary protects a
        different step, which is why the guard is on both.
        """
        called = {"n": 0}
        monkeypatch.setattr(tools, "_call", lambda *a, **k: called.update(n=called["n"] + 1) or {})
        for handler in (tools.handle_get, tools.handle_pin, tools.handle_delete):
            out = json.loads(handler({"id": bad}))
            assert out["ok"] is False, f"{handler.__name__} accepted {bad!r}"
        assert called["n"] == 0, "no request should have been made"

    def test_a_kind_filter_cannot_change_the_request(self, monkeypatch) -> None:
        seen: dict = {}
        fake(monkeypatch, {"artifacts": []}, seen)
        tools.handle_list({"kind": "image/&pinned=true#"})
        assert "&pinned=true" not in seen["path"]
        assert "%26" in seen["path"] and "%23" in seen["path"]

    def test_a_symlink_is_refused_rather_than_followed(self, tmp_path: Path, monkeypatch) -> None:
        """`toctou-symlink`.

        The agent chooses this path and the agent reads web pages. A link planted
        at a predictable name pointing at a secret would otherwise publish it to
        a URL, which is exactly the store working as designed.
        """
        secret = tmp_path / "secret.txt"
        secret.write_text("shhh")
        link = tmp_path / "innocent.png"
        link.symlink_to(secret)
        called = {"n": 0}
        monkeypatch.setattr(tools, "_call", lambda *a, **k: called.update(n=called["n"] + 1) or {})
        out = json.loads(tools.handle_publish({"path": str(link)}))
        assert out["ok"] is False and "symlink" in out["error"]
        assert called["n"] == 0

    def test_a_directory_is_not_a_publishable_file(self, tmp_path: Path, monkeypatch) -> None:
        called = {"n": 0}
        monkeypatch.setattr(tools, "_call", lambda *a, **k: called.update(n=called["n"] + 1) or {})
        out = json.loads(tools.handle_publish({"path": str(tmp_path)}))
        assert out["ok"] is False
        assert called["n"] == 0

    def test_the_size_is_measured_through_the_open_handle(self, tmp_path: Path, monkeypatch) -> None:
        """The file that is sized must be the file that is read.

        `is_file()` then `stat()` then `read_bytes()` is three chances for the
        path to become something else in between, and the third one is the read
        whose bytes leave the container.
        """
        f = tmp_path / "ok.bin"
        f.write_bytes(b"z" * 32)
        seen: dict = {}
        fake(monkeypatch, {"id": ID1}, seen)
        tools.handle_publish({"path": str(f)})
        assert seen["body"] == b"z" * 32


class TestDelivery:
    """M2b: a publish shows the artifact where the person is.

    The real jarvis-hud store with a recording emitter, so the panel goes through
    ``validate`` and the frame shape is the one a client would get. Not covered:
    the gateway routing from the durable session key to a live transport, which
    is jarvis-hud's own test.
    """

    #: What the adapter answers a publish with (``_row`` in app.py): the row plus
    #: the signed URL a media element can open without the bearer.
    REPLY = {
        "id": ID1, "mime": "image/png", "title": "A red fox", "size": 5,
        "signed_url": f"http://jarvis.tail:8643/api/artifacts/{ID1}?exp=1&sig=abc",
    }

    @pytest.fixture
    def hud(self, monkeypatch):
        hud = load_plugin("jarvis-hud").hud
        frames: list[tuple[str, str, dict]] = []
        monkeypatch.setattr(
            hud, "STORE",
            hud.HudStore(emitter=lambda key, event, payload: frames.append((key, event, payload)) or True),
        )
        hud.frames = frames
        return hud

    def _publish(self, tmp_path: Path, monkeypatch, reply: dict, **kwargs) -> dict:
        f = tmp_path / "fox.png"
        f.write_bytes(b"png")
        fake(monkeypatch, dict(reply))
        return json.loads(tools.handle_publish({"path": str(f), "mime": "image/png"}, **kwargs))

    def test_publishing_an_image_shows_a_panel_on_the_session(self, tmp_path, monkeypatch, hud) -> None:
        out = self._publish(tmp_path, monkeypatch, self.REPLY, session_id="sess-1")
        assert out.get("ok") is not False and out["delivered"] is True
        shows = [(k, p) for k, e, p in hud.frames if e == "hud.panel.show"]
        assert len(shows) == 1
        key, panel = shows[0]
        assert key == "sess-1", "filed under the session the tool was called for"
        assert panel["kind"] == "image" and panel["origin"] == "system"
        assert panel["title"] == "A red fox"
        assert panel["id"] == out["panel_id"]
        assert hud.STORE.list("sess-1")[0]["id"] == panel["id"], "hud_list sees it too"

    def test_publishing_with_no_hud_plugin_reports_undelivered(self, tmp_path, monkeypatch) -> None:
        """Absent jarvis-hud is a fact to report, not a reason the publish failed."""
        # `tools.deliver` is the module the handler calls; a copy reached through
        # sys.modules is whichever test loaded the plugin last.
        monkeypatch.setattr(tools.deliver, "_hud_module", lambda: None)
        out = self._publish(tmp_path, monkeypatch, self.REPLY, session_id="sess-1")
        assert out.get("ok") is not False and out["id"] == ID1, "the publish itself succeeded"
        assert out["delivered"] is False and "jarvis-hud" in out["reason"]

    def test_the_panel_carries_the_signed_url_not_the_bare_one(self, tmp_path, monkeypatch, hud) -> None:
        """M-D2: a media element cannot send the bearer, so the bare URL is a 401 on every surface."""
        out = self._publish(tmp_path, monkeypatch, self.REPLY, session_id="sess-1")
        panel = next(p for _, e, p in hud.frames if e == "hud.panel.show")
        assert panel["payload"]["url"] == self.REPLY["signed_url"]
        assert panel["payload"]["url"] != out["url"], "url_for's bare path never reaches a panel"
        # And with no signature to hand, nothing is shown rather than a link that 401s.
        hud.frames.clear()
        out = self._publish(tmp_path, monkeypatch, {"id": ID1, "mime": "image/png"}, session_id="s")
        assert out["delivered"] is False and "signed_url" in out["reason"]
        assert not hud.frames

    def test_a_video_is_a_video_panel_with_its_mime_and_provenance(self, tmp_path, monkeypatch, hud) -> None:
        """The M3 media kinds: url + mime is what the app plays; provenance is what it credits."""
        reply = {**self.REPLY, "mime": "video/mp4", "title": "Clip",
                 "provenance": {"tool": "fetch-video.sh", "source_url": "https://y/t"}}
        out = self._publish(tmp_path, monkeypatch, reply, session_id="sess-1")
        assert out["delivered"] is True
        panel = next(p for _, e, p in hud.frames if e == "hud.panel.show")
        assert panel["kind"] == "video"
        assert panel["payload"]["url"] == reply["signed_url"]
        assert panel["payload"]["mime"] == "video/mp4"
        assert panel["payload"]["provenance"]["source_url"] == "https://y/t"

    def test_a_kind_the_contract_lacks_is_a_notice_with_the_link(self, tmp_path, monkeypatch, hud) -> None:
        """A jarvis-hud deployed without the media kinds: the artifact still has to appear somewhere."""
        monkeypatch.setattr(hud, "KINDS", [k for k in hud.KINDS if k != "video"])
        reply = {**self.REPLY, "mime": "video/mp4", "title": "Clip"}
        out = self._publish(tmp_path, monkeypatch, reply, session_id="sess-1")
        assert out["delivered"] is True
        panel = next(p for _, e, p in hud.frames if e == "hud.panel.show")
        assert panel["kind"] == "notice"
        assert reply["signed_url"] in panel["payload"]["text"]

    def test_no_client_on_the_session_is_reported_not_raised(self, tmp_path, monkeypatch, hud) -> None:
        monkeypatch.setattr(hud, "STORE", hud.HudStore(emitter=lambda *a: False))
        out = self._publish(tmp_path, monkeypatch, self.REPLY, session_id="cron")
        assert out.get("ok") is not False and out["delivered"] is False
        assert "no HUD client" in out["reason"]


class TestBrowserReceipt:
    """T-0428: the last browser_vision screenshot stays on the HUD as the task's receipt.

    The live activity tile goes five seconds after a run ends, so without this
    "done" was all the owner had. Not covered: whether the model calls
    browser_vision at the end (the skill asks it to; nothing enforces it).
    """

    REPLY = TestDelivery.REPLY
    hud = TestDelivery.hud

    def _shot(self, tmp_path: Path, monkeypatch, name: str = "browser_screenshot_ab12.png") -> Path:
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        shots = tmp_path / "cache" / "screenshots"
        shots.mkdir(parents=True, exist_ok=True)
        f = shots / name
        f.write_bytes(b"png")
        return f

    def _receipt(self, monkeypatch, tool_name: str, result, seen: dict | None = None) -> None:
        fake(monkeypatch, dict(self.REPLY), seen)
        tools.browser_receipt(tool_name=tool_name, args={}, result=result, session_id="sess-1",
                              task_id="t", tool_call_id="c", status="ok")

    def test_a_vision_screenshot_is_shown_as_the_receipt(self, tmp_path, monkeypatch, hud) -> None:
        shot = self._shot(tmp_path, monkeypatch)
        seen: dict = {}
        self._receipt(monkeypatch, "browser_vision",
                      json.dumps({"success": True, "analysis": "ok", "screenshot_path": str(shot)}), seen)
        assert seen["body"] == b"png" and seen["headers"]["x-artifact-mime"] == "image/png"
        assert seen["headers"]["x-artifact-tool"] == "browser_vision"
        shows = [(k, p) for k, e, p in hud.frames if e == "hud.panel.show"]
        assert len(shows) == 1
        key, panel = shows[0]
        assert key == "sess-1" and panel["kind"] == "image"
        assert panel["id"] == tools.RECEIPT_PANEL_ID
        assert panel["payload"]["url"] == self.REPLY["signed_url"]

    def test_the_next_screenshot_replaces_the_receipt(self, tmp_path, monkeypatch, hud) -> None:
        """One receipt per session: a long task's ten looks are not ten panels."""
        shot = self._shot(tmp_path, monkeypatch)
        # Hermes' multimodal envelope carries the path in its meta.
        envelope = {"_multimodal": True, "content": [], "meta": {"screenshot_path": str(shot)}}
        self._receipt(monkeypatch, "browser_vision", envelope)
        self._receipt(monkeypatch, "browser_vision", envelope)
        assert len(hud.STORE.list("sess-1")) == 1
        assert hud.STORE.list("sess-1")[0]["rev"] == 2

    @pytest.mark.parametrize("tool_name, name, result_key", [
        ("browser_navigate", "browser_screenshot_ab12.png", "screenshot_path"),  # another tool
        ("browser_vision", "notes.png", "screenshot_path"),                      # not Hermes' shot
        ("browser_vision", "browser_screenshot_ab12.png", "path"),               # no screenshot_path
    ])
    def test_anything_else_publishes_nothing(self, tmp_path, monkeypatch, hud,
                                             tool_name, name, result_key) -> None:
        shot = self._shot(tmp_path, monkeypatch, name)
        seen: dict = {}
        self._receipt(monkeypatch, tool_name, json.dumps({result_key: str(shot)}), seen)
        assert not seen and not hud.frames

    def test_a_shot_outside_hermes_home_or_missing_publishes_nothing(self, tmp_path, monkeypatch, hud) -> None:
        self._shot(tmp_path, monkeypatch)
        outside = tmp_path.parent / "browser_screenshot_ff.png"
        outside.write_bytes(b"png")
        seen: dict = {}
        for path in (outside, tmp_path / "cache" / "screenshots" / "browser_screenshot_gone.png"):
            self._receipt(monkeypatch, "browser_vision", json.dumps({"screenshot_path": str(path)}), seen)
        assert not seen and not hud.frames

    def test_a_failure_never_reaches_the_turn(self, tmp_path, monkeypatch, hud) -> None:
        shot = self._shot(tmp_path, monkeypatch)

        def boom(*a, **k):
            raise RuntimeError("store down")

        monkeypatch.setattr(tools, "_call", boom)
        tools.browser_receipt(tool_name="browser_vision", result=json.dumps({"screenshot_path": str(shot)}),
                              session_id="sess-1")
        tools.browser_receipt(tool_name="browser_vision", result="not json", session_id="sess-1")
        tools.browser_receipt(tool_name="browser_vision", result=None, session_id="sess-1")
        assert not hud.frames


def test_health_answers_with_the_status_and_the_newest_car_log(monkeypatch) -> None:
    # "Is anything broken?" and "why was the car blank yesterday?" in one call: the
    # HUD's own status, and the tail of the last drive's log the phone uploaded.
    old = {"id": ID1, "title": "car log a", "created": 1.0, "provenance": {"tool": "car-log"}}
    new = {"id": ID2, "title": "car log b", "created": 2.0, "provenance": {"tool": "car-log"}}
    other = {"id": "c" * 64, "title": "clip", "created": 3.0, "provenance": {"tool": "media_fetch"}}
    paths: list[str] = []

    def _call(method, path, *, body=None, headers=None, raw=False):
        paths.append(path)
        if path == "/api/status":
            return {"ok": False, "model": {"state": "unloaded"}}
        if path.startswith("/api/artifacts?"):
            return {"artifacts": [old, new, other]}
        assert raw
        return "x" * 10_000 + "console page failed"

    monkeypatch.setattr(tools, "_call", _call)
    out = json.loads(tools.handle_health({}))
    assert out["status"]["model"]["state"] == "unloaded"
    assert out["car_log"]["title"] == "car log b"
    assert out["car_log"]["tail"].endswith("console page failed")
    assert len(out["car_log"]["tail"]) <= tools.CAR_LOG_TAIL
    assert f"/api/artifacts/{ID2}" in paths


def test_health_without_a_car_log_still_answers(monkeypatch) -> None:
    fake(monkeypatch, {"artifacts": []})
    out = json.loads(tools.handle_health({}))
    assert out["ok"] is True
    assert out["car_log"] is None


# --- T-1165: the key stays on the adapter, and publish reads only the output folders ---------

import re  # noqa: E402
import secrets  # noqa: E402
import tempfile  # noqa: E402
import threading  # noqa: E402
from http.server import BaseHTTPRequestHandler, HTTPServer  # noqa: E402
from urllib import request  # noqa: E402


class _Stub:
    """A loopback HTTP server that keeps every request's headers and answers with `reply`."""

    def __init__(self, reply) -> None:
        self.seen: list[dict] = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def _answer(self) -> None:
                outer.seen.append({"path": self.path, "headers": dict(self.headers)})
                status, headers, body = reply(outer)
                self.send_response(status)
                for k, v in headers.items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            do_GET = do_POST = _answer

            def log_message(self, *a) -> None:
                pass

        self.server = HTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def stubs():
    made: list[_Stub] = []

    def make(reply) -> _Stub:
        made.append(_Stub(reply))
        return made[-1]

    yield make
    for s in made:
        s.close()


def test_a_redirect_from_the_store_is_not_followed_and_the_key_never_reaches_the_second_host(
    monkeypatch, stubs
) -> None:
    key = secrets.token_hex(8)
    monkeypatch.setattr(tools, "_key", lambda: key)
    elsewhere = stubs(lambda s: (200, {}, b'{"ok": true}'))
    store = stubs(lambda s: (302, {"Location": elsewhere.url + "/steal"}, b""))
    monkeypatch.setattr(tools, "_base", lambda: store.url)
    out = tools._call("GET", "/api/artifacts?kind=")
    assert out["ok"] is False
    assert len(store.seen) == 1 and store.seen[0]["headers"]["Authorization"] == f"Bearer {key}"
    # The decision: the second host never saw the request at all, so it never saw the header.
    assert elsewhere.seen == []


def test_the_proxy_in_the_environment_is_ignored(monkeypatch, stubs) -> None:
    key = secrets.token_hex(8)
    monkeypatch.setattr(tools, "_key", lambda: key)
    proxy = stubs(lambda s: (200, {}, b'{"via": "proxy"}'))
    store = stubs(lambda s: (200, {}, b'{"via": "store"}'))
    for var in ("http_proxy", "HTTP_PROXY"):
        monkeypatch.setenv(var, proxy.url)
    monkeypatch.delenv("no_proxy", raising=False)
    monkeypatch.delenv("NO_PROXY", raising=False)
    # urllib builds its default opener once, reading the proxy variables then: reset it so a
    # plain urlopen would pick them up and this test fails if _call ever uses it.
    monkeypatch.setattr(request, "_opener", None)
    monkeypatch.setattr(tools, "_base", lambda: store.url)
    assert tools._call("GET", "/api/status") == {"via": "store"}
    assert proxy.seen == []


@pytest.fixture
def folders(tmp_path: Path, monkeypatch):
    """A Hermes home with its output folders, and a system temp dir that is *not* under tmp_path's other folders."""
    home = tmp_path / "home"
    for sub in ("work", "files", "cache/screenshots"):
        (home / sub).mkdir(parents=True)
    tmp = tmp_path / "systmp"
    tmp.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(tools, "_HOME_OUTPUT_DIRS", _REAL_DIRS)
    monkeypatch.setattr(tempfile, "tempdir", str(tmp))
    return home, tmp


def _publish(monkeypatch, path: Path) -> dict:
    called: list = []
    monkeypatch.setattr(tools, "_call", lambda *a, **k: called.append(1) or {"id": ID1, "mime": "x"})
    out = json.loads(tools.handle_publish({"path": str(path)}))
    out["_called"] = len(called)
    return out


def test_publish_reads_from_each_output_folder(folders, monkeypatch) -> None:
    home, tmp = folders
    for d in (home / "work", home / "files", home / "cache" / "screenshots"):
        f = d / "out.png"
        f.write_bytes(b"x")
        out = _publish(monkeypatch, f)
        assert "error" not in out and out["_called"] == 1, d


def test_publish_refuses_the_system_temp_dir_and_the_rest_of_the_cache(folders, monkeypatch) -> None:
    home, tmp = folders
    (home / "cache" / "delegation").mkdir()
    for d in (tmp, home / "cache", home / "cache" / "delegation"):
        f = d / "out.png"
        f.write_bytes(b"x")
        out = _publish(monkeypatch, f)
        assert out["ok"] is False and out["_called"] == 0, d


@pytest.mark.parametrize("where", ["elsewhere", "home", "home/state", "home/work/../sessions"])
def test_publish_refuses_a_folder_outside_the_output_folders(folders, monkeypatch, tmp_path, where) -> None:
    f = (tmp_path / where / "x.txt").resolve()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("data")
    out = _publish(monkeypatch, tmp_path / where / "x.txt")
    assert out["ok"] is False and "output folders" in out["error"] and out["_called"] == 0


@pytest.mark.parametrize("path", ["/etc/hostname", "/proc/self/environ", "/sys/kernel/notes", "/root/x"])
def test_publish_refuses_system_paths(folders, monkeypatch, path) -> None:
    out = _publish(monkeypatch, Path(path))
    assert out["ok"] is False and out["_called"] == 0


@pytest.mark.parametrize("name", [".env", ".env.production", "host.pem", "tls.key", "id_rsa", "id_ed25519.pub",
                                  "auth.json", "api_token.json", "config.yaml"])
def test_publish_refuses_a_credential_looking_name_inside_an_output_folder(folders, monkeypatch, name) -> None:
    home, _ = folders
    f = home / "work" / name
    f.write_text("data")
    out = _publish(monkeypatch, f)
    assert out["ok"] is False and "credential" in out["error"] and out["_called"] == 0


def test_publish_refuses_the_hermes_homes_own_config_and_a_link_out_of_the_folder(folders, monkeypatch) -> None:
    home, _ = folders
    (home / "config.yaml").write_text("data")
    assert _publish(monkeypatch, home / "config.yaml")["_called"] == 0
    # A directory link inside work/ pointing out of it: the path is not a symlink itself, its parent is.
    (home / "work" / "out").symlink_to("/etc")
    out = _publish(monkeypatch, home / "work" / "out" / "hostname")
    assert out["ok"] is False and out["_called"] == 0


def test_the_folder_rule_allows_every_folder_the_skills_publish_from(monkeypatch) -> None:
    """The skills name /opt/data/work and /opt/data/files; the rule is read against them so
    a skill moving its output folder cannot leave publish refusing it."""
    monkeypatch.setenv("HERMES_HOME", "/opt/data")
    monkeypatch.setattr(tools, "_HOME_OUTPUT_DIRS", _REAL_DIRS)
    skills = Path(__file__).resolve().parents[3] / "skills"
    found: set[str] = set()
    for md in skills.glob("*/SKILL.md"):
        text = md.read_text()
        if "artifact_publish" in text:
            found |= {m.group(1) for m in re.finditer(r"/opt/data/([A-Za-z0-9_-]+)", text)}
    assert {"work", "files"} <= found, found
    for top in sorted(found):
        assert tools._refusal(Path(f"/opt/data/{top}/probe.bin")) is None, top
