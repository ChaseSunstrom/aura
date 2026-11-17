#!/usr/bin/env python3
"""Live check: the car's maps are answered by the house, and nothing points at a public map host (T-0765).

    python3 scripts/verify/maps-live.py

The owner, 2026-11-08: "I would like it so my location and everything is private and not
making requests out to stuff". This asks the house from inside its own container (the house's
token never reaches this shell) and prints which checks passed, never a coordinate:

  1. the region is the house's (a setting, what map-region.sh fetched, or the car's drives),
     not the house coordinates, which may still be the shipped placeholder (GAPS G17);
  2. a route, the car's turn-by-turn in OSRM's shape, and a map-match with the road's limit come
     from the house's Valhalla, on roads the route itself found inside the region;
  3. the house's place index answers;
  4. the console's /api/route answers from the house;
  5. no running container is pointed at a public place service (PHOTON_PUBLIC, OVERPASS_URL).

Exit 0 when all pass, 1 when one fails, 2 when the house is not running. Does not cover: what
a model provider reads in a question that names a place (T-0767), or the phone's own location
providers.
"""

from __future__ import annotations

import json
import subprocess
import sys

HOUSE_PROBE = r'''
import json, math, os, urllib.error, urllib.parse, urllib.request
TOKEN = os.environ.get("AURA_TOKEN", "")

def ask(method, path, body=None):
    req = urllib.request.Request("http://127.0.0.1:8080" + path, method=method,
                                 data=None if body is None else json.dumps(body).encode(),
                                 headers={"Authorization": "Bearer " + TOKEN, "content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as err:
        return err.code, None
    except OSError:
        return 0, None

out = {}
status, region = ask("GET", "/api/maps/region")
out["region"] = (region or {}).get("source") if status == 200 else f"http {status}"
if status == 200:
    w, s, e, n = region["bbox"]
    a = [(s + n) / 2, (w + e) / 2]
    b = [a[0] + 0.02, a[1]]  # about 2 km north
    out["point"] = [a, b]  # for the console's check; this process reads it, nothing prints it
    status, route = ask("POST", "/api/maps/route", {"from": a, "to": b})
    out["route"] = status == 200 and (route or {}).get("distance_m", 0) > 0
    status, osrm = ask("POST", "/api/maps/directions", {"from": a, "to": b, "via": []})
    out["directions"] = status == 200 and bool(((osrm or {}).get("routes") or [{}])[0].get("legs"))
    shape = (route or {}).get("shape") or []
    if len(shape) >= 2:
        status, matched = ask("POST", "/api/maps/match", {"points": shape[:6]})
        out["match"] = status == 200 and (matched or {}).get("limit_source") in ("posted", "typical")
    else:
        out["match"] = False
    status, here = ask("GET", "/api/maps/places?" + urllib.parse.urlencode({"lat": a[0], "lon": a[1], "kind": "amenity=fuel"}))
    out["places"] = status == 200
print(json.dumps(out))
'''

CONSOLE_PROBE = r'''
const key = process.env.AURA_ADAPTER_KEY || '';
const port = process.env.PORT || '3000';
const [a, b] = JSON.parse(process.argv[1]);
fetch(`http://127.0.0.1:${port}/api/route?from=${a[0]},${a[1]}&to=${b[0]},${b[1]}`, { headers: { Authorization: `Bearer ${key}` } })
	.then(async (r) => console.log(JSON.stringify({ status: r.status, routes: r.ok ? ((await r.json()).routes || []).length : 0 })))
	.catch(() => console.log(JSON.stringify({ status: 0, routes: 0 })));
'''


def _exec(container: str, *argv: str, stdin: str | None = None) -> str:
    run = subprocess.run(["docker", "exec", "-i", container, *argv], input=stdin, capture_output=True, text=True, timeout=180)
    return run.stdout.strip() if run.returncode == 0 else ""


def _env(container: str, name: str) -> str:
    return _exec(container, "printenv", name)


def main() -> int:
    raw = _exec("jarvis-core", "python3", "-", stdin=HOUSE_PROBE)
    if not raw:
        print("FAIL the house (jarvis-core) is not running or did not answer")
        return 2
    got = json.loads(raw.splitlines()[-1])
    checks = [("the region is the house's own (setting, fetched or drives)", got.get("region") in ("setting", "fetched", "drives"))]
    for key, what in (("route", "a route from the house's Valhalla"), ("directions", "the car's turn-by-turn, OSRM-shaped, from the house"),
                      ("match", "a map-match with the road's speed limit"), ("places", "the house's place index answers")):
        checks.append((what, bool(got.get(key))))
    # The console's route between the same two points, handed from one container to the other
    # through this process and never printed.
    point = got.get("point")
    console = json.loads(_exec("jarvis-web", "node", "-e", CONSOLE_PROBE, json.dumps(point)) or "{}") if point else {}
    checks.append(("the console's /api/route answers from the house", console.get("status") == 200 and console.get("routes", 0) > 0))
    public = _env("jarvis", "PHOTON_PUBLIC").lower() in ("1", "true", "yes", "on") or bool(_env("jarvis", "OVERPASS_URL"))
    checks.append(("no public place service is switched on (PHOTON_PUBLIC, OVERPASS_URL)", not public))
    for what, ok in checks:
        print(("ok   " if ok else "FAIL ") + what)
    if got.get("region") not in ("setting", "fetched", "drives"):
        print(f"     region source: {got.get('region')}; scripts/map-region-from-house.sh fetches it once the house has one")
    return 0 if all(ok for _, ok in checks) else 1


if __name__ == "__main__":
    sys.exit(main())
