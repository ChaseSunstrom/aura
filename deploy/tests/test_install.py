"""scripts/install.sh: the one-script install, run against a fake docker, never a real one.

What these pin: a dry run writes nothing; a fresh run creates both stacks' .env files from their
templates with every generated secret filled, mode 0600, and starts both stacks; an existing .env
is left byte for byte and only its missing keys are named; a running Jarvis stops the install
(dry run included) unless --upgrade.

Not covered: a real docker, a real GPU, a real pull; the health phase (a fake curl answers).
The upgrade path itself is scripts/deploy-here.sh and is only checked to be named, not run.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import socket
import stat
import subprocess
import tarfile
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
INSTALL = ROOT / "scripts" / "install.sh"


@pytest.fixture
def fakes(tmp_path: Path) -> dict[str, str]:
    """A PATH with a recording docker, nvidia-smi and curl in front of the real one."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "docker.log"
    ps = tmp_path / "ps.txt"
    ps.write_text("")
    (bin_dir / "docker").write_text(
        "#!/bin/sh\n"
        f'echo "$@" >> "{log}"\n'
        'case "$1" in ps) cat "$FAKE_PS";; info) echo "{}";; inspect) echo healthy;; esac\n'
        "exit 0\n"
    )
    (bin_dir / "nvidia-smi").write_text("#!/bin/sh\necho 'GPU 0: Fake RTX (UUID: x)'\n")
    (bin_dir / "curl").write_text("#!/bin/sh\nexit 0\n")
    for f in bin_dir.iterdir():
        f.chmod(0o755)
    # A throwaway checkout: the installer writes aura-core/.env into the checkout it runs from.
    co = tmp_path / "checkout"
    shutil.copytree(ROOT / "deploy", co / "deploy",
                    # seed-repo.tar: a checkout that has deployed holds one, and the installer
                    # reuses a copy it finds, so the seed test read this machine's old tree.
                    ignore=shutil.ignore_patterns("tests", "__pycache__", ".env", "n8n.env", "seed-repo.tar"))
    (co / "aura-core").mkdir(parents=True)
    for name in ("docker-compose.yml", ".env.example"):
        shutil.copy(ROOT / "aura-core" / name, co / "aura-core" / name)
    (co / "scripts").mkdir()
    shutil.copy(INSTALL, co / "scripts" / "install.sh")
    return {
        "_CHECKOUT": str(co),
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "FAKE_PS": str(ps),
        "AURA_INSTALL_PORTS": "",
        "AURA_INSTALL_MIN_GB": "0",
        "AURA_INSTALL_HEALTH_TIMEOUT": "2",
        "HOME": str(tmp_path),
    }


def _script(env: dict[str, str]) -> Path:
    return Path(env["_CHECKOUT"]) / "scripts" / "install.sh"


def _run(env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(_script(env)), "--bind", "127.0.0.1", "--yes", *args],
        env=env, capture_output=True, text=True, timeout=60,
    )


def _values(path: Path) -> dict[str, str]:
    out = {}
    for line in path.read_text().splitlines():
        m = re.match(r"([A-Z][A-Z0-9_]*)=(.*)$", line)
        if m:
            out[m.group(1)] = m.group(2)
    return out


def test_bash_syntax_and_shellcheck() -> None:
    assert subprocess.run(["bash", "-n", str(INSTALL)]).returncode == 0
    if shutil.which("shellcheck"):
        assert subprocess.run(["shellcheck", "-S", "warning", str(INSTALL)]).returncode == 0


def test_install_dry_run_writes_nothing_and_names_every_phase(
    tmp_path: Path, fakes: dict[str, str]
) -> None:
    target = tmp_path / "jarvis"
    r = _run(fakes, "--dir", str(target), "--dry-run")
    assert r.returncode == 0, r.stderr
    assert not target.exists()
    for phase in ("preflight", "secrets", "n8n", "images", "start", "verify", "pair"):
        assert f"[{phase}]" in r.stdout, phase
    # nothing but reads reached docker: no pull, build, up
    calls = (tmp_path / "docker.log").read_text() if (tmp_path / "docker.log").exists() else ""
    assert not re.search(r"\b(pull|build|up)\b", calls)


