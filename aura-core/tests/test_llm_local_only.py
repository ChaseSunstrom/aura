"""The promise the whole project rests on, checked rather than trusted.

"100 % local" is the reason this software exists, and nothing in the code
stopped `llm: url:` naming a cloud endpoint. A promise nothing verifies is a
hope, so the model server's address is resolved at startup and refused if it is
somebody else's computer.

What this deliberately does NOT do: refuse a name that does not resolve. On a
first boot a compose service may not have started yet, and failing to start
because DNS was not ready would be a worse bug than the one being prevented.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from conftest import PUBLIC_ADDRESS

from aura.core import Jarvis
from aura.integrations.llm import (
    DATA_CLIENT,
    DATA_TRANSPORT,
    LocalOnlyGate,
    is_local_url,
    local_only_gate,
)
from aura.llm.ollama import OllamaError
from aura.llm.rerank import Reranker

# No test here asks a real resolver: tests/conftest.py answers the guard's lookups
# (`_the_local_only_guard_asks_no_real_resolver`), so a public name is public without DNS.


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:11434",
        "http://localhost:8000/v1",
        "http://192.168.1.20:8080/v1",
        "http://10.4.0.9/v1",
        "http://172.16.5.5:4000/v1",
        # Tailscale and friends: CGNAT space, which is an overlay of machines
        # the operator owns.
        "http://100.64.0.51:8080/v1",
        "http://[::1]:8000/v1",
        "http://nas.local:8086",
        "http://host.docker.internal:11434",
    ],
)
def test_a_machine_the_operator_plausibly_owns_is_allowed(url):
    ok, why = is_local_url(url)
    assert ok, why


@pytest.mark.parametrize(
    "url",
    ["https://api.openai.com/v1", "https://1.1.1.1/v1", "http://8.8.8.8:8000/v1"],
)
def test_somebody_else_s_computer_is_refused_with_the_address_it_resolved_to(url):
    ok, why = is_local_url(url)
    assert not ok
    assert "public address" in why
    # The message has to say what to do, not only that it said no.
    assert "local_only" in why


def test_an_unresolvable_name_is_allowed_because_a_container_may_be_starting():
    ok, why = is_local_url("http://a-service-that-has-not-started-yet.invalid:8000/v1")
    assert ok, why


def test_something_that_is_not_a_url_is_refused_rather_than_assumed_local():
    ok, why = is_local_url("not a url at all")
    assert not ok


async def test_setup_refuses_to_start_against_a_public_model_server(monkeypatch, caplog):
    """The guard is not advisory: the integration does not come up."""
    from aura.integrations import llm as module

    class FakeJarvis:
        config_dir = "/tmp"
        config: dict = {}
        data: dict = {}

        def __getattr__(self, name):  # pragma: no cover - nothing else is reached
            raise AssertionError(f"setup went further than it should have ({name})")

    ok = await module.async_setup(FakeJarvis(), {"url": "https://api.openai.com/v1", "model": "gpt"})
    assert ok is False


async def test_the_guard_can_be_turned_off_deliberately():
    """An operator running a model through a relay they own may say so."""
    from aura.integrations import llm as module

    seen: list[str] = []

    class FakeJarvis:
        config_dir = "/tmp"
        config: dict = {}
        data: dict = {}

        def __getattr__(self, name):
            seen.append(name)
            raise RuntimeError("stop here: the guard let it through")

    with pytest.raises(RuntimeError):
        await module.async_setup(
            FakeJarvis(),
            {"url": "https://api.openai.com/v1", "model": "gpt", "local_only": False},
        )
    assert seen, "setup should have got past the guard"


# --- the second look: a name that did not resolve at startup (T-1461) -------------------------
#
# Setup lets an unresolved name through so a first boot is not held up by a compose service
# that has not started. Without a second look that was a way round the guard: a public host
# name plus a moment without DNS at boot, and every conversation went to it afterwards.

MODEL_HOST = "modelbox"
MODEL_URL = f"http://{MODEL_HOST}:8000/v1"
LAN_ADDRESS = "192.168.1.20"


async def _house(tmp_path, sent: list[httpx.Request], **llm_options) -> Jarvis:
    def answer(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200, json={"data": [{"id": "house-model"}]})

    jarvis = Jarvis(tmp_path)
    jarvis.data[DATA_TRANSPORT] = httpx.MockTransport(answer)
    options = {"url": MODEL_URL, "model": "house-model", "backend": "openai", **llm_options}
    await jarvis.async_setup({"llm": options})
    assert "llm" in jarvis.data, "the chat model did not come up, so nothing below would mean anything"
    return jarvis


async def test_a_name_unresolved_at_startup_that_turns_out_public_is_never_sent_a_request(
    tmp_path, names
):
    sent: list[httpx.Request] = []
    jarvis = await _house(tmp_path, sent)
    names.table[MODEL_HOST] = PUBLIC_ADDRESS
    with pytest.raises(OllamaError) as refused:
        await jarvis.data["llm"].list_models()
    assert sent == [], "a request left for a public model server"
    assert "public address" in str(refused.value) and "local_only" in str(refused.value)
    await jarvis.async_stop()


async def test_a_public_name_written_in_punycode_is_never_sent_a_request_either(tmp_path, names):
    """The hook finds the model server's requests by host, and two parsers spell this one
    differently: urllib keeps the punycode, the HTTP client's host is the decoded name. Taken
    from different parsers the two never matched, and the gate was silently off (T-1463)."""
    sent: list[httpx.Request] = []
    jarvis = await _house(tmp_path, sent, url="http://xn--bcher-kva.example:8000/v1")
    names.table["xn--bcher-kva.example"] = names.table["bücher.example"] = PUBLIC_ADDRESS
    with pytest.raises(OllamaError) as refused:
        await jarvis.data["llm"].list_models()
    assert sent == [], "a request left for a public model server"
    assert "public address" in str(refused.value)
    await jarvis.async_stop()


async def test_a_name_that_still_does_not_resolve_is_sent_nothing_either(tmp_path, names):
    """Otherwise the check could see no answer and the client's own lookup, a moment later, one."""
    sent: list[httpx.Request] = []
    jarvis = await _house(tmp_path, sent)
    with pytest.raises(OllamaError) as refused:
        await jarvis.data["llm"].list_models()
    assert sent == []
    assert "does not resolve" in str(refused.value)
    await jarvis.async_stop()


