"""Keep the unit suite off the house that shares this host.

This checkout is also the production host (CLAUDE.md), and the house stack
listens on 127.0.0.1: openWakeWord on 10400, whisper on 10300, piper on 10200.
A house built from an empty config takes exactly those defaults, so every test
that set one up was describing the live wake-word service — a second to answer,
eight to hang up on a cancel — and opening a detection stream to the production
microphone; 3,600 tests at nine seconds each (2026-09-18). CI has nothing on
those ports and is refused at once; this refuses the same way, before a socket
opens. A test that wants a Wyoming server starts its own on an ephemeral port
(`FakeWyomingServer` in test_voice.py), which this never touches.

What it does not cover: a client that opens its own socket rather than going
through `asyncio.open_connection` (httpx to the model server on 11434, MQTT on
1883) — nothing listens on 11434 here, and no fixture configures MQTT.
"""

from __future__ import annotations

import asyncio
import errno
import socket
import types

import pytest

#: The house's Wyoming ports on this host, in the order the voice integration
#: names them (tts, stt, wake).
HOUSE_PORTS = frozenset({10200, 10300, 10400})

_open_connection = asyncio.open_connection


async def _refuse_the_house(host=None, port=None, *args, **kwargs):
    if port in HOUSE_PORTS and host in (None, "127.0.0.1", "localhost", "::1"):
        raise ConnectionRefusedError(
            errno.ECONNREFUSED,
            f"tests/conftest.py refuses 127.0.0.1:{port}: the house's own service on this host",
        )
    return await _open_connection(host, port, *args, **kwargs)


@pytest.fixture(autouse=True, scope="session")
def _the_suite_never_reaches_the_house():
    mp = pytest.MonkeyPatch()
    mp.setattr(asyncio, "open_connection", _refuse_the_house)
    try:
        yield
    finally:
        mp.undo()


#: Where the made-up resolver says an internet name lives: a documentation range, which the
#: local-only guard counts as public because it is on none of its private networks.
PUBLIC_ADDRESS = "203.0.113.7"
#: Names that never resolve anywhere (RFC 6761), as the suite's "not started yet" cases use.
_NEVER_RESOLVES = (".invalid", ".test", ".example", ".localhost")


def _made_up_getaddrinfo(host, port, *args, **kwargs):
    name = host.decode() if isinstance(host, bytes) else str(host)
    try:
        return socket.getaddrinfo(name, port, flags=socket.AI_NUMERICHOST)
    except socket.gaierror:
        pass
    if "." not in name or name.endswith(_NEVER_RESOLVES):
        raise socket.gaierror(socket.EAI_NONAME, f"tests/conftest.py: {name} does not resolve")
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (PUBLIC_ADDRESS, 0))]


@pytest.fixture(autouse=True, scope="session")
def _the_local_only_guard_asks_no_real_resolver():
    """The model server's local-only guard gets its answers from here, not from DNS.

    The guard lets a name that does not resolve through, on purpose. Three tests named a real
    public host and relied on the network to resolve it, so a moment without DNS made them
    report that a public model server was accepted and that a camera frame left the LAN
    (T-1459, the pipeline's image). Here an address literal is parsed, a single label or a
    reserved name does not resolve, and any other name is a made-up public address. This does
    not cover a lookup made outside `aura.integrations.llm`, nor what a real resolver answers.
    """
    from aura.integrations import llm

    mp = pytest.MonkeyPatch()
    mp.setattr(
        llm,
        "socket",
        types.SimpleNamespace(getaddrinfo=_made_up_getaddrinfo, gaierror=socket.gaierror),
    )
    try:
        yield
    finally:
        mp.undo()


class MadeUpNames:
    """A resolver one test controls: `table[name] = address`; any other name does not resolve.

    `asked` lists every lookup, so a test can show that a request did or did not cause one.
    """

    def __init__(self) -> None:
        self.table: dict[str, str] = {}
        self.asked: list[str] = []

    def getaddrinfo(self, host, port, *args, **kwargs):
        name = host.decode() if isinstance(host, bytes) else str(host)
        self.asked.append(name)
        try:
            return socket.getaddrinfo(name, port, flags=socket.AI_NUMERICHOST)
        except socket.gaierror:
            pass
        address = self.table.get(name)
        if address is None:
            raise socket.gaierror(socket.EAI_NONAME, f"{name} does not resolve in this test")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 0))]


@pytest.fixture
def names(monkeypatch) -> MadeUpNames:
    """The local-only guard's resolver, in the test's hands for one test."""
    from aura.integrations import llm

    made_up = MadeUpNames()
    monkeypatch.setattr(
        llm,
        "socket",
        types.SimpleNamespace(getaddrinfo=made_up.getaddrinfo, gaierror=socket.gaierror),
    )
    return made_up


@pytest.fixture(autouse=True)
def _no_neural_voice_service(monkeypatch):
    """No test reaches a real voice service for a voiceprint (voice/neural.py): the
    host that runs these may run one, and CI does not. A test that wants prints
    patches `neural.embed` itself."""
    from aura.voice import neural

    async def unavailable(wav):
        return None

    monkeypatch.setattr(neural, "embed", unavailable)