def test_install_fresh_run_makes_private_env_files_and_starts_both_stacks(
    tmp_path: Path, fakes: dict[str, str]
) -> None:
    target = tmp_path / "jarvis"
    r = _run(fakes, "--dir", str(target))
    assert r.returncode == 0, r.stdout + r.stderr
    hermes, core = target / ".env", Path(fakes["_CHECKOUT"]) / "aura-core" / ".env"
    for env_file in (hermes, core):
        assert stat.S_IMODE(env_file.stat().st_mode) == 0o600
    h, c = _values(hermes), _values(core)
    for key in ("AURA_API_KEY", "HERMES_API_KEY", "HERMES_SERVE_TOKEN", "MODELS_API_KEY",
                "LLM_API_KEY", "HASS_TOKEN", "AURA_TOKEN", "AURA_CONSOLE_PASSWORD",
                "AURA_PAIRING_SECRET"):
        assert re.fullmatch(r"[0-9a-f]{64}", h[key]), key
    assert len({h[k] for k in ("AURA_API_KEY", "HERMES_API_KEY", "HERMES_SERVE_TOKEN")}) == 3
    # the two stacks agree on the secrets they share
    assert c["AURA_TOKEN"] == h["HASS_TOKEN"] == h["AURA_TOKEN"]
    assert c["AURA_ADAPTER_KEY"] == h["AURA_API_KEY"]
    assert c["LLM_API_KEY"] == h["LLM_API_KEY"]
    # the emergency plugin and the house prove a contact list under one secret, and it is not another key
    assert re.fullmatch(r"[0-9a-f]{64}", h["AURA_EMERGENCY_SECRET"])
    assert c["AURA_EMERGENCY_SECRET"] == h["AURA_EMERGENCY_SECRET"]
    assert h["AURA_EMERGENCY_SECRET"] not in {h["HASS_TOKEN"], h["AURA_API_KEY"], h["LLM_API_KEY"]}
    assert not any(v.startswith("change-me") for v in h.values())
    # binds the chosen address only
    assert h["TAILSCALE_IP"] == "127.0.0.1"
    # no secret value reaches the terminal
    out = r.stdout + r.stderr
    for v in list(h.values()) + list(c.values()):
        if re.fullmatch(r"[0-9a-f]{64}", v):
            assert v not in out
    calls = (tmp_path / "docker.log").read_text()
    assert calls.count(" up ") >= 2  # both stacks
    assert "http://127.0.0.1:8199" in r.stdout


def test_install_existing_env_is_left_alone_and_its_missing_keys_named(
    tmp_path: Path, fakes: dict[str, str]
) -> None:
    target = tmp_path / "jarvis"
    target.mkdir()
    old = "AURA_API_KEY=mine\nTAILSCALE_IP=127.0.0.1\n"
    (target / ".env").write_text(old)
    (target / ".env").chmod(0o640)
    r = _run(fakes, "--dir", str(target))
    assert r.returncode == 0, r.stdout + r.stderr
    assert (target / ".env").read_text() == old
    assert stat.S_IMODE((target / ".env").stat().st_mode) == 0o640
    assert "HERMES_API_KEY" in r.stdout  # reported missing
    assert "mine" not in r.stdout
    assert (Path(fakes["_CHECKOUT"]) / "aura-core" / ".env").exists()  # the other stack is new


def test_install_refuses_a_running_jarvis_even_in_a_dry_run(
    tmp_path: Path, fakes: dict[str, str]
) -> None:
    Path(fakes["FAKE_PS"]).write_text("jarvis\njarvis-adapter\n")
    target = tmp_path / "jarvis"
    for extra in (("--dry-run",), ()):
        r = _run(fakes, "--dir", str(target), *extra)
        assert r.returncode != 0
        assert "--upgrade" in r.stderr
        assert not target.exists()


def test_install_upgrade_hands_over_to_deploy_here(tmp_path: Path, fakes: dict[str, str]) -> None:
    Path(fakes["FAKE_PS"]).write_text("jarvis\n")
    r = _run(fakes, "--dir", str(tmp_path / "j"), "--upgrade", "--dry-run")
    assert r.returncode == 0, r.stderr
    assert "deploy-here.sh" in r.stdout


def test_install_refuses_the_open_bind_address(tmp_path: Path, fakes: dict[str, str]) -> None:
    r = subprocess.run(
        ["bash", str(_script(fakes)), "--bind", "0.0.0.0", "--yes", "--dir", str(tmp_path / "j")],
        env=fakes, capture_output=True, text=True, timeout=60,
    )
    assert r.returncode != 0


def _real_docker() -> str | None:
    """The real docker, if it can run `compose config` (a CLI check, no daemon needed)."""
    exe = shutil.which("docker")
    if not exe:
        return None
    ok = subprocess.run([exe, "compose", "version"], capture_output=True).returncode == 0
    return exe if ok else None


