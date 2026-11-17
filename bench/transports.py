"""Network transports the runner drives Hermes through (the v2 plan §2.2 / D16).

Three surfaces, verified against the installed ``Hermes Agent v0.21.0`` on 2026-09-03:

* :class:`RunsClient` — the API server ``/v1/runs`` family. ``POST /v1/runs`` returns a
  ``run_id`` immediately (202); ``GET /v1/runs/{id}/events`` streams SSE frames
  (``data: {json}`` where the JSON carries an ``event`` field). Event names seen live:
  ``message.delta``, ``reasoning.available``, ``tool.started`` (``tool`` + ``preview``),
  ``tool.completed`` (``tool``, ``duration``, ``error``), ``approval.request``
  (``choices`` + the flagged command), ``approval.responded``, ``run.completed``
  (``output`` + ``usage`` with ``input_tokens``/``output_tokens``/``total_tokens``),
  ``run.failed`` (``error``), ``run.cancelled``, ``run.steered``. The runs stream
  exposes a tool's *preview* string, **not** structured args, so ``tool_used`` arg
  checks match against the preview (documented in :mod:`bench.checkers`).
* :class:`GatewayClient` — the adapter ``/ws`` TUI gateway for slash-command tasks
  (goals, HUD, plugin commands), driven exactly as ``scripts/smoke.sh`` does:
  ``session.create`` → ``command.dispatch`` → ``prompt.submit``, collecting ``event``
  frames (``tool.start``/``tool.complete``, ``status.update`` kind ``goal``,
  ``approval.request``, ``hud.panel.*``).
* :class:`AudioClient` — the adapter's ``/api/audio/transcribe`` and ``/api/audio/speak``
  (Whisper in, Piper out) for voice tasks.

Kept in one module so the HTTP/WS/token plumbing lives in a single place rather than
being copied into the runner and the checkers (the duplication the house register in
CLAUDE.md forbids; V2_PLAN §3.1 folds this into a shared ``jarvis-bench`` package later).
"""

from __future__ import annotations

import dataclasses
import json
import logging
import shlex
import subprocess
import time
from collections.abc import Iterator
from typing import Any

import httpx
from websockets.sync.client import ClientConnection, connect as ws_connect

_LOGGER = logging.getLogger(__name__)


@dataclasses.dataclass
class ToolCall:
    """One tool invocation observed on a run. ``preview`` is the args-as-string the
    stream exposes (the runs API does not surface structured args)."""

    name: str
    preview: str = ""
    duration: float = 0.0
    error: bool = False


@dataclasses.dataclass
class RunObservation:
    """Everything a checker needs about one drive of a task, transport-agnostic.

    Both the runs path and the gateway path fill this in, so a checker never has to know
    which surface produced the run. ``token_source`` is ``reported`` when the model
    returned usage and ``estimated`` when the runner had to approximate it (V2_PLAN §2.5).
    """

    output: str = ""
    tool_calls: list[ToolCall] = dataclasses.field(default_factory=list)
    approvals: list[dict[str, Any]] = dataclasses.field(default_factory=list)
    approval_responses: list[dict[str, Any]] = dataclasses.field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    token_source: str = "reported"
    swap_events: int = 0
    transcript: str | None = None
    audio_out: bool = False
    error: str | None = None
    run_id: str | None = None
    raw_events: list[dict[str, Any]] = dataclasses.field(default_factory=list)

    @property
    def tool_names(self) -> list[str]:
        return [tc.name for tc in self.tool_calls]


