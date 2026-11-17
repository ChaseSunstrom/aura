"""One command: a throwaway house with the demo entities, and the scenario that needs it.

    python3 -m testing.live.throwaway            # every scenario that `requires: demo_house`
    python3 -m testing.live.throwaway --only interactions-thread-continuity

`interactions-thread-continuity` turns on `light.bed_light`, an entity only the
demo house has, so it cannot run on the stack and is skipped there by name. This
boots what it needs instead: a jarvis-core of our own with the demo house, and a
Hermes gateway whose only tool is the house, pointed at that core and nowhere
else, with the core answering voice turns through the gateway (`voice.agent`,
the way the deployed one does). The run's report names the throwaway core's URL
as its target, and a finalizer takes both down however the run ends.

What this does *not* promise. The guard below refuses to run against anything
that is not a loopback port this process picked and a core that answers with a
marker this process generated; it cannot know the gateway's model is the one the
house uses, and a scenario's own assertions are all that judge the model's reply.
The gateway has no `jarvis-guard` plugin, so the approval matrix is not part of
what a throwaway run proves. A launcher killed with SIGKILL runs no finalizer:
the next launch reaps what its pid file names, and only a process whose start
time and command line still match what was recorded.
"""

from __future__ import annotations

import argparse
import asyncio
import atexit
import contextlib
import json
import os
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

if str(Path(__file__).resolve().parents[2]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from testing.harness.harness import free_port, http_json  # noqa: E402
from testing.live import LiveError  # noqa: E402
from testing.live import runner as runner_module  # noqa: E402
from testing.live.ground import Ground  # noqa: E402
from testing.live.rig import OUT_DIR  # noqa: E402
from testing.live.scenario import load_all  # noqa: E402
from testing.live.transport import Console  # noqa: E402

#: Where the deployed stack listens: the core, the Hermes API, the adapter and the HUD.
#: Nothing here is ever bound, and nothing here is ever a target.
FORBIDDEN_PORTS = frozenset({8080, 8642, 8643, 9119})
#: The only address this ever binds or talks to. A name is not an address.
LOOPBACK = "127.0.0.1"
PID_FILE = OUT_DIR / "throwaway-pids.json"
GATEWAY_TIMEOUT = 180.0
#: What a gateway may inherit. Everything else — chat tokens, cloud keys, the
#: deployed stack's own credentials — stays with the caller.
INHERITED = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "TZ", "LLM_URL", "LLM_MODEL", "LLM_API_KEY")


# --- the guard ---------------------------------------------------------------


def refuse_production(url: str) -> tuple[str, int]:
    """The decision: may this URL be a throwaway's? Returns (host, port) or raises.

    Refuses by default: only plain http to the literal loopback address with an
    explicit port that is not one of the deployed stack's and not the address
    the environment names as the deployed core (`AURA_URL`).
    """
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        raise LiveError(f"refusing {url!r}: not a URL") from None
    if parts.scheme != "http" or parts.hostname != LOOPBACK or port is None:
        raise LiveError(
            f"refusing {url!r}: a throwaway is plain http to {LOOPBACK} with an explicit port"
        )
    if port in FORBIDDEN_PORTS:
        raise LiveError(f"refusing {url!r}: port {port} is the deployed stack's")
    try:
        deployed = urlsplit(os.environ.get("AURA_URL", ""))
        same = deployed.hostname == parts.hostname and deployed.port == port
    except ValueError:
        same = False
    if same:
        raise LiveError(f"refusing {url!r}: it is the deployed core's address (AURA_URL)")
    return LOOPBACK, port