@pytest.mark.skipif(_real_docker() is None, reason="docker compose is not installed here")
def test_install_generated_layouts_pass_a_real_compose_config(
    tmp_path: Path, fakes: dict[str, str]
) -> None:
    """Every env_file, mount and variable resolves in the installed layout, not the checkout's."""
    docker = _real_docker()
    assert docker
    target, co = tmp_path / "jarvis", Path(fakes["_CHECKOUT"])
    r = _run(fakes, "--dir", str(target))
    assert r.returncode == 0, r.stdout + r.stderr
    assert (target / "n8n.env").is_file() and stat.S_IMODE((target / "n8n.env").stat().st_mode) == 0o600
    assert (target / "docker-compose.yml").is_file() and (target / "dind-daemon.json").is_file()
    assert not (target / "tests").exists()
    env = {k: v for k, v in os.environ.items() if not k.startswith("COMPOSE_")}
    for project_dir in (target, co / "aura-core"):
        c = subprocess.run(
            [docker, "compose", "--project-directory", str(project_dir),
             "-f", str(project_dir / "docker-compose.yml"), "config", "-q"],
            env=env, capture_output=True, text=True, timeout=60,
        )
        assert c.returncode == 0, c.stderr
        assert "is not set" not in c.stderr, c.stderr
    # the checkout's deploy/ was not where the stack reads its files
    assert not (co / "deploy" / ".env").exists() and not (co / "deploy" / "n8n.env").exists()


def test_a_fallback_to_an_old_name_never_makes_compose_say_a_variable_is_not_set() -> None:
    """A new install has only the AURA_ names. `${AURA_X:-${JARVIS_X}}` reads the old name as a
    fallback, and compose 2.40 (Ubuntu 24.04's package) reports the old one as "not set" on
    every call even when the new one is set: two dozen warning lines were the first thing the
    clean-machine run printed under its own build. Newer compose stays quiet, so the run above
    cannot catch it here; the shape is what is pinned: the inner name carries a default."""
    bare = re.compile(r"\$\{[A-Z0-9_]+:-\$\{[A-Z0-9_]+\}\}")
    for name in ("deploy/docker-compose.yml", "deploy/docker-compose.build.yml",
                 "deploy/docker-compose.gpu.yml", "aura-core/docker-compose.yml"):
        found = bare.findall((ROOT / name).read_text(encoding="utf-8"))
        assert found == [], (name, sorted(set(found)))


# ---- the setup page and the build from source (T-0864) ----------------------------------------

PASSWORD = " ".join(["correct", "horse", "battery"])
ENDPOINT_KEY = "-".join(["key", "for", "the", "endpoint"])


def _list(name: str) -> list[str]:
    """One of the installer's service lists, read from the script itself."""
    m = re.search(rf'^{name}="([^"]+)"$', INSTALL.read_text(), re.M)
    assert m, name
    return m.group(1).split()


def test_no_service_mounts_a_file_of_the_hosts_own_system() -> None:
    """The second clean-machine run stopped at "start": the house mounted the host's
    /etc/localtime, a machine with no zone data installed has no such file, Docker made a folder
    in its place and could not mount a folder over a file. A file under /etc is the host's to
    have or not; the zone comes from TZ and the image's own zone data."""
    for name in ("aura-core/docker-compose.yml", "deploy/docker-compose.yml", "deploy/docker-compose.build.yml"):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert re.findall(r"^\s+- (/etc/[^:\s]+):", text, flags=re.M) == [], name


def test_the_installers_service_lists_are_the_compose_files_own() -> None:
    """A service added to the stack and not to the installer would be pulled by nobody."""
    base = yaml.safe_load((ROOT / "deploy" / "docker-compose.yml").read_text())["services"]
    built = yaml.safe_load((ROOT / "deploy" / "docker-compose.build.yml").read_text())["services"]
    assert sorted(_list("HERMES_ALL")) == sorted(base)
    assert sorted(_list("HERMES_BUILT")) == sorted(built)
    for name, spec in built.items():
        assert set(spec) == {"build"}, f"{name}: the build file adds a build and changes nothing else"
        assert "AURA_REGISTRY" in base[name]["image"], f"{name} is built here but the base file pulls it by another name"
        dockerfile = Path(spec["build"]["context"].split("}")[-1].lstrip("/")) / spec["build"]["dockerfile"]
        assert (ROOT / dockerfile).is_file(), dockerfile
    # What shares a built image is neither built twice nor pulled from a registry that has none.
    shared = _list("HERMES_SHARED")
    assert shared and all(base[s]["image"] == base["jarvis"]["image"] and s not in built for s in shared)
    ours = {n for n, s in base.items() if "AURA_REGISTRY" in s["image"]}
    assert ours == set(built) | set(shared)


def _calls(tmp_path: Path) -> list[str]:
    return (tmp_path / "docker.log").read_text().splitlines()


