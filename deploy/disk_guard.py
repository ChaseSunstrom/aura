#!/usr/bin/env python3
"""Give disk back on the JARVIS host before it fills, from the classes known to be safe.

The host is production, the dev box and the CI runner at once. On 2026-10-06 its root disk
reached 100 % mid-session: a container build failed with ENOSPC and every image pull and
apt step after it would have too. What filled it was always the same four things
(``ci-disk-hygiene``): buildx builders left by killed CI jobs, each with a ~5 GB state
volume; anonymous and runner-cache volumes nothing mounts; a 5 GB ``jarvis:<sha>`` tag per
deploy; and buildx cache. Reclaiming them by hand worked every time and was always late.

Run hourly from cron. Below ``--min-free-gb`` it removes, in this order, until there is
room: stale CI builders and their state, dangling anonymous and runner-cache volumes,
deploy tags the running and rollback releases do not need, then old build cache (the
manual builder's and the default one's). Exited CI job containers go with the builders. Nothing
else is touched.

What it never does: remove an image any container (running or stopped) uses, the running
``AURA_TAG``, the ``.previous_tag`` that ``make rollback`` pulls, the newest
``KEEP_TAGS`` sha tags of each image, a ``latest``/``cache`` tag, a named volume (the
embeddings cache, the Hermes home), a builder younger than ``BUILDER_MAX_AGE_H`` (a CI job
may be using it), or anything outside Docker -- the models under ``aura-core/`` stay.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

#: Sha tags kept per image beyond the running and rollback ones, newest first.
KEEP_TAGS = 2
#: A CI builder older than this is a killed job's leftover (jobs time out at 60 min).
BUILDER_MAX_AGE_H = 6
SHA_TAG = re.compile(r"^[0-9a-f]{7,40}$")
ANON_VOLUME = re.compile(r"^[0-9a-f]{64}$")
RUNNER_CACHE = re.compile(r"^runner-.*-cache-[0-9a-f]+(-protected)?$")
CI_BUILDER = re.compile(r"^buildx_buildkit_jarvis-builder-\d+$")
#: A CI job's own containers; the runner leaves them exited when a job dies (2.2 GB one, 2026-10-08).
CI_JOB = re.compile(r"^runner-.*-(build|predefined)$")


def stale_builders(containers: list[dict], now: datetime) -> list[str]:
    """CI builder containers older than BUILDER_MAX_AGE_H; ``jarvis-manual`` is never one."""
    cutoff = now - timedelta(hours=BUILDER_MAX_AGE_H)
    return [c["name"] for c in containers
            if CI_BUILDER.match(c["name"]) and c["created"] < cutoff]


def stale_jobs(containers: list[dict], now: datetime) -> list[str]:
    """Exited CI job containers older than BUILDER_MAX_AGE_H; a running job's are never one."""
    cutoff = now - timedelta(hours=BUILDER_MAX_AGE_H)
    return [c["name"] for c in containers
            if CI_JOB.match(c["name"]) and c.get("state") == "exited" and c["created"] < cutoff]


def reclaimable_volumes(dangling: list[str]) -> list[str]:
    """Dangling anonymous and runner-cache volumes; a named volume is someone's data."""
    return [v for v in dangling if ANON_VOLUME.match(v) or RUNNER_CACHE.match(v)]


def reclaimable_tags(images: list[dict], in_use: set[str], keep: set[str],
                     repo_path: str | None) -> list[str]:
    """``repo:tag`` refs under the deploys' repo path with a sha tag that nothing needs.

    ``images`` rows carry ``ref`` (repo:tag), ``id`` and ``created``; ``in_use`` is the image
    ids containers use; ``keep`` the tags never removed (running and rollback); ``repo_path``
    the namespace the deploys tag into (``<ns>/jarvis/``), None for nothing at all.
    """
    by_repo: dict[str, list[dict]] = {}
    for img in images:
        repo, _, tag = img["ref"].rpartition(":")
        if repo_path and repo.partition("/")[2].startswith(repo_path) and SHA_TAG.match(tag):
            by_repo.setdefault(repo, []).append(img)
    out = []
    for rows in by_repo.values():
        rows.sort(key=lambda r: r["created"], reverse=True)
        for i, img in enumerate(rows):
            tag = img["ref"].rpartition(":")[2]
            if i < KEEP_TAGS or tag in keep or img["id"] in in_use:
                continue
            out.append(img["ref"])
    return out


def reclaim_tags(refs: list[str], dry_run: bool) -> list[str]:
    """Remove the given deploy tags, then the layers nothing references any more."""
    done = []
    for args in [("rmi", ref) for ref in refs] + [("image", "prune", "-f")]:
        done.append("docker " + " ".join(args))
        if not dry_run:
            subprocess.run(["docker", *args], capture_output=True, text=True)
    return done


# --- the thin part that talks to Docker --------------------------------------------------

def _docker(*args: str) -> str:
    return subprocess.run(["docker", *args], capture_output=True, text=True, check=True).stdout


