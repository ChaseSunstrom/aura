"""The bubblewrap command line, and the two ways JARVIS runs something inside it.

:func:`bwrap_argv` is a pure function so ``tests/test_bwrap.py`` can pin every fence
(``--unshare-net``, ``--clearenv``, no bind of the Hermes home) the way
``aura-core/tests/test_code_sandbox.py`` pins ``container_argv``: with no bwrap, no
Docker and no Hermes on the machine. :func:`run_sandboxed` is the one-shot form other
plugins use (aura-selfext runs its checkers through it); :func:`environment_class`
builds the Hermes terminal environment, lazily, because its base class lives in Hermes.

What is bound, and why only that: ``/usr`` (with the merged-usr ``/bin`` ``/lib``
``/lib64`` ``/sbin``) and ``/opt/hermes`` read-only so ``bash``, ``python3`` and the
checkers exist; ``/etc/alternatives`` and ``/etc/python3*`` read-only because Debian's
``awk``, ``pager`` and ``sitecustomize`` resolve through them; the container's ``/proc``
read-only (a fresh ``--proc`` is refused inside Docker, whose masked ``/proc`` paths
make procfs "not fully visible" to a new mount; measured 2026-09-18, and measured with
it that a same-uid read of the gateway's ``/proc/<pid>/environ`` or ``root`` from the
sandbox's user namespace is ``Permission denied``); a fresh ``/dev``; and one writable
directory, the task's scratch, bound at its own path so a file the model writes there
has the same name on both sides of the fence. ``/tmp`` is the scratch's ``tmp/``
rather than a tmpfs: ``execute_code`` on a non-local backend talks to the gateway
through files under ``env.get_temp_dir()`` across separate ``execute()`` calls, and a
tmpfs that vanished with each command broke every tool call made from Python.

Not bound, on purpose: ``/etc`` (passwd, hosts, the resolver — nothing to resolve),
``/opt/data`` (config.yaml, the memories, the audit log, the sessions -- the notes are the
one exception, read-only, with the model's own ``notes/journal`` writable over them) and
``/opt/jarvis`` (the seed). ``--clearenv`` is what keeps the gateway's environment —
every API key and token the container was started with — out of the model's shell;
the sandbox gets ``PATH``, ``HOME``, ``LANG``, ``TERM`` and whatever the caller names.
"""
from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Sequence

#: The four the sandbox always has. PATH puts the Hermes venv first because the
#: selfext gate runs ``python3 -m ruff`` and expects the venv's, not Debian's.
BASE_ENV: dict[str, str] = {
    "PATH": "/opt/hermes/.venv/bin:/usr/local/bin:/usr/bin:/bin",
    "LANG": "C.UTF-8",
    "TERM": "dumb",
}

#: Read-only, required: the toolchain. ``/usr`` alone would do on a merged-usr Debian,
#: but the symlinks ``/bin -> usr/bin`` are not there in a fresh root, so they are bound
#: by their old names too (bwrap follows the symlink on the host side).
RO_BINDS: tuple[str, ...] = ("/usr", "/bin", "/lib")
#: Read-only, if present: the rest of the toolchain and the three ``/etc`` corners
#: Debian's tools resolve through. ``-try`` so an image without one still starts.
RO_BINDS_TRY: tuple[str, ...] = (
    "/lib64", "/sbin", "/opt/hermes", "/etc/alternatives", "/etc/python3", "/etc/python3.13",
)


class BwrapMissing(OSError):
    """bubblewrap is not installed where the gateway can run it.

    Raised instead of running the command anywhere else: a sandbox backend that fell
    back to the gateway process when its tool was missing would be the ``local``
    backend with a reassuring name.
    """


def bwrap_path() -> str | None:
    return shutil.which("bwrap")


def scratch_root() -> Path:
    """Where the per-task scratch directories live: ``terminal.sandbox_dir``.

    Hermes' own ``get_sandbox_dir`` answers when Hermes is importable (it reads the
    bridged ``TERMINAL_SANDBOX_DIR``, then defaults under the home); off the runtime
    — the pure tests, the drill's ``--help`` — the home's ``sandboxes/`` stands in.
    The key is Hermes' plumbing, not a JARVIS setting, so it is not read here by name.
    """
    try:
        from tools.environments.base import get_sandbox_dir
    except ImportError:
        return Path(os.environ.get("HERMES_HOME", "~/.hermes")).expanduser() / "sandboxes"
    return get_sandbox_dir()


