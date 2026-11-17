"""install.sh and install.ps1: the piped entry points of the one-line install, run against fakes.

What these pin: the entry point checks the system and every prerequisite before it fetches
anything, names each missing one, clones (or pulls, or unpacks a tarball), and hands over to
scripts/install.sh with `--dir <home>` first and the caller's options after; a download cut off
at any line runs nothing; the script behaves the same read from a file and from stdin, under
dash and under bash in POSIX mode.

Not covered: a real docker, git, curl or network (every program is a fake that logs its argv
and touches nothing else); a real terminal beyond one pty case; macOS's own sh; the installer it
hands over to (scripts/install.sh has test_install.py). The PowerShell script is run only where
`pwsh` is installed; elsewhere its tests skip by name and the text checks below are all that
guard it, so a first run under real PowerShell 5.1 on Windows is still owed.
"""

from __future__ import annotations

import os
import pty
import re
import select
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BOOT = ROOT / "install.sh"
PS1 = ROOT / "install.ps1"
DASH = shutil.which("dash")
BASH = shutil.which("bash") or "/bin/bash"
PWSH = shutil.which("pwsh")
URL = "https://example.com/aura.git"

# Real programs the script is allowed to find; everything else on PATH is a fake or missing, so a
# docker or git that happens to be installed on the machine running the tests cannot leak in.
REAL_TOOLS = ("sh", "mkdir", "rm", "rmdir", "mktemp", "cat", "cp", "chmod", "dirname")

DOCKER_FAKE = (
    'case "$1 $2" in "compose version") [ -z "$FAKE_NO_COMPOSE" ] || exit 1;; esac\n'
    '[ "$1" != info ] || [ -z "$FAKE_NO_DAEMON" ] || exit 1\n'
)
FAKES = {
    "docker": DOCKER_FAKE,
    "git": (
        'case "$1" in\n'
        '  clone) for last; do :; done\n'
        '         mkdir -p "$last/.git" "$last/scripts"; : > "$last/scripts/install.sh";;\n'
        '  -C) [ -z "$FAKE_PULL_FAIL" ] || exit 1;;\n'
        'esac\n'
    ),
    "curl": (
        '[ -z "$FAKE_CURL_FAIL" ] || exit 22\n'
        'out=""; prev=""; docker=""\n'
        'for a in "$@"; do [ "$prev" = -o ] && out=$a; [ "$a" = https://get.docker.com ] && docker=1; prev=$a; done\n'
        'if [ -n "$docker" ]; then\n'
        "  printf '%s\\n' 'echo docker-script-ran >> \"$FAKE_LOG\"' "
        "'cp \"$FAKE_STASH/docker\" \"$FAKE_BIN/docker\"' > \"$out\"\n"
        'else echo tarball > "$out"; fi\n'
    ),
    "wget": (
        '[ -z "$FAKE_CURL_FAIL" ] || exit 4\n'
        'out=""; prev=""\n'
        'for a in "$@"; do [ "$prev" = -qO ] && out=$a; prev=$a; done\n'
        'echo tarball > "$out"\n'
    ),
    "tar": (
        'dest=""; prev=""\n'
        'for a in "$@"; do [ "$prev" = -C ] && dest=$a; prev=$a; done\n'
        'mkdir -p "$dest/scripts"; : > "$dest/scripts/install.sh"\n'
    ),
    "bash": (
        '[ ! -t 0 ] || echo "bash-stdin-tty" >> "$FAKE_LOG"\n'
        'echo "bash-env MSYS_NO_PATHCONV=${MSYS_NO_PATHCONV:-unset}" >> "$FAKE_LOG"\n'
        'exit "${FAKE_BASH_RC:-0}"\n'
    ),
    "uname": 'echo "${FAKE_UNAME:-Linux}"\n',
    "id": 'echo "${FAKE_UID:-1000}"\n',
    "sudo": 'exec "$@"\n',
}


