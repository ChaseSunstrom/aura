"""The disk guard removes only what the host can lose: never what runs, rolls back or holds data."""
from __future__ import annotations

import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path

_spec = importlib.util.spec_from_file_location("disk_guard", Path(__file__).resolve().parents[1] / "disk_guard.py")
assert _spec and _spec.loader
guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guard)

NOW = datetime(2026, 10, 6, 22, 0, tzinfo=timezone.utc)
#: A made-up registry and namespace: the real ones are the host's (AURA_REGISTRY, T-0841).
R = "registry.home.example/owner/jarvis/"
REPO = "owner/jarvis/"


def _img(repo: str, tag: str, hours_ago: int, iid: str | None = None) -> dict:
    return {"ref": f"{R}{repo}:{tag}", "id": iid or f"sha256:{repo}{tag}", "created": NOW - timedelta(hours=hours_ago)}


def test_tags_keep_the_running_the_rollback_the_newest_and_whatever_a_container_uses():
    images = [_img("jarvis", t, h) for t, h in
              [("aaaaaaa1", 1), ("aaaaaaa2", 2), ("aaaaaaa3", 3), ("aaaaaaa4", 4), ("aaaaaaa5", 5), ("aaaaaaa6", 6)]]
    images += [_img("jarvis", "latest", 100), _img("jarvis", "cache", 100),
               {"ref": "python:3.12", "id": "x", "created": NOW}]
    in_use = {"sha256:jarvisaaaaaaa5"}
    out = guard.reclaimable_tags(images, in_use, keep={"aaaaaaa3", "aaaaaaa6"}, repo_path=REPO)
    # newest two (1, 2) kept; 3 and 6 are running/rollback; 5 is in a container's use.
    assert out == [f"{R}jarvis:aaaaaaa4"]


def test_the_repo_path_is_the_host_registrys_and_without_one_nothing_is_pruned(tmp_path):
    host_env = tmp_path / ("." + "env")
    host_env.write_text('AURA_TAG=aaaaaaa1\nJARVIS_REGISTRY="registry.home.example/owner/jarvis"\n')
    assert guard._repo_path(tmp_path) == REPO
    host_env.write_text("AURA_TAG=aaaaaaa1\n")
    assert guard._repo_path(tmp_path) is None
    images = [_img("jarvis", f"aaaaaaa{i}", i) for i in range(1, 6)]
    assert guard.reclaimable_tags(images, set(), keep=set(), repo_path=None) == []


def test_only_old_ci_builders_go_never_the_manual_one_or_a_running_job():
    containers = [
        {"name": "buildx_buildkit_jarvis-builder-139620", "created": NOW - timedelta(hours=15)},
        {"name": "buildx_buildkit_jarvis-builder-148380", "created": NOW - timedelta(minutes=20)},
        {"name": "buildx_buildkit_jarvis-manual0", "created": NOW - timedelta(days=3)},
        {"name": "jarvis", "created": NOW - timedelta(days=3)},
    ]
    assert guard.stale_builders(containers, NOW) == ["buildx_buildkit_jarvis-builder-139620"]


def test_only_old_exited_ci_job_containers_go():
    containers = [
        {"name": "runner-dhw6-project-3-concurrent-0-4a20-build", "created": NOW - timedelta(days=2), "state": "exited"},
        {"name": "runner-dhw6-project-3-concurrent-0-4a20-predefined", "created": NOW - timedelta(days=2), "state": "exited"},
        {"name": "runner-dhw6-project-3-concurrent-1-8830-build", "created": NOW - timedelta(minutes=5), "state": "exited"},
        {"name": "runner-dhw6-project-3-concurrent-1-9ce2-build", "created": NOW - timedelta(days=2), "state": "running"},
        {"name": "jarvis-tauri-dev", "created": NOW - timedelta(days=13), "state": "exited"},
    ]
    assert guard.stale_jobs(containers, NOW) == [c["name"] for c in containers[:2]]


def test_a_named_volume_is_data_and_stays():
    dangling = ["8cb9506cf38e1e327bf59973f6424f3e5bfdac8df2d8c0a4a6042262585c14af",
                "runner-dhw6xrqxy-project-3-concurrent-0-cache-3c3f060a0374fc8bc39395164f415a73",
                "runner-f7ed87579e7f62d12302e8b409a625ce-cache-c33bcaa1fd2c77edfc3893b41966cea8-protected",
                "jarvis-embeddings-cache", "jarvis-tailnet-gobuild", "jarvis-hermes-home"]
    # The protected-branch caches are the big ones: four held 22 GB on 2026-10-08.
    assert guard.reclaimable_volumes(dangling) == dangling[:3]


def test_a_dry_run_removes_nothing_and_says_everything(monkeypatch):
    ran: list = []
    monkeypatch.setattr(guard.subprocess, "run", lambda *a, **k: ran.append(a))
    done = guard.reclaim({"builders": ["b"], "jobs": ["j"], "volumes": ["v"], "tags": ["t"]}, min_free=20, dry_run=True)
    assert ran == []
    assert "docker rm j" in done and "docker builder prune --filter until=12h -f" in done
    assert "docker rm -f b" in done and "docker volume rm b_state" in done and "docker rmi t" in done


def test_tags_mode_drops_unneeded_tags_whatever_the_free_space(monkeypatch, capsys):
    # The free-space trigger alone let 30 deploy tags pile up; a deploy calls --tags last.
    ran = []
    monkeypatch.setattr(guard, "free_gb", lambda *a: 500.0)
    monkeypatch.setattr(guard, "plan", lambda host_dir: {"tags": ["reg/jarvis:aaaa1111"]})
    monkeypatch.setattr(guard.subprocess, "run", lambda args, **k: ran.append(args))
    assert guard.main(["--tags"]) == 0
    assert ["docker", "rmi", "reg/jarvis:aaaa1111"] in ran and ["docker", "image", "prune", "-f"] in ran
    assert guard.main([]) == 0 and len(ran) == 2  # without --tags, 500 GB free does nothing
