"""deploy/backup.sh writes an encrypted, mirrored, drilled backup of a volume that changes under it.

tar exits 1 when a file changed while it was read, which a live Hermes home
always has (the sessions database, a WAL). The nightly job on the host left
thirteen of sixteen runs as `.part` for that alone and pruned none of them
(2026-09-02 to 09-17). A shim `tar` stands in for the live volume: it runs the
real tar and then exits with the status under test.

Since T-0568 the file is `hermes-home-*.tar.gz.age` (the tar is piped into `age`, so no
plain tarball ever touches the disk), a copy goes to BACKUP_MIRROR_DIR, and `drill`
restores the newest one into a scratch dir. Everything here runs in tmp_path; the drill
reads a home built by the test, never the live volume or a real backup directory.

The tests need the `age` binary (v1.3.2, sha256-pinned in scripts/install-age.sh): the
one on PATH, else they skip naming the install line. Nothing here reaches the network.
"""

from __future__ import annotations

import os
import re
import shutil
import sqlite3
import subprocess
import time
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "deploy" / "backup.sh"
INSTALL = "bash scripts/install-age.sh .venv/bin"
NEEDS_AGE = pytest.mark.skipif(
    shutil.which("age") is None or shutil.which("age-keygen") is None,
    reason=f"age is not on PATH; install the pinned release with: {INSTALL}")
REAL_TAR = shutil.which("tar") or "tar"


def _keys(tmp_path: Path, name: str = "key") -> tuple[str, Path]:
    ident = tmp_path / f"{name}.txt"
    subprocess.run(["age-keygen", "-o", str(ident)], check=True, capture_output=True)
    pub = subprocess.run(["age-keygen", "-y", str(ident)], check=True, capture_output=True, text=True)
    return pub.stdout.strip(), ident


def _home(root: Path, *, db: bool = True, skills: bool = True, config: str = "model: jarvis\n") -> Path:
    home = root / "home"
    home.mkdir()
    (home / "config.yaml").write_text(config)
    if db:
        con = sqlite3.connect(home / "state.db")
        con.execute("create table sessions (id text primary key, title text)")
        con.execute("insert into sessions values ('s1', 'hello')")
        con.commit()
        con.close()
    if skills:
        (home / "skills" / "greet").mkdir(parents=True)
        (home / "skills" / "greet" / "SKILL.md").write_text("# greet\n")
    return home


def _env(tmp_path: Path, home: Path, *, tar_exit: int = 0, **extra: str) -> dict[str, str]:
    shim = tmp_path / "bin"
    shim.mkdir(exist_ok=True)
    # The shim runs the real tar, then reports the status under test (only when tar succeeded).
    (shim / "tar").write_text(f'#!/bin/sh\n"{REAL_TAR}" "$@"; rc=$?; [ $rc -eq 0 ] && exit {tar_exit}; exit $rc\n')
    (shim / "tar").chmod(0o755)
    scratch = tmp_path / "scratch"
    scratch.mkdir(exist_ok=True)
    return {**os.environ, "PATH": f"{shim}:{os.environ['PATH']}", "HERMES_HOME": str(home),
            "BACKUP_DEST": str(tmp_path / "backups"), "TMPDIR": str(scratch), **extra}