class Box:
    """A PATH of fakes, a log of every call, and a home to install into."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.bin = root / "bin"
        self.bin.mkdir()
        self.stash = root / "stash"
        self.stash.mkdir()
        self.log = root / "calls.log"
        self.user_home = root / "user"
        self.user_home.mkdir()
        self.tmp = root / "tmp"
        self.tmp.mkdir()
        self.aura_home = root / "aura"
        for name in REAL_TOOLS:
            real = shutil.which(name)
            if real:
                (self.bin / name).symlink_to(real)
        for name in ("docker", "git", "curl", "tar", "bash", "uname", "id", "sudo"):
            self.add(name)
        # docker is also kept aside: Docker's (fake) installer script copies it back in
        (self.stash / "docker").write_text(self._body("docker"))
        (self.stash / "docker").chmod(0o755)

    @staticmethod
    def _body(name: str) -> str:
        return f'#!/bin/sh\necho "{name} $*" >> "$FAKE_LOG"\n{FAKES[name]}'

    def add(self, name: str) -> None:
        path = self.bin / name
        path.write_text(self._body(name))
        path.chmod(0o755)

    def remove(self, *names: str) -> None:
        for name in names:
            (self.bin / name).unlink(missing_ok=True)

    def env(self, **extra: str) -> dict[str, str]:
        env = {
            "PATH": str(self.bin),
            "HOME": str(self.user_home),
            "TMPDIR": str(self.tmp),
            "FAKE_LOG": str(self.log),
            "FAKE_BIN": str(self.bin),
            "FAKE_STASH": str(self.stash),
            "AURA_REPO_URL": URL,
            "AURA_HOME": str(self.aura_home),
        }
        env.update(extra)
        return {k: v for k, v in env.items() if v != "<unset>"}

    def run(self, shell: list[str], *args: str, script: Path = BOOT,
            stdin_script: bool = False, **extra: str) -> subprocess.CompletedProcess[str]:
        # A new session has no controlling terminal, so /dev/tty cannot be opened, as in CI.
        if stdin_script:
            return subprocess.run(
                [*shell, "-s", "--", *args], input=script.read_text(), env=self.env(**extra),
                capture_output=True, text=True, timeout=60, start_new_session=True,
            )
        return subprocess.run(
            [*shell, str(script), *args], env=self.env(**extra), stdin=subprocess.DEVNULL,
            capture_output=True, text=True, timeout=60, start_new_session=True,
        )

    def calls(self) -> list[str]:
        return self.log.read_text().splitlines() if self.log.exists() else []

    def called(self, prefix: str) -> list[str]:
        return [c for c in self.calls() if c.startswith(prefix)]


@pytest.fixture(params=["dash", "bash-posix"])
def shell(request: pytest.FixtureRequest) -> list[str]:
    if request.param == "dash":
        if not DASH:
            pytest.skip("dash is not installed here")
        return [DASH]
    return [BASH, "--posix"]


@pytest.fixture
def box(tmp_path: Path) -> Box:
    return Box(tmp_path)


def _combined(r: subprocess.CompletedProcess[str]) -> str:
    return r.stdout + r.stderr


def _hand_over(box: Box, *args: str) -> str:
    return f"bash {box.aura_home}/src/scripts/install.sh --dir {box.aura_home} {' '.join(args)}".strip()


# 1
def test_with_git_one_clone_then_hand_over_with_dir_first(box: Box, shell: list[str]) -> None:
    r = box.run(shell, "--yes", "--bind", "192.0.2.7", AURA_REF="v1")
    assert r.returncode == 0, _combined(r)
    assert box.called("git") == [f"git clone --depth 1 --branch v1 {URL} {box.aura_home}/src"]
    assert box.called("bash ") == [_hand_over(box, "--yes", "--bind", "192.0.2.7")]
    # the clone came before the hand-over, and the hand-over is the last thing that ran
    calls = box.calls()
    assert calls.index(box.called("git")[0]) < calls.index(box.called("bash ")[0])
    assert box.called("bash-env")[-1] == "bash-env MSYS_NO_PATHCONV=unset"
    assert all(line.startswith("[aura]") for line in r.stdout.splitlines() if line.strip())


def test_defaults_ref_main_and_home_under_the_users_home(box: Box, shell: list[str]) -> None:
    r = box.run(shell, AURA_HOME="<unset>", AURA_REF="<unset>")
    assert r.returncode == 0, _combined(r)
    src = box.user_home / "aura" / "src"
    assert box.called("git") == [f"git clone --depth 1 --branch main {URL} {src}"]
    assert box.called("bash ") == [f"bash {src}/scripts/install.sh --dir {box.user_home / 'aura'}"]


def test_a_relative_home_is_made_absolute_before_anything_uses_it(box: Box, shell: list[str]) -> None:
    """The installer writes this path into env files compose reads, and compose takes a
    relative bind source for a volume's name."""
    r = subprocess.run(
        [*shell, str(BOOT)], env=box.env(AURA_HOME="rel/aura/"), cwd=box.user_home, stdin=subprocess.DEVNULL,
        capture_output=True, text=True, timeout=60, start_new_session=True,
    )
    assert r.returncode == 0, _combined(r)
    home = box.user_home / "rel" / "aura"
    assert box.called("git") == [f"git clone --depth 1 --branch main {URL} {home}/src"]
    assert box.called("bash ") == [f"bash {home}/src/scripts/install.sh --dir {home}"]


