"""The shared files, searchable by meaning (T-1249).

The share is a WebDAV server only the Hermes files plugin reaches; the embedder and
`VectorIndex` live here. So the plugin fetches the text and this module indexes it: the
plugin sends the share's whole current listing (path -> etag) and the text of some files
that changed, the house embeds the passages with the client memory already uses, and a
search answers file and passage. A second index with its own two files under `.storage`,
never mixed into memory's notes: a file passage must not come back as something the
owner told the house, and memory's pruning must not eat it.

Does not cover: anything that is not text (the plugin skips PDFs, office files and
anything over its size cap); a share bigger than a few thousand passages (the search is
`VectorIndex`'s brute-force scan); freshness between searches (the plugin syncs when it
is asked to search, nothing runs in the background). The passages are kept on the house's
disk in the clear, like memory's notes.
"""
from __future__ import annotations

import asyncio
from typing import Any

from .vectors import VectorIndex

#: About a screenful: long enough to answer from, short enough that five of them fit a reply.
CHUNK_CHARS = 1200
#: What one search answers with.
RESULTS = 5
#: A listing longer than this is refused whole: the index is a brute-force scan.
MAX_FILES = 5000
#: And the passages it holds. ponytail: `VectorIndex.async_search` scans every vector in
#: Python on the event loop, about a second at this size; past it the index says it is full
#: and takes no new file. A real ANN index (or the scan in a thread) lifts the ceiling.
MAX_PASSAGES = 5000
#: Passages per embedding request: a 256 KB file is some 200, more than a server takes at once.
EMBED_BATCH = 32


def chunks(text: str, size: int = CHUNK_CHARS) -> list[str]:
    """Paragraphs packed into passages of at most *size* characters; a longer paragraph is cut."""
    out: list[str] = []
    current = ""
    for para in (p.strip() for p in text.replace("\r\n", "\n").split("\n\n")):
        while len(para) > size:
            if current:
                out.append(current)
                current = ""
            out.append(para[:size])
            para = para[size:].strip()
        if not para:
            continue
        if current and len(current) + 2 + len(para) > size:
            out.append(current)
            current = para
        else:
            current = f"{current}\n\n{para}" if current else para
    if current:
        out.append(current)
    return out


class FilesIndex:
    """path -> (etag, passages), plus the vectors for those passages."""

    def __init__(self, vectors: VectorIndex, store: Any = None) -> None:
        self.vectors = vectors
        self._store = store
        self._files: dict[str, dict[str, Any]] = {}
        # One sync at a time: two searches arriving together would otherwise both embed the
        # same file and race on the two files this writes.
        self._lock = asyncio.Lock()

    async def async_load(self) -> None:
        await self.vectors.async_load()
        data = await self._store.load() if self._store is not None else None
        files = (data or {}).get("files")
        if isinstance(files, dict):
            self._files = {
                str(path): {"etag": str(row.get("etag") or ""), "chunks": [str(c) for c in row.get("chunks") or []]}
                for path, row in files.items() if isinstance(row, dict)
            }
        # The vectors are rebuilt whole when the embedding model changes (`VectorIndex.async_load`);
        # a file whose passages lost their vectors is then stale, whatever its etag says.
        for path in [p for p, row in self._files.items() if not self._indexed(p, row["chunks"])]:
            del self._files[path]

    def _indexed(self, path: str, parts: list[str]) -> bool:
        return all(self.vectors.is_current(f"{path}#{n}", part) for n, part in enumerate(parts))

    def stale(self, listing: dict[str, str]) -> list[str]:
        """The listed paths whose text the house does not hold at that etag."""
        return sorted(p for p, etag in listing.items() if self._files.get(p, {}).get("etag") != etag)

    async def async_sync(self, listing: dict[str, str], files: dict[str, str]) -> dict[str, Any]:
        """Bring the index to *listing* (every text file on the share now, path -> etag), using the
        text in *files* for the ones sent. A path gone from the listing is dropped with its passages.
        Answers how far along the index is and which paths it still needs the text of."""
        if len(listing) > MAX_FILES:
            raise ValueError(f"the share lists more than {MAX_FILES} text files; the index does not take that many")
        async with self._lock:
            before = dict(self._files)
            # A failed embed leaves `VectorIndex` alone for a minute (`RETRY_AFTER_S`), which
            # suits notes written one at a time and not this: a pass over the share is asked
            # for on purpose, so it asks the embedder now, whatever happened a moment ago.
            self.vectors.retry()
            full = False
            for path in [p for p in self._files if p not in listing]:
                del self._files[path]
            for path, text in files.items():
                if path not in listing:
                    continue
                parts = chunks(text)
                held = sum(len(row["chunks"]) for p, row in self._files.items() if p != path)
                if held + len(parts) > MAX_PASSAGES:
                    full = True
                    continue
                items = [(f"{path}#{n}", part) for n, part in enumerate(parts)]
                for start in range(0, len(items), EMBED_BATCH):
                    await self.vectors.async_index(items[start:start + EMBED_BATCH])
                if self._indexed(path, parts):
                    self._files[path] = {"etag": listing[path], "chunks": parts}
                else:
                    # The embedder gave out part way: the passages that did get a vector would
                    # otherwise be answered with the old text kept here. Dropped whole; the
                    # prune below takes its vectors and the file stays stale for the next sync.
                    self._files.pop(path, None)
            # Every file again, not only the ones sent: a change of embedding dimension clears
            # the vectors of all of them (`VectorIndex.async_index`).
            for path in [p for p, row in self._files.items() if not self._indexed(p, row["chunks"])]:
                del self._files[path]
            pruned = self.vectors.prune(f"{p}#{n}" for p, row in self._files.items() for n in range(len(row["chunks"])))
            if pruned or before != self._files:
                await self.vectors.async_save()
                if self._store is not None:
                    await self._store.save({"files": self._files})
            stale = self.stale(listing)
            if full or sum(len(row["chunks"]) for row in self._files.values()) >= MAX_PASSAGES:
                # Full: ask only for files already held, so their edits still land and the
                # plugin stops reading the ones that will never fit.
                full, stale = True, [p for p in stale if p in before]
            return {"available": self.vectors.enabled, "files": len(listing),
                    "indexed": len(listing) - len(self.stale(listing)), "stale": stale, "full": full}

    async def async_search(self, query: str, limit: int = RESULTS) -> list[dict[str, Any]]:
        """The best passages at or above the model's floor, best first: path, passage, score."""
        scores = await self.vectors.async_search(query)
        hits = []
        for entry_id, score in sorted(scores.items(), key=lambda kv: -kv[1]):
            path, _, n = entry_id.rpartition("#")
            parts = self._files.get(path, {}).get("chunks") or []
            if n.isdigit() and int(n) < len(parts):
                hits.append({"path": path, "passage": parts[int(n)], "score": round(score, 3)})
            if len(hits) >= limit:
                break
        return hits
