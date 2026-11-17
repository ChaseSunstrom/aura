"""`make up` stops while the house's own data is still under the folder's old name (T-1435).

T-0960 renamed ``jarvis-core/`` to ``aura-core/``. Git moves the tracked files; the env file, the
voice models and the map data are untracked, so after a pull on a host that ran the old tree they
stay where they were. ``make up`` would then start the house from a folder with no env file and
empty model directories: the settings are not read and the models are fetched again. The check
runs first and names what to move.

Does not cover: moving anything (an operator does that, with the house down), or a host whose
data was already copied rather than moved (the old folder still holds it, so the check still
stops: delete or move the old copy).
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "check-house-folder.py"


def run(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(SCRIPT), str(root)], capture_output=True, text=True,
                          timeout=30, check=False)


def put(path: Path, text: str = "x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_house_folder_check_passes_with_no_old_folder_and_with_only_caches_left(tmp_path):
    put(tmp_path / "aura-core" / "docker-compose.yml")
    assert run(tmp_path).returncode == 0

    # What a pull leaves behind when there was no data: caches and the directories that held them.
    put(tmp_path / "jarvis-core" / "jarvis" / "__pycache__" / "app.cpython-311.pyc")
    put(tmp_path / "jarvis-core" / "tests" / ".pytest_cache" / "v" / "cache" / "lastfailed")
    put(tmp_path / "jarvis-core" / ".mypy_cache" / "3.11" / "x.json")
    put(tmp_path / "jarvis-core" / ".ruff_cache" / "0.16.3" / "abc")
    (tmp_path / "jarvis-core" / "config" / "empty").mkdir(parents=True)
    # And what a test run, an editable install, a venv or a file manager leaves: none is the
    # house's data, and counting one would stop every start on a host with nothing to move.
    put(tmp_path / "jarvis-core" / ".hypothesis" / "examples" / "a")
    put(tmp_path / "jarvis-core" / ".coverage")
    put(tmp_path / "jarvis-core" / "jarvis_core.egg-info" / "PKG-INFO")
    put(tmp_path / "jarvis-core" / ".venv" / "bin" / "python")
    put(tmp_path / "jarvis-core" / "config" / ".DS_Store")
    put(tmp_path / "jarvis-core" / "maps-data" / ".gitkeep")
    done = run(tmp_path)
    assert done.returncode == 0, done.stderr
    assert done.stderr == ""


def test_house_folder_check_stops_and_names_each_thing_to_move(tmp_path):
    put(tmp_path / "aura-core" / "docker-compose.yml")
    put(tmp_path / "aura-core" / "config" / "models" / "tracked.onnx")
    put(tmp_path / "jarvis-core" / ".env", "HASS_TOKEN=not-a-real-one\n")
    put(tmp_path / "jarvis-core" / "config" / "models" / "voice.onnx")
    put(tmp_path / "jarvis-core" / "config" / "models" / "tracked.onnx", "an older copy")
    put(tmp_path / "jarvis-core" / "maps-data" / "ruritania-zenda.pbf")
    put(tmp_path / "jarvis-core" / "wyoming" / "whisper" / "model.bin")

    done = run(tmp_path)
    assert done.returncode == 3
    err = done.stderr
    # Whole entries the new folder lacks, as one move each, the env file among them.
    for entry in (".env", "maps-data", "wyoming"):
        assert f"mv jarvis-core/{entry} aura-core/{entry}" in err, err
    # Inside a directory both folders have, the one file the new folder lacks.
    assert "mv jarvis-core/config/models/voice.onnx aura-core/config/models/voice.onnx" in err
    # A file both have is not overwritten on a guess: it is named for a person to compare.
    assert "jarvis-core/config/models/tracked.onnx" in err and "mv jarvis-core/config/models/tracked.onnx" not in err
    # A directory that moves whole is not walked: a map file's name says where the house is, and
    # the env file is never opened.
    assert "ruritania" not in err and "not-a-real-one" not in err
    # How to stop the house first. After the pull the compose file is no longer in the old folder,
    # so a `docker compose down` there fails; `make down` leaves the profile services running.
    assert "make down" in err and "docker compose --profile '*' down" in err
    assert "in the old folder" not in err


def test_house_folder_check_does_not_walk_a_directory_the_new_folder_holds_only_as_a_placeholder(tmp_path):
    # The real tree: `aura-core/maps-data/.gitkeep` is tracked, so the new folder always has the
    # directory. Walking it would print each map file's name, and a map file is named after the
    # region the house is in.
    put(tmp_path / "aura-core" / "maps-data" / ".gitkeep", "")
    put(tmp_path / "jarvis-core" / "maps-data" / "ruritania-zenda.pbf")
    put(tmp_path / "jarvis-core" / "maps-data" / "tiles" / "ruritania.mbtiles")
    # A directory docker made, empty, for a bind mount when the house was started by hand.
    (tmp_path / "aura-core" / "wyoming").mkdir()
    put(tmp_path / "jarvis-core" / "wyoming" / "whisper" / "model.bin")

    done = run(tmp_path)
    assert done.returncode == 3
    err = done.stderr
    assert "mv jarvis-core/maps-data/* aura-core/maps-data/" in err, err
    assert "mv jarvis-core/wyoming/* aura-core/wyoming/" in err, err
    assert "ruritania" not in err and "whisper" not in err


def test_house_folder_check_passes_when_the_old_name_is_a_link_to_the_new_folder(tmp_path):
    # Someone kept the old path alive with a symlink: every entry is then "in both folders", and
    # the check would stop every start for good with nothing anyone could move.
    put(tmp_path / "aura-core" / ".env", "HASS_TOKEN=not-a-real-one\n")
    put(tmp_path / "aura-core" / "config" / "models" / "voice.onnx")
    (tmp_path / "jarvis-core").symlink_to("aura-core", target_is_directory=True)
    done = run(tmp_path)
    assert done.returncode == 0, done.stderr
    assert done.stderr == ""


def test_house_folder_check_skips_an_entry_that_is_a_link_to_its_new_place(tmp_path):
    put(tmp_path / "aura-core" / ".env", "HASS_TOKEN=not-a-real-one\n")
    (tmp_path / "jarvis-core").mkdir()
    (tmp_path / "jarvis-core" / ".env").symlink_to("../aura-core/.env")
    done = run(tmp_path)
    assert done.returncode == 0, done.stderr


def test_house_folder_check_names_a_directory_it_cannot_list_instead_of_crashing(tmp_path):
    if os.geteuid() == 0:
        pytest.skip("root can list any directory")
    # A container's user owns it in both folders (a service wrote there before and after).
    put(tmp_path / "aura-core" / "state" / "db")
    locked = tmp_path / "jarvis-core" / "state"
    put(locked / "db")
    locked.chmod(0)
    try:
        done = run(tmp_path)
    finally:
        locked.chmod(0o755)
    assert done.returncode == 3
    assert "Traceback" not in done.stderr, done.stderr
    assert "\n  jarvis-core/state\n" in done.stderr, done.stderr


def test_house_folder_check_prints_a_move_that_can_be_pasted_whatever_the_name(tmp_path):
    put(tmp_path / "aura-core" / "docker-compose.yml")
    put(tmp_path / "jarvis-core" / "my notes.txt")
    put(tmp_path / "jarvis-core" / "odd\nrm -rf name")
    done = run(tmp_path)
    assert done.returncode == 3
    lines = done.stderr.splitlines()
    assert "  mv 'jarvis-core/my notes.txt' 'aura-core/my notes.txt'" in lines, done.stderr
    # A name with a line break never becomes a line of its own, and never a mv line: it is shown
    # escaped, for a person to move by hand.
    assert not any(line.startswith("rm -rf") for line in lines), done.stderr
    assert not any(line.lstrip().startswith("mv") and "odd" in line for line in lines), done.stderr
    assert "'jarvis-core/odd\\nrm -rf name'" in done.stderr, done.stderr


def test_make_up_runs_the_house_folder_check_before_compose():
    recipe = (REPO / "Makefile").read_text(encoding="utf-8").split("\nup:", 1)[1].split("\n\n", 1)[0]
    assert recipe.index("scripts/check-house-folder.py") < recipe.index("$(COMPOSE) up")
