"""The probes' shared JSON-RPC loop, against a fake socket.

``scripts/_probe.py`` is what every live probe under ``scripts/hermes-probes/``
talks to a gateway through. The probes themselves need a running ``hermes
serve``; the loop does not, and it is the part that used to exist in six copies
with two of them silently dropping events that arrived mid-call. These pin the
contract: replies are matched by id, not order; events that land while a call
waits reach the handler; a plugin command gets the ``@<sid>`` prefix on request;
the event iterator stops at its deadline instead of raising.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import _probe  # noqa: E402


class FakeSocket:
    """Scripted frames in, sent frames recorded; ``recv`` blocks forever when the script runs dry."""

    def __init__(self, frames: list[dict]):
        self.sent: list[dict] = []
        self._frames = [json.dumps(f) for f in frames]

    async def send(self, raw: str) -> None:
        self.sent.append(json.loads(raw))

    async def recv(self) -> str:
        if self._frames:
            return self._frames.pop(0)
        await asyncio.sleep(3600)
        raise AssertionError("unreachable")


def test_a_reply_is_matched_by_id_and_events_in_between_reach_the_handler() -> None:
    seen: list[dict] = []
    ws = FakeSocket([
        {"jsonrpc": "2.0", "method": "event", "params": {"type": "tool.start"}},
        {"jsonrpc": "2.0", "id": 99, "result": "someone else's"},
        {"jsonrpc": "2.0", "id": 1, "result": {"session_id": "s1"}},
    ])
    rpc = _probe.Rpc(ws, on_event=seen.append)
    sid = asyncio.run(rpc.create_session())
    assert sid == "s1" and rpc.sid == "s1"
    assert seen == [{"type": "tool.start"}]
    assert ws.sent == [{"jsonrpc": "2.0", "id": 1, "method": "session.create", "params": {}}]


def test_dispatch_prefixes_the_session_only_when_asked() -> None:
    ws = FakeSocket([
        {"id": 1, "result": {"session_id": "abc"}},
        {"id": 2, "result": {"output": "ok"}},
        {"id": 3, "result": {"output": "ok"}},
    ])
    rpc = _probe.Rpc(ws)

    async def run():
        await rpc.create_session()
        await rpc.dispatch("goal", "status")
        await rpc.dispatch("jsettings", "list", prefix_session=True)

    asyncio.run(run())
    assert ws.sent[1]["params"] == {"session_id": "abc", "name": "goal", "arg": "status"}
    assert ws.sent[2]["params"] == {"session_id": "abc", "name": "jsettings", "arg": "@abc list"}


def test_dispatch_before_a_session_is_a_programming_error() -> None:
    with pytest.raises(AssertionError):
        asyncio.run(_probe.Rpc(FakeSocket([])).dispatch("goal", "status"))


def test_events_stop_at_the_deadline_without_raising() -> None:
    ws = FakeSocket([
        {"method": "event", "params": {"type": "message.delta", "payload": {"text": "hi"}}},
        {"id": 7, "result": "a reply nobody is waiting for"},
        {"method": "event", "params": {"type": "message.complete"}},
    ])
    rpc = _probe.Rpc(ws)

    async def collect():
        return [p async for p in rpc.events(0.2)]

    got = asyncio.run(collect())
    assert [p["type"] for p in got] == ["message.delta", "message.complete"]


def test_outcome_prefers_the_result_and_brief_cuts_it() -> None:
    assert _probe.outcome({"result": {"a": 1}, "error": "no"}) == {"a": 1}
    assert _probe.outcome({"error": {"code": 4001}}) == {"code": 4001}
    assert _probe.brief({"result": "x" * 500}, 10) == '"xxxxxxxxx'


def test_the_adapter_socket_url_follows_the_http_base() -> None:
    assert _probe.adapter_ws_url("http://h:8643", "k") == "ws://h:8643/ws?key=k"
    assert _probe.adapter_ws_url("https://h", "k") == "wss://h/ws?key=k"


def test_every_probe_imports_the_shared_client_and_none_carries_its_own_loop() -> None:
    """The point of _probe.py: a probe that re-grows `async def call(` is the clone coming back."""
    probes = sorted((Path(_probe.__file__).parent / "hermes-probes").glob("*.py"))
    assert probes, "no probes found"
    for path in probes:
        source = path.read_text(encoding="utf-8")
        assert "from _probe import" in source, f"{path.name} does not use scripts/_probe.py"
        assert "def call(" not in source and "websockets.connect(" not in source, path.name


def test_the_house_socket_authenticates_and_refuses_a_bad_token(monkeypatch) -> None:
    """The house's HA-shaped socket (jarvis-core /api/websocket): auth first, or say why not.

    car_turn_probe's phone mode carried its own copy of this handshake, which the rule above
    exists to stop; the pipeline's test:config-render failed on it from 2026-09-24 11:02.
    """
    import contextlib
    import sys
    import types

    def fake(frames):
        sock = FakeSocket(frames)

        @contextlib.asynccontextmanager
        async def connect(url, **_):
            sock.url = url
            yield sock

        monkeypatch.setitem(sys.modules, "websockets", types.SimpleNamespace(connect=connect))
        return sock

    ok = fake([{"type": "auth_required"}, {"type": "auth_ok"}])

    async def use(token):
        async with _probe.house("http://core:8080", token) as ws:
            return ws

    assert asyncio.run(use("t")) is ok
    assert ok.url == "ws://core:8080/api/websocket"
    assert ok.sent[0] == {"type": "auth", "access_token": "t"}

    fake([{"type": "auth_required"}, {"type": "auth_invalid", "message": "bad"}])
    with pytest.raises(RuntimeError, match="refused"):
        asyncio.run(use("wrong"))