def test_with_no_registry_named_the_images_are_built_from_the_source(
    tmp_path: Path, fakes: dict[str, str]
) -> None:
    target, co = tmp_path / "jarvis", Path(fakes["_CHECKOUT"])
    r = _run(fakes, "--dir", str(target))
    assert r.returncode == 0, r.stdout + r.stderr
    h = _values(target / ".env")
    assert h["AURA_REGISTRY"] == "aura-local" and h["AURA_TAG"] == "local"
    calls = _calls(tmp_path)
    build = [c for c in calls if " build " in c and "--project-directory " + str(target) in c]
    assert len(build) == 1 and "docker-compose.build.yml" in build[0]
    assert build[0].split(" build ")[1].split() == _list("HERMES_BUILT")
    pulled = [c for c in calls if " pull " in c and "--project-directory " + str(target) in c][0].split(" pull ")[1].split()
    assert not set(pulled) & set(_list("HERMES_BUILT") + _list("HERMES_SHARED"))
    assert "searxng" in pulled and "n8n" in pulled
    # The gateway image's copy of the source: under repo/, and never the env file with the house's secrets.
    seed = co / "deploy" / "seed-repo.tar"
    with tarfile.open(seed) as tar:
        names = tar.getnames()
    assert "repo/scripts/install.sh" in names
    assert not [n for n in names if n.endswith(".env")]
    assert (co / "aura-core" / ".env").exists()
    assert not (target / "seed-repo.tar").exists() and not (target / "setup" / "server.py").exists()


def test_a_named_registry_is_pulled_from_and_nothing_is_built(tmp_path: Path, fakes: dict[str, str]) -> None:
    target = tmp_path / "jarvis"
    r = _run({**fakes, "AURA_REGISTRY": "registry.example.test/aura"}, "--dir", str(target))
    assert r.returncode == 0, r.stdout + r.stderr
    assert _values(target / ".env")["AURA_REGISTRY"] == "registry.example.test/aura"
    hermes = [c for c in _calls(tmp_path) if "--project-directory " + str(target) in c]
    assert not [c for c in hermes if " build" in c or "docker-compose.build.yml" in c]
    assert [c for c in hermes if " pull " in c]


def _running(tmp_path: Path, fakes: dict[str, str], started_from: str) -> None:
    """The fake docker with this install's containers up, started by compose from `started_from`."""
    Path(fakes["FAKE_PS"]).write_text("jarvis\njarvis-web\n")
    docker = tmp_path / "bin" / "docker"
    docker.write_text(
        "#!/bin/sh\n"
        f'echo "$@" >> "{tmp_path / "docker.log"}"\n'
        'case "$1" in\n'
        '  ps) cat "$FAKE_PS";;\n'
        '  info) echo "{}";;\n'
        f'  inspect) case "$3" in *working_dir*) echo "{started_from}";; *) echo healthy;; esac;;\n'
        "esac\nexit 0\n"
    )


def test_the_line_run_again_carries_on_with_its_own_half_done_install(
    tmp_path: Path, fakes: dict[str, str]
) -> None:
    """A first run that stopped after its containers started (a slow first health check) must
    not leave a person with "already running, use --upgrade", which is production's deploy."""
    target = tmp_path / "jarvis"
    assert _run(fakes, "--dir", str(target)).returncode == 0
    before = (target / ".env").read_text()
    (tmp_path / "docker.log").unlink()
    _running(tmp_path, fakes, str(target.resolve()))
    r = _run(fakes, "--dir", str(target))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "carrying on" in r.stdout
    assert (target / ".env").read_text() == before
    assert [c for c in _calls(tmp_path) if " up -d" in c]


def test_containers_started_from_somewhere_else_are_still_refused(tmp_path: Path, fakes: dict[str, str]) -> None:
    target = tmp_path / "jarvis"
    assert _run(fakes, "--dir", str(target)).returncode == 0
    (tmp_path / "docker.log").unlink()
    _running(tmp_path, fakes, str(tmp_path / "another-install"))
    r = _run(fakes, "--dir", str(target))
    assert r.returncode != 0 and "--upgrade" in r.stderr
    assert not [c for c in _calls(tmp_path) if " up -d" in c or " build " in c]


def test_the_name_in_the_terminal_is_the_themes(tmp_path: Path, fakes: dict[str, str]) -> None:
    r = _run(fakes, "--dir", str(tmp_path / "jarvis"))
    assert r.returncode == 0 and "AURA is up." in r.stdout
    code, out, _target = _install_through_the_page(tmp_path / "themed", fakes, _answers())
    assert code == 0 and "JARVIS is up." in out