async def test_a_name_unresolved_at_startup_that_turns_out_local_is_used(tmp_path, names):
    sent: list[httpx.Request] = []
    jarvis = await _house(tmp_path, sent)
    names.table[MODEL_HOST] = LAN_ADDRESS
    assert await jarvis.data["llm"].list_models() == ["house-model"]
    assert sent and {request.url.host for request in sent} == {MODEL_HOST}
    await jarvis.async_stop()


async def test_a_local_name_that_later_resolves_public_is_refused_at_the_next_look(
    tmp_path, names, monkeypatch
):
    monkeypatch.setattr(LocalOnlyGate, "TTL", 0.0)
    names.table[MODEL_HOST] = LAN_ADDRESS
    sent: list[httpx.Request] = []
    jarvis = await _house(tmp_path, sent)
    assert await jarvis.data["llm"].list_models() == ["house-model"]
    before = len(sent)
    names.table[MODEL_HOST] = PUBLIC_ADDRESS
    with pytest.raises(OllamaError):
        await jarvis.data["llm"].list_models()
    assert len(sent) == before
    await jarvis.async_stop()


async def test_a_verified_local_name_survives_a_moment_without_dns(tmp_path, names, monkeypatch):
    """A blip must not cut a working local model: the last verdict stands until a new one."""
    monkeypatch.setattr(LocalOnlyGate, "TTL", 0.0)
    names.table[MODEL_HOST] = LAN_ADDRESS
    sent: list[httpx.Request] = []
    jarvis = await _house(tmp_path, sent)
    await jarvis.data["llm"].list_models()
    del names.table[MODEL_HOST]
    assert await jarvis.data["llm"].list_models() == ["house-model"]
    await jarvis.async_stop()


async def test_the_second_look_is_about_the_model_server_only(tmp_path, names):
    """YAML tools share the client; a tool's own address is not the guard's business."""
    sent: list[httpx.Request] = []
    jarvis = await _house(tmp_path, sent)
    response = await jarvis.data[DATA_CLIENT].get("http://192.168.1.50/status")
    assert response.status_code == 200
    assert [request.url.host for request in sent] == ["192.168.1.50"]
    await jarvis.async_stop()


