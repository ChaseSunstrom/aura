"""scripts/map-region-from-house.sh --rebuild (T-1169).

The wrapper answers "fetched already" before map-region.sh can apply its own staleness rule,
so a changed extraction script never reached the pack by the documented short way. The
wrapper is run here beside a stub map-region.sh, with `docker` stubbed on PATH: nothing is
fetched and no container is touched.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
WRAPPER = REPO / "scripts" / "map-region-from-house.sh"
BBOX = "1.5,2.5,3.5,4.5"  # made up


def _run(tmp_path: Path, *args: str, bbox: str | None = BBOX) -> tuple[subprocess.CompletedProcess, Path]:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    shutil.copy(WRAPPER, scripts / WRAPPER.name)
    ran = tmp_path / "ran"
    (scripts / "map-region.sh").write_text(
        f'#!/usr/bin/env bash\necho "bbox=$MAPS_BBOX pbf=${{MAPS_PBF_URL:-}} args=$*" > "{ran}"\necho "region $MAPS_BBOX"\n')
    (scripts / "map-region.sh").chmod(0o755)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    # The house says "fetched": the answer that used to end the run. Compose calls do nothing.
    (bin_dir / "docker").write_text(
        '#!/usr/bin/env bash\n[ "$1" = exec ] && { cat >/dev/null; echo \'{"source": "fetched"}\'; }\nexit 0\n')
    (bin_dir / "docker").chmod(0o755)
    data = tmp_path / "data"
    (data / "valhalla").mkdir(parents=True)
    (data / "valhalla" / "region.osm.pbf").write_bytes(b"x")
    if bbox is not None:
        (data / "region.bbox").write_text(bbox + "\n")
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "MAPS_DATA_DIR": str(data)}
    done = subprocess.run(["bash", str(scripts / WRAPPER.name), *args], env=env, capture_output=True, text=True, timeout=60)
    return done, ran


@pytest.mark.skipif(shutil.which("jq") is None, reason="the wrapper needs jq")
def test_map_region_from_house_without_rebuild_stops_at_fetched(tmp_path: Path) -> None:
    done, ran = _run(tmp_path)
    assert done.returncode == 0 and "fetched already" in done.stdout
    assert not ran.exists()


def test_map_region_from_house_rebuild_runs_map_region_on_the_stored_region(tmp_path: Path) -> None:
    done, ran = _run(tmp_path, "--rebuild")
    assert done.returncode == 0, done.stderr
    # The stored bbox, and no extract URL: map-region.sh then downloads nothing.
    assert ran.read_text().strip() == f"bbox={BBOX} pbf= args="
    # The region is not on the screen.
    assert BBOX not in done.stdout + done.stderr


def test_map_region_from_house_rebuild_with_no_stored_region_fails(tmp_path: Path) -> None:
    done, ran = _run(tmp_path, "--rebuild", bbox=None)
    assert done.returncode == 1 and "no fetched region" in done.stderr
    assert not ran.exists()
