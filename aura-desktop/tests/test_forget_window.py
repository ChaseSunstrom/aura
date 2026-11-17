"""`forget_screenshots`: the desktop deletes the screenshots it kept inside a window (T-1399).

Not covered here: the Hermes approval that sends it, and the consent prompt at the machine (the
action is Tier 3, so the dispatcher tests own that). Filenames are stamped in the made-up
clock of `time.time()` at the moment each test runs.
"""

from __future__ import annotations

import time
from dataclasses import replace
from pathlib import Path

import pytest

from aura_desktop.actions.base import Status
from aura_desktop.actions.builtins import TIER_TABLE, all_actions
from aura_desktop.actions.inputauto import ForgetScreenshots
from aura_desktop.policy import ActionTier


def shot(folder: Path, age_min: float, ms: bool = False) -> Path:
    stamp = time.time() - age_min * 60
    name = f"screenshot-{int(stamp * 1000) if ms else int(stamp)}.png"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_bytes(b"\x89PNG")
    return folder / name


def run(ctx, **params):
    return ForgetScreenshots().run(ctx, params)


def test_forget_window_deletes_screenshots_stamped_inside_and_leaves_older(ctx, workspace):
    folder = workspace / "screenshots"
    fresh, fresh_ms = shot(folder, 2), shot(folder, 3, ms=True)
    old, old_ms = shot(folder, 11), shot(folder, 90, ms=True)
    out = run(ctx, minutes=10)
    assert out.ok and out.data["erased"] == 2
    assert not fresh.exists() and not fresh_ms.exists()
    assert old.exists() and old_ms.exists()


def test_forget_window_leaves_a_picture_it_cannot_date_and_says_so(ctx, workspace):
    folder = workspace / "screenshots"
    named = folder / "my-bug-report.png"
    shot(folder, 1)
    named.write_bytes(b"\x89PNG")
    outside = workspace / f"screenshot-{int(time.time())}.png"
    outside.write_bytes(b"x")
    out = run(ctx, minutes=10)
    assert out.data["erased"] == 1 and out.data["undated"] == 1
    assert named.exists() and outside.exists()  # a name of its own and a file outside the folder stay


def test_forget_window_follows_the_configured_folder(ctx, workspace):
    ctx.config = replace(
        ctx.config, input_automation=replace(ctx.config.input_automation, screenshot_dir="pics")
    )
    kept, gone = shot(workspace / "screenshots", 1), shot(workspace / "pics", 1)
    assert run(ctx, minutes=5).data["erased"] == 1
    assert kept.exists() and not gone.exists()


def test_forget_window_never_follows_a_link_out_of_the_folder(ctx, workspace, tmp_path):
    folder = workspace / "screenshots"
    folder.mkdir()
    target = tmp_path / "elsewhere.png"
    target.write_bytes(b"precious")
    (folder / f"screenshot-{int(time.time())}.png").symlink_to(target)
    out = run(ctx, minutes=5)
    assert out.ok and out.data["erased"] == 0 and target.read_bytes() == b"precious"


def test_forget_window_with_no_folder_yet_is_zero(ctx):
    out = run(ctx, minutes=5)
    assert out.ok and out.data["erased"] == 0


@pytest.mark.parametrize("minutes", [0, 1441, -3, "10", None, True, 2.5])
def test_forget_window_takes_minutes_one_to_1440_only(ctx, workspace, minutes):
    kept = shot(workspace / "screenshots", 1)
    out = run(ctx, minutes=minutes)
    assert not out.ok and out.status == Status.ERROR and kept.exists()


def test_forget_window_reports_a_delete_that_failed_instead_of_a_count(ctx, workspace, monkeypatch):
    folder = workspace / "screenshots"
    a, b = shot(folder, 1), shot(folder, 2)
    real = Path.unlink

    def flaky(self, *args, **kw):
        if self == a:
            raise PermissionError("busy")
        return real(self, *args, **kw)

    monkeypatch.setattr(Path, "unlink", flaky)
    out = run(ctx, minutes=5)
    assert not out.ok and "1 of 2" in out.error and not b.exists() and a.exists()


def test_forget_window_is_tier_three_like_a_delete_and_works_without_input_automation(ctx):
    assert TIER_TABLE["forget_screenshots"] == ActionTier.CONFIRM
    assert any(isinstance(a, ForgetScreenshots) for a in all_actions())
    assert ctx.config.input_automation.enabled is False
    assert ForgetScreenshots().available(ctx)