def _when(text: str) -> datetime:
    # "2026-10-06 19:15:59 +0000 UTC" (docker ps/images) -> aware datetime
    return datetime.strptime(" ".join(text.split()[:3]), "%Y-%m-%d %H:%M:%S %z")


def free_gb(path: str = "/") -> float:
    return shutil.disk_usage(path).free / 2**30


def _host_env(host_dir: Path, key: str) -> str:
    """``key`` from the host's .env, or its pre-T-0959 ``JARVIS_`` spelling (that file is not the repo's to rename)."""
    env = host_dir / ("." + "env")
    names = (key + "=", key.replace("AURA_", "JARVIS_", 1) + "=")
    for line in env.read_text().splitlines() if env.exists() else []:
        if line.startswith(names):
            return line.split("=", 1)[1].strip().strip('"')
    return ""


def _repo_path(host_dir: Path) -> str | None:
    """``<ns>/jarvis/`` from the host's AURA_REGISTRY (``<registry>/<ns>/jarvis``), or None.

    Matched by path, not host, because the registry's host name has changed twice and a
    host-prefix match silently stopped pruning every image tagged under the new name. Read from
    the host's .env because the namespace is the owner's and the repo names none (T-0841).
    """
    path = _host_env(host_dir, "AURA_REGISTRY").partition("/")[2].strip("/")
    return path + "/" if path else None


def _keep_tags(host_dir: Path) -> set[str]:
    keep = {_host_env(host_dir, "AURA_TAG")}
    prev = host_dir / ".previous_tag"
    if prev.exists():
        keep.add(prev.read_text().strip())
    return {k for k in keep if k}


def plan(host_dir: Path) -> dict[str, list[str]]:
    now = datetime.now(timezone.utc)
    containers = [json.loads(line) for line in _docker("ps", "-a", "--format", "{{json .}}").splitlines()]
    in_use = set()
    for c in containers:
        try:
            in_use.add(_docker("inspect", "--format", "{{.Image}}", c["ID"]).strip())
        except subprocess.CalledProcessError:
            pass  # gone since `docker ps` (a CI job container on this host); it uses nothing now
    rows = [{"name": c["Names"], "created": _when(c["CreatedAt"]), "state": c.get("State", "")} for c in containers]
    builders = stale_builders(rows, now)
    volumes = reclaimable_volumes(_docker("volume", "ls", "-q", "-f", "dangling=true").split())
    images = []
    for line in _docker("images", "--no-trunc", "--format", "{{json .}}").splitlines():
        row = json.loads(line)
        if row["Tag"] != "<none>":
            images.append({"ref": f"{row['Repository']}:{row['Tag']}", "id": row["ID"],
                           "created": _when(row["CreatedAt"])})
    return {"builders": builders, "jobs": stale_jobs(rows, now), "volumes": volumes,
            "tags": reclaimable_tags(images, in_use, _keep_tags(host_dir), _repo_path(host_dir))}


def reclaim(steps: dict[str, list[str]], min_free: float, dry_run: bool) -> list[str]:
    """Run each class in order, stopping once there is room; the actions, for the log."""
    done: list[str] = []

    def act(*args: str) -> None:
        done.append("docker " + " ".join(args))
        if not dry_run:
            subprocess.run(["docker", *args], capture_output=True, text=True)

    for name in steps["builders"]:
        act("rm", "-f", name)
        act("volume", "rm", f"{name}_state")
    for name in steps.get("jobs", []):
        act("rm", name)
    for vol in steps["volumes"]:
        act("volume", "rm", vol)
    if dry_run or free_gb() < min_free:
        done += reclaim_tags(steps["tags"], dry_run)
    if dry_run or free_gb() < min_free:
        act("buildx", "prune", "--builder", "jarvis-manual", "--filter", "until=48h", "-f")
        # The default builder's cache: CI's buildx jobs fill it (32 GB on 2026-10-08, when the
        # disk reached 100 % with every class above already reclaimed).
        act("builder", "prune", "--filter", "until=12h", "-f")
    return done


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--min-free-gb", type=float, default=20.0)
    ap.add_argument("--host-dir", type=Path, default=Path("/opt/jarvis-host"))
    ap.add_argument("--dry-run", action="store_true", help="say what would go; remove nothing")
    ap.add_argument("--tags", action="store_true",
                    help="after a deploy: drop unneeded deploy tags now, whatever the free space")
    args = ap.parse_args(argv)
    if args.tags:
        # A deploy pulls five commit-tagged images (6 GB for jarvis) and the free-space trigger
        # let 30 of them pile up (2026-10-09); deploy.sh and deploy-here.sh call this last.
        done = reclaim_tags(plan(args.host_dir)["tags"], args.dry_run)
        print(f"disk-guard --tags: {len(done)} actions", *done, sep="\n  ")
        return 0
    before = free_gb()
    if before >= args.min_free_gb and not args.dry_run:
        return 0
    done = reclaim(plan(args.host_dir), args.min_free_gb, args.dry_run)
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    print(f"{stamp} disk-guard: {before:.1f} GB free -> {free_gb():.1f} GB "
          f"({'dry run, ' if args.dry_run else ''}{len(done)} actions)")
    for line in done:
        print(f"  {line}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