def test_hand_over_exit_status_is_the_installers(box: Box, shell: list[str]) -> None:
    assert box.run(shell, FAKE_BASH_RC="7").returncode == 7


def test_a_later_dir_from_the_caller_comes_after_ours(box: Box, shell: list[str]) -> None:
    r = box.run(shell, "--dir", "/srv/elsewhere")
    assert r.returncode == 0, _combined(r)
    assert box.called("bash ") == [_hand_over(box, "--dir", "/srv/elsewhere")]


# 2
def test_rerun_pulls_and_never_clones(box: Box, shell: list[str]) -> None:
    src = box.aura_home / "src"
    (src / ".git").mkdir(parents=True)
    (src / "scripts").mkdir()
    (src / "scripts" / "install.sh").write_text("")
    r = box.run(shell, "--yes")
    assert r.returncode == 0, _combined(r)
    assert box.called("git") == [f"git -C {src} pull --ff-only"]
    assert box.called("bash ") == [_hand_over(box, "--yes")]


def test_a_failing_pull_still_hands_over_with_the_source_there(box: Box, shell: list[str]) -> None:
    src = box.aura_home / "src"
    (src / ".git").mkdir(parents=True)
    (src / "scripts").mkdir()
    (src / "scripts" / "install.sh").write_text("")
    r = box.run(shell, FAKE_PULL_FAIL="1")
    assert r.returncode == 0, _combined(r)
    assert "pull failed" in r.stdout and "already in" in r.stdout
    assert box.called("bash ") == [_hand_over(box)]


def test_a_folder_that_is_not_ours_is_never_emptied(box: Box, shell: list[str]) -> None:
    mine = box.aura_home / "src"
    mine.mkdir(parents=True)
    (mine / "notes.txt").write_text("keep")
    r = box.run(shell)
    assert r.returncode != 0
    assert (mine / "notes.txt").read_text() == "keep"
    assert not box.called("git clone") and not box.called("bash ")


def test_a_source_without_the_installer_is_refused_by_name(box: Box, shell: list[str]) -> None:
    (box.bin / "git").write_text(
        '#!/bin/sh\necho "git $*" >> "$FAKE_LOG"\nfor last; do :; done\nmkdir -p "$last/.git"\n')
    r = box.run(shell)
    assert r.returncode != 0
    assert "scripts/install.sh" in _combined(r)
    assert not box.called("bash ")