def _sh(env: dict[str, str], *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", str(SCRIPT), *args], env=env, capture_output=True, text=True)


def _once(tmp_path: Path, *, tar_exit: int = 0, mirror: bool = True, **home_kw: bool):
    """One backup of a built home; returns (proc, backups, mirror, identity, env)."""
    pub, ident = _keys(tmp_path)
    home = _home(tmp_path, **home_kw)
    mir = tmp_path / "mirror"
    mir.mkdir()
    extra = {"BACKUP_AGE_RECIPIENT": pub, "BACKUP_AGE_IDENTITY": str(ident)}
    if mirror:
        extra["BACKUP_MIRROR_DIR"] = str(mir)
    env = _env(tmp_path, home, tar_exit=tar_exit, **extra)
    return _sh(env, "once"), tmp_path / "backups", mir, ident, env


def _newest(d: Path) -> Path:
    return sorted(d.glob("hermes-home-*.tar.gz.age"))[-1]


@NEEDS_AGE
def test_a_file_changed_while_reading_is_still_a_finished_backup(tmp_path):
    proc, dst, _, ident, _ = _once(tmp_path, tar_exit=1)
    assert proc.returncode == 0, proc.stderr
    done = list(dst.glob("hermes-home-*"))
    assert len(done) == 1 and done[0].name.endswith(".tar.gz.age") and not list(dst.glob("*.part"))
    assert "wrote" in proc.stdout and "()" not in proc.stdout  # the size is of a file that exists
    plain = subprocess.run(["age", "-d", "-i", str(ident), str(done[0])], capture_output=True, check=True)
    listing = subprocess.run([REAL_TAR, "-tz"], input=plain.stdout, capture_output=True, check=True)
    assert b"./config.yaml" in listing.stdout


@NEEDS_AGE
def test_a_real_tar_failure_leaves_no_half_written_tarball(tmp_path):
    proc, dst, mir, _, _ = _once(tmp_path, tar_exit=2)
    assert proc.returncode == 2
    assert not list(dst.glob("hermes-home-*")) and not list(mir.glob("*")) and "FAILED" in proc.stderr


@NEEDS_AGE
def test_once_without_a_recipient_writes_nothing_rather_than_a_clear_tarball(tmp_path):
    home = _home(tmp_path)
    proc = _sh(_env(tmp_path, home), "once")
    assert proc.returncode != 0 and "BACKUP_AGE_RECIPIENT" in proc.stderr
    assert not list((tmp_path / "backups").glob("*"))


@NEEDS_AGE
def test_once_leaves_no_plain_tarball_and_the_file_is_not_readable_without_the_key(tmp_path):
    proc, dst, mir, _, _ = _once(tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert not list(dst.glob("*.tar.gz")) and not list(dst.glob("*.part"))
    blob = _newest(dst).read_bytes()
    assert blob.startswith(b"age-encryption.org/") and b"config.yaml" not in blob
    # the mirror got the same bytes
    assert (mir / _newest(dst).name).read_bytes() == blob


@NEEDS_AGE
def test_prune_keeps_the_newest_KEEP_in_both_places(tmp_path):
    proc, dst, mir, _, env = _once(tmp_path)
    assert proc.returncode == 0, proc.stderr
    for d in (dst, mir):
        for day in range(1, 6):
            (d / f"hermes-home-2026010{day}T000000Z.tar.gz.age").write_bytes(b"old")
    (dst / "hermes-home-20250101T000000Z.tar.gz").write_bytes(b"a legacy clear tarball")
    again = _sh({**env, "BACKUP_KEEP": "3"}, "once")
    assert again.returncode == 0, again.stderr
    for d in (dst, mir):
        kept = sorted(p.name for p in d.glob("hermes-home-*.tar.gz.age"))
        assert len(kept) == 3 and kept[-1] == _newest(dst).name, kept
        assert "hermes-home-20260101T000000Z.tar.gz.age" not in kept, kept
    assert not (dst / "hermes-home-20260101T000000Z.tar.gz.age").exists()
    assert (dst / "hermes-home-20250101T000000Z.tar.gz").exists()  # a legacy tarball is the owner's to delete


@NEEDS_AGE
def test_an_unwritable_mirror_is_reported_but_the_local_copy_stays(tmp_path):
    pub, ident = _keys(tmp_path)
    env = _env(tmp_path, _home(tmp_path), BACKUP_AGE_RECIPIENT=pub, BACKUP_MIRROR_DIR=str(tmp_path / "no" / "such" / "file"))
    (tmp_path / "no").write_text("a file where the mirror's parent should be")
    proc = _sh(env, "once")
    assert proc.returncode != 0 and "mirror" in proc.stderr
    assert list((tmp_path / "backups").glob("hermes-home-*.tar.gz.age"))


# ---- the drill ---------------------------------------------------------------------------


@NEEDS_AGE
def test_drill_restores_the_newest_backup_and_reads_config_sessions_and_skills_back(tmp_path):
    proc, dst, _, _, env = _once(tmp_path)
    assert proc.returncode == 0, proc.stderr
    (dst / "hermes-home-20200101T000000Z.tar.gz.age").write_bytes(b"older and garbage")  # not the newest
    drill = _sh(env, "drill")
    assert drill.returncode == 0, drill.stderr
    assert "DRILL OK" in drill.stdout and "config.yaml" in drill.stdout
    assert "state.db" in drill.stdout and "skill" in drill.stdout and "mirror" in drill.stdout
    assert not list((tmp_path / "scratch").iterdir()), "the scratch home must be removed"
    assert (tmp_path / "home" / "state.db").exists()  # the source home was only read


@NEEDS_AGE
def test_drill_fails_on_a_corrupt_backup_and_says_so(tmp_path):
    proc, dst, _, _, env = _once(tmp_path)
    f = _newest(dst)
    blob = bytearray(f.read_bytes())
    blob[len(blob) // 2] ^= 0xFF
    blob[-5] ^= 0xFF
    f.write_bytes(bytes(blob))
    drill = _sh(env, "drill")
    assert drill.returncode != 0 and "DRILL FAILED" in drill.stderr and "corrupt" in drill.stderr
    assert not list((tmp_path / "scratch").iterdir())


@NEEDS_AGE
def test_drill_fails_on_a_truncated_backup_and_says_so(tmp_path):
    proc, dst, _, _, env = _once(tmp_path)
    f = _newest(dst)
    f.write_bytes(f.read_bytes()[: f.stat().st_size // 2])
    drill = _sh(env, "drill")
    assert drill.returncode != 0 and "DRILL FAILED" in drill.stderr and "truncated" in drill.stderr


@NEEDS_AGE
def test_drill_fails_when_the_decrypted_file_is_not_a_tarball(tmp_path):
    proc, dst, _, ident, env = _once(tmp_path)
    pub = subprocess.run(["age-keygen", "-y", str(ident)], capture_output=True, text=True).stdout.strip()
    junk = subprocess.run(["age", "-r", pub], input=b"this is not gzip", capture_output=True, check=True).stdout
    _newest(dst).write_bytes(junk)
    drill = _sh(env, "drill")
    assert drill.returncode != 0 and "DRILL FAILED" in drill.stderr and "corrupt" in drill.stderr


@NEEDS_AGE
def test_drill_fails_with_the_wrong_identity_and_names_it(tmp_path):
    proc, dst, _, _, env = _once(tmp_path)
    _, other = _keys(tmp_path, "other")
    drill = _sh({**env, "BACKUP_AGE_IDENTITY": str(other)}, "drill")
    assert drill.returncode != 0 and "DRILL FAILED" in drill.stderr and "identity" in drill.stderr
    assert "corrupt" not in drill.stderr


@NEEDS_AGE
def test_drill_fails_without_an_identity_and_names_the_setting(tmp_path):
    proc, _, _, _, env = _once(tmp_path)
    env = {k: v for k, v in env.items() if k != "BACKUP_AGE_IDENTITY"}
    drill = _sh(env, "drill")
    assert drill.returncode != 0 and "BACKUP_AGE_IDENTITY" in drill.stderr


@NEEDS_AGE
def test_drill_fails_when_the_sessions_db_is_missing(tmp_path):
    proc, _, _, _, env = _once(tmp_path, db=False)
    assert proc.returncode == 0, proc.stderr
    drill = _sh(env, "drill")
    assert drill.returncode != 0 and "DRILL FAILED" in drill.stderr and "state.db" in drill.stderr


@NEEDS_AGE
def test_drill_fails_when_the_sessions_db_does_not_read_back(tmp_path):
    pub, ident = _keys(tmp_path)
    home = _home(tmp_path)
    (home / "state.db").write_bytes(b"SQLite format 3\x00" + os.urandom(4096))
    env = _env(tmp_path, home, BACKUP_AGE_RECIPIENT=pub, BACKUP_AGE_IDENTITY=str(ident),
               BACKUP_MIRROR_DIR=str(tmp_path / "mirror"))
    (tmp_path / "mirror").mkdir()
    assert _sh(env, "once").returncode == 0
    drill = _sh(env, "drill")
    assert drill.returncode != 0 and "state.db" in drill.stderr


@NEEDS_AGE
def test_drill_fails_when_the_skills_are_missing(tmp_path):
    proc, _, _, _, env = _once(tmp_path, skills=False)
    drill = _sh(env, "drill")
    assert drill.returncode != 0 and "skills" in drill.stderr and "DRILL FAILED" in drill.stderr


@NEEDS_AGE
def test_drill_fails_when_the_config_does_not_parse(tmp_path):
    pytest.importorskip("yaml")
    pub, ident = _keys(tmp_path)
    home = _home(tmp_path, config="model: [unclosed\n  : :\n")
    env = _env(tmp_path, home, BACKUP_AGE_RECIPIENT=pub, BACKUP_AGE_IDENTITY=str(ident),
               BACKUP_MIRROR_DIR=str(tmp_path / "mirror"))
    (tmp_path / "mirror").mkdir()
    assert _sh(env, "once").returncode == 0
    drill = _sh(env, "drill")
    assert drill.returncode != 0 and "config.yaml" in drill.stderr


@NEEDS_AGE
def test_drill_fails_when_there_is_no_second_copy(tmp_path):
    proc, dst, mir, _, env = _once(tmp_path, mirror=False)
    drill = _sh(env, "drill")
    assert drill.returncode != 0 and "no second copy" in drill.stderr
    # and when a mirror is set but lacks the newest file
    drill = _sh({**env, "BACKUP_MIRROR_DIR": str(mir)}, "drill")
    assert drill.returncode != 0 and "no second copy" in drill.stderr


@NEEDS_AGE
def test_drill_fails_when_there_is_no_backup_at_all(tmp_path):
    pub, ident = _keys(tmp_path)
    env = _env(tmp_path, _home(tmp_path), BACKUP_AGE_RECIPIENT=pub, BACKUP_AGE_IDENTITY=str(ident))
    drill = _sh(env, "drill")
    assert drill.returncode != 0 and "no backup" in drill.stderr


def _under_or(env: dict[str, str], call: str) -> subprocess.CompletedProcess:
    """Run `call` the way `loop` does: sourced functions, called as `call || echo …`, where bash
    ignores errexit. The script is sourced, not run, so its case statement does not fire."""
    code = f'. "{SCRIPT}"; {call} || echo "fell-through rc=$?"'
    return subprocess.run(["bash", "-c", code], env=env, capture_output=True, text=True, cwd=env["TMPDIR"])


@NEEDS_AGE
def test_drill_under_or_with_an_unusable_tmpdir_stops_and_writes_nothing_outside_it(tmp_path):
    # errexit is off under `||`: a failed mktemp used to leave scratch empty, so the decrypted
    # home went to /home, age.err to /age.err and the trap ran `rm -rf ""`.
    proc, dst, _, _, env = _once(tmp_path)
    assert proc.returncode == 0, proc.stderr
    not_a_dir = tmp_path / "scratch" / "a-file"
    not_a_dir.write_text("mktemp -d cannot make a dir under a file")
    work = tmp_path / "cwd"
    work.mkdir()
    env = {**env, "TMPDIR": str(not_a_dir / "tmp")}
    run = subprocess.run(["bash", "-c", f'. "{SCRIPT}"; drill || echo "fell-through rc=$?"'], env=env,
                         capture_output=True, text=True, cwd=work)
    assert "fell-through rc=1" in run.stdout, (run.stdout, run.stderr)
    assert "DRILL FAILED" in run.stderr and "scratch" in run.stderr
    assert "corrupt" not in run.stderr and "wrong identity" not in run.stderr  # it stopped before decrypting
    assert not list(work.iterdir()) and not Path("/age.err").exists()
    assert sorted(p.name for p in (tmp_path / "scratch").iterdir()) == ["a-file"]
    # the same failure from the command line is a non-zero exit
    assert _sh(env, "drill").returncode != 0


@NEEDS_AGE
def test_once_under_or_with_an_unusable_destination_fails_and_says_so(tmp_path):
    pub, _ = _keys(tmp_path)
    (tmp_path / "blocker").write_text("a file where the backup dir should be")
    env = _env(tmp_path, _home(tmp_path), BACKUP_AGE_RECIPIENT=pub, BACKUP_DEST=str(tmp_path / "blocker" / "b"))
    run = _under_or(env, "once")
    assert "fell-through rc=" in run.stdout and "FAILED" in run.stderr, (run.stdout, run.stderr)
    assert "wrote" not in run.stdout


@NEEDS_AGE
def test_once_with_a_bad_recipient_fails_and_leaves_no_file(tmp_path):
    env = _env(tmp_path, _home(tmp_path), BACKUP_AGE_RECIPIENT="not-an-age-key")
    proc = _sh(env, "once")
    assert proc.returncode != 0 and "FAILED" in proc.stderr
    assert not list((tmp_path / "backups").glob("*")), "neither a .part nor an .age file may remain"


def test_the_loop_says_loudly_what_each_unset_setting_stops(tmp_path):
    env = {k: v for k, v in os.environ.items() if not k.startswith("BACKUP_")}
    run = subprocess.run(["bash", "-c", f'. "{SCRIPT}"; preflight'], env=env, capture_output=True, text=True)
    err = run.stderr
    for name, stops in (("BACKUP_AGE_RECIPIENT", "no backups"), ("BACKUP_AGE_IDENTITY", "no drill"),
                        ("BACKUP_MIRROR_DIR", "drill fails")):
        line = next((ln for ln in err.splitlines() if name in ln), "")
        assert stops in line and "WARNING" in line, (name, err)
    full = {**env, "BACKUP_AGE_RECIPIENT": "age1x", "BACKUP_AGE_IDENTITY": "/k", "BACKUP_MIRROR_DIR": "/m"}
    quiet = subprocess.run(["bash", "-c", f'. "{SCRIPT}"; preflight'], env=full, capture_output=True, text=True)
    assert quiet.stderr == ""


@NEEDS_AGE
@pytest.mark.parametrize("keep", ["0", "00", "-3", "many"])
def test_a_keep_below_one_never_deletes_the_file_just_written(tmp_path, keep):
    proc, dst, mir, _, env = _once(tmp_path)
    assert proc.returncode == 0, proc.stderr
    (dst / "hermes-home-20260101T000000Z.tar.gz.age").write_bytes(b"old")
    again = _sh({**env, "BACKUP_KEEP": keep}, "once")
    assert again.returncode == 0, again.stderr
    assert "BACKUP_KEEP" in again.stderr and "14" in again.stderr
    assert _newest(dst).exists() and len(list(dst.glob("hermes-home-*.age"))) >= 2


@NEEDS_AGE
def test_prune_handles_a_backup_directory_with_spaces(tmp_path):
    pub, _ = _keys(tmp_path)
    dst = tmp_path / "back ups"
    dst.mkdir()
    for day in range(1, 5):
        (dst / f"hermes-home-2026010{day}T000000Z.tar.gz.age").write_bytes(b"old")
    env = _env(tmp_path, _home(tmp_path), BACKUP_AGE_RECIPIENT=pub, BACKUP_DEST=str(dst), BACKUP_KEEP="2")
    assert _sh(env, "once").returncode == 0
    kept = sorted(p.name for p in dst.glob("hermes-home-*.age"))
    assert len(kept) == 2 and "hermes-home-20260101T000000Z.tar.gz.age" not in kept, kept


@NEEDS_AGE
def test_a_drill_that_cannot_extract_prints_tars_first_line_and_names_a_full_scratch(tmp_path):
    proc, dst, _, ident, env = _once(tmp_path)
    pub = subprocess.run(["age-keygen", "-y", str(ident)], capture_output=True, text=True).stdout.strip()
    junk = subprocess.run(["age", "-r", pub], input=b"this is not gzip", capture_output=True, check=True).stdout
    _newest(dst).write_bytes(junk)
    err = _sh(env, "drill").stderr
    assert "scratch space is full" in err and "tar said" in err and "gzip" in err.lower(), err


# ---- the status the daily health check reads (T-0679) -------------------------------------


def _status(d: Path, kind: str) -> list[str]:
    return (d / f"{kind}.status").read_text().split()


@NEEDS_AGE
def test_the_nightly_and_the_drill_leave_a_status_on_pass_and_on_fail(tmp_path):
    st = tmp_path / "status"
    pub, ident = _keys(tmp_path)
    mir = tmp_path / "mirror"
    mir.mkdir()
    env = _env(tmp_path, _home(tmp_path), BACKUP_AGE_RECIPIENT=pub, BACKUP_AGE_IDENTITY=str(ident),
               BACKUP_MIRROR_DIR=str(mir), BACKUP_STATUS_DIR=str(st))
    assert _sh(env, "once").returncode == 0
    kind, when, verdict = _status(st, "nightly")
    assert (kind, verdict) == ("nightly", "ok") and abs(int(when) - time.time()) < 120
    assert _sh(env, "drill").returncode == 0
    assert _status(st, "drill")[::2] == ["drill", "ok"]
    # a failed drill (the newest backup is damaged) and a failed nightly each say failed
    f = _newest(tmp_path / "backups")
    f.write_bytes(f.read_bytes()[: f.stat().st_size // 2])
    assert _sh(env, "drill").returncode != 0
    assert _status(st, "drill")[::2] == ["drill", "failed"]
    assert _sh({**env, "BACKUP_AGE_RECIPIENT": "age1nope"}, "once").returncode != 0
    assert _status(st, "nightly")[::2] == ["nightly", "failed"]
    # nothing but the two status files: no temp name left, and no path or key in them
    assert sorted(p.name for p in st.iterdir()) == ["drill.status", "nightly.status"]
    text = "".join(p.read_text() for p in st.iterdir())
    assert str(tmp_path) not in text and pub not in text


@NEEDS_AGE
def test_a_status_that_cannot_be_written_changes_neither_the_exit_code_nor_the_result(tmp_path):
    blocked = tmp_path / "blocked"
    blocked.write_text("a file where the directory should be")
    proc, _, _, _, env = _once(tmp_path)
    assert proc.returncode == 0  # no BACKUP_STATUS_DIR at all: skipped silently
    env = {**env, "BACKUP_STATUS_DIR": str(blocked / "status")}
    assert _sh(env, "once").returncode == 0
    assert _sh({**env, "BACKUP_AGE_RECIPIENT": ""}, "once").returncode == 2  # still its own code
    assert _sh({**env, "BACKUP_MIRROR_DIR": ""}, "drill").returncode == 1
    assert blocked.read_text() == "a file where the directory should be"


@NEEDS_AGE
def test_the_loop_records_a_nightly_that_fails_like_the_one_by_hand(tmp_path):
    st = tmp_path / "status"
    env = _env(tmp_path, _home(tmp_path), BACKUP_STATUS_DIR=str(st))  # no recipient
    code = f'. "{SCRIPT}"; run_once || echo "fell-through rc=$?"; run_drill || echo "drill rc=$?"'
    out = subprocess.run(["bash", "-c", code], env=env, capture_output=True, text=True, cwd=env["TMPDIR"])
    assert "fell-through rc=2" in out.stdout and "drill rc=1" in out.stdout, out.stdout + out.stderr
    assert _status(st, "nightly")[::2] == ["nightly", "failed"]
    assert _status(st, "drill")[::2] == ["drill", "failed"]


# ---- the wiring --------------------------------------------------------------------------


def test_the_status_volume_is_the_only_thing_the_assistant_shares_with_the_backup_service():
    doc = yaml.safe_load((ROOT / "deploy" / "docker-compose.yml").read_text())
    svc, hermes = doc["services"]["jarvis-backup"], doc["services"]["jarvis"]
    assert "backup-status" in doc["volumes"]
    assert "backup-status:/run/backup-status" in svc["volumes"]
    assert svc["environment"]["BACKUP_STATUS_DIR"] == "/run/backup-status"
    assert [v for v in hermes["volumes"] if v.startswith("backup-status")] == ["backup-status:/opt/backup-status:ro"]
    # the assistant never sees the backups, the mirror or the identity
    assert not any("/backups" in v or "/mirror" in v or "backup-age" in v for v in hermes["volumes"])
    assert "BACKUP_DIR" not in str(hermes)


def test_the_backup_service_has_a_tmpfs_tmp_and_the_image_checks_its_python_modules():
    compose = (ROOT / "deploy" / "docker-compose.yml").read_text()
    svc = compose[compose.index("  jarvis-backup:"):compose.index("  # ---- n8n")]
    assert "tmpfs:" in svc and "/tmp:size=" in svc  # the drill's clear text lives only there
    assert "import yaml, sqlite3" in (ROOT / "deploy" / "Dockerfile").read_text()
    # The runbook is reachable from the front page: the README links the runbook index, and the
    # index names it (the README stopped listing each runbook when it became the project's
    # front page, T-0865; a runbook in neither is one nobody finds).
    assert "docs/runbooks/README.md" in (ROOT / "README.md").read_text()
    assert "jarvis-backup.md" in (ROOT / "docs" / "runbooks" / "README.md").read_text()
    assert "tmpfs" in (ROOT / "docs" / "runbooks" / "jarvis-backup.md").read_text()


def test_the_owner_settings_are_in_compose_env_example_and_the_image():
    compose = (ROOT / "deploy" / "docker-compose.yml").read_text()
    svc = compose[compose.index("  jarvis-backup:"):compose.index("  # ---- n8n")]
    for name in ("BACKUP_AGE_RECIPIENT", "BACKUP_AGE_IDENTITY", "BACKUP_MIRROR_DIR"):
        assert name in svc, name
        assert name in (ROOT / "deploy" / ".env.example").read_text(), name
    # the identity is mounted read-only; the nightly never needs it, the drill does
    assert ":ro" in svc[svc.index("BACKUP_AGE_IDENTITY", svc.index("volumes:")):]
    assert "install-age.sh" in (ROOT / "deploy" / "Dockerfile").read_text()
    assert "install-age.sh" in (ROOT / ".gitlab-ci.yml").read_text()  # test:config-render runs this file's tests


def test_the_install_script_pins_a_version_and_checks_sha256():
    text = (ROOT / "scripts" / "install-age.sh").read_text()
    assert "sha256sum -c" in text and "AGE_VERSION=" in text and len(text.split("AGE_SHA256_AMD64=")[1].split()[0].strip('"')) == 64


def test_the_image_builds_the_pinned_age_with_a_patched_go():
    """T-0758: age's release binaries (v1.2.1 and v1.3.2 alike) are built with a Go whose stdlib
    has critical fixed CVEs, so build:jarvis's grype gate failed on /usr/local/bin/age (pipeline
    681). The image builds the version install-age.sh pins from its module, in a Go stage the
    gate passes. T-0766: age v1.3.2 requires a golang.org/x/crypto with High advisories, so the
    stage builds it in a throwaway module that requires a fixed one. Does not cover: a Go or
    x/crypto CVE published after these pins; that is a bump."""
    dockerfile = (ROOT / "deploy" / "Dockerfile").read_text()
    stage = re.search(r"^FROM golang:(\d+)\.(\d+)\.(\d+)-\S+ AS age$", dockerfile, re.M)
    assert stage and tuple(map(int, stage.groups())) >= (1, 27, 3), "age needs a Go with the fixes"
    assert 'go get "filippo.io/age/cmd/age@$v"' in dockerfile and "COPY --from=age" in dockerfile
    assert "go install -trimpath filippo.io/age/cmd/age " in dockerfile
    crypto = re.search(r"golang\.org/x/crypto@v0\.(\d+)\.", dockerfile)
    assert crypto and int(crypto.group(1)) >= 55, "age needs x/crypto v0.55.0 or later (T-0766)"
    assert "RUN bash /tmp/install-age.sh" not in dockerfile, "the release binary failed the gate"
    assert 'AGE_VERSION="v1.3.2"' in (ROOT / "scripts" / "install-age.sh").read_text()
