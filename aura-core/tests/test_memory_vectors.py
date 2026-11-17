"""Semantic recall over the note store.

## Why this exists

`_score` is token overlap. It finds *"the good coffee is in the left cupboard"*
from "where is the good coffee" and misses it completely from "where do we keep
the caffeine" — the words do not touch, and no amount of stop-list tuning makes
them.

The fix is embeddings, and the interesting decisions are about what NOT to
build. No vector database: 500 notes is 380k multiply-adds, single-digit
milliseconds in pure Python beside an 8B generation. No new dependency: the
vectors come from the model server already running, over `embed()`, so
`requirements.txt` stays seven pure-Python wheels and the image still builds on
a Pi. No new container: Qdrant was measured and rejected — and separately its
stock image POSTs to `telemetry.qdrant.io` hourly, which is disqualifying for a
project whose README opens with "nothing goes to the cloud at runtime".

So what has to be pinned is mostly the degradation: every way this can be
unavailable must end in the keyword search that shipped before it, never in an
error the user sees or a turn that is lost.

The embedder here is a fake with a fixed vocabulary. It is not a language
model and does not need to be — what is under test is the indexing, the
fusion, the invalidation and the fallbacks, all of which are ours.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aura.core import Jarvis  # noqa: E402
from aura.integrations.memory import MemoryStore  # noqa: E402
from aura.integrations.memory.vectors import (  # noqa: E402
    DOCUMENT_PREFIX,
    QUERY_PREFIX,
    VectorIndex,
    fuse,
    normalise,
    pack,
    similarity,
    unpack,
)

#: A toy semantic space. Each concept is an axis, so texts sharing a concept
#: point the same way regardless of sharing any words — which is the entire
#: property real embeddings have and token overlap does not.
CONCEPTS = {
    "coffee": ("coffee", "caffeine", "espresso", "beans", "cupboard"),
    "medical": ("penicillin", "allergic", "allergy", "medicine", "doctor"),
    "transport": ("bicycle", "bike", "cycling", "puncture"),
}


class FakeEmbedder:
    """Answers `embed()` with a concept vector. Records what it was asked."""

    def __init__(self) -> None:
        self.seen: list[str] = []
        self.calls = 0

    async def embed(self, texts, model=None):
        self.calls += 1
        out = []
        for text in texts:
            self.seen.append(text)
            lowered = text.lower()
            vector = [
                float(sum(word in lowered for word in words))
                for words in CONCEPTS.values()
            ]
            # A tiny constant so a text matching nothing still has a direction
            # and `normalise` does not divide by zero.
            out.append([v + 0.01 for v in vector])
        return out


class BrokenEmbedder:
    async def embed(self, texts, model=None):
        raise RuntimeError("model 'nomic-embed-text' not found")


@pytest.fixture
def jarvis(tmp_path):
    return Jarvis(tmp_path)


async def _store(jarvis, embedder=None, **kw) -> MemoryStore:
    index = VectorIndex(client=embedder) if embedder is not None else None
    store = MemoryStore(jarvis, vectors=index, **kw)
    await store.async_load()
    return store


async def _add(store: MemoryStore, text: str, **kw):
    entry = await store.async_add(text, source="user", **kw)
    await store._async_reindex()
    return entry


# ---------------------------------------------------------------------------
# the point of the whole thing
# ---------------------------------------------------------------------------
async def test_a_note_is_found_by_meaning_when_no_word_matches(jarvis):
    """The case token overlap cannot reach, and the reason this exists."""
    embedder = FakeEmbedder()
    store = await _store(jarvis, embedder)
    await _add(store, "the good coffee is in the left cupboard")
    for index in range(10):
        await _add(store, f"unrelated note {index} about bicycles")

    hits = await store.async_semantic_ids("where do we keep the caffeine")

    assert hits, "nothing matched a query about the same concept"
    best = max(hits, key=lambda i: hits[i])
    found = next(e for e in store.entries if e.id == best)
    assert "coffee" in found.text


async def test_the_block_carries_it_through(jarvis):
    """End to end: the note reaches the prompt, which is what matters."""
    embedder = FakeEmbedder()
    store = await _store(jarvis, embedder)
    await _add(store, "the good coffee is in the left cupboard")
    for index in range(10):
        await _add(store, f"unrelated note {index} about bicycles")

    semantic = await store.async_semantic_ids("where do we keep the caffeine")
    block = store.get_context_block(query="where do we keep the caffeine", semantic=semantic)

    assert "coffee" in block


async def test_documents_and_queries_are_embedded_differently(jarvis):
    """`nomic-embed-text` is trained with task prefixes and is worse without.

    Omitting them does not fail — it retrieves badly, which is the exact
    failure this module exists to fix, so they are applied here rather than
    left to callers to remember.
    """
    embedder = FakeEmbedder()
    store = await _store(jarvis, embedder)
    await _add(store, "the good coffee is in the left cupboard")
    await store.async_semantic_ids("caffeine")

    documents = [t for t in embedder.seen if t.startswith(DOCUMENT_PREFIX)]
    queries = [t for t in embedder.seen if t.startswith(QUERY_PREFIX)]
    assert documents and queries
    assert not any(t.startswith(QUERY_PREFIX) for t in documents)


# ---------------------------------------------------------------------------
# degradation — every path back to keyword search
# ---------------------------------------------------------------------------
async def test_no_embedding_model_is_not_an_error(jarvis):
    """The commonest state on a fresh box: nothing pulled."""
    store = await _store(jarvis, BrokenEmbedder())
    await _add(store, "the good coffee is in the left cupboard")

    assert await store.async_semantic_ids("caffeine") == {}
    # And the note is still there, still findable the old way.
    assert "coffee" in store.get_context_block(query="where is the good coffee")


async def test_a_failing_embedder_is_asked_once_not_once_per_note(jarvis):
    """A model that is not pulled will not become pulled by being asked again.

    Without the latch this adds an HTTP round trip and a stack trace to every
    note written, forever.
    """

    class Counting(BrokenEmbedder):
        def __init__(self) -> None:
            self.calls = 0

        async def embed(self, texts, model=None):
            self.calls += 1
            raise RuntimeError("nope")

    embedder = Counting()
    store = await _store(jarvis, embedder)
    for index in range(5):
        await _add(store, f"note {index}")
    await store.async_semantic_ids("anything")

    assert embedder.calls == 1, f"asked {embedder.calls} times"


class StartsLate(FakeEmbedder):
    """An embedder that is not up yet: the ordinary boot order of a compose stack."""

    def __init__(self) -> None:
        super().__init__()
        self.up = False
        self.asked = 0

    async def embed(self, texts, model=None):
        self.asked += 1
        if not self.up:
            raise RuntimeError("connection refused")
        return await super().embed(texts, model=model)


COFFEE = ("coffee", "the good coffee is in the left cupboard")


async def test_semantic_recall_comes_back_after_the_wait_without_a_restart():
    """One failed embed used to switch the index off until jarvis-core restarted (T-1465):
    an embedder still loading when the first note was written, or a url the local-only gate
    refused for a minute, left memory on keyword search for the life of the process."""
    from aura.integrations.memory.vectors import RETRY_AFTER_S

    clock = [1000.0]
    embedder = StartsLate()
    index = VectorIndex(client=embedder, now=lambda: clock[0])
    assert await index.async_index([COFFEE]) == 0

    embedder.up = True
    # Inside the wait nothing is asked: a model that is not pulled must not cost a
    # round trip per note, which is what the off switch is for.
    clock[0] += RETRY_AFTER_S - 1
    assert await index.async_index([COFFEE]) == 0
    assert embedder.asked == 1

    clock[0] += 1
    assert await index.async_index([COFFEE]) == 1
    assert "coffee" in await index.async_search("where do we keep the caffeine")


async def test_an_embedder_that_stays_down_is_asked_once_per_wait():
    from aura.integrations.memory.vectors import RETRY_AFTER_S

    clock = [1000.0]
    embedder = StartsLate()
    index = VectorIndex(client=embedder, now=lambda: clock[0])
    for _ in range(3):
        for _ in range(5):
            await index.async_index([COFFEE])
        clock[0] += RETRY_AFTER_S
    assert embedder.asked == 3


async def test_an_outage_is_logged_once_and_its_end_once(caplog):
    from aura.integrations.memory.vectors import RETRY_AFTER_S

    clock = [1000.0]
    embedder = StartsLate()
    index = VectorIndex(client=embedder, now=lambda: clock[0])
    with caplog.at_level("INFO", logger="aura.integrations.memory.vectors"):
        for _ in range(3):
            await index.async_index([COFFEE])
            clock[0] += RETRY_AFTER_S
        embedder.up = True
        await index.async_index([COFFEE])
        await index.async_search("caffeine")
    said = [record.getMessage() for record in caplog.records]
    assert len([line for line in said if "Semantic recall is off" in line]) == 1, said
    assert len([line for line in said if "Semantic recall is back" in line]) == 1, said


async def test_retry_ends_the_wait_at_once():
    """The files index re-arms before each pass; that must still work."""
    clock = [1000.0]
    embedder = StartsLate()
    index = VectorIndex(client=embedder, now=lambda: clock[0])
    assert await index.async_index([COFFEE]) == 0
    embedder.up = True
    index.retry()
    assert await index.async_index([COFFEE]) == 1


async def test_a_client_that_cannot_embed_is_never_asked_again():
    """No `embed()` at all is not an outage: waiting will not grow one."""
    from aura.integrations.memory.vectors import RETRY_AFTER_S

    clock = [1000.0]
    index = VectorIndex(client=object(), now=lambda: clock[0])
    assert await index.async_index([COFFEE]) == 0
    clock[0] += RETRY_AFTER_S * 10
    assert not index.enabled


async def _store_on_a_clock(jarvis, embedder, clock: list[float]) -> MemoryStore:
    store = MemoryStore(jarvis, vectors=VectorIndex(client=embedder, now=lambda: clock[0]))
    await store.async_load()
    return store


async def test_a_note_written_during_an_outage_is_found_at_the_first_search_after_it(jarvis):
    """The index pass ran on load and after a mutation, and nowhere else (T-1467). A note
    written while the embedder was down therefore had no vector until something else was
    written, however long the embedder had been back: found by its own words, missed by any
    others, which is the one case the index is for."""
    from aura.integrations.memory.vectors import RETRY_AFTER_S

    clock = [1000.0]
    embedder = StartsLate()
    store = await _store_on_a_clock(jarvis, embedder, clock)
    await store.async_add("the good coffee is in the left cupboard")
    assert embedder.asked == 1, "the note's embed was the one failed call"

    embedder.up = True
    clock[0] += RETRY_AFTER_S
    hits = await store.async_semantic_ids("where do we keep the caffeine")
    assert hits, "the embedder is back and nothing was written since: the note has no vector"


async def test_inside_the_wait_a_search_asks_nothing_and_the_catch_up_still_comes(jarvis):
    """A search during the outage must not cost a failed round trip, and must not use up the
    one chance to catch up."""
    from aura.integrations.memory.vectors import RETRY_AFTER_S

    clock = [1000.0]
    embedder = StartsLate()
    store = await _store_on_a_clock(jarvis, embedder, clock)
    await store.async_add("the good coffee is in the left cupboard")

    clock[0] += RETRY_AFTER_S - 1
    assert await store.async_semantic_ids("where do we keep the caffeine") == {}
    assert embedder.asked == 1

    # Still down when the wait ends: one more failed call, and a new wait.
    clock[0] += 1
    assert await store.async_semantic_ids("where do we keep the caffeine") == {}
    assert embedder.asked == 2

    embedder.up = True
    clock[0] += RETRY_AFTER_S
    assert await store.async_semantic_ids("where do we keep the caffeine")


async def test_with_nothing_behind_a_search_embeds_the_query_and_nothing_else(jarvis):
    """The catch-up is one flag: an index that is in step costs a search no pass over the
    notes and no second call."""
    embedder = FakeEmbedder()
    store = await _store(jarvis, embedder)
    await store.async_add("the good coffee is in the left cupboard")
    await store.async_add("allergic to penicillin")
    calls, seen = embedder.calls, len(embedder.seen)
    passes = []
    reindex = store._async_reindex
    store._async_reindex = lambda: passes.append(1) or reindex()

    assert await store.async_semantic_ids("where do we keep the caffeine")
    assert embedder.calls == calls + 1 and len(embedder.seen) == seen + 1
    assert passes == []


async def test_a_catch_up_embeds_only_what_was_missed(jarvis):
    from aura.integrations.memory.vectors import RETRY_AFTER_S

    clock = [1000.0]
    embedder = StartsLate()
    embedder.up = True
    store = await _store_on_a_clock(jarvis, embedder, clock)
    await store.async_add("allergic to penicillin")
    embedder.up = False
    await store.async_add("the good coffee is in the left cupboard")
    embedder.up = True
    clock[0] += RETRY_AFTER_S
    before = len(embedder.seen)

    assert await store.async_semantic_ids("where do we keep the caffeine")
    new = embedder.seen[before:]
    assert len(new) == 2, f"one missed note and one query, not {new!r}"
    assert not any("penicillin" in text for text in new)

    # And once caught up, the next search is a query and nothing else.
    asked = embedder.asked
    await store.async_semantic_ids("where do we keep the caffeine")
    assert embedder.asked == asked + 1


async def test_no_index_at_all_behaves_exactly_as_before(jarvis):
    store = await _store(jarvis, None)
    await _add(store, "the good coffee is in the left cupboard")

    assert await store.async_semantic_ids("caffeine") == {}
    assert "coffee" in store.get_context_block(query="where is the good coffee")


async def test_a_pinned_note_still_survives_a_semantic_query(jarvis):
    """The pin rule holds whichever ranking is in play."""
    embedder = FakeEmbedder()
    store = await _store(jarvis, embedder)
    await _add(store, "I am allergic to penicillin", pinned=True)
    for index in range(10):
        await _add(store, f"unrelated note {index} about bicycles")

    semantic = await store.async_semantic_ids("where do we keep the caffeine")
    block = store.get_context_block(query="where do we keep the caffeine", semantic=semantic)

    assert "penicillin" in block


# ---------------------------------------------------------------------------
# the sidecar
# ---------------------------------------------------------------------------
def test_a_vector_survives_a_round_trip():
    original = [0.5, -0.25, 0.125]
    restored = unpack(pack(original))
    assert [round(x, 4) for x in restored] == [round(x, 4) for x in original]


def test_normalised_vectors_have_unit_length():
    unit = normalise([3.0, 4.0])
    assert math.isclose(math.sqrt(sum(x * x for x in unit)), 1.0, rel_tol=1e-6)
    # Which is what makes similarity a plain dot product.
    assert math.isclose(similarity(unit, unit), 1.0, rel_tol=1e-6)


def test_a_zero_vector_does_not_divide_by_zero():
    assert list(normalise([0.0, 0.0])) == [0.0, 0.0]


def test_vectors_of_different_lengths_do_not_compare():
    assert similarity([1.0, 0.0], [1.0, 0.0, 0.0]) == 0.0


async def test_editing_a_note_invalidates_exactly_its_own_vector(jarvis):
    """`memory.json` is meant to be hand-editable — that is a product promise.

    Keying vectors on a hash of the text is what makes an edit safe: the one
    note that changed is re-embedded and the rest are left alone.
    """
    embedder = FakeEmbedder()
    index = VectorIndex(client=embedder)
    await index.async_index([("a", "coffee in the cupboard"), ("b", "bicycle puncture")])
    assert index.is_current("a", "coffee in the cupboard")

    assert not index.is_current("a", "coffee in the OTHER cupboard")
    assert index.is_current("b", "bicycle puncture"), "an unrelated vector was invalidated"


async def test_a_forgotten_note_leaves_no_vector_behind(jarvis):
    """Being wholly deletable is a promise this integration makes out loud.

    A vector that outlived its note would be the one place deletion did not
    reach.
    """
    embedder = FakeEmbedder()
    store = await _store(jarvis, embedder)
    entry = await _add(store, "the good coffee is in the left cupboard")
    assert len(store.vectors) == 1

    await store.async_forget(entry_id=entry["entry"]["id"])
    await store._async_reindex()

    assert len(store.vectors) == 0


async def test_changing_the_embedding_model_discards_the_index(tmp_path):
    """Vectors from two models live in different spaces.

    Comparing across them is not "slightly worse", it is meaningless, so a
    model change throws the whole file away rather than mixing them.
    """

    class Recording:
        def __init__(self) -> None:
            self.saved: dict = {}

        async def load(self):
            return self.saved

        async def save(self, data):
            self.saved = data

    disk = Recording()
    first = VectorIndex(client=FakeEmbedder(), model="model-a", store=disk)
    await first.async_index([("a", "coffee")])
    assert disk.saved["model"] == "model-a"

    second = VectorIndex(client=FakeEmbedder(), model="model-b", store=disk)
    await second.async_load()

    assert len(second) == 0, "vectors from another model were loaded"


async def test_a_corrupt_sidecar_is_a_rebuild_not_a_crash():
    class Broken:
        async def load(self):
            raise ValueError("not json")

        async def save(self, data):
            return None

    index = VectorIndex(client=FakeEmbedder(), store=Broken())
    await index.async_load()  # must not raise
    assert len(index) == 0


# ---------------------------------------------------------------------------
# fusion
# ---------------------------------------------------------------------------
def test_a_note_both_rankings_agree_on_comes_first():
    """The property that makes fusing worth doing at all."""
    keyword = {"agreed": 0.9, "lexical_only": 0.8}
    semantic = {"agreed": 0.7, "semantic_only": 0.95}

    assert fuse(keyword, semantic)[0] == "agreed"


def test_fusion_reads_order_not_score():
    """The two do not share a scale — a fraction of terms and an angle.

    Weighting them against each other would need a constant that is right for
    one query and wrong for the next; RRF only uses the part both agree on the
    meaning of.
    """
    # Semantic scores an order of magnitude smaller must still count.
    keyword = {"a": 1000.0}
    semantic = {"b": 0.9, "a": 0.1}

    assert set(fuse(keyword, semantic)) == {"a", "b"}
    assert fuse(keyword, semantic)[0] == "a", "agreement lost to raw magnitude"


def test_fusing_nothing_with_nothing_is_nothing():
    assert fuse({}, {}) == []


# --- the two wiring gaps ------------------------------------------------------
#
# Both are the same shape: the machinery is complete and correct, and something
# never calls it. Neither produces an error, a warning or a wrong answer —
# semantic recall simply quietly does less than it says it does, which is why
# neither showed up in the tests above.

async def test_a_note_added_now_is_searchable_now(jarvis):
    """`_async_reindex` ran only from `async_load`.

    So a note the model just wrote — which is the whole point of the `remember`
    tool — had no vector until the process restarted. Keyword search still
    found it, so the failure was invisible unless you asked for it in words the
    note does not contain, which is exactly the case embeddings were added for.
    """
    embedder = FakeEmbedder()
    store = await _store(jarvis, embedder)
    await store.async_add("the good coffee is in the left cupboard")

    hits = await store.async_semantic_ids("where do we keep the caffeine")
    assert hits, (
        "a note added during this process is not in the vector index; semantic "
        "recall cannot see it until a restart"
    )


async def test_a_forgotten_note_stops_being_searchable_immediately(jarvis):
    """The other direction of the same gap, and the one that matters more.

    Being wholly deletable is a promise this integration makes in its own
    docstring. A vector that outlives the note it came from is the one place
    deletion did not reach.
    """
    embedder = FakeEmbedder()
    store = await _store(jarvis, embedder)
    result = await store.async_add("allergic to penicillin")
    await store.async_semantic_ids("medicine")  # force the index to exist
    entry_id = result["entry"]["id"]

    await store.async_forget(entry_id=entry_id)
    hits = await store.async_semantic_ids("medicine")
    assert entry_id not in hits, (
        "a forgotten note kept its vector; deletion has to reach the sidecar too"
    )


async def test_the_sidecar_is_read_back_instead_of_re_embedded_every_boot(jarvis):
    """`VectorIndex.async_load` existed and nothing called it.

    The sidecar's whole purpose is that embedding is the expensive part and the
    result is durable. Never reading it back means every restart re-embeds the
    entire store against the model server — slow on a Pi, and a burst of
    requests at exactly the moment everything else is starting.
    """
    from aura.integrations.memory.vectors import STORAGE_KEY, VectorIndex
    from aura.store import Store

    first_embedder = FakeEmbedder()
    disk = Store(jarvis.config_dir, STORAGE_KEY)
    index = VectorIndex(client=first_embedder, store=disk)
    store = MemoryStore(jarvis, vectors=index)
    await store.async_load()
    await store.async_add("the good coffee is in the left cupboard")
    await store.async_add("allergic to penicillin")
    embedded_first_time = len(first_embedder.seen)
    assert embedded_first_time >= 2

    # A restart: same notes on disk, same sidecar on disk, a fresh index.
    second_embedder = FakeEmbedder()
    reborn = VectorIndex(client=second_embedder, store=Store(jarvis.config_dir, STORAGE_KEY))
    revived = MemoryStore(jarvis, vectors=reborn)
    await revived.async_load()

    documents = [t for t in second_embedder.seen if not t.startswith(QUERY_PREFIX)]
    assert documents == [], (
        f"re-embedded {len(documents)} note(s) that were already in the sidecar: "
        f"{documents}. The sidecar is never read back."
    )
    # And it still answers, from the vectors it loaded rather than from nothing.
    assert await revived.async_semantic_ids("where do we keep the caffeine")


# --- per-model prefixes and floors (M33) -----------------------------------
def test_each_model_family_gets_its_own_prefixes():
    """A model trained with task prefixes is measurably worse without them."""
    from aura.integrations.memory.vectors import prefixes_for

    assert prefixes_for("nomic-embed-text") == ("search_document: ", "search_query: ")
    document, query = prefixes_for("BAAI/bge-small-en-v1.5")
    assert document == "" and query.startswith("Represent this sentence")
    assert prefixes_for("intfloat/e5-small-v2") == ("passage: ", "query: ")
    # And a model nobody recognises gets nothing, which is right for the
    # general case and harmless for one that wanted something.
    assert prefixes_for("some-new-embedder") == ("", "")


def test_the_similarity_floor_belongs_to_the_model():
    """0.62 was tuned for nomic and threw away five of six bge paraphrases.

    bge-small put the same six queries at 0.450-0.652 and ranked all six
    correctly; a constant floor turned a working model into an empty search.
    """
    from aura.integrations.memory.vectors import floor_for

    assert floor_for("nomic-embed-text") == 0.62
    assert floor_for("BAAI/bge-small-en-v1.5") < 0.45
    assert floor_for("something-unknown") == 0.62


def test_the_index_uses_its_models_floor_not_the_constant():
    from aura.integrations.memory.vectors import VectorIndex

    assert VectorIndex(model="BAAI/bge-small-en-v1.5").floor < 0.45
    assert VectorIndex(model="nomic-embed-text").floor == 0.62


# --- qwen3-embedding on llama-swap (T-0684) --------------------------------
QWEN = "qwen3-embedding-0.6b"
SCALES = Path(__file__).resolve().parents[2] / "bench/results/embed-rerank-scales.json"


def test_qwen_embeds_documents_bare_and_puts_its_own_instruction_on_the_query():
    """Qwen3-Embedding is trained with an instruction on the query only; the
    T-0513 bench scored it (recall@1 0.924) with exactly this one."""
    from aura.integrations.memory.vectors import prefixes_for

    for name in (QWEN, "Qwen/Qwen3-Embedding-0.6B"):
        document, query = prefixes_for(name)
        assert document == ""
        assert query == "Instruct: Given a web search query, retrieve relevant passages that answer the query\nQuery:"


def test_qwens_floor_keeps_its_worst_right_answer_and_drops_most_wrong_ones():
    """Measured on qwen's own scale (embed-rerank-bench.py --scales): bge's
    floor would be a guess for a model whose unrelated notes sit near 0.22."""
    import json

    from aura.integrations.memory.vectors import floor_for

    measured = json.loads(SCALES.read_text())["llama-swap"]
    assert measured["model"] == QWEN
    assert floor_for(QWEN) <= measured["right"]["min"]
    assert floor_for(QWEN) == measured["floor"]
    assert measured["any_wrong"]["kept"] < 0.15


def test_the_embedding_key_reaches_the_client_as_a_bearer(jarvis):
    """llama-swap refuses an unkeyed request, and a refused embed is silent:
    recall would quietly fall back to keyword search."""
    from aura.integrations.memory import _embedding_client

    keyed = _embedding_client(jarvis, {"embedding_url": "http://127.0.0.1:9000/v1",
                                       "embedding_model": QWEN, "embedding_key": "swap-key"})
    assert (keyed.request_headers() or {}).get("Authorization") == "Bearer swap-key"  # pragma: allowlist secret
    tei = _embedding_client(jarvis, {"embedding_url": "http://127.0.0.1:7997/v1", "embedding_key": ""})
    assert "Authorization" not in (tei.request_headers() or {})


async def test_switching_to_qwen_re_embeds_every_stored_note(jarvis):
    """The notes are the source of truth and the sidecar is derived: a model
    change discards it and the next index pass embeds every note again, bare."""

    class Disk:
        saved: dict = {}

        async def load(self):
            return self.saved

        async def save(self, data):
            self.saved = data

    disk = Disk()
    before = MemoryStore(jarvis, vectors=VectorIndex(client=FakeEmbedder(), model="BAAI/bge-small-en-v1.5", store=disk))
    await before.async_load()
    for text in ("the good coffee is in the left cupboard", "I am allergic to penicillin"):
        await _add(before, text)
    assert disk.saved["model"] == "BAAI/bge-small-en-v1.5"

    qwen = FakeEmbedder()
    after = MemoryStore(jarvis, vectors=VectorIndex(client=qwen, model=QWEN, store=disk))
    await after.async_load()
    await after._async_reindex()

    assert disk.saved["model"] == QWEN
    assert len(after.vectors) == len(after.entries) == 2
    assert sorted(qwen.seen) == sorted(e.text for e in after.entries), "notes go to qwen bare"