def test_up_is_not_said_before_the_assistant_behind_the_adapter_is_healthy(
    tmp_path: Path, fakes: dict[str, str]
) -> None:
    """The adapter answers its health route before the assistant it relays to is ready. The
    clean-machine run said "AURA is up", the console opened, and it read "link closed": the
    first question went unanswered. Docker's own health of that container is waited for."""
    (tmp_path / "bin" / "docker").write_text(
        "#!/bin/sh\n"
        f'echo "$@" >> "{tmp_path / "docker.log"}"\n'
        'case "$1" in\n'
        '  ps) cat "$FAKE_PS";;\n'
        '  info) echo "{}";;\n'
        '  inspect) for last; do :; done; case "$last" in jarvis) echo starting;; *) echo healthy;; esac;;\n'
        "esac\nexit 0\n"
    )
    r = _run(fakes, "--dir", str(tmp_path / "jarvis"))
    assert r.returncode != 0, r.stdout
    assert "assistant: not healthy after 2s" in r.stdout + r.stderr
    assert "is up." not in r.stdout


def _disks(tmp_path: Path, docker_root: Path, free_gb_there: int) -> None:
    """A docker that says where it keeps its data, and a df with 100 GB free everywhere but there."""
    bin_dir = tmp_path / "bin"
    (bin_dir / "docker").write_text(
        "#!/bin/sh\n"
        f'echo "$@" >> "{tmp_path / "docker.log"}"\n'
        'case "$1" in\n'
        '  ps) cat "$FAKE_PS";;\n'
        f'  info) case "$*" in *DockerRootDir*) echo "{docker_root}";; *) echo "{{}}";; esac;;\n'
        "  inspect) echo healthy;;\n"
        "esac\nexit 0\n"
    )
    (bin_dir / "df").write_text(
        "#!/bin/sh\n"
        "for last; do :; done\n"
        'echo "Filesystem 1024-blocks Used Available Capacity Mounted on"\n'
        f'case "$last" in "{docker_root}") echo "disk 1 0 {free_gb_there * 1048576} 0% /";;\n'
        '  *) echo "disk 1 0 104857600 0% /";; esac\n'
    )
    (bin_dir / "df").chmod(0o755)


def test_too_little_room_where_docker_keeps_its_images_is_refused_before_anything_is_made(
    tmp_path: Path, fakes: dict[str, str]
) -> None:
    """Most of what an install stores is images, and Docker often keeps them on another disk
    than the install directory. The clean-machine run filled that disk while the directory's
    had room, and failed an hour in instead of at the first line."""
    store = tmp_path / "docker-root"
    store.mkdir()
    env = dict(fakes, AURA_INSTALL_MIN_GB="20")
    _disks(tmp_path, store, 5)
    r = _run(env, "--dir", str(tmp_path / "jarvis"))
    assert r.returncode != 0, r.stdout
    assert "where Docker keeps its images" in r.stderr and str(store) in r.stderr
    assert not (tmp_path / "jarvis" / ".env").exists()
    assert not [c for c in _calls(tmp_path) if " build " in c or " up -d" in c]
    _disks(tmp_path, store, 60)
    assert _run(env, "--dir", str(tmp_path / "jarvis")).returncode == 0
    # Left to itself the floor is above what an install stores: the clean-machine run's image
    # store held 24 to 26 GB at its fullest with no local model and no natural voice, and a floor of
    # 20 let an install start that could not end.
    left_alone = {k: v for k, v in fakes.items() if k != "AURA_INSTALL_MIN_GB"}
    _disks(tmp_path, store, 25)
    r = _run(left_alone, "--dir", str(tmp_path / "fresh"))
    assert r.returncode != 0 and "where Docker keeps its images" in r.stderr, r.stdout


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _post(url: str, token: str, body: dict) -> tuple[int, dict]:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as res:
            return res.status, json.loads(res.read() or b"{}")
    except urllib.error.HTTPError as err:
        return err.code, json.loads(err.read() or b"{}")


def _answers(**over: object) -> dict:
    answers: dict = {
        "server": {"bind": "192.0.2.10", "public_url": "", "timezone": "Europe/Berlin", "password": PASSWORD},
        "models": {"mode": "endpoint", "url": "http://192.0.2.20:11434/v1", "model": "qwen3:8b", "key": ENDPOINT_KEY},
        "voice": {"tts": "piper", "stt": "base"},
        "wake": {"enabled": False},
        "devices": ["phone", "car"],
        "theme": "jarvis",
    }
    answers.update(over)
    return answers