def make_scratch(name: str) -> Path:
    """Create ``<sandbox_dir>/<name>`` and its ``tmp/``, 0700, and return it."""
    scratch = scratch_root() / name
    (scratch / "tmp").mkdir(parents=True, exist_ok=True)
    scratch.chmod(0o700)
    return scratch


def bwrap_argv(
    cwd: str | os.PathLike[str],
    scratch: str | os.PathLike[str],
    extra_binds: Sequence[str | os.PathLike[str]] = (),
    *,
    readonly_binds: Sequence[str | os.PathLike[str]] = (),
    env: dict[str, str] | None = None,
    command: Sequence[str] = ("/bin/bash", "-c", "true"),
    bwrap: str = "bwrap",
) -> list[str]:
    """The full bubblewrap command line for one command.

    ``scratch`` is the only host path bound writable unless the caller names more in
    ``extra_binds`` (aura-selfext binds its checkout); ``readonly_binds`` are mounted
    read-only before them, so a writable bind may sit inside one. ``cwd`` must be inside one of
    them, or the shell would start in a directory that does not exist behind the fence;
    anything else is replaced by the scratch. ``env`` is added to :data:`BASE_ENV` and
    ``HOME`` is the scratch — the caller cannot forward the gateway's environment
    wholesale, only name values.
    """
    scratch = str(scratch)
    writable = [scratch, *(str(p) for p in extra_binds)]
    cwd = str(cwd)
    if not any(cwd == root or cwd.startswith(root.rstrip("/") + "/") for root in writable):
        cwd = scratch
    argv = [
        bwrap,
        "--unshare-all", "--unshare-net",  # --unshare-all already means it; said twice so `ps` shows it
        "--die-with-parent", "--new-session", "--clearenv",
    ]
    for key, value in {**BASE_ENV, "HOME": scratch, **(env or {})}.items():
        argv += ["--setenv", key, value]
    for path in RO_BINDS:
        argv += ["--ro-bind", path, path]
    for path in RO_BINDS_TRY:
        argv += ["--ro-bind-try", path, path]
    argv += ["--ro-bind", "/proc", "/proc", "--dev", "/dev"]
    argv += ["--bind", f"{scratch}/tmp", "/tmp"]
    for shown in readonly_binds:
        argv += ["--ro-bind", str(shown), str(shown)]
    for path in writable:
        argv += ["--bind", path, path]
    argv += ["--chdir", cwd, "--", *command]
    return argv