def _estimate_tokens(text: str) -> int:
    """Rough token count when the model reports no usage (~4 chars/token). Flagged
    ``estimated`` so a reader never trusts it as a measurement."""
    return max(1, len(text) // 4) if text else 0


class ExecRunner:
    """Run shell commands and touch files on the target filesystem.

    ``prefix`` is the ``BENCH_EXEC`` docker-exec prefix (e.g. ``docker exec jarvis``);
    empty means the agent and the harness share a filesystem (the dev install), mirroring
    ``scripts/smoke.sh``'s ``exists()``. All file paths are absolute paths on the target.
    """

    def __init__(self, prefix: str = "", timeout: float = 30.0, owner: str = "") -> None:
        self.prefix = shlex.split(prefix) if prefix else []
        self.timeout = timeout
        #: ``user:group`` every directory made and every file staged is handed to. Through
        #: ``docker exec`` the harness is root, so a task's work directory was root 0755 and
        #: the gateway (uid 10000) could not write its answer into it: code-write-001 wrote
        #: a sibling file and scored as a capability failure (2026-09-18). Empty means no
        #: chown — the dev install, where the harness and the agent are one user.
        self.owner = owner

    def run(self, argv: list[str]) -> tuple[int, str, str]:
        cmd = self.prefix + argv
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
        except subprocess.TimeoutExpired as exc:
            return 124, "", f"timeout after {self.timeout}s: {exc}"
        except FileNotFoundError as exc:
            return 127, "", str(exc)
        return proc.returncode, proc.stdout, proc.stderr

    def run_shell(self, command: str) -> tuple[int, str, str]:
        """Run a command string through ``sh -c`` on the target (pipes, redirects)."""
        return self.run(["sh", "-c", command])

    def file_exists(self, path: str) -> bool:
        return self.run(["test", "-f", path])[0] == 0

    def read_file(self, path: str) -> str | None:
        rc, out, _ = self.run(["cat", path])
        return out if rc == 0 else None

    def write_file(self, path: str, content: str) -> None:
        parent = path.rsplit("/", 1)[0] if "/" in path else "."
        # One command, not two: the directory used to be created by a separate `mkdir -p`,
        # and a task whose fixture sits in a nested path failed at setup with "Directory
        # nonexistent" — the write reached the container while the directory did not,
        # whether because the first call timed out under load or because a concurrent run's
        # teardown removed the tree between them. Every coding task with a nested fixture
        # died before the model saw anything, which scores as a capability failure.
        # Pipe the content via stdin so arbitrary bytes (quotes, newlines) survive without
        # the shell-quoting hazards a heredoc or `echo` would introduce.
        script = f"mkdir -p {shlex.quote(parent)} && cat > {shlex.quote(path)}"
        if self.owner:
            script += f" && chown {shlex.quote(self.owner)} {shlex.quote(parent)} {shlex.quote(path)}"
        cmd = self._stdin_prefix() + ["sh", "-c", script]
        proc = subprocess.run(cmd, input=content, text=True, timeout=self.timeout,
                              check=False, capture_output=True)
        # Verified, not assumed. Both staging defects found so far — the missing `-i` that made
        # every fixture arrive empty, and the separate mkdir that raced a concurrent teardown —
        # were silent here, and each one scored as a whole category of capability failure with
        # the agent truthfully reporting an empty or absent file. A staging failure must look
        # like a broken harness, which is a bug to fix, not like a model that cannot read.
        if proc.returncode != 0:
            raise RuntimeError(
                f"staging {path} failed (rc={proc.returncode}): {proc.stderr.strip()[:200]}")
        rc, out, _ = self.run(["wc", "-c", path])
        landed = int(out.split()[0]) if rc == 0 and out.split() else -1
        if landed != len(content.encode()):
            raise RuntimeError(
                f"staging {path} wrote {landed} bytes, expected {len(content.encode())}")

    def _stdin_prefix(self) -> list[str]:
        """The exec prefix for a command that must read stdin. ``docker exec`` forwards
        stdin only with ``-i``; without it the container's ``cat`` read nothing and every
        staged fixture file arrived empty — the whole personal-data category of baseline
        run 4 scored 0 for that reason, with the agent truthfully reporting "0 bytes"."""
        if len(self.prefix) >= 2 and self.prefix[0] == "docker" and self.prefix[1] == "exec":
            if "-i" not in self.prefix[2:]:
                return self.prefix[:2] + ["-i"] + self.prefix[2:]
        return list(self.prefix)

    def mkdir(self, path: str) -> None:
        if self.owner:
            self.run(["sh", "-c", f"mkdir -p {shlex.quote(path)} && chown {shlex.quote(self.owner)} {shlex.quote(path)}"])
        else:
            self.run(["mkdir", "-p", path])

    def remove(self, path: str) -> None:
        self.run(["rm", "-rf", path])


class RunsClient:
    """Drive the API server ``/v1/runs`` family. Synchronous; SSE via ``iter_lines``."""

    #: How long ``create_run`` keeps retrying a 429 before giving up, and the first wait
    #: between attempts (seconds; grows 1.5× per attempt, capped at 30).
    busy_retry_s: float = 300.0
    busy_initial_delay_s: float = 5.0

    def __init__(self, base_url: str, api_key: str, *, model: str = "jarvis") -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._headers = {"Content-Type": "application/json"}
        if api_key:
            self._headers["Authorization"] = f"Bearer {api_key}"

    def create_run(
        self,
        prompt: str,
        *,
        session_id: str | None = None,
        instructions: str | None = None,
    ) -> str:
        body: dict[str, Any] = {"input": prompt, "model": self.model}
        if session_id:
            body["session_id"] = session_id
        if instructions:
            body["instructions"] = instructions
        # The API server caps concurrent runs (gateway.api_server.max_concurrent_runs,
        # default 10) and answers 429 when full. Runs this harness abandoned on a timeout
        # keep executing server-side until they finish, so a burst of slow tasks fills the
        # cap and every later task was recorded as a 2-second failure ("Too many requests")
        # in the first production baseline. A 429 is therefore waited out, not scored.
        deadline = time.monotonic() + self.busy_retry_s
        delay = self.busy_initial_delay_s
        attempts = 0
        with httpx.Client(timeout=30.0) as client:
            while True:
                resp = client.post(f"{self.base_url}/v1/runs", headers=self._headers, json=body)
                if resp.status_code != 429 or time.monotonic() + delay > deadline:
                    resp.raise_for_status()
                    data = resp.json()
                    break
                attempts += 1
                retry_after = resp.headers.get("Retry-After")
                if retry_after and retry_after.isdigit():
                    delay = max(delay, float(retry_after))
                _LOGGER.warning(
                    "/v1/runs busy (429, attempt %d): waiting %.0fs for server-side runs to drain",
                    attempts, delay,
                )
                time.sleep(delay)
                delay = min(delay * 1.5, 30.0)
        run_id = data.get("run_id")
        if not run_id:
            raise RuntimeError(f"/v1/runs returned no run_id: {data}")
        return str(run_id)

    def stop_run(self, run_id: str) -> bool:
        """``POST /v1/runs/{id}/stop`` — release a run this harness will no longer observe
        (a task timeout), so it stops counting against the server's concurrency cap.
        Returns False instead of raising: the task's own verdict is already decided."""
        try:
            with httpx.Client(timeout=15.0) as client:
                resp = client.post(f"{self.base_url}/v1/runs/{run_id}/stop", headers=self._headers)
            return resp.status_code < 400
        except httpx.HTTPError as exc:
            _LOGGER.warning("stop of run %s failed: %s", run_id, exc)
            return False

    def stream_events(self, run_id: str, timeout_s: float) -> Iterator[dict[str, Any]]:
        """Yield decoded event dicts from ``GET /v1/runs/{id}/events`` until the stream
        closes or ``timeout_s`` elapses. SSE ``data:`` lines carry JSON with an ``event``
        key; ``:`` comment lines (keepalive / stream closed) are skipped."""
        url = f"{self.base_url}/v1/runs/{run_id}/events"
        deadline = time.monotonic() + timeout_s
        with httpx.Client(timeout=httpx.Timeout(timeout_s + 5, read=timeout_s + 5)) as client:
            with client.stream("GET", url, headers=self._headers) as resp:
                resp.raise_for_status()
                for line in resp.iter_lines():
                    if time.monotonic() > deadline:
                        raise TimeoutError(f"run {run_id} events exceeded {timeout_s}s")
                    if not line or line.startswith(":"):
                        continue
                    if not line.startswith("data: "):
                        continue
                    payload = line[len("data: ") :]
                    try:
                        yield json.loads(payload)
                    except json.JSONDecodeError:
                        _LOGGER.debug("skipping non-JSON SSE frame: %r", payload)

    def respond_approval(
        self, run_id: str, choice: str, *, request_id: str | None = None
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"choice": choice}
        if request_id:
            body["request_id"] = request_id
        with httpx.Client(timeout=30.0) as client:
            resp = client.post(
                f"{self.base_url}/v1/runs/{run_id}/approval", headers=self._headers, json=body
            )
            resp.raise_for_status()
            return resp.json()

    def stop(self, run_id: str) -> None:
        try:
            with httpx.Client(timeout=15.0) as client:
                client.post(f"{self.base_url}/v1/runs/{run_id}/stop", headers=self._headers)
        except httpx.HTTPError as exc:
            _LOGGER.debug("stop(%s) failed: %s", run_id, exc)


def observe_run_stream(
    events: Iterator[dict[str, Any]],
    *,
    approval_responder=None,
) -> RunObservation:
    """Fold a ``/v1/runs`` event stream into a :class:`RunObservation`.

    ``approval_responder`` (``fn(event) -> None``) is called on each ``approval.request``
    so the caller can answer it per the task's ``safety_expectation`` while the stream is
    still open — a safety task asserts the agent *asked* rather than acted.
    """
    obs = RunObservation()
    text_parts: list[str] = []
    for event in events:
        obs.raw_events.append(event)
        kind = event.get("event")
        if kind == "message.delta":
            text_parts.append(event.get("delta") or "")
        elif kind == "tool.started":
            obs.tool_calls.append(
                ToolCall(name=event.get("tool") or "", preview=event.get("preview") or "")
            )
        elif kind == "tool.completed":
            name = event.get("tool") or ""
            # Attach duration/error to the most recent matching started call.
            for call in reversed(obs.tool_calls):
                if call.name == name and call.duration == 0.0:
                    call.duration = float(event.get("duration") or 0.0)
                    call.error = bool(event.get("error"))
                    break
        elif kind == "approval.request":
            obs.approvals.append(event)
            if approval_responder is not None:
                approval_responder(event)
        elif kind == "approval.responded":
            obs.approval_responses.append(event)
        elif kind == "run.completed":
            if event.get("output"):
                obs.output = str(event["output"])
            _apply_usage(obs, event.get("usage"))
        elif kind == "run.failed":
            obs.error = str(event.get("error") or "run failed")
        elif kind == "run.cancelled":
            obs.error = obs.error or "run cancelled"
    if not obs.output and text_parts:
        obs.output = "".join(text_parts)
    if obs.total_tokens == 0 and obs.token_source == "reported":
        # The model reported no usage — approximate from the visible text so a report row
        # still carries a number, flagged so it is never read as a measurement.
        obs.completion_tokens = _estimate_tokens(obs.output)
        obs.total_tokens = obs.completion_tokens
        obs.token_source = "estimated"
    return obs


def _apply_usage(obs: RunObservation, usage: Any) -> None:
    if not isinstance(usage, dict):
        return
    # v0.21.0 reports input_tokens/output_tokens; accept the OpenAI prompt/completion
    # names too so a future build that switches naming keeps reporting real usage.
    prompt = usage.get("input_tokens", usage.get("prompt_tokens", 0)) or 0
    completion = usage.get("output_tokens", usage.get("completion_tokens", 0)) or 0
    total = usage.get("total_tokens", prompt + completion) or 0
    obs.prompt_tokens += int(prompt)
    obs.completion_tokens += int(completion)
    obs.total_tokens += int(total)
    obs.token_source = "reported"


class GatewayClient:
    """The adapter ``/ws`` TUI gateway (slash commands: goals, HUD, plugin commands).

    A thin JSON-RPC-over-WebSocket client matching ``scripts/smoke.sh``: connect with
    ``?key=``, ``session.create``, ``command.dispatch``, ``prompt.submit``, then read
    ``event`` frames. Used as a context manager so the socket is always closed.
    """

    def __init__(self, base_url: str, key: str) -> None:
        self._url = (
            base_url.replace("http://", "ws://").replace("https://", "wss://").rstrip("/")
            + f"/ws?key={key}"
        )
        self._ws: ClientConnection | None = None
        self._rid = 0

    def __enter__(self) -> GatewayClient:
        self._ws = ws_connect(self._url, max_size=None)
        self._ws.recv()  # server hello
        return self

    def __exit__(self, *exc: object) -> None:
        if self._ws is not None:
            self._ws.close()
            self._ws = None

    def call(self, method: str, params: dict[str, Any], *, timeout: float = 30.0) -> dict[str, Any]:
        assert self._ws is not None, "GatewayClient used outside its context"
        self._rid += 1
        rid = self._rid
        self._ws.send(json.dumps({"jsonrpc": "2.0", "id": rid, "method": method, "params": params}))
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            raw = self._ws.recv(timeout=max(1.0, deadline - time.monotonic()))
            msg = json.loads(raw)
            if msg.get("id") == rid:
                return msg
        raise TimeoutError(f"gateway call {method} timed out")

    def send_only(self, method: str, params: dict[str, Any]) -> None:
        """Send a request and do not wait for its reply.

        :meth:`call` reads frames until it sees its own id, discarding every ``event``
        that arrives first. That is fine before the event loop starts and fatal inside
        it: answering an approval with :meth:`call` would swallow the tool events the
        run is about to emit. The reply frame is skipped by :meth:`read_events`, which
        already ignores anything that is not an ``event``.
        """
        assert self._ws is not None, "GatewayClient used outside its context"
        self._rid += 1
        self._ws.send(json.dumps(
            {"jsonrpc": "2.0", "id": self._rid, "method": method, "params": params}))

    def create_session(self) -> str:
        return self.call("session.create", {})["result"]["session_id"]

    def read_events(self, session_id: str, timeout_s: float):
        """Yield ``event`` payload dicts for ``session_id`` until ``timeout_s`` elapses.

        Each yielded item is ``{"type": ..., "payload": ..., "session_id": ...}`` — the
        caller decides when it has seen enough (a terminal ``status.update`` etc.).
        """
        assert self._ws is not None, "GatewayClient used outside its context"
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            try:
                raw = self._ws.recv(timeout=max(1.0, deadline - time.monotonic()))
            except TimeoutError:
                return
            msg = json.loads(raw)
            if msg.get("method") != "event":
                continue
            params = msg.get("params", {})
            if params.get("session_id") in (None, session_id):
                yield params


class AudioClient:
    """The adapter voice endpoints (``/api/audio/speak`` Piper, ``/api/audio/transcribe``
    Whisper). ``speak`` returns a ``data:`` URL; ``transcribe`` returns the transcript."""

    def __init__(self, base_url: str, key: str) -> None:
        self.base_url = base_url.rstrip("/")
        self._headers = {"Content-Type": "application/json"}
        if key:
            self._headers["Authorization"] = f"Bearer {key}"

    def speak(self, text: str, timeout: float = 120.0) -> tuple[str, str]:
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(
                f"{self.base_url}/api/audio/speak", headers=self._headers, json={"text": text}
            )
            resp.raise_for_status()
            data = resp.json()
        return data.get("data_url") or "", data.get("mime_type") or ""

    def transcribe(self, data_url: str, mime_type: str, timeout: float = 120.0) -> str:
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(
                f"{self.base_url}/api/audio/transcribe",
                headers=self._headers,
                json={"data_url": data_url, "mime_type": mime_type},
            )
            resp.raise_for_status()
            return resp.json().get("transcript") or ""
