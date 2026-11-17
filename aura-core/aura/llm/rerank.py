"""A cross-encoder, asked which of these results actually answers the question.

Retrieval gets you twenty candidates that share words with the query. A
cross-encoder reads the query and one candidate *together* and scores that pair
— which is slow enough that you would never run it over a whole store, and
cheap enough to run over twenty. Reranking after retrieval is the largest
quality gain per line of code in this system, and it is one HTTP call.

    reranker = Reranker(url="http://127.0.0.1:7998")
    order = await reranker.order("where do we keep the caffeine", texts)

What it must never do is fail a search. Every error path returns "no opinion"
and the caller keeps the order it already had — a reranker that is down makes
retrieval no better, never worse, and says so once rather than per query.

Two wire shapes exist for the same idea and this speaks both: Text Embeddings
Inference takes ``{"query", "texts"}``, Infinity and Jina take
``{"query", "documents"}``. The first shape is tried once, and a 4xx switches
to the other for the life of the process — a rerank service is not something
you swap mid-conversation, and probing on every call would double the traffic.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Sequence

import httpx

_LOGGER = logging.getLogger(__name__)

#: How long a rerank may take before the answer is "no opinion". A cross-encoder
#: over twenty short notes is tens of milliseconds on a CPU; a second is already
#: a broken service, and the turn behind this is waiting.
DEFAULT_TIMEOUT = 3.0

#: Never send the whole store to a cross-encoder. It is O(candidates), and the
#: point of retrieval is to have narrowed it first.
MAX_CANDIDATES = 40

#: How long a failed reranker is left alone before it is asked again. It is started beside
#: jarvis-core and takes up to 180 s to load, so a first query in that window used to turn
#: reranking off until jarvis-core restarted (T-0554).
RETRY_AFTER_S = 60.0


class RerankRefused(RuntimeError):
    """The local-only gate would not let a request go to the rerank url."""


class Reranker:
    """One rerank endpoint, or nothing at all."""

    def __init__(
        self,
        url: str = "",
        model: str = "",
        timeout: float = DEFAULT_TIMEOUT,
        client: Any = None,
        now: Callable[[], float] = time.monotonic,
        gate: Any = None,
    ) -> None:
        self.url = str(url or "").rstrip("/")
        self.model = str(model or "")
        self.timeout = float(timeout)
        self._client = client
        #: The house's local-only gate for this url (`integrations.llm.local_only_gate`),
        #: or None when the operator turned the rule off. Anything with an async
        #: `refusal()` that answers "" to allow. Without it the query and the texts
        #: went to whatever the url named (T-1462).
        self._gate = gate
        #: "texts" or "documents", learned on first use.
        self._key = "texts"
        self._now = now
        #: After a failure, nothing is sent until this time, so a dead service costs one
        #: timeout per RETRY_AFTER_S, not one per query; the log line is once per outage.
        self._off_until = 0.0
        self._failing = False

    @property
    def configured(self) -> bool:
        return bool(self.url) and self._now() >= self._off_until

    def _endpoint(self) -> str:
        return self.url if self.url.endswith("/rerank") else f"{self.url}/rerank"

    async def _post(self, body: dict[str, Any]) -> httpx.Response:
        # Asked here because this is the only place a request leaves from, so
        # no path to the service can skip it. A refusal is raised into `scores`,
        # where it is "no opinion" like any other failure, and is asked about
        # again after RETRY_AFTER_S.
        if self._gate is not None:
            why = await self._gate.refusal()
            if why:
                raise RerankRefused(why)
        if self._client is not None:
            return await self._client.post(self._endpoint(), json=body, timeout=self.timeout)
        async with httpx.AsyncClient(timeout=self.timeout) as http:
            return await http.post(self._endpoint(), json=body)

    async def scores(self, query: str, texts: Sequence[str]) -> list[float]:
        """A score per text, in the order given, or [] for "no opinion"."""
        candidates = [str(t) for t in texts][:MAX_CANDIDATES]
        if not self.configured or not query.strip() or len(candidates) < 2:
            return []
        body: dict[str, Any] = {"query": query, self._key: candidates}
        if self.model:
            body["model"] = self.model
        try:
            answer = await self._post(body)
            if answer.status_code in (400, 404, 422) and self._key == "texts":
                # The other dialect. Learned once, not probed per call.
                self._key = "documents"
                body = {"query": query, "documents": candidates}
                if self.model:
                    body["model"] = self.model
                answer = await self._post(body)
            answer.raise_for_status()
            payload = answer.json()
        except Exception as exc:  # noqa: BLE001 - a reranker is never fatal
            if not self._failing:
                _LOGGER.info(
                    "Reranking is off: %s at %s (%s). Retrieval still works — "
                    "results are in the order retrieval found them; retrying every %.0f s.",
                    type(exc).__name__, self._endpoint(), exc, RETRY_AFTER_S,
                )
            self._failing = True
            self._off_until = self._now() + RETRY_AFTER_S
            return []
        if self._failing:
            _LOGGER.info("Reranking is back at %s.", self._endpoint())
            self._failing = False

        rows = payload.get("results") if isinstance(payload, dict) else payload
        if not isinstance(rows, list):
            return []
        out = [0.0] * len(candidates)
        for row in rows:
            if not isinstance(row, dict):
                continue
            score = row.get("score", row.get("relevance_score"))
            try:
                index = int(row.get("index", -1))
                number = float(score) if score is not None else None
            except (TypeError, ValueError, OverflowError):  # JSON may carry Infinity
                continue
            # NaN would make the sort order arbitrary, so it counts as no score.
            if 0 <= index < len(out) and number is not None and number == number:
                out[index] = number
        return out

    async def order(self, query: str, texts: Sequence[str]) -> list[int]:
        """The indices of `texts`, best first — or [] for "keep what you had"."""
        scored = await self.scores(query, texts)
        if not scored:
            return []
        return sorted(range(len(scored)), key=lambda i: -scored[i])
