"""scripts/map_worker.py: the asks the house leaves for a map region, carried out (T-0979).

The download and the .pbf pass are stood in for here (a fake fetch, a fake extract that
writes two roads); what is pinned is the order of work, what is on disk after each ask,
and what the worker refuses. Not covered: Geofabrik answering, pyosmium reading a real
extract, and Valhalla rebuilding (docs/runbooks/maps.md has the live check).
"""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "map_worker", Path(__file__).resolve().parents[2] / "scripts" / "map_worker.py")
worker = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(worker)

PBF = "https://download.geofabrik.de/ruritania/strelsau-latest.osm.pbf"
INDEX = {"features": [{"properties": {"id": "ruritania/strelsau", "name": "Strelsau", "urls": {"pbf": PBF}},
                       "geometry": {"type": "Polygon", "coordinates": [[[4, 4], [6, 4], [6, 6], [4, 6], [4, 4]]]}}]}
ROAD = {"type": "Feature", "properties": {"highway": "primary", "name": "Koenigstrasse"},
        "geometry": {"type": "LineString", "coordinates": [[5, 5], [5.001, 5.001]]}}


class Fakes:
    def __init__(self, data: Path):
        # upstream: what Geofabrik says about the extract now; heads: the looks the worker took
        self.data, self.fetched, self.fail = data, [], None
        self.upstream, self.heads, self.fail_head = {"etag": '"v1"', "modified": "Mon, 02 Nov 2026"}, [], None

    def fetch(self, url, dest, max_bytes):
        self.fetched.append(url)
        if self.fail:
            raise OSError(self.fail)
        Path(dest).write_text(json.dumps(INDEX) if url == worker.INDEX_URL else f"pbf of {url}")
        return dict(self.upstream)

    def head(self, url):
        self.heads.append(url)
        if self.fail_head:
            raise OSError(self.fail_head)
        return dict(self.upstream)

    def extract(self, pbf, out_dir, bbox):
        assert Path(pbf).read_text().startswith("pbf of") and bbox == (4.0, 4.0, 6.0, 6.0)
        (out_dir / "roads.geojsonl").write_text(json.dumps(ROAD))
        return {"roads": out_dir / "roads.geojsonl"}


@pytest.fixture
def house(tmp_path):
    data, jobs = tmp_path / "maps", tmp_path / "maps" / "jobs"
    jobs.mkdir(parents=True)
    (data / "valhalla").mkdir()
    fakes = Fakes(data)

    def tick(now=1_000_000.0, **kw):
        return worker.tick(data, jobs, fetch=fakes.fetch, head=fakes.head, extract=fakes.extract, now=lambda: now, **kw)

    return data, jobs, fakes, tick


def _status(data, region="ruritania-strelsau"):
    return json.loads((data / "regions" / f"{region}.json").read_text())


def test_the_catalogue_is_fetched_once_and_again_when_it_is_a_week_old(house):
    data, _jobs, fakes, tick = house
    tick()
    tick(now=1_000_000.0 + 3600)
    assert fakes.fetched == [worker.INDEX_URL] and (data / "geofabrik-index.json").is_file()
    tick(now=1_000_000.0 + 8 * 86400)
    assert fakes.fetched == [worker.INDEX_URL] * 2
    # The privacy switch: a catalogue the house has is not refreshed on the worker's own.
    worker.tick(data, _jobs, fetch=fakes.fetch, extract=fakes.extract, now=lambda: 1_000_000.0 + 30 * 86400, quiet=True)
    assert fakes.fetched == [worker.INDEX_URL] * 2


def test_an_ask_becomes_a_pack_the_extract_is_kept_for_routing_and_valhalla_is_told(house):
    data, jobs, fakes, tick = house
    (jobs / "ruritania-strelsau.want").write_text("present")
    tick()
    assert fakes.fetched == [worker.INDEX_URL, PBF]
    about = worker.pack.info(data / "packs" / "ruritania-strelsau.pack")
    assert (about["roads"], about["bbox"], about["name"]) == (1, [4.0, 4.0, 6.0, 6.0], "Strelsau")
    assert _status(data)["state"] == "ready"
    assert (data / "valhalla" / "ruritania-strelsau.osm.pbf").read_text() == f"pbf of {PBF}"
    assert (data / "valhalla" / ".rebuild").is_file()
    assert not list(data.rglob("*.geojsonl")) and not list(data.rglob("*.part"))
    # A second look at the same ask does nothing: no download, no rebuild.
    (data / "valhalla" / ".rebuild").unlink()
    tick()
    assert fakes.fetched == [worker.INDEX_URL, PBF] and not (data / "valhalla" / ".rebuild").exists()


