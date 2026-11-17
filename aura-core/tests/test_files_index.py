"""The shared files' index in the house (T-1249): sync by etag, search by meaning."""
from __future__ import annotations

import pytest

from aura.integrations.memory import files as files_module
from aura.integrations.memory.files import FilesIndex, chunks
from aura.integrations.memory.vectors import VectorIndex
from aura.store import Store

CONCEPTS = {
    "coffee": ("coffee", "espresso", "beans"),
    "boiler": ("boiler", "heating", "radiator"),
    "bicycle": ("bicycle", "puncture", "tyre"),
}


class FakeEmbedder:
    def __init__(self) -> None:
        self.calls = 0
        self.down = False
        self.fail_from = 0  # the call number from which embed() raises; 0 is never

    async def embed(self, texts, model=None):
        self.calls += 1
        if self.down or (self.fail_from and self.calls >= self.fail_from):
            raise RuntimeError("no embedding model")
        return [[float(sum(w in t.lower() for w in words)) + 0.01 for words in CONCEPTS.values()] for t in texts]


def _index(tmp_path, embedder) -> FilesIndex:
    vectors = VectorIndex(client=embedder, store=Store(tmp_path, "files-vectors", 1))
    return FilesIndex(vectors, Store(tmp_path, "files-index", 1))


def test_files_index_chunks_pack_paragraphs_and_cut_a_long_one() -> None:
    assert chunks("a\n\nb", size=10) == ["a\n\nb"]
    assert chunks("aaaa\n\nbbbb\n\ncccc", size=10) == ["aaaa\n\nbbbb", "cccc"]
    assert chunks("x" * 25, size=10) == ["x" * 10, "x" * 10, "x" * 5]
    assert chunks("  \n\n \r\n\r\n") == []


async def test_files_index_sync_then_search_answers_path_and_passage(tmp_path) -> None:
    index = _index(tmp_path, FakeEmbedder())
    listing = {"notes/house.md": "e1", "notes/bike.md": "e2"}
    state = await index.async_sync(listing, {})
    assert state["stale"] == ["notes/bike.md", "notes/house.md"] and state["indexed"] == 0
    state = await index.async_sync(listing, {
        "notes/house.md": "The boiler is serviced every autumn.\n\nThe radiator key is in the hall.",
        "notes/bike.md": "A puncture kit is under the saddle.",
        "not/listed.md": "boiler boiler boiler",
    })
    assert state == {"available": True, "files": 2, "indexed": 2, "stale": [], "full": False}
    hits = await index.async_search("when is the heating boiler serviced")
    assert hits[0]["path"] == "notes/house.md" and "boiler" in hits[0]["passage"]
    assert all(h["path"] != "not/listed.md" for h in hits)


async def test_files_index_embeds_an_unchanged_file_once(tmp_path) -> None:
    embedder = FakeEmbedder()
    index = _index(tmp_path, embedder)
    listing = {"a.md": "e1"}
    await index.async_sync(listing, {"a.md": "The boiler."})
    calls = embedder.calls
    await index.async_sync(listing, {"a.md": "The boiler."})
    assert embedder.calls == calls


async def test_files_index_edit_replaces_the_old_passage(tmp_path) -> None:
    index = _index(tmp_path, FakeEmbedder())
    await index.async_sync({"a.md": "e1"}, {"a.md": "The boiler is in the loft.\n\n" + "The radiator key. " * 80})
    state = await index.async_sync({"a.md": "e2"}, {})
    assert state["stale"] == ["a.md"]
    await index.async_sync({"a.md": "e2"}, {"a.md": "The bicycle has a puncture."})
    assert await index.async_search("boiler heating") == []
    hits = await index.async_search("bicycle puncture")
    assert [h["path"] for h in hits] == ["a.md"] and "puncture" in hits[0]["passage"]


async def test_files_index_removed_file_is_dropped(tmp_path) -> None:
    index = _index(tmp_path, FakeEmbedder())
    await index.async_sync({"a.md": "e1", "b.md": "e1"}, {"a.md": "The boiler.", "b.md": "The bicycle."})
    state = await index.async_sync({"b.md": "e1"}, {})
    assert state["files"] == 1 and state["indexed"] == 1
    assert await index.async_search("boiler heating") == []
    assert len(index.vectors) == 1


async def test_files_index_survives_a_restart_and_reads_nothing_twice(tmp_path) -> None:
    embedder = FakeEmbedder()
    await _index(tmp_path, embedder).async_sync({"a.md": "e1"}, {"a.md": "The boiler."})
    again = _index(tmp_path, embedder)
    await again.async_load()
    assert again.stale({"a.md": "e1"}) == []
    assert (await again.async_search("boiler"))[0]["path"] == "a.md"


