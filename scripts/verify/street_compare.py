#!/usr/bin/env python3
"""A real street-level photo beside the drive map drawn at the same spot and heading (T-1180).

    street_compare.py POINTS OUT [--pack DIR] [--no-render]
    street_compare.py --check OUT N

POINTS is a text file, one ``lat,lon`` a line (``#`` starts a comment). It is the only place a
position is named: keep it out of the repository. For each point the nearest photo with a
compass heading is asked of Panoramax (open street-level imagery, no key; CC-BY-SA, each
photo's own licence is written beside it), the map pack's answer round that photo is written
as ``NN.json``, and `StreetCompareRenderTest` draws each one to ``NN.map.png``. ``index.html``
in OUT shows the pairs. ``--check`` exits 1 unless OUT holds at least N spots with both a photo
and a drawn map: it counts pairs, it does not judge them.

Not covered: the map is drawn from the drive's chase camera, above and behind the car, not
from the photo's eye height, so structures line up by position and heading, not pixel for
pixel. A point with no photo within reach is skipped and said so. Photos are fetched one at a
time; this is for dozens of spots, not thousands.
"""
from __future__ import annotations

import io
import json
import math
import os
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

REPO = Path(__file__).resolve().parents[2]
SEARCH = "https://api.panoramax.xyz/api/search"
#: Degrees of latitude either side of a point a photo is looked for in (about 80 m).
REACH = 0.0007
#: Metres of map either side of the photo: the drive feed's own reach (DriveFeed.NEAR_M).
NEAR_M = 1000.0
#: Features of each kind asked of the pack: the phone's own limit (MapPack.LIMIT).
LIMIT = 4000
#: The share of a full-circle photo kept, centred on its heading: about what a windscreen sees.
VIEW = 100 / 360

Fetch = Callable[[str], bytes]


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "street-compare/0.1"})
    with urllib.request.urlopen(req, timeout=30) as r:  # noqa: S310 - fixed https hosts
        return r.read()


def points(text: str) -> list[tuple[float, float]]:
    out = []
    for line in text.splitlines():
        line = line.split("#")[0].strip()
        if line:
            lat, lon = (float(v) for v in line.split(",")[:2])
            out.append((lat, lon))
    return out


def nearest_photo(lat: float, lon: float, fetch: Fetch = _get) -> dict[str, Any] | None:
    """The photo with a heading nearest [lat, lon]: its position, heading, picture and licence."""
    reach_lon = REACH / max(0.2, math.cos(math.radians(lat)))
    query = urllib.parse.urlencode({"bbox": f"{lon - reach_lon},{lat - REACH},{lon + reach_lon},{lat + REACH}", "limit": 50})
    best: tuple[float, dict[str, Any]] | None = None
    for f in json.loads(fetch(f"{SEARCH}?{query}")).get("features") or []:
        p, at = f.get("properties") or {}, (f.get("geometry") or {}).get("coordinates")
        href = ((f.get("assets") or {}).get("sd") or {}).get("href")
        if not at or href is None or p.get("view:azimuth") is None:
            continue
        d = math.hypot((at[0] - lon) * math.cos(math.radians(lat)), at[1] - lat)
        if best is None or d < best[0]:
            full = (p.get("pers:interior_orientation") or {}).get("field_of_view") == 360
            best = (d, {"lat": at[1], "lon": at[0], "heading": float(p["view:azimuth"]), "photo": href,
                        "taken": p.get("datetime"), "licence": p.get("license"), "full_circle": full})
    return best[1] if best else None


def box_round(lat: float, lon: float, metres: float = NEAR_M) -> tuple[float, float, float, float]:
    dlat = metres / 111_320
    dlon = dlat / max(0.2, math.cos(math.radians(lat)))
    return (lon - dlon, lat - dlat, lon + dlon, lat + dlat)


