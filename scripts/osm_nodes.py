"""Where the OSM readers keep every node's position while they read an extract (T-1270).

pyosmium's default index is in memory, measured at 4.5 times the extract's size (581 MB for a
128 MB one), so a country-sized extract killed the worker. Past ``THRESHOLD`` the readers use
``sparse_file_array`` instead: the same lookups, backed by a file in ``<data>/tmp`` that is removed
when the read ends. Slower than memory, so a small extract stays in memory.

Does not cover: the pass that builds areas (osm_buildings.py, osm_places.py) also holds the ways of
every multipolygon in memory, which a file-backed node index does not shrink; a read killed so hard
that its ``finally`` never runs leaves its index file until the next read of the same extract.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from pathlib import Path
from typing import Any

#: Extract size (bytes) above which the node index goes to disk: 256 MB is about 1.2 GB in memory.
THRESHOLD = 256 * 2**20
#: pyosmium's own default (``FileProcessor.with_locations()``), so a small read is as it was.
IN_MEMORY = "flex_mem"


def storage(pbf: str | Path, tmp: str | Path | None = None) -> str:
    """The ``with_locations`` storage for this extract: in memory, or a file under [tmp].

    [tmp] defaults to ``<data>/tmp``: an extract lives in ``<data>/regions`` or ``<data>/valhalla``.
    The file is named for the extract, so two reads of different ones never share an index.
    """
    pbf = Path(pbf)
    if pbf.stat().st_size <= THRESHOLD:
        return IN_MEMORY
    return f"sparse_file_array,{Path(tmp) if tmp else pbf.resolve().parent.parent / 'tmp'}/{pbf.name}.nodes"


@contextlib.contextmanager
def node_index(pbf: str | Path, tmp: str | Path | None = None) -> Iterator[str]:
    """Yield the storage to read [pbf] with; a file index is made fresh and removed after, error or not."""
    where = storage(pbf, tmp)
    path = None if where == IN_MEMORY else Path(where.split(",", 1)[1])
    if path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.unlink(missing_ok=True)  # a killed read's leftovers are not a valid index
    try:
        yield where
    finally:
        if path:
            path.unlink(missing_ok=True)


def with_index(proc: Any, where: str, always: bool = True) -> Any:
    """``proc.with_locations(where)``. A reader that only builds areas passes ``always=False``: pyosmium
    gives areas their own in-memory index, so it is touched only to put the index on disk."""
    return proc.with_locations(where) if always or where != IN_MEMORY else proc
