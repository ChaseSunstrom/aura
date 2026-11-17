"""Nothing JARVIS asks of the outside world says where the owner is (T-0765).

The owner, 2026-11-08: "I would like it so my location and everything is private and not making
requests out to stuff". Five paths sent the position out: the car's map tiles
(tile.openstreetmap.org), the speed limit (overpass-api.de), routes (router.project-osrm.org),
place search (photon.komoot.io, nominatim.openstreetmap.org, Overpass round a point) and weather
(the house's exact coordinates; hermes/plugins/jarvis-hud/tests/test_weather.py holds that one).
The maps come from the house instead (jarvis-core integrations/maps: Valhalla, the region's
PMTiles, a local place index), so no shipped default may name one of these hosts.

Does not cover: a host the owner points a setting at on purpose (their choice, in their .env, or
one of OPT_IN's switches turned on);
what a model provider reads in a question that names a place (T-0767); the phone's own location
providers.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

#: Public services that answer a question about a place near the owner with that place in it.
LOCATION_HOSTS = (
    "tile.openstreetmap.org",
    "overpass-api.de",
    "router.project-osrm.org",
    "photon.komoot.io",
    "nominatim.openstreetmap.org",
)
#: Everything that ships: the apps, the house, the plugins, the settings and the deploy files.
TREES = (
    "jarvis-web/src", "aura-core/aura", "aura-core/config", "aura-core/.env.example",
    "hermes/plugins", "aura-schema/aura_schema", "aura-adapter", "deploy",
    "android-app/app/src/main",
)
#: The public hosts a shipped file may still name, each only behind a switch that is off by default
#: (`test_each_public_opt_in_is_off_by_default`): path -> (the line's start, the setting's field).
OPT_IN = {
    "hermes/plugins/jarvis-hud/places.py": {
        'PUBLIC = "https://photon.komoot.io"': "places_photon_public",
        'NOMINATIM = "https://nominatim.openstreetmap.org"': "places_photon_public",
    },
}
SUFFIXES = {".py", ".ts", ".svelte", ".kt", ".json", ".in", ".yaml", ".yml", ".example", ".sh"}
URL = re.compile(r"https?://(?:" + "|".join(re.escape(h) for h in LOCATION_HOSTS) + r")\b")


def _shipped(path: Path) -> bool:
    parts = path.relative_to(ROOT).parts
    if any(p in ("tests", "test", "node_modules", "__pycache__", "build") for p in parts):
        return False
    return ".test." not in path.name and ".spec." not in path.name and not path.name.startswith("test_")


def _files():
    for tree in TREES:
        top = ROOT / tree
        for path in [top] if top.is_file() else top.rglob("*"):
            if path.is_file() and (path.suffix in SUFFIXES or path.name.endswith(".example")) and _shipped(path):
                yield path


def test_no_shipped_default_sends_a_position_to_a_public_map_host():
    hits = [
        f"{path.relative_to(ROOT)}:{n}: {line.strip()[:110]}"
        for path in _files()
        for n, line in enumerate(path.read_text(errors="replace").splitlines(), 1)
        if URL.search(line) and line.strip() not in OPT_IN.get(path.relative_to(ROOT).as_posix(), {})
    ]
    assert not hits, "these would send where the owner is out of the house:\n" + "\n".join(hits)


def test_the_scan_sees_the_files_that_used_to_send_it():
    # A scan that reads nothing passes forever; these are where the five hosts were asked from.
    seen = {path.relative_to(ROOT).as_posix() for path in _files()}
    for name in ("jarvis-web/src/lib/ds/screens/Car.svelte", "jarvis-web/src/routes/api/route/+server.ts",
                 "hermes/plugins/jarvis-hud/places.py", "hermes/plugins/jarvis-hud/weather.py",
                 "aura-schema/aura_schema/settings.py", "deploy/.env.example"):
        assert name in seen, name


def test_each_public_opt_in_is_off_by_default():
    from aura_schema import from_env

    defaults = from_env({})
    for field in {field for lines in OPT_IN.values() for field in lines.values()}:
        assert getattr(defaults, field) is False, field
    for path, lines in OPT_IN.items():
        text = (ROOT / path).read_text()
        for line in lines:
            assert line in text, f"{path} no longer has {line!r}: drop it from OPT_IN"