def test_a_failed_download_says_why_and_is_not_tried_again_for_an_hour(house):
    data, jobs, fakes, tick = house
    tick()
    (jobs / "ruritania-strelsau.want").write_text("present")
    fakes.fail = "the network is down"
    tick()
    said = _status(data)
    assert said["state"] == "failed" and "network is down" in said["note"]
    fakes.fail = None
    tick(now=1_000_000.0 + 600)
    assert fakes.fetched.count(PBF) == 1 and _status(data)["state"] == "failed"
    tick(now=1_000_000.0 + 3700)
    assert _status(data)["state"] == "ready"


def test_asking_again_retries_a_failed_region_at_once(house):
    data, jobs, fakes, tick = house
    tick()
    want = jobs / "ruritania-strelsau.want"
    want.write_text("present")
    fakes.fail = "the network is down"
    tick()
    fakes.fail = None
    want.write_text("present")  # what the house does on a second ask
    os.utime(want, ns=(want.stat().st_atime_ns, want.stat().st_mtime_ns + 1))  # coarse clocks
    tick(now=1_000_000.0 + 60)
    assert _status(data)["state"] == "ready"


@pytest.mark.parametrize("name,url", [("atlantis", PBF), ("ruritania-strelsau", "https://evil.example/x.pbf"),
                                      ("ruritania-strelsau", "http://download.geofabrik.de/x.pbf")])
def test_an_ask_for_no_region_or_from_elsewhere_downloads_nothing(house, name, url):
    data, jobs, fakes, tick = house
    INDEX["features"][0]["properties"]["urls"]["pbf"] = url
    try:
        (jobs / f"{name}.want").write_text("present")
        tick()
    finally:
        INDEX["features"][0]["properties"]["urls"]["pbf"] = PBF
    assert fakes.fetched == [worker.INDEX_URL] and _status(data, name)["state"] == "failed"
    assert not (data / "packs").exists() or not list((data / "packs").iterdir())


def test_a_want_file_with_a_strange_name_is_left_alone(house):
    data, jobs, fakes, tick = house
    (jobs / "..want").write_text("present")
    (jobs / "UPPER.want").write_text("absent")
    tick()
    assert fakes.fetched == [worker.INDEX_URL] and not (data / "regions").exists()


def test_removing_a_region_takes_its_pack_its_extract_and_its_ask(house):
    data, jobs, _fakes, tick = house
    (jobs / "ruritania-strelsau.want").write_text("present")
    tick()
    (data / "valhalla" / ".rebuild").unlink()
    (data / "valhalla" / "file_hashes.txt").write_text("old")
    (jobs / "ruritania-strelsau.want").write_text("absent")
    tick()
    left = sorted(p.relative_to(data).as_posix() for p in data.rglob("*") if p.is_file())
    # The hashes go so Valhalla rebuilds from the extracts that remain.
    assert left == ["geofabrik-index.json", "valhalla/.rebuild"]


def test_the_home_pack_is_never_removed_by_an_ask(house):
    data, jobs, _fakes, tick = house
    (data / "packs").mkdir()
    (data / "packs" / "home.pack").write_text("x")
    (jobs / "home.want").write_text("absent")
    tick()
    assert (data / "packs" / "home.pack").is_file() and not (jobs / "home.want").exists()


def test_an_extract_valhalla_already_has_is_not_given_to_it_twice(house):
    data, jobs, _fakes, tick = house
    (data / "valhalla" / "region.osm.pbf").write_text(f"pbf of {PBF}")  # the home extract is this state's
    (jobs / "ruritania-strelsau.want").write_text("present")
    tick()
    assert not (data / "valhalla" / "ruritania-strelsau.osm.pbf").exists()
    assert not (data / "valhalla" / ".rebuild").exists() and _status(data)["state"] == "ready"


