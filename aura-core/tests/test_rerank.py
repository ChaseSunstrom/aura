"""The reranker: two wire dialects, and never fatal.

A cross-encoder can only ever reorder a shortlist retrieval already chose, so
every failure here has one correct answer — "no opinion" — and the caller keeps
what it had. These tests are mostly about that: the ways a rerank service can
be wrong, and the search still working.
"""

from __future__ import annotations

import pytest

from aura.llm.rerank import MAX_CANDIDATES, RETRY_AFTER_S, Reranker


class FakeResponse:
    def __init__(self, payload=None, status_code=200) -> None:
        self._payload = payload if payload is not None else []
        self.status_code = status_code

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeHttp:
    """Records what was sent, answers what the test says."""

    def __init__(self, *answers) -> None:
        self.answers = list(answers)
        self.sent: list[dict] = []

    async def post(self, url, json=None, timeout=None):
        self.sent.append({"url": url, "body": json})
        return self.answers.pop(0) if self.answers else FakeResponse()


@pytest.mark.asyncio
async def test_it_reorders_by_score():
    http = FakeHttp(FakeResponse([{"index": 2, "score": 9.0}, {"index": 0, "score": 1.0}]))
    reranker = Reranker(url="http://rerank", client=http)
    assert await reranker.order("q", ["a", "b", "c"]) == [2, 0, 1]


@pytest.mark.asyncio
async def test_no_url_means_no_opinion_and_no_traffic():
    """The default. Retrieval works exactly as it did before this existed."""
    http = FakeHttp()
    reranker = Reranker(client=http)
    assert await reranker.order("q", ["a", "b"]) == []
    assert http.sent == []


@pytest.mark.asyncio
async def test_a_service_that_is_down_is_not_an_error():
    class Broken:
        async def post(self, *_a, **_k):
            raise ConnectionError("refused")

    reranker = Reranker(url="http://rerank", client=Broken())
    assert await reranker.order("q", ["a", "b"]) == []
    # And it stops asking, so a dead service costs one timeout, not one per query.
    assert not reranker.configured


@pytest.mark.asyncio
async def test_a_service_still_starting_is_asked_again_after_a_while():
    """T-0554: one failure during the reranker's 180 s start turned reranking off until restart."""
    clock = [0.0]

    class StartsLate:
        up = False

        async def post(self, *_a, **_k):
            if not self.up:
                raise ConnectionError("refused")
            return FakeResponse([{"index": 1, "score": 2.0}, {"index": 0, "score": 1.0}])

    http = StartsLate()
    reranker = Reranker(url="http://rerank", client=http, now=lambda: clock[0])
    assert await reranker.order("q", ["a", "b"]) == []
    http.up = True
    clock[0] += RETRY_AFTER_S - 1
    assert await reranker.order("q", ["a", "b"]) == []
    clock[0] += 1
    assert await reranker.order("q", ["a", "b"]) == [1, 0]


@pytest.mark.asyncio
async def test_the_other_dialect_is_learned_once():
    """TEI takes `texts`; Infinity and Jina take `documents`."""
    http = FakeHttp(
        FakeResponse(status_code=422),
        FakeResponse([{"index": 1, "score": 5.0}]),
        FakeResponse([{"index": 0, "score": 5.0}]),
    )
    reranker = Reranker(url="http://rerank", client=http)
    assert await reranker.order("q", ["a", "b"]) == [1, 0]
    assert "texts" in http.sent[0]["body"]
    assert "documents" in http.sent[1]["body"]
    # Second call goes straight to the dialect that worked.
    await reranker.order("q", ["a", "b"])
    assert "documents" in http.sent[2]["body"]
    assert len(http.sent) == 3


@pytest.mark.asyncio
async def test_relevance_score_is_the_same_field_by_another_name():
    http = FakeHttp(FakeResponse([{"index": 1, "relevance_score": 0.9}]))
    assert await Reranker(url="http://r", client=http).order("q", ["a", "b"]) == [1, 0]


@pytest.mark.asyncio
async def test_one_candidate_is_not_worth_a_round_trip():
    http = FakeHttp()
    assert await Reranker(url="http://r", client=http).order("q", ["only"]) == []
    assert http.sent == []


@pytest.mark.asyncio
async def test_the_whole_store_is_never_sent_to_a_cross_encoder():
    """It is O(candidates) and reads each one with the query."""
    http = FakeHttp(FakeResponse([]))
    await Reranker(url="http://r", client=http).scores("q", [f"note {i}" for i in range(500)])
    assert len(http.sent[0]["body"]["texts"]) == MAX_CANDIDATES


@pytest.mark.asyncio
async def test_a_nonsense_payload_is_no_opinion_rather_than_a_crash():
    http = FakeHttp(FakeResponse({"unexpected": "shape"}))
    assert await Reranker(url="http://r", client=http).order("q", ["a", "b"]) == []


@pytest.mark.asyncio
async def test_the_endpoint_is_not_doubled_when_the_url_already_has_it():
    http = FakeHttp(FakeResponse([]))
    await Reranker(url="http://r/rerank", client=http).scores("q", ["a", "b"])
    assert http.sent[0]["url"] == "http://r/rerank"


class FakeGate:
    """The house's local-only gate as the reranker sees it: `why` refuses, "" lets it send."""

    def __init__(self, why: str = "") -> None:
        self.why = why
        self.asked = 0

    async def refusal(self) -> str:
        self.asked += 1
        return self.why


@pytest.mark.asyncio
async def test_a_refused_url_is_sent_nothing_and_the_order_stands():
    """The query and the texts are what would leave: neither may, and the search still works."""
    http = FakeHttp(FakeResponse([{"index": 1, "score": 9.0}]))
    gate = FakeGate("rerank.example.com resolves to a public address")
    reranker = Reranker(url="http://rerank.example.com", client=http, gate=gate)
    assert await reranker.order("where is the spare key", ["under the mat", "in the shed"]) == []
    assert http.sent == []


@pytest.mark.asyncio
async def test_the_gate_is_asked_before_every_request_including_the_second_dialect():
    http = FakeHttp(FakeResponse(status_code=422), FakeResponse([{"index": 1, "score": 2.0}]))
    gate = FakeGate()
    reranker = Reranker(url="http://rerank", client=http, gate=gate)
    assert await reranker.order("q", ["a", "b"]) == [1, 0]
    assert gate.asked == len(http.sent) == 2


@pytest.mark.asyncio
async def test_a_refused_url_is_looked_at_again_after_the_wait_and_used_once_it_is_local():
    clock = [1000.0]
    http = FakeHttp(FakeResponse([{"index": 1, "score": 2.0}]))
    gate = FakeGate("rerank does not resolve yet")
    reranker = Reranker(url="http://rerank", client=http, gate=gate, now=lambda: clock[0])
    assert await reranker.order("q", ["a", "b"]) == []
    gate.why = ""
    assert await reranker.order("q", ["a", "b"]) == [], "asked again inside the wait"
    assert gate.asked == 1
    clock[0] += RETRY_AFTER_S
    assert await reranker.order("q", ["a", "b"]) == [1, 0]
    assert len(http.sent) == 1