# 3
def test_without_git_the_tarball_is_fetched_and_unpacked_flat(box: Box, shell: list[str]) -> None:
    box.remove("git")
    src = box.aura_home / "src"
    src.mkdir(parents=True)
    (src / "scripts").mkdir()
    (src / "scripts" / "install.sh").write_text("")
    _house_state(src)
    r = box.run(shell, "--yes", AURA_REF="v2")
    assert r.returncode == 0, _combined(r)
    curl = box.called("curl")
    assert len(curl) == 1 and curl[0].startswith(
        "curl -fsSL https://example.com/aura/archive/refs/heads/v2.tar.gz -o ")
    tmp_file = curl[0].rsplit(" ", 1)[1]
    assert box.called("tar") == [f"tar -xzf {tmp_file} -C {src} --strip-components 1"]
    _assert_house_state_kept(src)
    assert not Path(tmp_file).exists(), "the downloaded archive is removed"
    assert box.called("bash ") == [_hand_over(box, "--yes")]


def _house_state(src: Path) -> None:
    """What a running house keeps inside the source: its settings and what it has learned."""
    (src / "aura-core" / "config" / ".storage").mkdir(parents=True)
    (src / "aura-core" / ".env").write_text("HOUSE=kept\n")
    (src / "aura-core" / "config" / ".storage" / "people").write_text("kept")


def _assert_house_state_kept(src: Path) -> None:
    assert (src / "aura-core" / ".env").read_text() == "HOUSE=kept\n"
    assert (src / "aura-core" / "config" / ".storage" / "people").read_text() == "kept"


def test_git_arriving_later_never_clones_over_a_source_that_came_as_an_archive(
    box: Box, shell: list[str]
) -> None:
    """The first install had no git; git was installed since. A clone needs an empty folder, and
    emptying this one would take the house's settings and memory with it."""
    src = box.aura_home / "src"
    (src / "scripts").mkdir(parents=True)
    (src / "scripts" / "install.sh").write_text("")
    _house_state(src)
    r = box.run(shell, "--yes")
    assert r.returncode == 0, _combined(r)
    assert not box.called("git clone")
    assert len(box.called("tar")) == 1, "refreshed the way it came"
    _assert_house_state_kept(src)
    assert box.called("bash ") == [_hand_over(box, "--yes")]


def test_an_archive_source_with_no_way_to_refresh_it_is_used_as_it_is(box: Box, shell: list[str]) -> None:
    box.remove("curl", "tar")
    src = box.aura_home / "src"
    (src / "scripts").mkdir(parents=True)
    (src / "scripts" / "install.sh").write_text("")
    _house_state(src)
    r = box.run(shell)
    assert r.returncode == 0, _combined(r)
    assert not box.called("git clone") and "as it is" in r.stdout
    _assert_house_state_kept(src)
    assert box.called("bash ") == [_hand_over(box)]


def test_the_tarball_url_can_be_set(box: Box, shell: list[str]) -> None:
    box.remove("git")
    r = box.run(shell, AURA_TARBALL_URL="https://example.com/a.tgz")
    assert r.returncode == 0, _combined(r)
    assert box.called("curl")[0].startswith("curl -fsSL https://example.com/a.tgz -o ")


def test_wget_does_when_there_is_no_curl(box: Box, shell: list[str]) -> None:
    box.remove("git", "curl")
    box.add("wget")
    r = box.run(shell)
    assert r.returncode == 0, _combined(r)
    assert box.called("wget")[0].startswith("wget -qO ")
    assert box.called("wget")[0].endswith(" https://example.com/aura/archive/refs/heads/main.tar.gz")


def test_a_failed_download_uses_the_source_already_there_or_stops(box: Box, shell: list[str]) -> None:
    box.remove("git")
    r = box.run(shell, FAKE_CURL_FAIL="1")
    assert r.returncode != 0 and not box.called("bash ") and not box.called("tar")
    src = box.aura_home / "src" / "scripts"
    src.mkdir(parents=True)
    (src / "install.sh").write_text("")
    r = box.run(shell, FAKE_CURL_FAIL="1")
    assert r.returncode == 0, _combined(r)
    assert "already in" in r.stdout and not box.called("tar")
    assert box.called("bash ") == [_hand_over(box)]


