#!/usr/bin/env python3
"""min/max of a house sensor over a period via the jarvis-core REST history API.

Usage: python3 query_history.py <entity_id> [hours=168] [--host 172.17.0.1]
Needs AURA_TOKEN in the environment. The host defaults to the docker bridge
gateway — pass --host (or set AURA_HOUSE_HOST) to override.
Prints: point count, max/min with timestamps, and per-day maxima.
"""
import argparse
import datetime
import json
import os
import sys
import time
import urllib.request


BASE = "http://{}:8080"


def fetch(base, tok, entity_id, hours):
    now = time.time()
    start = now - hours * 3600
    url = BASE.format(base) + (
        "/api/history/period/{:.0f}?filter_entity_id={}&now={:.0f}"
        .format(start, entity_id, now * 1000)
    )
    req = urllib.request.Request(url, headers={"Authorization": "Bearer " + tok})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def collect(data):
    states = []
    for day in data or []:
        if not isinstance(day, dict):
            continue
        if "states" in day:  # standard nested shape
            states.extend(day["states"])
        elif "entity_id" in day:  # flat state object
            states.append(day)
        else:  # list of state objects
            states.extend([s for s in day if isinstance(s, dict)])
    out = []
    for s in states:
        ts = s.get("last_updated") or s.get("last_changed")
        if not ts:
            continue
        val = None
        if s.get("state") not in (None, "unavailable", "unknown"):
            try:
                val = float(s["state"])
            except (TypeError, ValueError):
                val = None
        if val is None:
            val = (s.get("attributes") or {}).get("current_temperature")
        if val is not None:
            out.append((float(ts), float(val)))
    return sorted(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("entity_id")
    ap.add_argument("hours", type=float, default=168.0)
    ap.add_argument("--host", default=os.environ.get("AURA_HOUSE_HOST", "172.17.0.1"))
    args = ap.parse_args()
    tok = os.environ.get("AURA_TOKEN")
    if not tok:
        sys.exit("AURA_TOKEN not set")
    vals = collect(fetch(args.host, tok, args.entity_id, args.hours))
    if not vals:
        print("no numeric data for {} in the last {:.0f}h".format(args.entity_id, args.hours))
        return
    hi = max(vals, key=lambda v: v[1])
    lo = min(vals, key=lambda v: v[1])
    fmt = lambda t: datetime.datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M").strip()
    print("{}: {} points, min {} ({}) max {} ({})".format(
        args.entity_id, len(vals), lo[1], fmt(lo[0]), hi[1], fmt(hi[0])))
    daily = {}
    for t, v in vals:
        d = datetime.datetime.fromtimestamp(t).strftime("%a %Y-%m-%d")
        daily[d] = max(daily.get(d, float("-inf")), v)
    for d in sorted(daily):
        print("  " + d + "  max " + str(daily[d]))


if __name__ == "__main__":
    main()