def _install_through_the_page(tmp_path: Path, fakes: dict[str, str], answers: dict,
                              extra: dict[str, str] | None = None) -> tuple[int, str, Path]:
    """Run the installer with its setup page, answer the page as a browser would, wait for the end."""
    target, port = tmp_path / "jarvis", _free_port()
    env = {**fakes, "AURA_SETUP_NO_BROWSER": "1", "AURA_SETUP_LINGER": "1", "AURA_SETUP_TIMEOUT": "40", **(extra or {})}
    proc = subprocess.Popen(
        ["bash", str(_script(fakes)), "--dir", str(target), "--setup-port", str(port)],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, text=True,
    )
    out, token = "", ""
    assert proc.stdout is not None
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and not token:
            line = proc.stdout.readline()
            if not line:
                break
            out += line
            m = re.search(rf"setup page: http://127\.0\.0\.1:{port}/\?t=([0-9a-f]{{64}})", line)
            if m:
                token = m.group(1)
        assert token, out
        status = 0
        for _ in range(50):   # the fake curl says "ready" at once; the real server needs a moment
            try:
                status, reply = _post(f"http://127.0.0.1:{port}/api/answers", token, answers)
                break
            except (urllib.error.URLError, ConnectionError):
                time.sleep(0.1)
        assert status == 200, (status, reply)
        rest, _ = proc.communicate(timeout=60)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.communicate()
    # Nothing is left listening once the installer has ended.
    with socket.socket() as s:
        assert s.connect_ex(("127.0.0.1", port)) != 0
    return proc.returncode, out + rest, target


def test_the_setup_pages_answers_become_the_install(tmp_path: Path, fakes: dict[str, str]) -> None:
    code, out, target = _install_through_the_page(tmp_path, fakes, _answers())
    assert code == 0, out
    co = Path(fakes["_CHECKOUT"])
    h, c = _values(target / ".env"), _values(co / "aura-core" / ".env")
    # server
    assert h["TAILSCALE_IP"] == "192.0.2.10" and h["AURA_PUBLIC_URL"] == "http://192.0.2.10:8643"
    assert h["TZ"] == c["TZ"] == "Europe/Berlin"
    # the password is kept as the console's own hash and nowhere as itself
    assert h["AURA_CONSOLE_PASSWORD"] == ""
    quoted = h["AURA_CONSOLE_PASSWORD_HASH"]
    assert quoted.startswith("'scrypt$16384$8$1$") and quoted.endswith("'")
    _, n, r_, p, salt, key = quoted.strip("'").split("$")
    made = hashlib.scrypt(PASSWORD.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r_), p=int(p), dklen=32,
                          maxmem=128 * 16384 * 8 * 4)
    assert base64.b64encode(made).decode() == key
    # models: the endpoint is the gateway's; the house's own (unused) model settings stay the template's
    assert (h["LLM_URL"], h["LLM_MODEL"], h["LLM_API_KEY"]) == ("http://192.0.2.20:11434/v1", "qwen3:8b", ENDPOINT_KEY)
    assert c["LLM_API_KEY"] == "" and c["LLM_URL"] != h["LLM_URL"]
    # voice: the light voice, and the listening model the image is built with
    assert h["TTS_PROVIDER"] == "piper" and h["AURA_TTS_ENGINE"].split()[0] == "wyoming"
    assert h["WHISPER_MODEL"].split()[0] == "base" and c["WHISPER_MODEL"] == "base.en"
    # only what was asked for is started: no model server, no card voice, no wake service
    calls = _calls(tmp_path)
    up = [x for x in calls if " up -d" in x]
    hermes_up = [x for x in up if "--project-directory " + str(target) in x][0].split(" up -d ")[1].split()
    assert "jarvis" in hermes_up and "jarvis-web" in hermes_up and "n8n" in hermes_up
    assert "jarvis-models" not in hermes_up and "jarvis-cosyvoice" not in hermes_up
    assert [x for x in up if "--scale wyoming-openwakeword=0" in x]
    built = [x for x in calls if " build " in x and "--project-directory " + str(target) in x][0].split(" build ")[1].split()
    assert built == ["jarvis", "jarvis-adapter", "jarvis-web", "jarvis-extract"]
    # the theme is the primary persona, put in the volume before the first start
    seed = [i for i, x in enumerate(calls) if "--entrypoint sh jarvis" in x and '"active": "jarvis"' in x]
    first_up = min(i for i, x in enumerate(calls) if " up -d" in x)
    assert seed and seed[0] < first_up
    # the answers file keeps the choices and loses its two secrets; both are private
    answers = target / "setup" / "answers.env"
    kept = _values(answers)
    assert kept["SETUP_CONSOLE_HASH"] == "" and kept["SETUP_LLM_KEY"] == "" and kept["SETUP_THEME"] == "jarvis"
    assert stat.S_IMODE(answers.stat().st_mode) == 0o600
    assert stat.S_IMODE((target / "setup").stat().st_mode) == 0o700
    # the page was told the end, with the console to open
    progress = json.loads((target / "setup" / "progress.json").read_text())
    assert progress["phase"] == "done" and progress["console"] == "http://192.0.2.10:8199"
    assert progress["done"] == ["preflight", "setup", "secrets", "n8n", "images", "start", "verify", "pair"]
    # the terminal: what to do next for the devices chosen, and none of the secrets
    assert "sign in with the password you chose" in out
    assert "Phone:" in out and "Car:" in out and "Computer:" not in out
    for secret in (PASSWORD, ENDPOINT_KEY, key, h["AURA_API_KEY"], h["HASS_TOKEN"]):
        assert secret not in out