# 4
@pytest.mark.parametrize("missing, needle", [
    ("docker", "docker"),
    ("compose", "compose"),
    ("daemon", "daemon"),
    ("bash", "bash"),
    ("fetch", "git"),
])
def test_each_missing_prerequisite_is_named_and_nothing_is_fetched(
    box: Box, shell: list[str], missing: str, needle: str
) -> None:
    extra: dict[str, str] = {}
    if missing == "docker":
        box.remove("docker")
    elif missing == "compose":
        extra["FAKE_NO_COMPOSE"] = "1"
    elif missing == "daemon":
        extra["FAKE_NO_DAEMON"] = "1"
    elif missing == "bash":
        box.remove("bash")
    else:
        box.remove("git", "curl", "tar")
    r = box.run(shell, **extra)
    assert r.returncode != 0
    assert needle in r.stderr.lower(), r.stderr
    for fetched in ("git clone", "git -C", "curl", "wget", "tar", "bash ", "sudo"):
        assert not box.called(fetched), (fetched, box.calls())
    assert not box.aura_home.exists()


def test_every_missing_prerequisite_is_listed_in_one_run(box: Box, shell: list[str]) -> None:
    box.remove("bash", "git", "curl", "tar")
    r = box.run(shell, FAKE_NO_DAEMON="1")
    assert r.returncode != 0
    for word in ("bash", "git", "daemon"):
        assert word in r.stderr.lower(), r.stderr


def test_docker_missing_names_the_engine_page_on_linux(box: Box, shell: list[str]) -> None:
    box.remove("docker")
    r = box.run(shell)
    assert "https://docs.docker.com/engine/install/" in r.stderr


# 5
@pytest.mark.parametrize("name", ["MINGW64_NT-10.0", "MSYS_NT-10.0", "CYGWIN_NT-10.0", "FreeBSD"])
def test_an_unsupported_system_is_refused_and_powershell_named(
    box: Box, shell: list[str], name: str
) -> None:
    r = box.run(shell, FAKE_UNAME=name)
    assert r.returncode != 0
    assert "install.ps1" in r.stderr and "iex" in r.stderr
    assert box.calls() == ["uname -s"], "nothing but the system check ran"


# 6
def test_docker_is_installed_through_sudo_when_asked_by_env(box: Box, shell: list[str]) -> None:
    box.remove("docker")
    r = box.run(shell, "--yes", AURA_INSTALL_DOCKER="1")
    assert r.returncode == 0, _combined(r)
    calls = box.calls()
    fetch = next(c for c in calls if c.startswith("curl") and "https://get.docker.com" in c)
    tmp_file = fetch.rsplit(" ", 1)[1]
    assert f"sudo sh {tmp_file}" in calls
    assert "docker-script-ran" in calls
    assert not Path(tmp_file).exists(), "Docker's script is removed after it ran"
    # then the checks ran again against the new docker, and the install went on
    assert calls.index("docker-script-ran") < calls.index("docker compose version")
    assert calls.index("docker info") < calls.index(box.called("git clone")[0])
    assert box.called("bash ") == [_hand_over(box, "--yes")]


def test_docker_install_as_root_runs_without_sudo(box: Box, shell: list[str]) -> None:
    box.remove("docker", "sudo")
    r = box.run(shell, AURA_INSTALL_DOCKER="1", FAKE_UID="0")
    assert r.returncode == 0, _combined(r)
    assert "docker-script-ran" in box.calls() and not box.called("sudo")


def test_docker_install_without_root_or_sudo_stops(box: Box, shell: list[str]) -> None:
    box.remove("docker", "sudo")
    r = box.run(shell, AURA_INSTALL_DOCKER="1")
    assert r.returncode != 0 and "sudo" in r.stderr
    assert not box.called("git clone") and not box.called("bash ")


