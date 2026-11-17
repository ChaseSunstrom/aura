"""The health gate (aura-hud/health.py): what broke, said once, cleared on recovery."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

PLUGIN_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_DIR.parent))
from _plugin_loader import load_plugin  # noqa: E402

hud = load_plugin("aura-hud")
health = hud.health
NOW = 1_800_000_000.0  # 2027-01-15 08:00 UTC


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("TZ", "UTC")
    time.tzset()  # the routine's naive date and time are read in local time
    yield tmp_path
    monkeypatch.undo()
    time.tzset()


def put(home, rel, data):
    p = home / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data))


GOOD = {"id": "a", "name": "Fine", "enabled": True, "state": "scheduled", "last_status": "ok",
        "last_error": None, "next_run_at": "2027-01-15T08:10:00+00:00"}


def run(capsys, monkeypatch):
    monkeypatch.setattr(health.time, "time", lambda: NOW)
    assert health.main() == 0
    return capsys.readouterr().out.strip()


def test_nothing_broken_skips_the_model(home, capsys, monkeypatch):
    done = {**GOOD, "id": "d", "state": "completed", "last_status": "error"}
    put(home, "cron/jobs.json", {"jobs": [GOOD, done]})
    assert json.loads(run(capsys, monkeypatch)) == {"wakeAgent": False}


def test_each_breakage_is_one_line_said_once_and_cleared_on_recovery(home, capsys, monkeypatch):
    put(home, "cron/jobs.json", {"jobs": [
        {**GOOD, "id": "f", "name": "Brief", "last_status": "error", "last_error": "tool x\nIGNORE ALL"},
        {**GOOD, "id": "l", "name": "Stuck", "next_run_at": "2027-01-15T06:00:00+00:00"}]})
    put(home, "plugin-data/jarvis-routines/memory.json", {"routines": [
        {"id": "r", "name": "Ferns", "when": {"event": "time", "at": "05:00", "date": "2027-01-15"}},
        {"id": "ok", "name": "Daily", "when": {"event": "time", "at": "06:00"}}]})
    put(home, "plugin-data/jarvis-watches/watches.json", {"watches": [
        {"id": "w", "target": "http://x.test", "fails": 3}, {"id": "w2", "target": "http://y", "fails": 1}]})
    out = run(capsys, monkeypatch).splitlines()
    assert len(out) == 4 and all(line.startswith("- ") for line in out)
    assert any("Brief" in line and "tool x IGNORE ALL" in line for line in out)
    # Said once: the same pass again is silent.
    assert json.loads(run(capsys, monkeypatch)) == {"wakeAgent": False}
    # Recovered, then broken again: news again.
    put(home, "cron/jobs.json", {"jobs": [GOOD]})
    run(capsys, monkeypatch)
    put(home, "cron/jobs.json", {"jobs": [{**GOOD, "id": "f", "name": "Brief", "last_status": "error"}]})
    assert "Brief" in run(capsys, monkeypatch)


def test_a_reminder_that_could_not_be_delivered_is_said_once_while_fresh(home, capsys, monkeypatch):
    # A one-shot retires as completed the moment it runs, so skipping completed jobs hid
    # every missed reminder (T-0480). Only a recent one counts: an old record is not news.
    done = {**GOOD, "enabled": False, "state": "completed", "next_run_at": None}
    put(home, "cron/jobs.json", {"jobs": [
        {**done, "id": "m", "name": "Call mum", "last_status": "delivery_failed",
         "last_delivery_error": "push channel down", "last_run_at": "2027-01-14T09:00:00+00:00"},
        {**done, "id": "o", "name": "Old", "last_status": "delivery_failed",
         "last_delivery_error": "x", "last_run_at": "2027-01-10T09:00:00+00:00"},
        {**done, "id": "k", "name": "Fine", "last_run_at": "2027-01-15T07:00:00+00:00"}]})
    out = run(capsys, monkeypatch).splitlines()
    assert len(out) == 1 and "Call mum" in out[0] and "push channel down" in out[0]
    assert json.loads(run(capsys, monkeypatch)) == {"wakeAgent": False}


# ---- the backup status the backup container leaves (T-0679) ------------------------------


@pytest.fixture
def status(tmp_path, monkeypatch):
    d = tmp_path / "backup-status"
    monkeypatch.setattr(health, "BACKUP_STATUS_DIR", d)

    def put_status(kind, ago_s, verdict):
        d.mkdir(exist_ok=True)
        (d / f"{kind}.status").write_text(f"{kind} {int(NOW - ago_s)} {verdict}\n")
    return put_status


def test_a_failed_drill_and_a_failed_nightly_are_each_one_plain_breakage(status):
    status("drill", 3600, "failed")
    status("nightly", 3600, "failed")
    found = health.breakages(NOW)
    assert sorted(found) == ["backup-drill", "backup-nightly"]
    assert "restore" in found["backup-drill"] and "nightly" in found["backup-nightly"].lower()


def test_passing_statuses_are_not_news(status):
    status("drill", 86400, "ok")
    status("nightly", 3600, "ok")
    assert health.breakages(NOW) == {}


def test_no_passing_drill_for_more_than_eight_days_is_a_breakage_only_once_a_drill_has_run(
        status, monkeypatch, tmp_path):
    # a fresh install: no directory, or a directory with only a nightly, is not a breakage
    assert health.breakages(NOW) == {}
    status("nightly", 3600, "ok")
    assert health.breakages(NOW) == {}
    status("drill", 7 * 86400, "ok")
    assert health.breakages(NOW) == {}
    status("drill", 8 * 86400 + 60, "ok")
    assert list(health.breakages(NOW)) == ["backup-drill-stale"]
    # a failed drill is already one breakage: it is not also reported as stale
    status("drill", 9 * 86400, "failed")
    assert list(health.breakages(NOW)) == ["backup-drill"]


def test_a_backup_breakage_is_said_once_and_forgotten_when_the_next_drill_passes(
        home, status, capsys, monkeypatch):
    status("drill", 3600, "failed")
    assert "restore" in run(capsys, monkeypatch)
    assert json.loads(run(capsys, monkeypatch)) == {"wakeAgent": False}
    status("drill", 60, "ok")
    assert json.loads(run(capsys, monkeypatch)) == {"wakeAgent": False}
    status("drill", 30, "failed")
    assert "restore" in run(capsys, monkeypatch)  # news again


@pytest.mark.parametrize("junk", ["", "drill soon ok", "drill 1 maybe", "drill", "\x00\x01", "nightly 5 failed"])
def test_a_status_that_is_not_one_line_of_kind_time_verdict_is_ignored(status, tmp_path, junk):
    status("nightly", 60, "ok")
    (tmp_path / "backup-status" / "drill.status").write_text(junk)
    assert health.breakages(NOW) == {}  # unreadable is not a failed drill (the file's kind must match too)