async def test_with_the_guard_off_an_unresolved_name_is_not_looked_at_again(tmp_path, names):
    sent: list[httpx.Request] = []
    jarvis = await _house(tmp_path, sent, local_only=False)
    names.table[MODEL_HOST] = PUBLIC_ADDRESS
    assert await jarvis.data["llm"].list_models() == ["house-model"]
    await jarvis.async_stop()


async def test_an_address_needs_no_second_look(tmp_path, names):
    sent: list[httpx.Request] = []
    jarvis = await _house(tmp_path, sent, url="http://192.168.1.20:8000/v1")
    asked = len(names.asked)
    assert await jarvis.data["llm"].list_models() == ["house-model"]
    assert len(names.asked) == asked, "a request to an address caused a lookup"
    await jarvis.async_stop()


# ---------------------------------------------------------------------------
# The reranker: memory texts, the query and research snippets (T-1462)
# ---------------------------------------------------------------------------
#
# It has its own url and its own HTTP client, so the model server's gate never
# saw it. These assert the decision: what was sent, and the order that came back.

RERANK_NAME = "rerank.example.com"
QUERY = "where is the spare key"
TEXTS = ["under the mat", "in the shed"]


class _Scores:
    """A rerank service's answer that puts the second text first."""

    status_code = 200

    def json(self):
        return [{"index": 1, "score": 9.0}, {"index": 0, "score": 1.0}]

    def raise_for_status(self) -> None:
        return None


class _RerankHttp:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def post(self, url, json=None, timeout=None):
        self.sent.append(url)
        return _Scores()


def _bare_house(**llm):
    """What the gate reads of a house: its config and its data, nothing running."""
    return SimpleNamespace(config={"llm": llm} if llm else {}, data={})


def _gated(house, url: str, http: _RerankHttp) -> Reranker:
    return Reranker(url=url, client=http, gate=local_only_gate(house, url, what="rerank"))


@pytest.mark.parametrize(
    "url",
    [
        f"http://{RERANK_NAME}:7998",
        f"http://{PUBLIC_ADDRESS}:7998/rerank",
        "http://[2001:db8::7]:7998",
        # Not shown to be local is not local: a name that does not resolve is sent nothing.
        "http://rerankbox:7998",
    ],
)
async def test_a_rerank_url_not_shown_to_be_local_is_sent_nothing(url, names):
    names.table[RERANK_NAME] = PUBLIC_ADDRESS
    http = _RerankHttp()
    assert await _gated(_bare_house(), url, http).order(QUERY, TEXTS) == []
    assert http.sent == []


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:7998/rerank",
        "http://192.168.1.30:7998",
        "http://rerankbox:7998",
        "http://nas.local:7998",
    ],
)
async def test_a_local_rerank_url_is_used(url, names):
    names.table["rerankbox"] = "192.168.1.30"
    http = _RerankHttp()
    assert await _gated(_bare_house(), url, http).order(QUERY, TEXTS) == [1, 0]
    assert len(http.sent) == 1


async def test_a_rerank_address_is_judged_once_and_never_looked_up_again(names):
    http = _RerankHttp()
    reranker = _gated(_bare_house(), "http://192.168.1.30:7998", http)
    assert await reranker.order(QUERY, TEXTS) == [1, 0]
    asked = len(names.asked)
    assert await reranker.order(QUERY, TEXTS) == [1, 0]
    assert len(names.asked) == asked, "a second rerank to an address caused a lookup"


async def test_one_house_keeps_one_gate_per_rerank_host(names):
    """Research builds a reranker per run; the answer for its host must outlive the run."""
    house = _bare_house()
    first = local_only_gate(house, "http://rerankbox:7998", what="research rerank")
    assert local_only_gate(house, "http://RERANKBOX:7998/rerank", what="memory rerank") is first
    assert local_only_gate(house, "http://otherbox:7998", what="rerank") is not first


def test_with_local_only_off_there_is_no_gate():
    assert local_only_gate(_bare_house(local_only=False), f"http://{RERANK_NAME}", what="rerank") is None


async def test_memory_builds_its_reranker_under_the_gate(names):
    from aura.integrations.memory import _build_reranker

    names.table[RERANK_NAME] = PUBLIC_ADDRESS
    options = {"rerank_url": f"http://{RERANK_NAME}:7998"}

    http = _RerankHttp()
    reranker = _build_reranker(_bare_house(), options)
    reranker._client = http
    assert await reranker.order(QUERY, TEXTS) == []
    assert http.sent == []

    # `local_only: false` is the operator saying a remote one is meant.
    reranker = _build_reranker(_bare_house(local_only=False), options)
    reranker._client = http
    assert await reranker.order(QUERY, TEXTS) == [1, 0]


