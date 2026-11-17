#!/usr/bin/env python3
"""Ground heights for a region's pack cells, from public elevation tiles (T-1105).

    python3 osm_ground.py OUT.jsonl WEST SOUTH EAST NORTH [CACHE_DIR]

One line per 0.01 degree cell the box touches: ``{"cell": [cx, cy], "h": [...]}``, where
the cell's south-west corner is (cx / 100, cy / 100) degrees and ``h`` is ``GRID`` by
``GRID`` whole metres above sea level, rows south to north, each west to east, the first
and last on the cell's edges (so two neighbours agree along the edge they share).
``pack.py --ground`` puts them in the pack and the car's map stands its roads on them.

The source is the keyless 1 degree "skadi" tiles (SRTM-shaped: gzip'd big-endian int16,
3601 or 1201 samples a side, north row first, -32768 where nothing was measured). A tile
is downloaded once into CACHE_DIR (default: beside OUT, ``terrain/``).

Stdlib only. Not covered: anything finer than the source's 30 m (a cutting, a kerb, a
bridge deck: a bridge is drawn on the valley floor); a tile that cannot be fetched fails
the whole run, writing nothing, so the pack is built flat rather than with a cliff where
the missing tile starts; a place with no tile at all (the bucket answers 404) is 0 m; a
tile already in the cache that no longer reads fails every run until it is deleted.
"""

from __future__ import annotations

import gzip
import io
import http.client
import json
import math
import sys
import urllib.error
import urllib.request
import zlib
from array import array
from pathlib import Path

GRID = 17  # samples a side of a cell: about 65 m apart, twice the source's spacing at most
URL = "https://s3.amazonaws.com/elevation-tiles-prod/skadi/{band}/{name}.hgt.gz"
_VOID = -32768


def tile_name(lat0: int, lon0: int) -> str:
    """The tile whose south-west corner is (lat0, lon0): ``N46E008``, ``S01W078``."""
    return f"{'N' if lat0 >= 0 else 'S'}{abs(lat0):02d}{'E' if lon0 >= 0 else 'W'}{abs(lon0):03d}"


#: The largest real tile: 3601 x 3601 big-endian int16 (one arc second).
MAX_TILE_BYTES = 3601 * 3601 * 2


def _unpack(data: bytes) -> bytes:
    """The tile's bytes. A body that unpacks past the largest tile is refused before it is held
    (T-1127): a few kilobytes of gzip can unpack to gigabytes, and this runs inside the pack build."""
    raw = gzip.GzipFile(fileobj=io.BytesIO(data)).read(MAX_TILE_BYTES + 1)
    if len(raw) > MAX_TILE_BYTES:
        raise ValueError("not a tile: it unpacks past the largest tile's size")
    return raw


class Tile:
    """One degree of heights. ``at(lat, lon)`` takes the fraction of a degree past its corner."""

    def __init__(self, raw: bytes) -> None:
        a = array("h")
        a.frombytes(raw)
        if sys.byteorder == "little":
            a.byteswap()
        self.n = math.isqrt(len(a))
        if self.n * self.n != len(a) or self.n < 2:
            raise ValueError(f"not a square tile: {len(a)} samples")
        self.a = a

    def at(self, lat: float, lon: float) -> float:
        """Metres at the point, between its four samples; a void sample is left out, all void is 0."""
        y, x = (1.0 - lat) * (self.n - 1), lon * (self.n - 1)
        r, c = min(int(y), self.n - 2), min(int(x), self.n - 2)
        fy, fx = y - r, x - c
        total = weight = 0.0
        for dr, dc, w in ((0, 0, (1 - fy) * (1 - fx)), (0, 1, (1 - fy) * fx), (1, 0, fy * (1 - fx)), (1, 1, fy * fx)):
            v = self.a[(r + dr) * self.n + c + dc]
            if v != _VOID and w > 0:
                total, weight = total + v * w, weight + w
        return total / weight if weight else 0.0


def fetch(lat0: int, lon0: int, cache: Path) -> Tile | None:
    """The tile, from [cache] or the network; None where there is none (open sea). Raises OSError
    when it cannot be had, and ValueError when what came is not a tile."""
    name = tile_name(lat0, lon0)
    path = cache / f"{name}.hgt.gz"
    if not path.exists():
        cache.mkdir(parents=True, exist_ok=True)
        part = path.with_name(path.name + ".part")
        try:
            with urllib.request.urlopen(URL.format(band=name[:3], name=name), timeout=120) as r:  # noqa: S310
                data = r.read()
        except urllib.error.HTTPError as e:
            # Only "not there" is sea. A 403 is a proxy or a filter in the way: taking it for
            # sea would write 0 m beside a real tile, a cliff along the tile's edge.
            if e.code == 404:
                return None
            raise
        # Kept only once it reads as a tile: a cut or corrupt download left in the cache
        # would fail every later run, and the pack would stay flat for good.
        tile = Tile(_unpack(data))
        part.write_bytes(data)
        part.replace(path)
        return tile
    return Tile(_unpack(path.read_bytes()))


def cells(w: float, s: float, e: float, n: float, tile) -> list[dict]:
    """The lines for the box. [tile] is ``(lat0, lon0) -> Tile | None``."""
    held: dict[tuple[int, int], Tile | None] = {}

    def metres(lat: float, lon: float) -> int:
        key = (math.floor(lat), math.floor(lon))
        if key not in held:
            held[key] = tile(*key)
        t = held[key]
        return round(t.at(lat - key[0], lon - key[1])) if t else 0

    out = []
    for cy in range(math.floor(s * 100), math.floor(n * 100) + 1):
        for cx in range(math.floor(w * 100), math.floor(e * 100) + 1):
            out.append({"cell": [cx, cy], "h": [
                metres((cy + j / (GRID - 1)) / 100, (cx + i / (GRID - 1)) / 100)
                for j in range(GRID) for i in range(GRID)]})
    return out


def main(out: str, w: float, s: float, e: float, n: float, cache: str = "") -> int:
    dest = Path(out)
    store = Path(cache) if cache else dest.parent / "terrain"
    try:
        lines = cells(w, s, e, n, lambda lat0, lon0: fetch(lat0, lon0, store))
    except (OSError, ValueError, EOFError, zlib.error, http.client.HTTPException) as err:
        print(f"ground: no heights ({type(err).__name__}); the pack is built flat", file=sys.stderr)
        return 3
    part = dest.with_name(dest.name + ".part")
    with part.open("w") as f:
        for line in lines:
            f.write(json.dumps(line, separators=(",", ":")) + "\n")
    part.replace(dest)
    print(f"{dest.name}: {len(lines)} cells")
    return 0


if __name__ == "__main__":
    if len(sys.argv) not in (6, 7):
        print(__doc__, file=sys.stderr)
        sys.exit(2)
    sys.exit(main(sys.argv[1], *(float(v) for v in sys.argv[2:6]), *sys.argv[6:]))