def test_docker_missing_with_no_terminal_and_no_flag_is_refused(box: Box, shell: list[str]) -> None:
    box.remove("docker")
    r = box.run(shell)
    assert r.returncode != 0
    assert "https://docs.docker.com/engine/install/" in r.stderr
    assert "AURA_INSTALL_DOCKER" in r.stderr
    assert not box.called("curl") and not box.called("sudo") and not box.called("git")


def test_docker_is_never_installed_on_macos(box: Box, shell: list[str]) -> None:
    box.remove("docker")
    r = box.run(shell, FAKE_UNAME="Darwin", AURA_INSTALL_DOCKER="1")
    assert r.returncode != 0 and "Docker Desktop" in r.stderr
    assert not box.called("curl") and not box.called("sudo")


def test_docker_is_not_installed_while_something_else_is_missing(box: Box, shell: list[str]) -> None:
    box.remove("docker", "bash")
    r = box.run(shell, AURA_INSTALL_DOCKER="1")
    assert r.returncode != 0 and "bash" in r.stderr.lower()
    assert not box.called("curl") and not box.called("sudo")


def test_macos_with_docker_goes_on(box: Box, shell: list[str]) -> None:
    r = box.run(shell, FAKE_UNAME="Darwin")
    assert r.returncode == 0, _combined(r)
    assert box.called("bash ") == [_hand_over(box)]


def _in_a_terminal(box: Box, answers: bytes, *args: str) -> tuple[int, str]:
    """Run install.sh with a controlling terminal whose input is `answers`, stdin from /dev/null."""
    pid, fd = pty.fork()
    if pid == 0:
        try:
            os.execve("/bin/sh", ["sh", "-c", 'exec "$0" "$@" </dev/null',
                                  DASH or BASH, str(BOOT), *args], box.env())
        finally:
            os._exit(127)
    os.write(fd, answers)
    out = b""
    deadline = time.time() + 30
    while time.time() < deadline:
        ready, _, _ = select.select([fd], [], [], 1)
        if not ready:
            continue
        try:
            chunk = os.read(fd, 4096)
        except OSError:
            break
        if not chunk:
            break
        out += chunk
    else:
        os.kill(pid, signal.SIGKILL)
    _, status = os.waitpid(pid, 0)
    os.close(fd)
    return os.waitstatus_to_exitcode(status), out.decode(errors="replace")


def test_a_person_at_a_terminal_can_say_yes_to_docker_and_the_installer_gets_the_terminal(
    box: Box,
) -> None:
    box.remove("docker")
    code, out = _in_a_terminal(box, b"y\n", "--yes")
    assert code == 0, out
    assert "docker-script-ran" in box.calls()
    assert [c for c in box.calls() if c.startswith("sudo sh ")]
    assert box.called("bash-stdin-tty") == ["bash-stdin-tty"], "the installer asks on the terminal"


def test_a_person_at_a_terminal_can_say_no_to_docker(box: Box) -> None:
    box.remove("docker")
    code, out = _in_a_terminal(box, b"n\n")
    assert code != 0, out
    assert not box.called("curl") and not box.called("sudo")
    assert "https://docs.docker.com/engine/install/" in out


def test_without_a_terminal_the_installer_keeps_its_own_stdin(box: Box, shell: list[str]) -> None:
    r = box.run(shell)
    assert r.returncode == 0, _combined(r)
    assert not box.called("bash-stdin-tty")


# 7
def test_a_download_cut_off_anywhere_runs_nothing(box: Box, shell: list[str]) -> None:
    lines = BOOT.read_text().splitlines(keepends=True)
    assert len(lines) > 50
    for n in range(len(lines)):
        subprocess.run(
            [*shell, "-s", "--", "--yes"], input="".join(lines[:n]), env=box.env(),
            capture_output=True, text=True, timeout=30, start_new_session=True,
        )
        assert box.calls() == [], f"the first {n} lines ran {box.calls()}"
        assert not box.aura_home.exists(), n


