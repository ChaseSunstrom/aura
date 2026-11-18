#!/usr/bin/env python3
"""The public repo's GitHub Actions runs for one commit, and each failure's own words (T-1554).

    AURA_GH_REPO=owner/name AURA_RELEASE_DIR=/path/to/release-copy \
      python3 scripts/verify/gh-runs.py --head [--wait 1800]

Reads the public API unauthenticated (60 requests an hour): each workflow run for the commit,
each failed job's failed steps, and the job's error annotations, which scripts/ci/gh-step.sh
fills with a failing step's output tail. The repo and the release copy come from the
environment, never from a tracked file.

Exit 0 when every run for the commit concluded success (skipped and neutral count as success),
1 when one failed, 2 when none were found or some are still running at the end of the wait.
Does not cover: a run GitHub has not queued yet when the wait ends.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request

API = "https://api.github.com/repos/"
OK = {"success", "skipped", "neutral"}


def _get(path: str) -> dict | list:
    req = urllib.request.Request(API + path, headers={"Accept": "application/vnd.github+json",
                                                      "User-Agent": "gh-runs-check"})
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 - a fixed https host
        return json.load(resp)


def _runs(repo: str, sha: str) -> list[dict]:
    data = _get(f"{repo}/actions/runs?head_sha={sha}&per_page=50")
    return list(data.get("workflow_runs", [])) if isinstance(data, dict) else []


def _report(repo: str, run: dict) -> None:
    print(f"{run['name']}: {run['conclusion'] or run['status']}")
    if run.get("conclusion") in OK or run.get("status") != "completed":
        return
    data = _get(f"{repo}/actions/runs/{run['id']}/jobs?per_page=100")
    for job in data.get("jobs", []) if isinstance(data, dict) else []:
        if job.get("conclusion") in OK:
            continue
        steps = [s["name"] for s in job.get("steps", []) if s.get("conclusion") == "failure"]
        print(f"  job {job['name']}: {job.get('conclusion')} at {steps}")
        notes = _get(f"{repo}/check-runs/{job['id']}/annotations")
        for note in notes if isinstance(notes, list) else []:
            if note.get("annotation_level") == "failure":
                for line in str(note.get("message") or "").splitlines()[-40:]:
                    print(f"    | {line}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--repo", default=os.environ.get("AURA_GH_REPO", ""))
    ap.add_argument("--sha", default="")
    ap.add_argument("--head", action="store_true", help="the release copy's HEAD (AURA_RELEASE_DIR)")
    ap.add_argument("--wait", type=int, default=0, help="seconds to wait for the runs to finish")
    args = ap.parse_args(argv)
    sha = args.sha
    if args.head:
        where = os.environ.get("AURA_RELEASE_DIR", "")
        if not where:
            ap.error("--head needs AURA_RELEASE_DIR")
        sha = subprocess.run(["git", "-C", where, "rev-parse", "HEAD"], capture_output=True,
                             text=True, check=True).stdout.strip()
    if not args.repo or not sha:
        ap.error("a repo (--repo or AURA_GH_REPO) and a commit (--sha or --head) are needed")
    end = time.time() + args.wait
    while True:
        runs = _runs(args.repo, sha)
        done = bool(runs) and all(r.get("status") == "completed" for r in runs)
        if done or time.time() >= end:
            break
        time.sleep(60)
    print(f"commit {sha[:7]}: {len(runs)} run(s)")
    for run in sorted(runs, key=lambda r: r["name"]):
        _report(args.repo, run)
    if not done:
        return 2
    return 0 if all(r.get("conclusion") in OK for r in runs) else 1


if __name__ == "__main__":
    sys.exit(main())