def test_the_cap_is_on_everything_the_maps_hold_and_one_extract_has_its_own(house):
    data, jobs, fakes, _tick = house
    seen = []

    def fetch(url, dest, max_bytes):
        seen.append(max_bytes)
        fakes.fetch(url, dest, max_bytes)

    def tick(**caps):
        worker.tick(data, jobs, fetch=fetch, extract=fakes.extract, now=lambda: 1_000_000.0, **caps)

    tick()
    (data / "packs").mkdir()
    (data / "packs" / "elsewhere.pack").write_bytes(b"x" * 700)
    (jobs / "ruritania-strelsau.want").write_text("present")
    tick(max_bytes=1000, region_max=10_000)
    assert seen[-1] == 300 and _status(data)["state"] == "ready"  # what is left, not the whole cap
    tick(max_bytes=10_000, region_max=50)
    (jobs / "ruritania-strelsau.want").write_text("absent")
    tick()
    (jobs / "ruritania-strelsau.want").write_text("present")
    tick(max_bytes=10_000, region_max=50)
    assert seen[-1] == 50
    (jobs / "ruritania-strelsau.want").write_text("absent")
    tick()
    (jobs / "ruritania-strelsau.want").write_text("present")
    tick(max_bytes=600)
    said = _status(data)
    assert said["state"] == "failed" and "remove a region first" in said["note"]


def test_an_extract_too_large_to_route_is_packed_and_not_handed_to_valhalla(house):
    data, jobs, fakes, _tick = house
    worker.tick(data, jobs, fetch=fakes.fetch, extract=fakes.extract, now=lambda: 1_000_000.0)
    (jobs / "ruritania-strelsau.want").write_text("present")
    worker.tick(data, jobs, fetch=fakes.fetch, extract=fakes.extract, now=lambda: 1_000_000.0, route_max=5)
    assert _status(data)["state"] == "ready" and (data / "packs" / "ruritania-strelsau.pack").is_file()
    assert not list((data / "valhalla").glob("*.pbf")) and not (data / "valhalla" / ".rebuild").exists()


def test_a_build_the_last_worker_died_in_is_a_failure_not_a_loop(house):
    data, jobs, fakes, tick = house
    tick()
    (jobs / "ruritania-strelsau.want").write_text("present")
    worker._say(data, "ruritania-strelsau", "working", "reading the extract", 1_000_000.0)
    worker.orphans(data, jobs, 1_000_000.0)
    assert _status(data)["state"] == "failed" and "stopped" in _status(data)["note"]
    tick(now=1_000_000.0 + 60)
    assert PBF not in fakes.fetched  # waits its hour, or a new ask


T0, DAY = 1_000_000.0, 86400.0


def _ready(house):
    data, jobs, fakes, tick = house
    (jobs / "ruritania-strelsau.want").write_text("present")
    tick()
    assert _status(data)["state"] == "ready"
    return data, jobs, fakes, tick


def test_refresh_looks_upstream_weekly_and_rebuilds_only_a_changed_extract(house):
    data, _jobs, fakes, tick = _ready(house)
    assert _status(data)["etag"] == '"v1"'  # what the download said, kept for the look
    tick(now=T0 + 3 * DAY)
    assert fakes.heads == []  # not a week yet
    tick(now=T0 + 8 * DAY)
    assert fakes.heads == [PBF] and fakes.fetched.count(PBF) == 1  # looked, unchanged: not fetched
    tick(now=T0 + 9 * DAY)
    assert fakes.heads == [PBF]  # once a week, not every 30 s
    fakes.upstream = {"etag": '"v2"', "modified": "Mon, 09 Nov 2026"}
    (data / "valhalla" / ".rebuild").unlink(missing_ok=True)
    tick(now=T0 + 16 * DAY)
    assert fakes.heads == [PBF] * 2 and fakes.fetched.count(PBF) == 2
    said = _status(data)
    assert said["state"] == "ready" and said["etag"] == '"v2"' and said["checked"] == T0 + 16 * DAY
    assert (data / "packs" / "ruritania-strelsau.pack").is_file() and (data / "valhalla" / ".rebuild").is_file()