def windscreen(jpeg: bytes, full_circle: bool) -> bytes:
    """The part of a photo a driver looks at: a full circle is cut to the view ahead."""
    if not full_circle:
        return jpeg
    from PIL import Image
    im = Image.open(io.BytesIO(jpeg))
    w, h = im.size
    half = int(w * VIEW / 2)
    out = io.BytesIO()
    im.crop((w // 2 - half, h // 4, w // 2 + half, h * 3 // 4)).save(out, "JPEG", quality=88)
    return out.getvalue()


def gather(spots: list[tuple[float, float]], out: Path, packs: list[Path], fetch: Fetch = _get,
           read: Callable[..., dict[str, Any] | None] | None = None) -> list[str]:
    """Write NN.json and NN.photo.jpg for each spot with a photo and a map; the names written."""
    if read is None:
        sys.path.insert(0, str(REPO / "aura-core"))
        from aura.integrations.maps.pack import read as read_pack
        read = read_pack
    out.mkdir(parents=True, exist_ok=True)
    done = []
    for i, (lat, lon) in enumerate(spots):
        name = f"{i:02d}"
        photo = nearest_photo(lat, lon, fetch)
        if photo is None:
            print(f"{name}: no photo with a heading within reach; skipped")
            continue
        near = read(packs, box_round(photo["lat"], photo["lon"]), LIMIT)
        if near is None:
            print(f"{name}: no map pack covers it; skipped")
            continue
        (out / f"{name}.photo.jpg").write_bytes(windscreen(fetch(photo["photo"]), photo["full_circle"]))
        (out / f"{name}.json").write_text(json.dumps({**photo, "near": near}), encoding="utf-8")
        done.append(name)
    return done


def index(out: Path, names: list[str]) -> None:
    rows = []
    for n in names:
        meta = json.loads((out / f"{n}.json").read_text(encoding="utf-8"))
        rows.append(f"<h2>{n} · heading {meta['heading']:.0f}° · photo {meta.get('taken') or '?'} · {meta.get('licence') or '?'}</h2>"
                    f"<div><img src='{n}.photo.jpg'><img src='{n}.map.png'></div>")
    (out / "index.html").write_text(
        "<!doctype html><meta charset=utf-8><title>Street compare</title>"
        "<style>body{background:#111;color:#ccc;font:14px monospace}div{display:flex;gap:8px}img{width:49%;height:auto}</style>"
        + "".join(rows), encoding="utf-8")


def render(out: Path) -> int:
    """Draw every NN.json in [out] with the app's own renderer (a Robolectric test)."""
    env = {**os.environ, "STREET_COMPARE_DIR": str(out.resolve())}
    return subprocess.call(
        ["gradle", ":app:testDebugUnitTest", "--tests", "*StreetCompareRenderTest*", "--no-daemon", "-q"],
        cwd=REPO / "android-app", env=env)


def pairs(out: Path) -> int:
    """The spots in [out] that have both their photo and their drawn map."""
    return sum(1 for m in out.glob("*.map.png") if (out / m.name.replace(".map.png", ".photo.jpg")).is_file())


def main(argv: list[str]) -> int:
    if argv[:1] == ["--check"] and len(argv) == 3:
        have, want = pairs(Path(argv[1])), int(argv[2])
        print(f"{have} pairs in {argv[1]}, {want} wanted")
        return 0 if have >= want else 1
    args = [a for a in argv if not a.startswith("--")]
    if len(args) != 2:
        print(__doc__)
        return 2
    spots, out = points(Path(args[0]).read_text(encoding="utf-8")), Path(args[1])
    pack_dir = Path(argv[argv.index("--pack") + 1]) if "--pack" in argv else REPO / "aura-core/maps-data/packs"
    packs = sorted(p for p in pack_dir.rglob("*") if p.is_file() and p.stat().st_size > 4096)
    names = gather(spots, out, packs)
    code = 0 if "--no-render" in argv else render(out)
    index(out, names)
    print(f"{len(names)} of {len(spots)} spots written to {out}/index.html")
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
