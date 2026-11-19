"""Every plugin that asks the adapter where a session's prompt came from asks through one reader.

aura-hud (is this the phone's turn), aura-artifacts (what can this screen show) and smart-home
(which device rings the timer) each read the adapter's surface record with the apps' bearer
key. Two of them followed a redirect, or the gateway's HTTP_PROXY, with that key still on the
request; the artifacts store had refused both for its own calls. These pin the decision for
every entry point, against real loopback listeners: an answer that says "go elsewhere" and a
proxy in the environment never see the key, and the read is None, as for an adapter that is
down.

Not covered: the adapter URL itself is the owner's setting; a wrong one is trusted.
"""
from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import import_module
from pathlib import Path
from typing import Any, Callable, Iterator

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _plugin_loader import load_plugin  # noqa: E402

KEY = "k-test"
RECORD = {"session_id": "s1", "surface": {"name": "android"}, "at": 1.0}


class _Listener:
    """A loopback HTTP server that records each request's path and Authorization header."""

    def __init__(self, answer: Callable[[BaseHTTPRequestHandler], None]) -> None:
        self.seen: list[tuple[str, str]] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 - the stdlib's name
                outer.seen.append((self.path, self.headers.get("Authorization", "")))
                answer(self)

            def log_message(self, *args: Any) -> None:
                return None

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def _json(body: Any) -> Callable[[BaseHTTPRequestHandler], None]:
    def answer(h: BaseHTTPRequestHandler) -> None:
        data = json.dumps(body).encode()
        h.send_response(200)
        h.send_header("Content-Type", "application/json")
        h.send_header("Content-Length", str(len(data)))
        h.end_headers()
        h.wfile.write(data)
    return answer


def _redirect(to: str) -> Callable[[BaseHTTPRequestHandler], None]:
    def answer(h: BaseHTTPRequestHandler) -> None:
        h.send_response(302)
        h.send_header("Location", to + h.path)
        h.send_header("Content-Length", "0")
        h.end_headers()
    return answer


@pytest.fixture
def listeners() -> Iterator[list[_Listener]]:
    made: list[_Listener] = []
    yield made
    for one in made:
        one.close()


def _phone(sid: str) -> Any:
    return import_module(load_plugin("aura-hud").__name__ + ".phone").surface_record(sid)


def _artifacts(sid: str) -> Any:
    return load_plugin("aura-artifacts").tools.reported_surface(sid)


def _smart_home(sid: str) -> Any:
    return load_plugin("smart-home").tools._surface_current(sid)


ENTRY_POINTS = [_phone, _artifacts, _smart_home]


@pytest.mark.parametrize("read", ENTRY_POINTS, ids=lambda f: f.__name__.strip("_"))
def test_a_redirect_never_carries_the_key_to_a_second_host(read, listeners, monkeypatch) -> None:
    elsewhere = _Listener(_json(RECORD))
    adapter = _Listener(_redirect(elsewhere.url))
    listeners += [elsewhere, adapter]
    monkeypatch.setenv("AURA_ADAPTER_URL", adapter.url)
    monkeypatch.setenv("AURA_ADAPTER_KEY", KEY)
    for name in ("HTTP_PROXY", "http_proxy", "NO_PROXY", "no_proxy"):
        monkeypatch.delenv(name, raising=False)
    assert read("s1") is None, "a redirected read is no answer"
    assert adapter.seen and adapter.seen[0][1] == f"Bearer {KEY}"
    assert elsewhere.seen == [], f"the redirect target received {elsewhere.seen}"


@pytest.mark.parametrize("read", ENTRY_POINTS, ids=lambda f: f.__name__.strip("_"))
def test_a_proxy_in_the_environment_never_sees_the_key(read, listeners, monkeypatch) -> None:
    proxy = _Listener(_json(RECORD))
    adapter = _Listener(_json(RECORD))
    listeners += [proxy, adapter]
    monkeypatch.setenv("AURA_ADAPTER_URL", adapter.url)
    monkeypatch.setenv("AURA_ADAPTER_KEY", KEY)
    for name in ("HTTP_PROXY", "http_proxy"):
        monkeypatch.setenv(name, proxy.url)
    for name in ("NO_PROXY", "no_proxy"):
        monkeypatch.delenv(name, raising=False)
    read("s1")
    assert proxy.seen == [], f"the proxy received {proxy.seen}"
    assert adapter.seen, "the adapter is reached directly"