# 8
def test_read_from_stdin_behaves_like_the_file(box: Box, shell: list[str]) -> None:
    r = box.run(shell, "--yes", stdin_script=True)
    assert r.returncode == 0, _combined(r)
    stdin_calls = box.calls()
    box.log.unlink()
    shutil.rmtree(box.aura_home)
    r2 = box.run(shell, "--yes")
    assert r2.returncode == 0, _combined(r2)
    assert stdin_calls == box.calls() and r.stdout == r2.stdout
    assert box.called("bash ") == [_hand_over(box, "--yes")]


def test_read_from_stdin_refuses_too(box: Box, shell: list[str]) -> None:
    box.remove("docker")
    r = box.run(shell, stdin_script=True)
    assert r.returncode != 0 and "docker" in r.stderr.lower()


# 9
@pytest.mark.parametrize("flag", ["--help", "-h"])
def test_help_exits_zero_and_checks_nothing(box: Box, shell: list[str], flag: str) -> None:
    box.remove("docker", "git", "bash")
    r = box.run(shell, flag)
    assert r.returncode == 0, _combined(r)
    assert "AURA_REPO_URL" in r.stdout and "AURA_INSTALL_DOCKER" in r.stdout
    assert "install.ps1" in r.stdout
    assert box.calls() == []
    r = box.run(shell, "--yes", flag, stdin_script=True)
    assert r.returncode == 0 and box.calls() == [], "help also works when piped"


# 10
def _shellcheck() -> str | None:
    return shutil.which("shellcheck") or (
        str(Path(sys.executable).parent / "shellcheck")
        if (Path(sys.executable).parent / "shellcheck").exists() else None)