def test_answers_for_the_default_theme_and_a_wake_word_seed_nothing_and_start_the_wake_service(
    tmp_path: Path, fakes: dict[str, str]
) -> None:
    code, out, target = _install_through_the_page(
        tmp_path, fakes, _answers(theme="aura", wake={"enabled": True}, devices=[]))
    assert code == 0, out
    calls = _calls(tmp_path)
    assert not [x for x in calls if "--entrypoint sh jarvis" in x]
    assert not [x for x in calls if "--scale" in x]
    assert "Phone:" not in out and "Computer:" not in out
    assert "No device was chosen" in out


def test_a_second_run_reuses_the_answers_and_asks_nothing(tmp_path: Path, fakes: dict[str, str]) -> None:
    code, out, target = _install_through_the_page(tmp_path, fakes, _answers())
    assert code == 0, out
    before = (target / ".env").read_text()
    port = _free_port()
    r = subprocess.run(
        ["bash", str(_script(fakes)), "--dir", str(target), "--setup-port", str(port)],
        env={**fakes, "AURA_SETUP_NO_BROWSER": "1", "AURA_SETUP_LINGER": "0"},
        capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert "already in" in r.stdout and "waiting for your answers" not in r.stdout
    assert (target / ".env").read_text() == before
    assert "sign in with the password you chose" in r.stdout


def test_an_answers_file_is_read_as_data_never_run(tmp_path: Path, fakes: dict[str, str]) -> None:
    target = tmp_path / "jarvis"
    (target / "setup").mkdir(parents=True)
    marker = tmp_path / "ran"
    (target / "setup" / "answers.env").write_text(
        "SETUP_VERSION=1\n"
        f"SETUP_BIND=$(touch {marker})\n"
        f"SETUP_MODEL=endpoint; touch {marker}\n"
        f"PATH=/nowhere\n"
        f"touch {marker}\n"
    )
    r = subprocess.run(
        ["bash", str(_script(fakes)), "--dir", str(target), "--setup-port", str(_free_port())],
        env={**fakes, "AURA_SETUP_NO_BROWSER": "1", "AURA_SETUP_LINGER": "0"},
        capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60,
    )
    assert r.returncode != 0
    assert "answers.env" in r.stderr
    assert not marker.exists()
    assert not (target / ".env").exists()


def test_a_dry_run_names_the_setup_page_and_serves_nothing(tmp_path: Path, fakes: dict[str, str]) -> None:
    target, port = tmp_path / "jarvis", _free_port()
    r = subprocess.run(
        ["bash", str(_script(fakes)), "--dir", str(target), "--setup-port", str(port), "--dry-run"],
        env=fakes, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert f"[setup] would serve the setup page on http://127.0.0.1:{port}" in r.stdout
    assert not target.exists()


def test_the_setup_page_never_binds_every_address(tmp_path: Path, fakes: dict[str, str]) -> None:
    r = subprocess.run(
        ["bash", str(_script(fakes)), "--dir", str(tmp_path / "j"), "--setup-bind", "0.0.0.0"],
        env=fakes, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60,
    )
    assert r.returncode != 0 and "never all of them" in r.stderr


def test_help_prints_the_whole_header_and_every_option() -> None:
    r = subprocess.run(["bash", str(INSTALL), "--help"], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0
    for flag in ("--dir", "--bind", "--no-gui", "--setup-bind", "--setup-port", "--build", "--upgrade",
                 "--repo", "--yes", "--dry-run"):
        assert flag in r.stdout, flag
    assert "Not covered" in r.stdout and "set -euo" not in r.stdout


@pytest.mark.skipif(_real_docker() is None, reason="docker compose is not installed here")
def test_the_hash_reaches_the_console_whole_and_the_build_file_resolves(
    tmp_path: Path, fakes: dict[str, str]
) -> None:
    """The hash is full of dollar signs, and compose substitutes those unless the value is quoted."""
    docker = _real_docker()
    assert docker
    code, out, target = _install_through_the_page(tmp_path, fakes, _answers())
    assert code == 0, out
    stored = _values(target / ".env")["AURA_CONSOLE_PASSWORD_HASH"].strip("'")
    env = {k: v for k, v in os.environ.items() if not k.startswith(("COMPOSE_", "AURA_", "JARVIS_"))}
    env["AURA_SRC"] = fakes["_CHECKOUT"]
    c = subprocess.run(
        [docker, "compose", "--project-directory", str(target), "-f", str(target / "docker-compose.yml"),
         "-f", str(target / "docker-compose.build.yml"), "config", "--format", "json"],
        env=env, capture_output=True, text=True, timeout=60,
    )
    assert c.returncode == 0, c.stderr
    services = json.loads(c.stdout)["services"]
    # `config` prints a literal dollar doubled; the container gets it single.
    assert services["jarvis-web"]["environment"]["AURA_CONSOLE_PASSWORD_HASH"].replace("$$", "$") == stored
    assert services["jarvis-web"]["environment"]["AURA_CONSOLE_PASSWORD"] == ""
    assert services["jarvis"]["build"]["context"] == fakes["_CHECKOUT"]
    assert services["jarvis"]["build"]["args"]["WHISPER_MODEL"] == "base"
    assert services["jarvis"]["image"] == "aura-local/jarvis:local"
    assert services["jarvis-web"]["build"]["context"] == str(Path(fakes["_CHECKOUT"]) / "jarvis-web")


# ---- what a review of the install found (T-0864) ----------------------------------------------

def test_a_relative_dir_is_made_absolute_before_an_env_file_names_it(tmp_path: Path, fakes: dict[str, str]) -> None:
    """Compose reads a relative bind source as a volume's name: the backups and the voice's
    models went into volumes nobody looks in."""
    r = subprocess.run(
        ["bash", str(_script(fakes)), "--bind", "127.0.0.1", "--yes", "--dir", "rel/house"],
        env=fakes, cwd=tmp_path, capture_output=True, text=True, timeout=60,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    target = tmp_path / "rel" / "house"
    h = _values(target / ".env")
    for name in ("BACKUP_DIR", "COSYVOICE_MODELS_DIR"):
        assert h[name].strip("'").startswith(str(target) + "/"), (name, h[name])
    assert [c for c in _calls(tmp_path) if "--project-directory " + str(target) in c]
    assert not [c for c in _calls(tmp_path) if "--project-directory rel/house" in c]


def test_a_port_another_program_holds_is_never_taken_for_the_setup_page(tmp_path: Path, fakes: dict[str, str]) -> None:
    """The fake curl says "ready" for any port, as a real one does when another program listens
    there. The address carries the token: it is not printed, nor opened, for a page that is not ours.
    Preflight refuses a taken port where it can see one; here it cannot (no `ss`, as on macOS,
    or a port taken after it looked)."""
    blind = Path(fakes["PATH"].split(":")[0]) / "ss"
    blind.write_text("#!/bin/sh\nexit 0\n")
    blind.chmod(0o755)
    with socket.socket() as held:
        held.bind(("127.0.0.1", 0))
        held.listen(1)
        port = held.getsockname()[1]
        r = subprocess.run(
            ["bash", str(_script(fakes)), "--dir", str(tmp_path / "jarvis"), "--setup-port", str(port)],
            env={**fakes, "AURA_SETUP_NO_BROWSER": "1", "AURA_SETUP_LINGER": "0", "AURA_SETUP_TIMEOUT": "5"},
            capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=90,
        )
    out = r.stdout + r.stderr
    assert r.returncode != 0 and "the setup page did not start" in out, out
    assert "setup page: http" not in out


def test_the_browser_is_handed_a_file_and_never_the_token(tmp_path: Path, fakes: dict[str, str]) -> None:
    """A command line is readable by every user of the machine, and the address carries the
    token: the opener gets a private file that leads there. Not covered: Windows, where the
    opener still gets the address."""
    if os.uname().sysname != "Linux":
        pytest.skip("the opener faked here is the Linux one")
    seen, kept = tmp_path / "opened.txt", tmp_path / "opened.html"
    opener = Path(fakes["PATH"].split(":")[0]) / "xdg-open"
    opener.write_text(f'#!/bin/sh\necho "$@" >> "{seen}"\nstat -c %a "$1" >> "{seen}"\ncat "$1" > "{kept}"\n')
    opener.chmod(0o755)
    code, out, target = _install_through_the_page(
        tmp_path, fakes, _answers(), extra={"AURA_SETUP_NO_BROWSER": "", "DISPLAY": ":0"})
    assert code == 0, out
    token = re.search(r"/\?t=([0-9a-f]{64})", out)
    assert token
    door = target / "setup" / "open.html"
    assert seen.read_text().splitlines() == [str(door), "600"]
    assert token.group(1) in kept.read_text()        # the file leads to the page
    assert not door.exists()                         # and is gone with the page