class DemoHouse(Ground):
    """The throwaway core as a ground: guarded before anything is sent to it."""

    name = "throwaway"

    def __init__(self, base_url: str, token: str, *, marker: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.marker = marker
        self._console: Console | None = None

    def start(self) -> "DemoHouse":
        refuse_production(self.base_url)
        # The marker is the proof that what answers is the core this process
        # started: a production core, or a stranger on a recycled port, does not
        # know a name drawn a moment ago.
        try:
            config = http_json(f"{self.base_url}/api/config", token=self.token, timeout=5.0)
        except Exception as err:  # noqa: BLE001 - any failure is a refusal
            raise LiveError(f"refusing {self.base_url}: /api/config did not answer ({err})") from None
        if not isinstance(config, dict) or config.get("location_name") != self.marker:
            raise LiveError(f"refusing {self.base_url}: it is not the throwaway core this run started")
        try:
            http_json(f"{self.base_url}/api/states/light.bed_light", token=self.token, timeout=5.0)
        except Exception as err:  # noqa: BLE001
            raise LiveError(
                f"refusing {self.base_url}: it has no light.bed_light, so no demo house ({err})"
            ) from None
        return self

    def console(self) -> Console | None:
        if self._console is None:
            self._console = Console(self.base_url, self.token).start()
        return self._console

    def restart_core(self) -> None:
        raise LiveError("the throwaway core is not restarted mid-run")

    def stop(self) -> None:
        # The core and the gateway belong to Throwaway, which outlives the run.
        if self._console is not None:
            self._console.stop()
            self._console = None


# --- processes: recorded, fingerprinted, reaped ------------------------------


def fingerprint(pid: int) -> dict[str, Any] | None:
    """What identifies a process across a pid reuse: its start tick and command line."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        cmdline = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
    except OSError:
        return None
    # Field 22, counted after the "(comm)" which may itself hold spaces or parens.
    start = int(stat.rsplit(")", 1)[1].split()[19])
    return {"pid": pid, "start": start, "cmdline": [c.decode(errors="replace") for c in cmdline if c]}


def _kill_group(pid: int, wait: Callable[[float], Any] | None = None) -> None:
    """TERM the process group, then KILL what is left after a few seconds."""
    for sig, grace in ((signal.SIGTERM, 8.0), (signal.SIGKILL, 5.0)):
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(pid, sig)
        deadline = time.monotonic() + grace
        while time.monotonic() < deadline:
            if wait is not None:
                with contextlib.suppress(Exception):
                    wait(0.05)
            try:
                state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
            except OSError:
                return
            if state == "Z":  # dead, waiting only for its parent to collect it
                return
            time.sleep(0.05)


def _read_pids(pid_file: Path) -> list[dict[str, Any]]:
    try:
        data = json.loads(pid_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [e for e in data if isinstance(e, dict) and isinstance(e.get("pid"), int)] if isinstance(data, list) else []


def _write_pids(pid_file: Path, entries: list[dict[str, Any]]) -> None:
    # ponytail: read-modify-write without a lock; two launches at once are
    # already serialised by the rig, and a lost update costs one orphan that
    # the next reap cannot name. A lock file if launches ever overlap.
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    tmp = pid_file.with_suffix(".tmp")
    tmp.write_text(json.dumps(entries), encoding="utf-8")
    os.replace(tmp, pid_file)


def reap_stale(pid_file: Path) -> list[int]:
    """Kill what a launcher that never ran its finalizers left behind.

    Only a process whose start time and command line still equal what was
    recorded: a recycled pid is somebody else's and is left alone, and so is
    anything owned by a launcher that is still running.
    """
    reaped: list[int] = []
    for entry in _read_pids(pid_file):
        owner = entry.get("owner")
        if isinstance(owner, dict) and owner.get("pid") != os.getpid():
            alive = fingerprint(int(owner["pid"]))
            if alive is not None and alive["start"] == owner.get("start"):
                continue
        now = fingerprint(entry["pid"])
        if now is None or now["start"] != entry.get("start") or now["cmdline"] != entry.get("cmdline"):
            continue
        _kill_group(entry["pid"])
        reaped.append(entry["pid"])
    with contextlib.suppress(OSError):
        pid_file.unlink()
    return reaped


# --- the gateway -------------------------------------------------------------

GATEWAY_CONFIG = """\
model:
  default: "{model}"
  provider: throwaway-llm
  base_url: "{llm_url}"
  api_key: ""
  context_length: 131072
  api_mode: chat_completions
providers:
  throwaway-llm:
    api: "{llm_url}"
    transport: chat_completions
    default_model: "{model}"
    context_length: 131072
    request_timeout_seconds: 900
# The house and nothing else: no shell, no files, no web for a scenario that
# only needs a light turned on.
platform_toolsets:
  api_server:
    - homeassistant
gateway:
  api_server:
    enabled: true
    host: {host}
    port: {port}
    model_name: jarvis
  platforms:
    homeassistant:
      enabled: false
approvals:
  mode: smart
"""


def gateway_env(home: Path, *, core_url: str, core_token: str, port: int, key: str) -> dict[str, str]:
    """The gateway's whole environment: an allow-list, and the throwaway core."""
    env = {name: os.environ[name] for name in INHERITED if name in os.environ}
    env.update(
        HERMES_HOME=str(home),
        HASS_URL=core_url,
        HASS_TOKEN=core_token,
        API_SERVER_ENABLED="true",
        API_SERVER_HOST=LOOPBACK,
        API_SERVER_PORT=str(port),
        API_SERVER_KEY=key,
        PYTHONUNBUFFERED="1",
    )
    return env


# --- the throwaway -----------------------------------------------------------


def _boot_core(tw: "Throwaway") -> None:
    from testing.harness import Harness

    h = Harness(
        work_dir=tw.work_dir / "core",
        host=LOOPBACK,
        port=tw.core_port,
        token=tw.token,
        name=tw.marker,
        agent_url=f"http://{LOOPBACK}:{tw.gateway_port}",
        agent_key=tw.gateway_key,
        llm_api_key="",
        keep=False,
        wyoming={
            "host": os.environ.get("LIVE_STT_HOST", LOOPBACK),
            "stt": int(os.environ.get("LIVE_STT_PORT", "10300")),
            "tts": int(os.environ.get("LIVE_TTS_PORT", "10200")),
            "wake": int(os.environ.get("LIVE_WAKE_PORT", "10400")),
        },
    )
    tw.on_close(h.stop)
    try:
        h.start()
    finally:
        # Recorded even when the boot failed half way: whatever started is ours to reap.
        for child in getattr(h, "_children", []):
            tw.record(child.process.pid)


def _boot_gateway(tw: "Throwaway") -> None:
    exe = shutil.which("hermes")
    model, llm_url = os.environ.get("LLM_MODEL", ""), os.environ.get("LLM_URL", "")
    if not exe:
        raise LiveError("the throwaway gateway needs `hermes` on PATH")
    if not (model and llm_url):
        raise LiveError("the throwaway gateway needs LLM_URL and LLM_MODEL in the environment")
    home = tw.work_dir / "hermes-home"
    home.mkdir(parents=True)
    (home / "config.yaml").write_text(
        GATEWAY_CONFIG.format(model=model, llm_url=llm_url, host=LOOPBACK, port=tw.gateway_port),
        encoding="utf-8",
    )
    env = gateway_env(home, core_url=tw.base_url, core_token=tw.token,
                      port=tw.gateway_port, key=tw.gateway_key)
    proc = tw.spawn("gateway", [exe, "gateway", "run"], env=env)
    tw.wait_until_listening(tw.gateway_port, GATEWAY_TIMEOUT, proc=proc, log=tw.work_dir / "gateway.log")


class Throwaway:
    """A core with the demo house and a gateway for it; both gone when this closes."""

    def __init__(
        self,
        boot_core: Callable[["Throwaway"], None] = _boot_core,
        boot_gateway: Callable[["Throwaway"], None] = _boot_gateway,
        pick_port: Callable[[], int] | None = None,
        pid_file: Path = PID_FILE,
    ) -> None:
        self.boot_core = boot_core
        self.boot_gateway = boot_gateway
        self.pick_port = pick_port or (lambda: free_port(LOOPBACK, avoid=FORBIDDEN_PORTS))
        self.pid_file = Path(pid_file)
        # Drawn now, never written down: nothing here is a credential anywhere else.
        self.token = secrets.token_urlsafe(24)
        self.gateway_key = secrets.token_urlsafe(24)
        self.marker = "throwaway-" + secrets.token_hex(8)
        self.core_port = 0
        self.gateway_port = 0
        self.work_dir: Path | None = None
        self.pids: list[int] = []
        self._procs: list[subprocess.Popen[bytes]] = []
        self._closers: list[Callable[[], Any]] = []
        self._ground: DemoHouse | None = None
        self._closed = False

    @property
    def base_url(self) -> str:
        return f"http://{LOOPBACK}:{self.core_port}"

    def ground(self) -> DemoHouse:
        if self._ground is None:
            self._ground = DemoHouse(self.base_url, self.token, marker=self.marker)
        return self._ground

    def on_close(self, fn: Callable[[], Any]) -> None:
        self._closers.append(fn)

    def record(self, pid: int) -> None:
        """Write a pid and its fingerprint down, so a launcher that dies unclean is reaped."""
        if pid in self.pids:
            return
        self.pids.append(pid)
        print_fp = fingerprint(pid)
        if print_fp is None:
            return
        mine = fingerprint(os.getpid())
        entries = _read_pids(self.pid_file)
        entries.append({**print_fp, "owner": {"pid": os.getpid(), "start": mine["start"] if mine else 0}})
        _write_pids(self.pid_file, entries)

    def spawn(self, name: str, argv: list[str], env: dict[str, str] | None = None) -> subprocess.Popen[bytes]:
        assert self.work_dir is not None
        # To a file, never a pipe nobody reads: a chatty child would block on it.
        log = (self.work_dir / f"{name}.log").open("wb")
        try:
            proc = subprocess.Popen(  # noqa: S603
                argv, env=env, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                start_new_session=True,
            )
        finally:
            log.close()
        self._procs.append(proc)
        self.on_close(lambda p=proc: self._stop_proc(p))
        self.record(proc.pid)
        return proc

    @staticmethod
    def _stop_proc(proc: subprocess.Popen[bytes]) -> None:
        if proc.poll() is None:
            _kill_group(proc.pid, wait=lambda t: proc.wait(timeout=t))
        with contextlib.suppress(Exception):
            proc.wait(timeout=5)

    def wait_until_listening(
        self, port: int, timeout: float, proc: subprocess.Popen[bytes] | None = None,
        log: Path | None = None,
    ) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if proc is not None and proc.poll() is not None:
                raise LiveError(f"a process exited ({proc.returncode}) before listening on {port}{_tail(log)}")
            with contextlib.suppress(OSError):
                socket.create_connection((LOOPBACK, port), 0.5).close()
                return
            time.sleep(0.2)
        raise LiveError(f"nothing listened on {LOOPBACK}:{port} within {timeout:g}s{_tail(log)}")

    def __enter__(self) -> "Throwaway":
        try:
            reap_stale(self.pid_file)
            self.core_port = self.pick_port()
            for _ in range(8):
                self.gateway_port = self.pick_port()
                if self.gateway_port != self.core_port:
                    break
            # Before a directory is made or a process started.
            refuse_production(self.base_url)
            refuse_production(f"http://{LOOPBACK}:{self.gateway_port}")
            if self.gateway_port == self.core_port:
                raise LiveError(f"refusing: could not pick two different ports ({self.core_port})")
            self.work_dir = Path(tempfile.mkdtemp(prefix="throwaway-house-"))
            atexit.register(self.close)
            self.boot_core(self)
            self.ground().start()
            self.boot_gateway(self)
        except BaseException:
            self.close()
            raise
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        """Idempotent, and it carries on past a closer that fails."""
        if self._closed:
            return
        self._closed = True
        atexit.unregister(self.close)
        if self._ground is not None:
            with contextlib.suppress(Exception):
                self._ground.stop()
        for closer in reversed(self._closers):
            with contextlib.suppress(Exception):
                closer()
        self._closers = []
        mine = set(self.pids)
        with contextlib.suppress(OSError):
            left = [e for e in _read_pids(self.pid_file) if e["pid"] not in mine]
            if left:
                _write_pids(self.pid_file, left)
            else:
                self.pid_file.unlink()
        if self.work_dir is not None:
            shutil.rmtree(self.work_dir, ignore_errors=True)


def _tail(log: Path | None, lines: int = 30) -> str:
    if log is None:
        return ""
    try:
        text = log.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return "\nlog tail:\n" + "\n".join(text.splitlines()[-lines:])


# --- the command -------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the demo-house scenarios on a throwaway core.")
    parser.add_argument("--only", default="", help="comma-separated scenario names "
                        "(default: every scenario that requires the demo house)")
    parser.add_argument("--variants", default="voice,text,voice-ui,text-ui")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--write-report", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    return parser


def launch(argv: list[str], make_throwaway: Callable[[], Throwaway] = Throwaway) -> int:
    args = _parser().parse_args(argv)
    if not args.only:
        args.only = ",".join(s.name for s in load_all() if s.needs_demo_house)
    # The rest of what the runner reads from its own parser.
    args.implemented_only, args.full, args.capability = False, True, ""
    args.target, args.no_protect, args.keep = "throwaway", True, False
    with make_throwaway() as tw:
        print(f"throwaway: demo-house core at {tw.base_url}, gateway on {LOOPBACK}:{tw.gateway_port}", flush=True)
        # Looked up on the module at call time, so a caller can stand in for the run.
        return asyncio.run(runner_module._main(args, ground=tw.ground()))


def main(argv: list[str] | None = None) -> int:
    # SIGTERM raises like Ctrl-C does, so the finalizers run for both.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
    try:
        return launch(sys.argv[1:] if argv is None else argv)
    except LiveError as err:
        print(f"the throwaway could not run: {err}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
