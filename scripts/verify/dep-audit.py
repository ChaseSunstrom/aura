#!/usr/bin/env python3
"""Audit the production npm dependencies against OSV, because npm's own endpoint stopped answering.

`npm audit` reads its advisories from registry.npmjs.org. On 2026-09-03 that service
answered ``500 Internal Server Error`` on ``/-/npm/v1/security/audits/quick`` (with a notice
that the endpoint "is being retired") and hung past 40 s on the bulk endpoint, while the
registry itself answered in 0.3 s. Retrying does not help an outage, and a dependency gate
that cannot run is a gate that blocks every merge for reasons that have nothing to do with
the tree.

So the advisories come from OSV (api.osv.dev), which carries the same GitHub Advisory
Database records npm serves. OSV is asked about an exact version, so the semver range
matching stays on its side; nothing here reimplements it.

Only production dependencies are audited, which is what ``npm audit --omit=dev`` meant: a
vulnerable build-time package does not ship. A lockfile entry is dev if npm marked it
``"dev": true``; entries reachable from both trees are not marked, and so are audited.

This does **not** cover: packages OSV has no record for (absence of a record is not a proof
of safety), the Python and Gradle trees (pip-audit and the Gradle audit are their own jobs),
or anything about a package other than published advisories.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

OSV_BATCH = "https://api.osv.dev/v1/querybatch"
#: OSV accepts large batches; 250 keeps a single request well under any proxy's body limit.
CHUNK = 250
TIMEOUT_S = 60


def production_packages(lockfile: Path) -> dict[str, str]:
    """Every non-dev package in *lockfile*, as ``name -> version``.

    npm's v3 lockfile keys are install paths ("node_modules/a/node_modules/b"); the package's
    own name is the segment after the last "node_modules/", not the whole key, and the root
    entry ("") is the project itself rather than a dependency.
    """
    data = json.loads(lockfile.read_text(encoding="utf-8"))
    if data.get("lockfileVersion", 0) < 2:
        raise SystemExit(f"{lockfile}: lockfileVersion {data.get('lockfileVersion')} has no "
                         "package map; run `npm install` with npm 7+ to regenerate it")
    out: dict[str, str] = {}
    for path, meta in data.get("packages", {}).items():
        if not path or meta.get("dev") or meta.get("link"):
            continue
        version = meta.get("version")
        if not version:
            continue
        out[meta.get("name") or path.rsplit("node_modules/", 1)[-1]] = version
    return out


def query_osv(packages: dict[str, str]) -> dict[str, list[str]]:
    """Advisory ids per package name, asking OSV in batches. Raises on an unusable service."""
    names = sorted(packages)
    found: dict[str, list[str]] = {}
    for start in range(0, len(names), CHUNK):
        window = names[start:start + CHUNK]
        body = {"queries": [{"package": {"name": n, "ecosystem": "npm"},
                             "version": packages[n]} for n in window]}
        req = urllib.request.Request(
            OSV_BATCH, data=json.dumps(body).encode(), method="POST",
            headers={"content-type": "application/json"})
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:  # noqa: S310 - fixed https URL
            results = json.loads(resp.read()).get("results", [])
        if len(results) != len(window):
            raise SystemExit(f"OSV returned {len(results)} results for {len(window)} queries")
        for name, result in zip(window, results):
            ids = [v["id"] for v in result.get("vulns", [])]
            if ids:
                found[name] = ids
    return found


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("lockfiles", nargs="+", type=Path)
    args = ap.parse_args()

    failed = False
    for lockfile in args.lockfiles:
        if not lockfile.exists():
            print(f"dep-audit: {lockfile} does not exist", file=sys.stderr)
            return 2
        packages = production_packages(lockfile)
        try:
            hits = query_osv(packages)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            # An unreachable advisory service is not a clean tree. Say which it was, so the
            # next reader does not repeat the half hour spent deciding npm's outage was npm's.
            print(f"dep-audit: OSV could not be reached ({exc}); the audit did not run",
                  file=sys.stderr)
            return 2
        if hits:
            failed = True
            print(f"dep-audit: {lockfile} — {len(hits)} production package(s) with advisories")
            for name in sorted(hits):
                print(f"  {name} {packages[name]}: {', '.join(hits[name])}")
        else:
            print(f"dep-audit: {lockfile} — {len(packages)} production packages, no advisories")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