async def test_files_index_with_the_embedder_down_leaves_the_file_stale(tmp_path) -> None:
    embedder = FakeEmbedder()
    embedder.down = True
    index = _index(tmp_path, embedder)
    state = await index.async_sync({"a.md": "e1"}, {"a.md": "The boiler."})
    assert state["available"] is False and state["stale"] == ["a.md"] and state["indexed"] == 0
    assert await index.async_search("boiler") == []


async def test_files_index_recovers_once_the_embedder_is_back(tmp_path) -> None:
    embedder = FakeEmbedder()
    embedder.down = True
    index = _index(tmp_path, embedder)
    await index.async_sync({"a.md": "e1"}, {"a.md": "The boiler."})
    embedder.down = False
    state = await index.async_sync({"a.md": "e1"}, {"a.md": "The boiler."})
    assert state["available"] is True and state["indexed"] == 1


async def test_files_index_edit_half_embedded_keeps_no_old_passage(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(files_module, "EMBED_BATCH", 1)
    embedder = FakeEmbedder()
    index = _index(tmp_path, embedder)
    await index.async_sync({"a.md": "e1"}, {"a.md": "The boiler is in the loft."})
    embedder.fail_from = embedder.calls + 2  # the edit's first passage embeds, its second does not
    state = await index.async_sync({"a.md": "e2"}, {"a.md": "x" * 1200 + "\n\n" + "The bicycle has a puncture."})
    assert state["stale"] == ["a.md"] and state["indexed"] == 0
    assert len(index.vectors) == 0
    embedder.fail_from = 0
    assert await index.async_search("boiler heating") == []


async def test_files_index_edit_to_fewer_passages_drops_the_extra_vectors(tmp_path) -> None:
    index = _index(tmp_path, FakeEmbedder())
    await index.async_sync({"a.md": "e1"}, {"a.md": "\n\n".join(["The boiler. " * 90] * 3)})
    assert len(index.vectors) == 3
    await index.async_sync({"a.md": "e2"}, {"a.md": "The bicycle."})
    assert len(index.vectors) == 1


async def test_files_index_full_takes_no_new_file_and_stops_asking_for_it(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(files_module, "MAX_PASSAGES", 1)
    index = _index(tmp_path, FakeEmbedder())
    listing = {"a.md": "e1", "b.md": "e1"}
    state = await index.async_sync(listing, {"a.md": "The boiler.", "b.md": "The bicycle."})
    assert state["full"] is True and state["indexed"] == 1 and state["stale"] == []
    # An edit of a file it holds is still asked for.
    assert (await index.async_sync({"a.md": "e2", "b.md": "e1"}, {}))["stale"] == ["a.md"]


async def test_files_index_refuses_a_listing_it_cannot_scan(tmp_path) -> None:
    index = _index(tmp_path, FakeEmbedder())
    with pytest.raises(ValueError):
        await index.async_sync({f"{n}.md": "e" for n in range(5001)}, {})


def test_files_index_routes_need_the_token_and_sync_then_search(tmp_path, monkeypatch) -> None:
    import asyncio

    from fastapi.testclient import TestClient

    from aura.api.server import create_app
    from aura.auth import DATA_AUTH, ENV_TOKEN, AuthManager
    from aura.core import Jarvis

    monkeypatch.delenv(ENV_TOKEN, raising=False)
    j = Jarvis(tmp_path)
    j.data[DATA_AUTH] = AuthManager()
    _info, token = asyncio.run(j.data[DATA_AUTH].create_token("test-client"))
    auth = {"Authorization": f"Bearer {token}"}
    listing = {"a.md": "e1"}
    with TestClient(create_app(j, static_dir=tmp_path / "no-www")) as client:
        assert client.post("/api/files_index/sync", json={"listing": listing}).status_code == 401
        assert client.post("/api/files_index/search", json={"query": "boiler"}).status_code == 401
        # No embedding model: said, and nothing is asked for.
        assert client.post("/api/files_index/sync", json={"listing": listing}, headers=auth).json() == {
            "available": False, "files": 1, "indexed": 0, "stale": [], "full": False}
        assert client.post("/api/files_index/search", json={"query": "boiler"}, headers=auth).json() == {"results": []}
        j.data["files_index"] = _index(tmp_path, FakeEmbedder())
        assert client.post("/api/files_index/sync", json={"listing": listing}, headers=auth).json()["stale"] == ["a.md"]
        done = client.post("/api/files_index/sync", json={"listing": listing, "files": {"a.md": "The boiler."}}, headers=auth)
        assert done.json() == {"available": True, "files": 1, "indexed": 1, "stale": [], "full": False}
        hits = client.post("/api/files_index/search", json={"query": "boiler"}, headers=auth).json()["results"]
        assert hits[0]["path"] == "a.md" and hits[0]["passage"] == "The boiler."
        assert client.post("/api/files_index/sync", json={"listing": ["a.md"]}, headers=auth).status_code == 400
        assert client.post("/api/files_index/sync", json={"listing": {"a.md": 1}}, headers=auth).status_code == 400
