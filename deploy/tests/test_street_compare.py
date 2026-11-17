"""The photo-beside-map comparison's gathering, with no network and no pack (T-1180).

Not covered: the drawing itself (a Robolectric test the script starts) and what Panoramax
really answers; the shapes here are the ones read from it on 2026-11-12.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "verify" / "street_compare.py"
spec = importlib.util.spec_from_file_location("street_compare", SCRIPT)
sc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sc)


def feature(lon, lat, azimuth, href="https://photos.example.com/a.jpg"):
    return {"geometry": {"coordinates": [lon, lat]}, "assets": {"sd": {"href": href}},
            "properties": {"view:azimuth": azimuth, "datetime": "2026-02-14T14:34:01+00:00", "license": "CC-BY-SA-4.0"}}


def fetcher(features):
    def fetch(url):
        return json.dumps({"features": features}).encode() if "search" in url else b"jpeg"
    return fetch


def test_street_compare_takes_the_nearest_photo_that_has_a_heading():
    got = sc.nearest_photo(10.0, 20.0, fetcher([feature(20.0005, 10.0, 90), feature(20.0001, 10.0, None), feature(20.0002, 10.0, 270)]))
    assert (got["lon"], got["heading"]) == (20.0002, 270.0)
    assert sc.nearest_photo(10.0, 20.0, fetcher([])) is None


def test_street_compare_writes_a_pair_per_spot_and_skips_one_with_no_photo_or_no_map(tmp_path):
    asked = []

    def read(packs, box, limit):
        asked.append(box)
        return {"buildings": [], "roads": []} if len(asked) == 1 else None

    def fetch(url):
        if "search" in url and "30." in url:
            return json.dumps({"features": []}).encode()
        return fetcher([feature(20.0, 10.0, 45)])(url)

    names = sc.gather([(10.0, 20.0), (10.0, 30.0), (10.0, 20.0)], tmp_path, [], fetch, read)
    assert names == ["00"]
    meta = json.loads((tmp_path / "00.json").read_text())
    assert meta["heading"] == 45.0 and meta["near"] == {"buildings": [], "roads": []}
    assert (tmp_path / "00.photo.jpg").read_bytes() == b"jpeg"
    west, south, east, north = asked[0]
    assert west < 20.0 < east and south < 10.0 < north
    assert sc.main(["--check", str(tmp_path), "1"]) == 1  # a photo with no drawn map is not a pair
    (tmp_path / "00.map.png").write_bytes(b"png")
    assert sc.main(["--check", str(tmp_path), "1"]) == 0 and sc.main(["--check", str(tmp_path), "2"]) == 1
    sc.index(tmp_path, names)
    assert "00.map.png" in (tmp_path / "index.html").read_text()


def test_street_compare_names_no_place():
    # The points file is the only place a position is named; the script itself carries none.
    assert sc.points("# a comment\n10.5,20.25\n\n") == [(10.5, 20.25)]
    import re
    assert not re.search(r"-?\d{2}\.\d{3,}\s*,\s*-?\d{2,3}\.\d{3,}", SCRIPT.read_text(encoding="utf-8"))
