"""scripts/deploy.sh can roll back to a tag that only this host has.

2026-10-29: production ran 8707a2a7, a tag scripts/deploy-here.sh built with `buildx --load`
and never pushed. deploy.sh's auto-rollback restored only a tag `docker manifest inspect`
found in the registry, and `--rollback` ran `compose pull`, which fails on a local-only tag,
so after a hand deploy neither rollback could bring the previous stack back.

Not covered: a tag that is neither on the host nor in the registry (nothing can restore it;
the script says so and leaves the new stack up), and the rollback actually running (it needs
the host, ssh and a failing health gate).
"""

from __future__ import annotations

from pathlib import Path

DEPLOY = Path(__file__).resolve().parents[2] / "scripts" / "deploy.sh"


def test_the_auto_rollback_accepts_an_image_this_host_already_has() -> None:
    text = DEPLOY.read_text(encoding="utf-8")
    guard = next(line for line in text.splitlines() if "manifest inspect" in line)
    assert "docker image inspect \\$AURA_REGISTRY/jarvis:$prev" in guard, guard
    assert guard.index("image inspect") < guard.index("manifest inspect"), "local first"


def test_the_rollout_pull_does_not_fail_on_a_local_only_tag() -> None:
    text = DEPLOY.read_text(encoding="utf-8")
    pull = next(line for line in text.splitlines() if "$COMPOSE pull" in line)
    assert "--ignore-pull-failures" in pull, pull