@pytest.mark.parametrize(
    ("llm", "sends"),
    [
        # Production's shape: the chat model is not the house's, and the rule still holds.
        ({"enabled": False}, False),
        ({"enabled": False, "local_only": False}, True),
    ],
)
async def test_a_real_house_reads_the_rule_from_its_llm_block(tmp_path, names, llm, sends):
    names.table[RERANK_NAME] = PUBLIC_ADDRESS
    jarvis = Jarvis(tmp_path)
    await jarvis.async_setup({"llm": llm, "memory": {"rerank_url": f"http://{RERANK_NAME}:7998"}})
    http = _RerankHttp()
    reranker = jarvis.data["memory"].reranker
    reranker._client = http
    assert bool(await reranker.order(QUERY, TEXTS)) is sends
    assert bool(http.sent) is sends
    await jarvis.async_stop()


async def test_research_reranks_under_the_gate(names, monkeypatch):
    import aura.llm.rerank as rerank_module
    from aura.integrations.research import ResearchConfig, _reranked
    from aura.integrations.research.plan import Source

    posts: list[str] = []

    class Client:
        """The client the reranker builds for itself, with nothing behind it."""

        def __init__(self, **_kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc) -> bool:
            return False

        async def post(self, url, json=None, **_kwargs):
            posts.append(url)
            return _Scores()

    monkeypatch.setattr(rerank_module.httpx, "AsyncClient", Client)
    names.table[RERANK_NAME] = PUBLIC_ADDRESS
    cfg = ResearchConfig.from_config({"rerank_url": f"http://{RERANK_NAME}:7998"})

    def found() -> list[Source]:
        return [
            Source(url="https://a.example/keys", title="Keys", snippet="where keys are kept"),
            Source(url="https://b.example/sheds", title="Sheds", snippet="what is in the shed"),
        ]

    kept = await _reranked(_bare_house(), cfg, QUERY, found())
    assert posts == []
    assert [source.best_rank for source in kept] == [999, 999]

    reordered = await _reranked(_bare_house(local_only=False), cfg, QUERY, found())
    assert len(posts) == 1
    assert [source.best_rank for source in reordered] == [1, 0]


# ---------------------------------------------------------------------------
# Memory's embedder: every memory text and every recall query (T-1464)
# ---------------------------------------------------------------------------
#
# `memory: embedding_url` is a url of its own. Asserted on what the transport
# was sent, through the client memory builds for itself.

EMBED_NAME = "embed.example.com"
NOTE = "I take my coffee black"


def _embedder(house, url: str, sent: list[httpx.Request]):
    """Memory's embedding client, built the way memory builds it, over a transport that records."""
    from aura.integrations.memory import _embedding_client

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [0.25, 0.75]}]})

    house.data[DATA_CLIENT] = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return _embedding_client(house, {"embedding_url": url})


@pytest.mark.parametrize(
    "url",
    [
        f"http://{EMBED_NAME}:7997/v1",
        f"http://{PUBLIC_ADDRESS}:7997/v1",
        "http://[2001:db8::7]:7997/v1",
        # Not shown to be local is not local: a name that does not resolve is sent nothing.
        "http://embedbox:7997/v1",
    ],
)
async def test_an_embedding_url_not_shown_to_be_local_is_sent_nothing(url, names):
    names.table[EMBED_NAME] = PUBLIC_ADDRESS
    sent: list[httpx.Request] = []
    client = _embedder(_bare_house(), url, sent)
    with pytest.raises(OllamaError):
        await client.embed([NOTE])
    assert sent == []


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:7997/v1",
        "http://192.168.1.31:7997/v1",
        "http://embedbox:7997/v1",
        "http://nas.local:7997/v1",
    ],
)
async def test_a_local_embedding_url_is_used(url, names):
    names.table["embedbox"] = "192.168.1.31"
    sent: list[httpx.Request] = []
    client = _embedder(_bare_house(), url, sent)
    assert await client.embed([NOTE]) == [[0.25, 0.75]]
    assert len(sent) == 1


async def test_with_local_only_off_the_embedder_is_not_looked_at(names):
    names.table[EMBED_NAME] = PUBLIC_ADDRESS
    sent: list[httpx.Request] = []
    client = _embedder(_bare_house(local_only=False), f"http://{EMBED_NAME}:7997/v1", sent)
    assert await client.embed([NOTE]) == [[0.25, 0.75]]
    assert len(sent) == 1