def run_sandboxed(
    argv: Sequence[str],
    *,
    cwd: str | os.PathLike[str],
    extra_binds: Sequence[str | os.PathLike[str]] = (),
    timeout: float | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run ``argv`` once inside the sandbox with a throwaway scratch; capture its output.

    For callers that are not the terminal tool. The scratch is created for the call
    and removed after it, whatever the exit; ``subprocess.TimeoutExpired`` propagates
    so the caller can report the timeout in its own words. Raises :class:`BwrapMissing`
    rather than running ``argv`` in the gateway.
    """
    bwrap = bwrap_path()
    if bwrap is None:
        raise BwrapMissing("bwrap is not installed: refusing to run the command unsandboxed")
    scratch = make_scratch(f"run-{os.getpid()}-{uuid.uuid4().hex[:8]}")
    try:
        full = bwrap_argv(cwd, scratch, extra_binds, env=env, command=list(argv), bwrap=bwrap)
        return subprocess.run(full, capture_output=True, text=True, timeout=timeout)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def describe(argv: Sequence[str]) -> str:
    """The command line as one shell-quoted string, for logs and the drill."""
    return " ".join(shlex.quote(a) for a in argv)


def environment_class() -> type:
    """Build the Hermes terminal environment lazily (its base class lives in Hermes).

    One class per call is deliberate: the module stays importable on a checkout with
    no Hermes, and the tests that need the real base class skip there.
    """
    from tools.environments.base import BaseEnvironment

    # v2026.9.24 moved the stdin writer to base_output; imported from base, every
    # command with stdin -- every file write -- failed with "cannot import name
    # '_pipe_stdin'" and the deploy smoke's goals stopped (2026-10-06).
    try:
        from tools.environments.base_output import _pipe_stdin
    except ImportError:  # Hermes before v2026.9.24
        from tools.environments.base import _pipe_stdin
    from tools.environments.path_utils import sanitize_task_id_for_path

    class BwrapEnvironment(BaseEnvironment):
        """One task's shell: every command is a fresh ``bwrap`` around ``bash``.

        The session snapshot and cwd file Hermes keeps under ``get_temp_dir()`` live in
        ``<scratch>/tmp``, which is the sandbox's ``/tmp``, so they survive between
        commands and die with the scratch. ``is_local`` stays False: what the gateway
        knows about its own host (its home, its platform) describes nothing in here.
        """

        is_local = False

        def __init__(self, *, cwd: str, timeout: int, task_id: str = "default"):
            # The work directory Hermes hands over (`terminal.cwd`, /opt/data/work) is bound
            # writable at its own path and is where the shell starts. It is the one part
            # of the home that is shared on purpose: the goal gates, the bench's checkers
            # and the file-delivery skill all read the agent's files there from the
            # gateway side, and Hermes' own command prelude does `cd` into it — the first
            # cut bound only the scratch and every command failed on that `cd` (pipeline
            # 350, 2026-09-18). Config, memories, state and the audit log stay out.
            found = bwrap_path()
            if found is None:
                raise BwrapMissing(
                    "bwrap is not installed in this image: the aura-bwrap backend refuses "
                    "to create an environment rather than run commands in the gateway"
                )
            self._bwrap: str = found
            self.work = os.path.abspath(cwd)
            os.makedirs(self.work, exist_ok=True)  # after the refusal: no side effect without bwrap
            # The files area is the person's: what the browser downloads and what the
            # file-delivery skill leaves for them. It holds no secret and no state, and a
            # shell that cannot list it sent the model hunting the filesystem for a CSV
            # the browser had already saved there (browser-download-003, 2026-09-18).
            files = os.path.join(os.environ.get("HERMES_HOME", "/opt/data"), "files")
            self.shared = [self.work] + ([files] if os.path.isdir(files) and files != self.work else [])
            # The owner's notes, read-only: SOUL's "search the notes before saying there is
            # no record" and every read of them ran in here and found "Path not found"
            # (voice-question-004, 2026-10-06). Reads were already free through the notes
            # tools; changing a note stays on those, gated. The journal under it is the
            # model's own diary (the nightly cron writes it) and is bound writable over it.
            notes = os.path.join(os.environ.get("HERMES_HOME", "/opt/data"), "notes")
            self.readonly: list[str] = []
            if os.path.isdir(notes):
                journal = os.path.join(notes, "journal")
                os.makedirs(journal, exist_ok=True)
                self.readonly = [notes]
                self.shared.append(journal)
            self.scratch = make_scratch(sanitize_task_id_for_path(task_id or "default"))
            super().__init__(cwd=self.work, timeout=timeout)
            self.init_session()

        def get_temp_dir(self) -> str:
            return "/tmp"  # the sandbox's view of <scratch>/tmp

        def _run_bash(self, cmd_string: str, *, login: bool = False, timeout: int = 120,
                      stdin_data: str | None = None):
            bash = ["/bin/bash", "-l", "-c", cmd_string] if login else ["/bin/bash", "-c", cmd_string]
            argv = bwrap_argv(self.cwd, self.scratch, extra_binds=self.shared, readonly_binds=self.readonly,
                              command=bash, bwrap=self._bwrap)
            proc = subprocess.Popen(
                argv, text=True, encoding="utf-8", errors="replace",
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                stdin=subprocess.PIPE if stdin_data is not None else subprocess.DEVNULL,
                start_new_session=True,
            )
            if stdin_data is not None:
                _pipe_stdin(proc, stdin_data)
            return proc

        def _kill_process(self, proc) -> None:
            # bwrap is pid 1's parent in the new pid namespace and the child carries
            # --die-with-parent, so killing bwrap takes the whole namespace with it —
            # a process-group sweep like LocalEnvironment's would find nothing to do.
            try:
                proc.kill()
            except (ProcessLookupError, PermissionError, OSError):
                pass

        def cleanup(self) -> None:
            """Delete the scratch: this is the reset between tasks.

            Hermes calls it when the task's environment is retired
            (``terminal.lifetime_seconds`` idle, or the session ending under
            ``container_persistent: false``). Not covered: a file the model copied
            out through a tool that reads the gateway's filesystem, such as an
            artifact — that is the tool's copy, and it stays.
            """
            shutil.rmtree(self.scratch, ignore_errors=True)

    return BwrapEnvironment