def test_refresh_by_last_modified_when_there_is_no_etag(house):
    _data, _jobs, fakes, tick = _ready(house)
    fakes.upstream = {"etag": "", "modified": "Mon, 02 Nov 2026"}  # a server that dropped its ETag: no verdict
    tick(now=T0 + 8 * DAY)
    assert fakes.fetched.count(PBF) == 1
    fakes.upstream = {"etag": '"v1"', "modified": "Mon, 16 Nov 2026"}  # the same ETag, a newer date
    tick(now=T0 + 16 * DAY)
    assert fakes.fetched.count(PBF) == 2


def test_refresh_of_a_region_built_before_validators_were_kept_only_learns_them(house):
    data, _jobs, fakes, tick = _ready(house)
    worker._say(data, "ruritania-strelsau", "ready", "", T0)  # the old status file: no etag
    tick(now=T0 + 8 * DAY)
    assert fakes.fetched.count(PBF) == 1 and _status(data)["etag"] == '"v1"'


def test_refresh_that_fails_keeps_the_pack_and_looks_again_in_an_hour(house):
    data, _jobs, fakes, tick = _ready(house)
    fakes.fail_head = "the network is down"
    tick(now=T0 + 8 * DAY)
    assert _status(data)["state"] == "ready" and _status(data)["etag"] == '"v1"'
    tick(now=T0 + 8 * DAY + 600)
    assert len(fakes.heads) == 1
    fakes.fail_head, fakes.upstream = None, {"etag": '"v2"', "modified": ""}
    fakes.fail = "cut"  # the download breaks mid-refresh
    tick(now=T0 + 8 * DAY + 3700)
    said = _status(data)
    assert said["state"] == "ready" and said["etag"] == '"v1"' and (data / "packs" / "ruritania-strelsau.pack").is_file()
    fakes.fail = None
    tick(now=T0 + 8 * DAY + 7500)  # the hour's wait, then the change is picked up
    assert _status(data)["etag"] == '"v2"'


def test_a_quiet_house_takes_no_refresh_look(house):
    _data, _jobs, fakes, tick = _ready(house)
    fakes.upstream = {"etag": '"v2"', "modified": ""}
    tick(now=T0 + 30 * DAY, quiet=True)
    assert fakes.heads == [] and fakes.fetched.count(PBF) == 1


def test_quiet_holds_a_present_ask_installs_nothing_and_still_removes(house, monkeypatch):
    data, jobs, fakes, tick = house
    pips = []
    monkeypatch.setattr(worker.subprocess, "run", lambda *a, **k: pips.append(a))
    monkeypatch.setitem(worker.sys.modules, "osmium", None)  # not importable: the install would run
    tick()  # the catalogue, the one call a quiet house still makes once
    (data / "packs").mkdir(exist_ok=True)
    (data / "packs" / "gone.pack").write_text("x")
    (jobs / "gone.want").write_text("absent")
    (jobs / "ruritania-strelsau.want").write_text("present")
    # The real extract, so a pip install would be reached if the ask were not held.
    worker.tick(data, jobs, fetch=fakes.fetch, head=fakes.head, now=lambda: T0 + 60, quiet=True)
    said = _status(data)
    assert said["state"] == "held" and "quiet" in said["note"]
    assert fakes.fetched == [worker.INDEX_URL] and pips == []
    assert not (data / "packs" / "gone.pack").exists() and not (jobs / "gone.want").exists()


def test_quiet_holds_before_any_fetch_and_the_ask_runs_when_it_is_off(house):
    data, jobs, fakes, tick = house
    tick(quiet=True)  # no catalogue yet: it is fetched once, without it nothing can be asked for
    (jobs / "ruritania-strelsau.want").write_text("present")
    tick(now=T0 + 60, quiet=True)
    tick(now=T0 + 90, quiet=True)
    assert fakes.fetched == [worker.INDEX_URL] and _status(data)["state"] == "held"
    assert _status(data)["at"] == T0 + 60  # said once, not rewritten every look
    tick(now=T0 + 120)
    assert _status(data)["state"] == "ready" and fakes.fetched == [worker.INDEX_URL, PBF]