async def test_an_embedder_with_a_client_of_its_own_is_held_too(names, monkeypatch):
    """Production's shape: no chat model in the house, so no shared client to borrow."""
    import aura.llm.openai_compat as openai_compat
    from aura.integrations.memory import _embedding_client

    sent: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [0.25, 0.75]}]})

    real = httpx.AsyncClient

    def over_a_recording_transport(**kwargs):
        return real(**{**kwargs, "transport": httpx.MockTransport(handler)})

    monkeypatch.setattr(openai_compat.httpx, "AsyncClient", over_a_recording_transport)
    names.table[EMBED_NAME] = PUBLIC_ADDRESS
    client = _embedding_client(_bare_house(), {"embedding_url": f"http://{EMBED_NAME}:7997/v1"})
    with pytest.raises(OllamaError):
        await client.embed([NOTE])
    assert sent == []

    local = _embedding_client(_bare_house(), {"embedding_url": "http://127.0.0.1:7997/v1"})
    assert await local.embed([NOTE]) == [[0.25, 0.75]]
    assert len(sent) == 1


async def test_a_note_is_kept_and_found_by_its_words_when_the_embedder_is_refused(tmp_path, names):
    """The house's own path, end to end: the note never leaves, and recall still answers."""
    names.table[EMBED_NAME] = PUBLIC_ADDRESS
    sent: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [0.25, 0.75]}]})

    jarvis = Jarvis(tmp_path)
    jarvis.data[DATA_CLIENT] = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    await jarvis.async_setup(
        {"llm": {"enabled": False}, "memory": {"embedding_url": f"http://{EMBED_NAME}:7997/v1"}}
    )
    memory = jarvis.data["memory"]
    assert (await memory.async_add(NOTE))["stored"]
    found = await memory.async_search("coffee")
    assert [entry.text for entry in found] == [NOTE]
    assert sent == []
    await jarvis.async_stop()


async def test_the_borrowed_chat_client_is_already_under_the_model_servers_gate(tmp_path, names):
    """No `embedding_url`: memory embeds through the chat client, which T-1461 gated."""
    sent: list[httpx.Request] = []
    jarvis = await _house(tmp_path, sent)
    names.table[MODEL_HOST] = PUBLIC_ADDRESS
    with pytest.raises(OllamaError):
        await jarvis.data["llm"].embed([NOTE])
    assert sent == []
    await jarvis.async_stop()


# --- a redirect answered by the model server (T-1466) -----------------------------------------
#
# The gate's request hook looks at the model server's own host, and the model clients follow
# redirects: a 307 from a local server sent the same request, body and all, to whatever host
# its Location named, and nothing looked at that host. The gate now judges it before the client
# follows. What the tests look at is what the transport was sent.

ELSEWHERE = "elsewhere.example.com"
OTHER_BOX = "otherbox"
MOVED = "/moved"


