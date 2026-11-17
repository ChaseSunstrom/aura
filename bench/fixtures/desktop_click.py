"""Fixture for desktop-click-001: a real desktop agent on a virtual display, and a window to close.

The task is "close the window on my desktop", and the only honest way to score it is to
have a desktop. ``setup`` puts one on the harness host: ``Xvfb :99``, the *real*
``jarvis-desktop`` agent paired to the house (``POST /api/pair/new`` with the house's
pairing secret, then ``/api/pair/claim``), started through
``aura-desktop/tests_e2e/agent_runner.py`` so its consent prompt is answered from a file
(``{"verdict": "approved"}`` -- the person at the machine saying yes to every click and
screenshot, which is what the task is about) and nothing else is replaced. Then an
``xmessage`` with an ``okay`` button at a fixed spot on that display.

The proof of the outcome is the window going away: the fixture holds the ``xmessage``
process and, the moment it exits, writes ``desktop-gone`` into the task's work directory
on the target (through the exec runner, so the ``file_exists`` checker sees it in the
container). On ``:99`` nothing but a click on that button ends the process before
``teardown`` does, and ``teardown`` marks itself first so its own kill never writes the
marker. ``teardown`` also revokes the device token (``DELETE /api/auth/tokens/<id>``),
stops the agent and the display it started, and removes the scratch directory.

Needs on the harness host: ``Xvfb`` and ``xmessage`` on PATH; ``pyautogui``, ``Pillow``
and ``websockets`` in the venv the bench runs from (the agent is started with this
interpreter); the house at ``BENCH_HOUSE_URL`` (default ``http://127.0.0.1:8080``) with
``AURA_TOKEN`` and ``AURA_PAIRING_SECRET`` in the environment or in
``aura-core/.env``. Gate the task with ``desktop`` in ``BENCH_CAPS``: on any other box
``setup`` raises and the row is an error, not a capability failure.

Not covered: a deterministic *miss*. ``xmessage`` cannot be moved once mapped and
``xdotool`` is not installed, so a click that lands the first time passes; the retry after
a miss is pinned where it can be forced, in the plugin's own tests.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

import httpx

_LOGGER = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
AGENT_DIR = ROOT / "aura-desktop"
AGENT_RUNNER = AGENT_DIR / "tests_e2e" / "agent_runner.py"
#: The virtual display. The same one the by-hand proofs used, so a display left up by one
#: of them is reused rather than fought over.
DISPLAY = ":99"
DEVICE_ID = "jcb-desktop"
DEVICE_NAME = "JCB desktop"
#: The file the checker looks for, under the task's ``${work}`` on the target.
MARKER = "desktop-gone"
#: How long the agent gets to register with the house before setup gives up.
ONLINE_TIMEOUT_S = 30.0

#: What setup started, for teardown. The runner loads this module afresh per task.
_state: dict[str, Any] = {}


def _dotenv(key: str) -> str:
    """``KEY=value`` from ``aura-core/.env``, the house's own file, or ``""``.

    An ``AURA_X`` key also matches the file's ``JARVIS_X`` (T-0959: the file predates the rename).
    """
    path = ROOT / "aura-core" / ".env"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    for line in lines:
        name, sep, value = line.strip().partition("=")
        if sep and name in (key, key.replace("AURA_", "JARVIS_", 1)):
            return value.strip().strip("'\"")
    return ""


def _house_credentials() -> tuple[str, str, str]:
    env = os.environ
    house = env.get("BENCH_HOUSE_URL", "http://127.0.0.1:8080").rstrip("/")
    token = env.get("AURA_TOKEN") or _dotenv("AURA_TOKEN")
    secret = env.get("AURA_PAIRING_SECRET") or _dotenv("AURA_PAIRING_SECRET")
    if not token or not secret:
        raise RuntimeError(
            "desktop fixture: AURA_TOKEN and AURA_PAIRING_SECRET are needed to pair the agent "
            "(the environment, or aura-core/.env)"
        )
    return house, token, secret


def _start_xvfb(tmp: Path) -> subprocess.Popen[bytes] | None:
    """``Xvfb :99`` unless a display is already listening there (then it is left alone)."""
    socket = Path(f"/tmp/.X11-unix/X{DISPLAY[1:]}")
    if socket.exists():
        return None
    log = (tmp / "xvfb.log").open("wb")
    proc = subprocess.Popen(  # noqa: S603
        ["Xvfb", DISPLAY, "-screen", "0", "1280x800x24", "-nolisten", "tcp"],
        stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
    )
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if socket.exists():
            return proc
        if proc.poll() is not None:
            break
        time.sleep(0.1)
    raise RuntimeError(f"desktop fixture: Xvfb {DISPLAY} did not come up (see {tmp / 'xvfb.log'})")


def _pair(house: str, token: str, secret: str) -> tuple[str, str]:
    """A device token for the agent: ``(token_id, token)``. The code is spent on the claim."""
    with httpx.Client(base_url=house, timeout=15.0) as c:
        issued = c.post("/api/pair/new", headers={"Authorization": f"Bearer {token}"}, json={"secret": secret})
        issued.raise_for_status()
        claimed = c.post("/api/pair/claim", json={"code": issued.json()["code"], "name": DEVICE_NAME})
        claimed.raise_for_status()
    reply = claimed.json()
    return str(reply.get("token_id") or ""), str(reply["token"])


def _start_agent(tmp: Path, house: str, device_token: str) -> subprocess.Popen[bytes]:
    control = tmp / "control"
    control.mkdir()
    (control / "consent.json").write_text(json.dumps({"verdict": "approved"}), encoding="utf-8")
    config = tmp / "config.json"
    config.write_text(json.dumps({"input_automation": {"enabled": True}, "consent_timeout_s": 20.0}), encoding="utf-8")
    workspace = tmp / "workspace"
    workspace.mkdir()
    env = dict(os.environ)
    env.update({
        "AURA_TOKEN": device_token,
        "AURA_STATE_DIR": str(tmp / "state"),
        "AURA_DEVICE_ID": DEVICE_ID,
        "AURA_DEVICE_NAME": DEVICE_NAME,
        "AURA_E2E_CONTROL": str(control),
        "DISPLAY": DISPLAY,
        "PYTHONUNBUFFERED": "1",
        "PYTHONPATH": os.pathsep.join(p for p in (str(AGENT_DIR), env.get("PYTHONPATH", "")) if p),
    })
    env.pop("AURA_TOKEN_FILE", None)
    log = (tmp / "agent.log").open("wb")
    return subprocess.Popen(  # noqa: S603
        [sys.executable, "-u", str(AGENT_RUNNER), "run", "--server", "ws" + house[len("http"):],
         "--config", str(config), "--workspace", str(workspace), "-v"],
        cwd=str(AGENT_DIR), env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
    )


def _wait_online(house: str, token: str, agent: subprocess.Popen[bytes], log: Path) -> None:
    deadline = time.monotonic() + ONLINE_TIMEOUT_S
    with httpx.Client(base_url=house, timeout=10.0, headers={"Authorization": f"Bearer {token}"}) as c:
        while time.monotonic() < deadline:
            if agent.poll() is not None:
                break
            data = c.get("/api/config/companion/list").raise_for_status().json()
            items = data if isinstance(data, list) else data.get("devices", data.get("companions", []))
            for d in items:
                if (d.get("id") or d.get("device_id")) == DEVICE_ID and d.get("online", d.get("connected")):
                    return
            time.sleep(1.0)
    tail = ""
    try:
        tail = log.read_text(encoding="utf-8", errors="replace")[-600:]
    except OSError:
        pass
    raise RuntimeError(f"desktop fixture: {DEVICE_NAME} never came online at {house}; agent log tail:\n{tail}")


def _mark_when_gone(window: subprocess.Popen[bytes], exec_runner: Any, marker: str) -> None:
    window.wait()
    if _state.get("closing"):
        return
    try:
        exec_runner.write_file(marker, "gone\n")
    except Exception as exc:  # noqa: BLE001 - a daemon thread; the row then fails and says why
        _LOGGER.warning("desktop fixture: could not write %s: %s", marker, exc)


def setup(ctx: dict[str, Any]) -> dict[str, str]:
    for tool in ("Xvfb", "xmessage"):
        if shutil.which(tool) is None:
            raise RuntimeError(f"desktop fixture: {tool} is not installed on the harness host")
    house, token, secret = _house_credentials()
    tmp = Path(tempfile.mkdtemp(prefix="jcb-desktop-"))
    _state.clear()
    _state.update({"tmp": tmp, "house": house, "token": token, "closing": False})
    marker = f"{ctx['work_root']}/{MARKER}"
    try:
        # The work directory outlives a run: a marker from the last run would pass this one.
        ctx["exec"].remove(marker)
        _state["xvfb"] = _start_xvfb(tmp)
        _state["token_id"], device_token = _pair(house, token, secret)
        _state["agent"] = _start_agent(tmp, house, device_token)
        _wait_online(house, token, _state["agent"], tmp / "agent.log")
        window = subprocess.Popen(  # noqa: S603
            ["xmessage", "-display", DISPLAY, "-geometry", "+200+300", "-buttons", "okay", "-default", "okay", "okay"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        time.sleep(1.0)
        if window.poll() is not None:
            raise RuntimeError(f"desktop fixture: xmessage exited at once with {window.returncode} on {DISPLAY}")
        _state["window"] = window
    except Exception:
        teardown(ctx)
        raise
    threading.Thread(target=_mark_when_gone, args=(window, ctx["exec"], marker), daemon=True).start()
    return {"desktop_name": DEVICE_NAME, "desktop_device": DEVICE_ID, "desktop_display": DISPLAY}


def _stop(proc: subprocess.Popen[bytes] | None, group: bool = False) -> None:
    if proc is None or proc.poll() is not None:
        return
    # The agent is started in its own session so a child it left (pyautogui's helpers)
    # goes with it; a plain terminate() would orphan them on the display.
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(proc.pid, sig) if group else proc.send_signal(sig)
            proc.wait(timeout=5.0)
            return
        except (subprocess.TimeoutExpired, ProcessLookupError, PermissionError):
            continue


def _revoke() -> None:
    token_id = _state.get("token_id")
    if not token_id:
        return
    try:
        with httpx.Client(base_url=_state["house"], timeout=10.0) as c:
            c.delete(f"/api/auth/tokens/{token_id}", headers={"Authorization": f"Bearer {_state['token']}"}).raise_for_status()
    except httpx.HTTPError as exc:
        _LOGGER.warning("desktop fixture: token %s not revoked: %s", token_id, exc)


def teardown(ctx: dict[str, Any]) -> None:
    del ctx
    _state["closing"] = True
    _stop(_state.get("window"))
    _stop(_state.get("agent"), group=True)
    _revoke()
    _stop(_state.get("xvfb"), group=True)
    tmp = _state.get("tmp")
    if tmp is not None:
        shutil.rmtree(tmp, ignore_errors=True)