class _Answer:
    def __init__(self, url, body=b"new", status=200):
        self._url, self._body, self.status, self.headers = url, [body], status, {"Content-Length": str(len(body))}

    def geturl(self):
        return self._url

    def read(self, _n):
        return self._body.pop() if self._body else b""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_a_download_that_leaves_geofabrik_is_not_read_and_an_old_part_is_not_resumed(tmp_path, monkeypatch):
    asked = []

    def urlopen(req, timeout):
        asked.append(req.get_header("Range"))
        return _Answer(next(urls))

    monkeypatch.setattr(worker.urllib.request, "urlopen", urlopen)
    dest = tmp_path / "x.osm.pbf"
    urls = iter(["https://mirror.example/x.osm.pbf"])
    with pytest.raises(ValueError, match="only Geofabrik"):
        worker._fetch(PBF, dest, 1000)
    assert not dest.exists()
    part = tmp_path / "x.osm.pbf.part"
    part.write_bytes(b"yesterday")
    os.utime(part, (0, 0))
    urls = iter([PBF.replace("latest", "261109")])  # Geofabrik's own redirect, to the dated file
    got = worker._fetch(PBF, dest, 1000)
    assert asked[-1] is None and dest.read_bytes() == b"new"
    assert got == {"etag": "", "modified": ""}  # an answer with neither says so, it is not a failure


def test_refresh_look_reads_the_validators_and_never_leaves_geofabrik(monkeypatch):
    answer = _Answer(PBF.replace("latest", "261109"))
    answer.headers = {"ETag": '"abc"', "Last-Modified": "Mon, 09 Nov 2026"}
    seen = []
    monkeypatch.setattr(worker.urllib.request, "urlopen", lambda req, timeout: seen.append(req.get_method()) or answer)
    assert worker._head(PBF) == {"etag": '"abc"', "modified": "Mon, 09 Nov 2026"} and seen == ["HEAD"]
    monkeypatch.setattr(worker.urllib.request, "urlopen", lambda req, timeout: _Answer("https://mirror.example/x"))
    with pytest.raises(ValueError, match="only Geofabrik"):
        worker._head(PBF)
    with pytest.raises(ValueError, match="only Geofabrik"):
        worker._head("https://evil.example/x.pbf")


def test_at_the_cap_the_oldest_region_the_house_fetched_itself_makes_room_t1074(house):
    data, jobs, fakes, _tick = house
    worker.tick(data, jobs, fetch=fakes.fetch, extract=fakes.extract, now=lambda: 1_000_000.0)
    (data / "packs").mkdir()
    for name, age in (("older", 100), ("newer", 200), ("asked", 50), ("home", 10)):
        (data / "packs" / f"{name}.pack").write_bytes(b"x" * 300)
        os.utime(data / "packs" / f"{name}.pack", (age, age))
        (jobs / f"{name}.want").write_text("present") if name != "home" else None
        (data / "regions").mkdir(exist_ok=True)
        (data / "regions" / f"{name}.json").write_text(json.dumps({"state": "ready"}))
    (jobs / "older.auto").touch()
    (jobs / "newer.auto").touch()
    (jobs / "ruritania-strelsau.want").write_text("present")
    # 1200 held of 1400, and an extract may be 400: one self-fetched region goes, the oldest.
    worker.tick(data, jobs, fetch=fakes.fetch, extract=fakes.extract, now=lambda: 1_000_000.0, max_bytes=1400, region_max=400)
    assert _status(data)["state"] == "ready"
    assert sorted(p.stem for p in (data / "packs").glob("*.pack")) == ["asked", "home", "newer", "ruritania-strelsau"]
    assert not (jobs / "older.want").exists() and not (jobs / "older.auto").exists()
    # Nothing self-fetched left to drop: what the owner asked for and home stay, and the ask fails.
    (jobs / "newer.auto").unlink()
    (jobs / "ruritania-strelsau.want").write_text("absent")
    worker.tick(data, jobs, fetch=fakes.fetch, extract=fakes.extract, now=lambda: 1_000_000.0)
    (jobs / "ruritania-strelsau.want").write_text("present")
    worker.tick(data, jobs, fetch=fakes.fetch, extract=fakes.extract, now=lambda: 1_000_000.0, max_bytes=800, region_max=400)
    assert "remove a region first" in _status(data)["note"] and (data / "packs" / "asked.pack").is_file()
