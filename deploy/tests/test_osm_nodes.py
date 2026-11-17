"""scripts/osm_nodes.py and the four readers' use of it: where the node index lives (T-1270).

pyosmium is not installed where the tests run, so the readers run against a fake ``osmium`` that
records the storage it is given; what is pinned is the choice by size, the file's place and its
removal. Not covered: pyosmium accepting ``sparse_file_array,<path>`` (its documented storage
string, from its docs, not run here), and a real country-sized read (docs/runbooks/maps.md).
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"


class FakeProcessor:
    made: list["FakeProcessor"] = []

    def __init__(self, pbf):
        self.calls = []
        self.during = None  # whether the index file existed while the read ran
        FakeProcessor.made.append(self)

    def with_filter(self, *a):
        return self

    def with_locations(self, storage="flex_mem"):
        self.calls.append(("locations", storage))
        self.storage = storage
        return self

    def with_areas(self, *a):
        self.calls.append(("areas",))
        return self

    def __iter__(self):
        if self.calls and self.calls[0][0] == "locations" and "," in self.calls[0][1]:
            self.during = Path(self.calls[0][1].split(",", 1)[1]).parent.is_dir()
        return iter(())


@pytest.fixture
def fake_osmium(monkeypatch):
    mod = types.ModuleType("osmium")
    mod.FileProcessor = FakeProcessor
    mod.InvalidLocationError = ValueError
    mod.filter = types.ModuleType("osmium.filter")
    mod.filter.KeyFilter = lambda *a: None
    mod.geom = types.SimpleNamespace(GeoJSONFactory=lambda: None)
    monkeypatch.setitem(sys.modules, "osmium", mod)
    monkeypatch.setitem(sys.modules, "osmium.filter", mod.filter)
    monkeypatch.syspath_prepend(str(SCRIPTS))
    FakeProcessor.made = []
    sys.modules.pop("osm_nodes", None)
    return mod


def _load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _extract(tmp_path, size):
    pbf = tmp_path / "data" / "regions" / "europe-monaco.osm.pbf"
    pbf.parent.mkdir(parents=True)
    pbf.write_bytes(b"x" * size)
    return pbf


def test_node_index_is_in_memory_for_a_small_extract_and_a_file_in_the_data_tmp_for_a_big_one(tmp_path, monkeypatch):
    nodes = _load("osm_nodes")
    # The default threshold is the worker's memory budget: a state's extract stays in memory.
    assert nodes.THRESHOLD == 256 * 2**20
    monkeypatch.setattr(nodes, "THRESHOLD", 100)
    small = _extract(tmp_path, 100)
    assert nodes.storage(small) == "flex_mem"
    (tmp_path / "data" / "regions" / "big.osm.pbf").write_bytes(b"x" * 101)
    where = nodes.storage(tmp_path / "data" / "regions" / "big.osm.pbf")
    assert where == f"sparse_file_array,{tmp_path / 'data' / 'tmp'}/big.osm.pbf.nodes"


def test_node_index_file_is_made_clean_and_removed_after_even_when_the_read_fails(tmp_path, monkeypatch):
    nodes = _load("osm_nodes")
    monkeypatch.setattr(nodes, "THRESHOLD", 10)
    pbf = _extract(tmp_path, 50)
    left = tmp_path / "data" / "tmp" / "europe-monaco.osm.pbf.nodes"
    left.parent.mkdir()
    left.write_bytes(b"a killed read's index")
    with nodes.node_index(pbf) as where:
        assert where.endswith(str(left)) and not left.exists()  # stale index gone before the read
        left.write_bytes(b"index")
    assert not left.exists()
    with pytest.raises(RuntimeError), nodes.node_index(pbf):
        left.write_bytes(b"index")
        raise RuntimeError("killed by the read")
    assert not left.exists()


@pytest.mark.usefixtures("fake_osmium")
@pytest.mark.parametrize("name", ["osm_roads", "osm_detail", "osm_buildings", "osm_places"])
def test_node_index_in_every_reader_a_big_extract_is_read_through_a_file(tmp_path, monkeypatch, name):
    reader = _load(name)
    nodes = sys.modules["osm_nodes"]
    monkeypatch.setattr(nodes, "THRESHOLD", 10)
    pbf = _extract(tmp_path, 50)
    reader.main(str(pbf), str(tmp_path / "out.geojsonl"), 0, 0, 1, 1)
    proc, = FakeProcessor.made
    where = f"sparse_file_array,{tmp_path / 'data' / 'tmp'}/europe-monaco.osm.pbf.nodes"
    assert ("locations", where) in proc.calls and proc.during
    # Locations come before the area pass, which would otherwise take its own in-memory index.
    assert proc.calls.index(("locations", where)) < proc.calls.index(("areas",)) if ("areas",) in proc.calls else True
    assert not list((tmp_path / "data" / "tmp").iterdir())  # removed afterwards


@pytest.mark.usefixtures("fake_osmium")
@pytest.mark.parametrize("name,asks", [("osm_roads", True), ("osm_detail", True), ("osm_buildings", False), ("osm_places", False)])
def test_node_index_a_small_extract_stays_in_memory(tmp_path, name, asks):
    reader = _load(name)
    pbf = _extract(tmp_path, 50)
    reader.main(str(pbf), str(tmp_path / "out.geojsonl"), 0, 0, 1, 1)
    proc, = FakeProcessor.made
    # The two that always had an index still ask for pyosmium's default; the area readers are untouched.
    assert proc.calls[:1] == ([("locations", "flex_mem")] if asks else [("areas",)])
    assert not (tmp_path / "data" / "tmp").exists()