def _answers(sent: list[httpx.Request], moves: dict[str, str]) -> httpx.MockTransport:
    """A network: a host in `moves` answers 307 to its target (once: not from under /moved),
    every other host answers as a model server would, to a chat, an embed or a model list."""

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        target = moves.get(request.url.host)
        if target and not request.url.path.startswith(MOVED):
            return httpx.Response(307, headers={"location": target})
        if request.url.path.endswith("/chat/completions"):
            chunk = {"model": "house-model", "choices": [{"index": 0, "delta": {"content": "Sir."}}]}
            return httpx.Response(200, content=f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n".encode())
        return httpx.Response(
            200, json={"data": [{"id": "house-model", "index": 0, "embedding": [0.25, 0.75]}]}
        )

    return httpx.MockTransport(handler)


async def _house_on(tmp_path, sent: list[httpx.Request], moves: dict[str, str], **llm_options) -> Jarvis:
    """A house whose chat model lives on that network. `sent` starts empty: what setup asked
    the model server on its way up is not what a test is about."""
    jarvis = Jarvis(tmp_path)
    jarvis.data[DATA_TRANSPORT] = _answers(sent, moves)
    options = {"url": MODEL_URL, "model": "house-model", "backend": "openai", **llm_options}
    await jarvis.async_setup({"llm": options})
    assert "llm" in jarvis.data, "the chat model did not come up, so nothing below would mean anything"
    # Setup starts its reachability probe as a task and does not wait for it. On a slower
    # machine the probe asked the model server after `sent` was emptied, and a test counted
    # its request as the embed's (CI's image, 2026-11-16). A probe that has already ended has
    # left the house's task set, and then there is nothing to wait for.
    probes = [task for task in list(jarvis._tasks)
              if getattr(task.get_coro(), "__name__", "") == "_probe_model_server"]
    await asyncio.gather(*probes, return_exceptions=True)
    del sent[:]
    return jarvis


def _hosts(sent: list[httpx.Request]) -> list[str]:
    return [request.url.host for request in sent]


async def test_what_setup_asks_in_the_background_is_over_before_a_test_counts(
    tmp_path, names, monkeypatch
):
    """The tests below count requests from an empty list. A probe that is slow (here, held
    back; in CI's image, simply late) must have asked and ended before that list is emptied."""
    import aura.integrations.llm as integration

    real = integration._probe_model_server

    async def _probe_model_server(client, url):
        await asyncio.sleep(0.05)
        await real(client, url)

    monkeypatch.setattr(integration, "_probe_model_server", _probe_model_server)
    sent: list[httpx.Request] = []
    names.table[MODEL_HOST] = LAN_ADDRESS
    jarvis = await _house_on(tmp_path, sent, {})
    await asyncio.sleep(0.1)
    assert _hosts(sent) == [], "the setup probe's request arrived after the list was emptied"
    await jarvis.async_stop()


@pytest.mark.parametrize(
    "target",
    [
        f"http://{ELSEWHERE}/v1/embeddings",  # a public name
        f"http://{PUBLIC_ADDRESS}/v1/embeddings",  # a public address
        f"//{ELSEWHERE}/v1/embeddings",  # the same, with the scheme left to the client
        "http://nowhere-yet:8000/v1/embeddings",  # a name that does not resolve
    ],
)
async def test_a_model_servers_redirect_to_a_host_not_shown_local_is_not_followed(
    tmp_path, names, target
):
    sent: list[httpx.Request] = []
    names.table[MODEL_HOST] = LAN_ADDRESS
    names.table[ELSEWHERE] = PUBLIC_ADDRESS
    jarvis = await _house_on(tmp_path, sent, {MODEL_HOST: target})
    with pytest.raises(OllamaError) as refused:
        await jarvis.data["llm"].embed([NOTE])
    assert _hosts(sent) == [MODEL_HOST], "the memory text followed the redirect off this network"
    assert "redirect" in str(refused.value)
    await jarvis.async_stop()


async def test_a_streamed_conversation_does_not_follow_the_redirect_either(tmp_path, names):
    sent: list[httpx.Request] = []
    names.table[MODEL_HOST] = LAN_ADDRESS
    names.table[ELSEWHERE] = PUBLIC_ADDRESS
    moves = {MODEL_HOST: f"https://{ELSEWHERE}/v1/chat/completions"}
    jarvis = await _house_on(tmp_path, sent, moves)
    stream = jarvis.data["llm"].chat(messages=[{"role": "user", "content": "who is at the door"}])
    with pytest.raises(OllamaError):
        [delta async for delta in stream]
    assert _hosts(sent) == [MODEL_HOST], "the conversation followed the redirect off this network"
    await jarvis.async_stop()


async def test_a_redirect_on_the_model_servers_own_host_is_followed(tmp_path, names):
    sent: list[httpx.Request] = []
    names.table[MODEL_HOST] = LAN_ADDRESS
    jarvis = await _house_on(tmp_path, sent, {MODEL_HOST: f"{MOVED}/v1/embeddings"})
    assert await jarvis.data["llm"].embed([NOTE]) == [[0.25, 0.75]]
    assert [request.url.path for request in sent] == ["/v1/embeddings", f"{MOVED}/v1/embeddings"]
    await jarvis.async_stop()


async def test_a_redirect_to_another_local_machine_is_followed_and_its_own_redirect_is_judged(
    tmp_path, names
):
    """Local to local is the operator's own business. The machine it went to is then held to
    the same rule: without that, one local hop would be all it took to get round the gate."""
    sent: list[httpx.Request] = []
    names.table[MODEL_HOST] = names.table[OTHER_BOX] = LAN_ADDRESS
    names.table[ELSEWHERE] = PUBLIC_ADDRESS
    moves = {MODEL_HOST: f"http://{OTHER_BOX}:8000/v1/embeddings"}
    jarvis = await _house_on(tmp_path, sent, moves)
    assert await jarvis.data["llm"].embed([NOTE]) == [[0.25, 0.75]]
    assert _hosts(sent) == [MODEL_HOST, OTHER_BOX]

    del sent[:]
    moves[OTHER_BOX] = f"http://{ELSEWHERE}/v1/embeddings"
    with pytest.raises(OllamaError):
        await jarvis.data["llm"].embed([NOTE])
    assert _hosts(sent) == [MODEL_HOST, OTHER_BOX], "the second hop left this network"
    await jarvis.async_stop()


async def test_a_redirect_from_a_server_that_needs_no_lookup_is_judged_too(tmp_path, names):
    """An address judged local at startup has no request hook (there is nothing to look up
    again), so the redirect check must not depend on one being installed."""
    sent: list[httpx.Request] = []
    names.table[ELSEWHERE] = PUBLIC_ADDRESS
    moves = {LAN_ADDRESS: f"http://{ELSEWHERE}/v1/embeddings"}
    jarvis = await _house_on(tmp_path, sent, moves, url=f"http://{LAN_ADDRESS}:8000/v1")
    with pytest.raises(OllamaError):
        await jarvis.data["llm"].embed([NOTE])
    assert _hosts(sent) == [LAN_ADDRESS]
    await jarvis.async_stop()


async def test_another_hosts_redirect_on_the_shared_client_is_none_of_the_gates_business(
    tmp_path, names
):
    """The chat client is also the client the house's own tools call the internet with: a
    public API that redirects to another public host is not a model server sending a prompt
    away, and the gate must leave it alone."""
    sent: list[httpx.Request] = []
    names.table[MODEL_HOST] = LAN_ADDRESS
    names.table[ELSEWHERE] = names.table["api.example.org"] = PUBLIC_ADDRESS
    moves = {"api.example.org": f"https://{ELSEWHERE}{MOVED}/forecast"}
    jarvis = await _house_on(tmp_path, sent, moves)
    response = await jarvis.data[DATA_CLIENT].get("https://api.example.org/forecast")
    assert response.status_code == 200
    assert _hosts(sent) == ["api.example.org", ELSEWHERE]
    await jarvis.async_stop()


async def test_with_the_guard_off_a_redirect_is_followed_wherever_it_goes(tmp_path, names):
    sent: list[httpx.Request] = []
    names.table[MODEL_HOST] = LAN_ADDRESS
    names.table[ELSEWHERE] = PUBLIC_ADDRESS
    moves = {MODEL_HOST: f"http://{ELSEWHERE}{MOVED}/v1/embeddings"}
    jarvis = await _house_on(tmp_path, sent, moves, local_only=False)
    assert await jarvis.data["llm"].embed([NOTE]) == [[0.25, 0.75]]
    assert _hosts(sent) == [MODEL_HOST, ELSEWHERE]
    await jarvis.async_stop()


async def test_the_embedders_own_client_does_not_follow_a_redirect_off_this_network(
    names, monkeypatch
):
    """Production's shape: no chat model in the house, the embedder on a client of its own,
    at an address, which is the case where no request hook is installed at all."""
    import aura.llm.openai_compat as openai_compat
    from aura.integrations.memory import _embedding_client

    sent: list[httpx.Request] = []
    names.table[ELSEWHERE] = PUBLIC_ADDRESS
    transport = _answers(sent, {"127.0.0.1": f"http://{ELSEWHERE}/v1/embeddings"})
    real = httpx.AsyncClient
    monkeypatch.setattr(
        openai_compat.httpx, "AsyncClient", lambda **kwargs: real(**{**kwargs, "transport": transport})
    )
    client = _embedding_client(_bare_house(), {"embedding_url": "http://127.0.0.1:7997/v1"})
    with pytest.raises(OllamaError):
        await client.embed([NOTE])
    assert _hosts(sent) == ["127.0.0.1"], "the memory text followed the redirect off this network"

    del sent[:]
    off = _embedding_client(
        _bare_house(local_only=False), {"embedding_url": "http://127.0.0.1:7997/v1"}
    )
    assert await off.embed([NOTE]) == [[0.25, 0.75]]
    assert _hosts(sent) == ["127.0.0.1", ELSEWHERE], "off, the redirect is followed as before"