def test_the_script_is_posix_sh() -> None:
    assert subprocess.run([BASH, "--posix", "-n", str(BOOT)]).returncode == 0
    if DASH:
        assert subprocess.run([DASH, "-n", str(BOOT)]).returncode == 0
    sc = _shellcheck()
    if sc is None:
        pytest.skip("shellcheck is not installed here")
    r = subprocess.run([sc, "--shell=sh", str(BOOT)], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout


def test_the_script_uses_nothing_bash_only() -> None:
    text = BOOT.read_text()
    assert text.startswith("#!/bin/sh\n")
    code = "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))
    for banned in ("[[", "local ", "<(", "declare ", "function ", "pipefail", "${!", "==", "&>"):
        assert banned not in code, banned
    assert not re.search(r"^\s*source\s", code, re.M)
    assert "set -eu\n" in text


def test_the_body_is_functions_and_the_last_line_runs_them() -> None:
    lines = BOOT.read_text().splitlines()
    assert lines[-1] == 'main "$@"'
    comment = "\n".join(lines[-6:-1]).lower()
    assert "cut off" in comment or "part-way" in comment
    # Unindented lines (the top level) are comments, blanks, `set -eu`, two plain assignments, a
    # function's first or last line and the call: nothing that can run a program.
    for line in lines:
        if line[:1].isspace():
            continue
        ok = (not line.strip() or line.startswith("#") or line == "set -eu" or line == "}"
              or line.startswith(("AURA_REPO_DEFAULT=", "TMPFILE="))
              or re.match(r"^[a-z_]+\(\) \{", line) or line == 'main "$@"')
        assert ok, line


def test_the_default_repository_is_one_replaceable_line() -> None:
    lines = [ln for ln in BOOT.read_text().splitlines() if ln.startswith("AURA_REPO_DEFAULT=")]
    assert lines == ['AURA_REPO_DEFAULT="https://github.com/ChaseSunstrom/aura"']


def test_the_script_prints_no_secret_and_makes_none() -> None:
    text = BOOT.read_text().lower()
    for word in ("openssl", "token", "password", "secret", "/dev/urandom"):
        assert word not in text, word


# 11
PS_TEXT = PS1.read_text() if PS1.exists() else ""


def _code_lines(text: str) -> list[str]:
    return [ln for ln in text.splitlines() if ln.strip() and not ln.strip().startswith("#")]


def test_ps1_is_one_function_called_on_the_last_line() -> None:
    code = _code_lines(PS_TEXT)
    assert code[-1] == "Install-Aura"
    top = [ln for ln in code if not ln[0].isspace()]
    assert top == ["function Install-Aura {", "}", "Install-Aura"], top
    assert "$ErrorActionPreference = 'Stop'" in PS_TEXT


def test_ps1_makes_the_home_absolute_before_it_is_joined_or_handed_over() -> None:
    text = PS1.read_text()
    made = text.index("GetUnresolvedProviderPathFromPSPath($homeDir)")
    assert made < text.index("$srcDir = Join-Path $homeDir 'src'") < text.index("$dirArg = $homeDir")


def test_ps1_default_repository_line_matches_the_shell_one() -> None:
    assert '$AURA_REPO_DEFAULT = "https://github.com/ChaseSunstrom/aura"' in PS_TEXT
    for name in ("AURA_REPO_URL", "AURA_REF", "AURA_HOME", "AURA_INSTALL_ARGS", "AURA_INSTALL_GIT"):
        assert f"$env:{name}" in PS_TEXT, name


def test_ps1_sets_msys_no_pathconv_before_bash_runs() -> None:
    code = _code_lines(PS_TEXT)
    setter = next(i for i, ln in enumerate(code) if "$env:MSYS_NO_PATHCONV = '1'" in ln)
    runner = next(i for i, ln in enumerate(code) if ln.strip().startswith("& $bash"))
    assert setter < runner
    assert "scripts/install.sh" in PS_TEXT and "'--dir'" in PS_TEXT


def test_ps1_never_picks_the_wsl_launcher_in_system32() -> None:
    assert not [ln for ln in _code_lines(PS_TEXT) if "system32" in ln.lower()]
    assert "system32" in PS_TEXT.lower(), "the reason is said in a comment"
    code = "\n".join(_code_lines(PS_TEXT))
    assert "Git\\bin\\bash.exe" in code and "winget install --id Git.Git -e --source winget" in code
    assert "https://docs.docker.com/desktop/" in code


def test_ps1_hands_over_with_its_exit_code() -> None:
    code = "\n".join(_code_lines(PS_TEXT))
    assert "exit $code" in code and "$code = [int]$global:LASTEXITCODE" in code


def _ps(box: Box, **extra: str) -> subprocess.CompletedProcess[str]:
    assert PWSH
    return subprocess.run(
        [PWSH, "-NoProfile", "-NonInteractive", "-File", str(PS1)], env=box.env(**extra),
        stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=120,
        start_new_session=True,
    )


@pytest.mark.skipif(PWSH is None, reason="pwsh is not installed here")
def test_ps1_under_pwsh_clones_and_hands_over(box: Box) -> None:
    r = _ps(box, AURA_REF="v1", AURA_INSTALL_ARGS="--yes --bind 192.0.2.7")
    assert r.returncode == 0, _combined(r)
    assert box.called("git") == [f"git clone --depth 1 --branch v1 {URL} {box.aura_home}/src"]
    assert box.called("bash ") == [_hand_over(box, "--yes", "--bind", "192.0.2.7")]
    assert box.called("bash-env")[-1] == "bash-env MSYS_NO_PATHCONV=1"


@pytest.mark.skipif(PWSH is None, reason="pwsh is not installed here")
@pytest.mark.parametrize("missing", ["docker", "git"])
def test_ps1_under_pwsh_refuses_by_name_and_runs_nothing(box: Box, missing: str) -> None:
    box.remove(missing)
    r = _ps(box)
    assert r.returncode == 1 and missing in _combined(r).lower()
    assert not box.called("git clone") and not box.called("bash ")


@pytest.mark.skipif(PWSH is None, reason="pwsh is not installed here")
def test_ps1_under_pwsh_exits_with_the_installers_status(box: Box) -> None:
    assert _ps(box, FAKE_BASH_RC="7").returncode == 7