def test_the_reader_answers_the_record_or_none(listeners, monkeypatch) -> None:
    from _shared import _surface

    adapter = _Listener(_json(RECORD))
    listeners.append(adapter)
    monkeypatch.setenv("AURA_ADAPTER_URL", adapter.url)
    monkeypatch.setenv("AURA_ADAPTER_KEY", KEY)
    for name in ("HTTP_PROXY", "http_proxy"):
        monkeypatch.delenv(name, raising=False)
    assert _surface.current("s 1") == RECORD
    assert adapter.seen[-1] == ("/api/surface/current?session=s+1", f"Bearer {KEY}")
    assert _surface.current("") is None and _surface.current(None) is None, "no session, no read"
    monkeypatch.setenv("AURA_ADAPTER_KEY", "")
    before = len(adapter.seen)
    assert _surface.current("s1") is None and len(adapter.seen) == before, "no key, no request"

    listeners.append(not_a_record := _Listener(_json(["not", "a", "record"])))
    monkeypatch.setenv("AURA_ADAPTER_URL", not_a_record.url)
    monkeypatch.setenv("AURA_ADAPTER_KEY", KEY)
    assert _surface.current("s1") is None, "an answer that is no record cannot say"

    adapter.close()
    listeners.remove(adapter)
    monkeypatch.setenv("AURA_ADAPTER_URL", adapter.url)
    assert _surface.current("s1", timeout=1) is None, "an adapter that is down cannot say"


def _by_session(records: dict[str, dict]) -> Callable[[BaseHTTPRequestHandler], None]:
    """The adapter as it answers: a session's own record, or an empty one for a session it never saw."""
    def answer(h: BaseHTTPRequestHandler) -> None:
        sid = h.path.split("session=", 1)[-1]
        _json(records.get(sid, {"session_id": sid, "surface": None, "at": None, "device": None, "device_at": None}))(h)
    return answer


@pytest.fixture
def durable(listeners, monkeypatch) -> _Listener:
    """The adapter holds the phone's record under the live sid the app sent; the gateway's table
    maps that sid to the durable key a tool call carries (T-1579)."""
    from _shared import _speaker

    adapter = _Listener(_by_session({
        "live-1": {"session_id": "live-1", "surface": {"name": "android"}, "at": 5.0, "device": None, "device_at": None},
        "live-0": {"session_id": "live-0", "surface": {"name": "web"}, "at": 2.0, "device": None, "device_at": None},
    }))
    listeners.append(adapter)
    monkeypatch.setenv("AURA_ADAPTER_URL", adapter.url)
    monkeypatch.setenv("AURA_ADAPTER_KEY", KEY)
    for name in ("HTTP_PROXY", "http_proxy"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(_speaker, "sessions", lambda: {
        "live-0": {"session_key": "durable-1"}, "live-1": {"session_key": "durable-1"}, "other": {"session_key": "x"},
    })
    return adapter


@pytest.mark.usefixtures("durable")
def test_the_shared_lookup_answers_the_newest_record_under_any_id_the_conversation_goes_by() -> None:
    from _shared import _surface

    got = _surface.latest("durable-1")
    assert got is not None and got["session_id"] == "live-1", "the newest prompt's record, on the second screen"
    assert _surface.latest("never-seen") == {"session_id": "never-seen", "surface": None, "at": None,
                                            "device": None, "device_at": None}, "a session nobody reported is the adapter's empty record"
    assert _surface.latest("") is None


@pytest.mark.usefixtures("durable")
def test_every_plugin_given_the_durable_key_finds_the_record_under_the_live_id() -> None:
    hud = load_plugin("aura-hud").__name__
    phone = import_module(hud + ".phone")
    habits = import_module(hud + ".habits")
    assert phone.phone_in_session("durable-1") is True, "the phone's turn, asked by the key a tool call carries"
    assert (phone.surface_record("durable-1") or {}).get("session_id") == "live-1"
    assert habits._probe("durable-1") is False
    assert _artifacts("durable-1") == {"name": "android"}
