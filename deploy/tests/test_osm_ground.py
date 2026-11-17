"""scripts/osm_ground.py: the ground's height per pack cell, from elevation tiles (T-1105).

Tiles here are made up in memory; nothing is downloaded. Does not cover the real bucket
(scripts/map-region.sh fetches from it) or heights finer than a tile's samples.
"""

from __future__ import annotations

import gzip
import importlib.util
import json
import sys
from array import array
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
_spec = importlib.util.spec_from_file_location("osm_ground", SCRIPTS / "osm_ground.py")
ground = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ground)


def raw(n: int, height) -> bytes:
    """A tile of n by n samples, north row first, big-endian: height(row, col)."""
    a = array("h", [height(r, c) for r in range(n) for c in range(n)])
    if sys.byteorder == "little":
        a.byteswap()
    return a.tobytes()


def test_a_tile_is_named_by_its_south_west_corner():
    assert ground.tile_name(46, 8) == "N46E008"
    assert ground.tile_name(-1, -78) == "S01W078"
    assert ground.tile_name(0, 0) == "N00E000"


def test_a_height_is_read_between_its_samples_north_row_first():
    # 100 m along the north edge falling to 0 along the south one; flat west to east.
    t = ground.Tile(raw(5, lambda r, c: 100 - 25 * r))
    assert t.at(1.0, 0.3) == 100 and t.at(0.0, 0.9) == 0
    assert t.at(0.5, 0.5) == 50
    assert t.at(0.125, 0.0) == pytest.approx(12.5)


def test_a_void_sample_is_left_out_and_an_all_void_tile_is_sea_level():
    holed = ground.Tile(raw(3, lambda r, c: ground._VOID if (r, c) == (0, 0) else 40))
    assert holed.at(0.9, 0.1) == 40
    assert ground.Tile(raw(3, lambda r, c: ground._VOID)).at(0.5, 0.5) == 0


def test_a_file_that_is_not_square_is_refused():
    with pytest.raises(ValueError):
        ground.Tile(b"\x00\x01" * 7)


def test_each_cell_has_a_grid_whose_edges_its_neighbours_share():
    # A slope rising 1 m per sample eastwards, 3601 samples: 36 m across one 0.01 degree cell.
    slope = ground.Tile(raw(101, lambda r, c: c * 36))
    got = ground.cells(8.0, 46.0, 8.015, 46.005, lambda lat0, lon0: slope if (lat0, lon0) == (46, 8) else None)
    assert [g["cell"] for g in got] == [[800, 4600], [801, 4600]]
    west, east = (g["h"] for g in got)
    n = ground.GRID
    assert len(west) == n * n
    assert west[0] == 0 and west[n - 1] == 36 and east[0] == 36 and east[n - 1] == 72
    assert [west[r * n + n - 1] for r in range(n)] == [east[r * n] for r in range(n)]


def test_where_there_is_no_tile_the_ground_is_sea_level():
    got = ground.cells(8.0, 46.0, 8.005, 46.005, lambda lat0, lon0: None)
    assert set(got[0]["h"]) == {0}


def test_a_tile_is_downloaded_once_and_kept(tmp_path, monkeypatch):
    asked = []

    class Answer:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return gzip.compress(raw(3, lambda r, c: 7))

    def urlopen(url, timeout):
        asked.append(url)
        return Answer()

    monkeypatch.setattr(ground.urllib.request, "urlopen", urlopen)
    assert ground.fetch(46, 8, tmp_path).at(0.5, 0.5) == 7
    assert ground.fetch(46, 8, tmp_path).at(0.5, 0.5) == 7
    assert asked == ["https://s3.amazonaws.com/elevation-tiles-prod/skadi/N46/N46E008.hgt.gz"]


def test_a_run_that_cannot_fetch_writes_nothing_and_says_flat(tmp_path, monkeypatch, capsys):
    def urlopen(url, timeout):
        raise OSError("unreachable")

    monkeypatch.setattr(ground.urllib.request, "urlopen", urlopen)
    out = tmp_path / "ground.jsonl"
    assert ground.main(str(out), 8.0, 46.0, 8.005, 46.005) == 3
    assert not out.exists() and "built flat" in capsys.readouterr().err


def test_a_download_that_is_not_a_tile_is_not_kept_and_a_refusal_is_not_the_sea(tmp_path, monkeypatch):
    class Cut:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return gzip.compress(raw(3, lambda r, c: 7))[:-9]

    monkeypatch.setattr(ground.urllib.request, "urlopen", lambda url, timeout: Cut())
    with pytest.raises((EOFError, ground.zlib.error, ValueError)):
        ground.fetch(46, 8, tmp_path)
    assert list(tmp_path.iterdir()) == []
    assert ground.main(str(tmp_path / "g.jsonl"), 8.0, 46.0, 8.005, 46.005, str(tmp_path)) == 3

    def refuse(code):
        def urlopen(url, timeout):
            raise ground.urllib.error.HTTPError(url, code, "no", None, None)
        return urlopen

    monkeypatch.setattr(ground.urllib.request, "urlopen", refuse(404))
    assert ground.fetch(46, 8, tmp_path) is None
    monkeypatch.setattr(ground.urllib.request, "urlopen", refuse(403))
    with pytest.raises(OSError):
        ground.fetch(46, 8, tmp_path)


def test_a_run_writes_one_line_per_cell(tmp_path, monkeypatch):
    monkeypatch.setattr(ground, "fetch", lambda lat0, lon0, cache: ground.Tile(raw(3, lambda r, c: 12)))
    out = tmp_path / "ground.jsonl"
    assert ground.main(str(out), 8.0, 46.0, 8.015, 46.015) == 0
    lines = [json.loads(ln) for ln in out.read_text().splitlines()]
    assert len(lines) == 4 and all(set(ln["h"]) == {12} for ln in lines)


def test_a_body_that_unpacks_past_the_largest_tile_is_refused_t1127():
    bomb = gzip.compress(bytes(ground.MAX_TILE_BYTES + 2))
    assert len(bomb) < 100_000
    with pytest.raises(ValueError, match="past the largest"):
        ground._unpack(bomb)
    assert ground._unpack(gzip.compress(raw(3, lambda r, c: 7))) == raw(3, lambda r, c: 7)
