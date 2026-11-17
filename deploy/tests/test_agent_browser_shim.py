"""T-0795: a second browsing session gets its own Chrome profile instead of dying on the first
one's profile lock.

Hermes gives every session its own agent-browser daemon and socket dir but one shared
AGENT_BROWSER_PROFILE, so the second Chrome fails on the first one's SingletonLock. The shim in
front of the real binary picks the profile per session. These tests drive the real shim script
with a fake "real binary" that prints the profile it was launched with; nothing here starts
Chrome, so "never ran with two real concurrent sessions" stays true (docs/verification.md).
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

DEPLOY = Path(__file__).resolve().parents[1]
SHIM = DEPLOY / "agent-browser-shim.sh"
DOCKERFILE = (DEPLOY / "Dockerfile").read_text(encoding="utf-8")


@pytest.fixture
def box(tmp_path):
    fake = tmp_path / "real"
    fake.write_text('#!/bin/sh\nprintf "%s" "$AGENT_BROWSER_PROFILE"\n')
    fake.chmod(0o755)
    (tmp_path / "profile").mkdir()
    procs: list[subprocess.Popen] = []

    class Box:
        root = tmp_path
        profile = tmp_path / "profile"
        owner = tmp_path / "profile.owner"

        def sock(self, name: str) -> Path:
            d = tmp_path / f"agent-browser-{name}"
            d.mkdir(exist_ok=True)
            return d

        def run(self, name: str | None, *, profile: bool = True) -> str:
            env = {"PATH": os.environ["PATH"], "AGENT_BROWSER_REAL": str(fake)}
            if profile:
                env["AGENT_BROWSER_PROFILE"] = str(self.profile)
            if name is not None:
                env["AGENT_BROWSER_SOCKET_DIR"] = str(self.sock(name))
            out = subprocess.run(
                ["sh", str(SHIM), "open", "about:blank"], env=env, capture_output=True,
                text=True, timeout=30, check=True,
            )
            return out.stdout

        def live(self, name: str = "sleep") -> int:
            exe = tmp_path / name
            if not exe.exists():
                shutil.copy(shutil.which("sleep"), exe)
            p = subprocess.Popen([str(exe), "60"])
            procs.append(p)
            return p.pid

        def dead_pid(self) -> int:
            p = subprocess.Popen(["true"])
            p.wait()
            return p.pid

        def daemon(self, name: str, pid: int) -> None:
            (self.sock(name) / "s.pid").write_text(str(pid))

        def lock(self, pid: int) -> None:
            for n in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
                os.symlink(f"host-{pid}", self.profile / n)

        def old_owner(self, name: str) -> None:
            self.owner.write_text(str(self.sock(name)))
            os.utime(self.owner, (1, 1))

    yield Box()
    for p in procs:
        p.kill()
        p.wait()


def test_no_session_env_is_passthrough(box):
    assert box.run("a", profile=False) == ""
    assert box.run(None) == str(box.profile)
    assert not box.owner.exists()


def test_free_profile_is_shared_and_owner_written(box):
    assert box.run("a") == str(box.profile)
    assert box.owner.read_text().strip() == str(box.sock("a"))
    assert (box.sock("a") / "profile-shared").exists()


def test_held_by_a_live_other_session_gets_a_private_profile(box):
    assert box.run("a") == str(box.profile)
    box.daemon("a", box.live())
    os.utime(box.owner, (1, 1))  # not the 60 s rule: the live daemon alone holds it
    private = box.run("b")
    assert private == str(box.sock("b") / "profile")
    assert (box.sock("b") / "profile").stat().st_mode & 0o777 == 0o700
    assert box.owner.read_text().strip() == str(box.sock("a"))


def test_private_session_stays_private_while_its_daemon_lives(box):
    box.run("a")
    a_pid = box.live()
    box.daemon("a", a_pid)
    assert box.run("b") == str(box.sock("b") / "profile")
    box.daemon("b", box.live())
    os.kill(a_pid, 9)  # the holder goes away: b must not flip mid-life
    os.utime(box.owner, (1, 1))
    assert box.run("b") == str(box.sock("b") / "profile")


def test_stale_lock_with_dead_pid_is_cleared_and_shared_kept(box):
    box.lock(box.dead_pid())
    box.old_owner("gone")
    assert box.run("b") == str(box.profile)
    assert not [p for p in box.profile.iterdir() if p.name.startswith("Singleton")]
    assert box.owner.read_text().strip() == str(box.sock("b"))


def test_live_non_chrome_pid_in_the_lock_counts_as_stale(box):
    box.lock(box.live("sleep"))
    box.old_owner("gone")
    assert box.run("b") == str(box.profile)
    assert not [p for p in box.profile.iterdir() if p.name.startswith("Singleton")]


def test_live_chrome_on_the_profile_is_never_cleared(box):
    box.lock(box.live("chrome"))
    box.old_owner("gone")  # the owner's daemon is gone but its Chrome is not
    assert box.run("b") == str(box.sock("b") / "profile")
    assert (box.profile / "SingletonLock").is_symlink()


def test_orphan_live_chrome_with_no_owner_counts_as_in_use(box):
    box.lock(box.live("chrome"))
    assert box.run("b") == str(box.sock("b") / "profile")


def test_same_session_twice_is_shared_both_times(box):
    assert box.run("a") == str(box.profile)
    box.daemon("a", box.live())
    assert box.run("a") == str(box.profile)


def test_fresh_owner_by_another_session_without_a_daemon_yet_is_private(box):
    box.owner.write_text(str(box.sock("a")))  # claimed a moment ago, daemon not up yet
    assert box.run("b") == str(box.sock("b") / "profile")


def test_dockerfile_puts_the_shim_in_front_of_the_binary():
    copy = DOCKERFILE.index("COPY deploy/agent-browser-shim.sh ")
    link = DOCKERFILE.index("ln -sf /opt/jarvis/bin/agent-browser-shim /usr/local/bin/agent-browser")
    npm = DOCKERFILE.index("npm install -g --allow-scripts=agent-browser")
    launch = DOCKERFILE.index("agent-browser --session build-check open about:blank")
    # after npm made the symlink (COPY onto a symlink would overwrite the real binary), and
    # before the build-time launch, so that launch goes through the shim.
    assert npm < copy < link < launch
    assert "agent-browser-linux-x64" in SHIM.read_text()


def test_shim_is_posix_sh():
    assert SHIM.read_text().splitlines()[0] == "#!/bin/sh"
