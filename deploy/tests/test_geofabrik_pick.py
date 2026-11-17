"""scripts/geofabrik_pick.py: which Geofabrik extract the house's region is fetched from (T-0765).

The house's region never reaches this conversation or a setting typed by hand, so the extract is
chosen from Geofabrik's own index: the smallest whose outline covers the region.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "geofabrik_pick.py"
_SPEC = importlib.util.spec_from_file_location("geofabrik_pick", SCRIPT)
pick = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(pick)


def _extract(name, *polygons):
    return {"type": "Feature", "properties": {"id": name, "urls": {"pbf": f"https://x.test/{name}-latest.osm.pbf"}},
            "geometry": {"type": "MultiPolygon", "coordinates": [[ring] for ring in polygons]}}


def _box(w, s, e, n):
    return [[w, s], [e, s], [e, n], [w, n], [w, s]]


INDEX = {"type": "FeatureCollection", "features": [
    _extract("continent", _box(-20, -20, 20, 20)),
    # An L: its bbox takes in the corner at (5..10, 5..10), its outline does not (the river border
    # that puts a city in one state's bbox and the next state's outline).
    _extract("state-a", [[0, 0], [10, 0], [10, 5], [5, 5], [5, 10], [0, 10], [0, 0]]),
    _extract("state-b", _box(5, 5, 10, 10)),
    {"type": "Feature", "properties": {"id": "no-file", "urls": {}}, "geometry": {"type": "Polygon", "coordinates": [_box(6, 6, 7, 7)]}},
]}


def test_the_smallest_outline_that_covers_the_region_wins():
    assert pick.pick(INDEX, (1, 1, 2, 2)).endswith("/state-a-latest.osm.pbf")
    assert pick.pick(INDEX, (6, 6, 7, 7)).endswith("/state-b-latest.osm.pbf")  # in A's bbox, not its outline
    assert pick.pick(INDEX, (4, 4, 6, 6)).endswith("/continent-latest.osm.pbf")  # straddles A and B
    assert pick.pick(INDEX, (30, 30, 31, 31)) is None


def test_the_command_prints_only_the_url(tmp_path):
    index = tmp_path / "index-v1.json"
    index.write_text(json.dumps(INDEX))
    run = subprocess.run([sys.executable, "-I", str(SCRIPT), str(index), "1,1,2,2"], capture_output=True, text=True)
    assert (run.returncode, run.stdout) == (0, "https://x.test/state-a-latest.osm.pbf\n")
    none = subprocess.run([sys.executable, "-I", str(SCRIPT), str(index), "30,30,31,31"], capture_output=True, text=True)
    assert none.returncode == 1 and none.stdout == "" and "30" not in none.stderr
