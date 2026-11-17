"""The fences in the bwrap command line, and what the backend refuses to do.

``bwrap_argv`` is pure, so every fence is pinned here the way
``aura-core/tests/test_code_sandbox.py`` pins ``container_argv``: no bwrap, no Docker,
no Hermes needed. The negatives carry the weight — a test that only looked for the flags
we pass would still pass if somebody added ``--share-net`` or bound ``/opt/data``.

The provider and the environment need Hermes' base classes; those tests skip on a
checkout with no Hermes beside it (CI's python image) and run on the dev box. Not
covered here: that bwrap really contains anything. That is
``scripts/verify/sandbox-drill.py`` against the deployed container, whose positive
control decides whether a run counts.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from _plugin_loader import load_plugin  # noqa: E402

plugin = load_plugin("aura-bwrap")
sandbox = plugin.sandbox

SCRATCH = "/opt/data/sandboxes/task-a"


def _argv(**kw) -> list[str]:
    return sandbox.bwrap_argv(kw.pop("cwd", SCRATCH), SCRATCH, **kw)


def _binds(argv: list[str]) -> list[tuple[str, str, str]]:
    """Every (flag, source, target) mount in the command line."""
    out = []
    for i, a in enumerate(argv):
        if a in ("--bind", "--ro-bind", "--ro-bind-try", "--dev-bind"):
            out.append((a, argv[i + 1], argv[i + 2]))
    return out


# --------------------------------------------------------------------------- #
# The command line
# --------------------------------------------------------------------------- #
def test_the_namespaces_and_the_environment_are_cut() -> None:
    argv = _argv()
    for flag in ("--unshare-all", "--unshare-net", "--clearenv", "--die-with-parent", "--new-session"):
        assert flag in argv, f"{flag} is missing"
    for flag in ("--share-net", "--share-user", "--share-pid", "--share-ipc", "--share-uts"):
        assert flag not in argv, f"the argv carries {flag}"
    assert "--proc" not in argv, "a fresh procfs is refused inside Docker; the container's is bound read-only"
    assert argv[0] == "bwrap" and argv[-4:] == ["--", "/bin/bash", "-c", "true"]


def test_the_environment_is_exactly_the_four_names_plus_what_the_caller_names() -> None:
    argv = _argv(env={"LC_ALL": "C"})
    names = [argv[i + 1] for i, a in enumerate(argv) if a == "--setenv"]
    assert sorted(names) == ["HOME", "LANG", "LC_ALL", "PATH", "TERM"]
    assert argv[argv.index("HOME") + 1] == SCRATCH
    assert argv[argv.index("PATH") + 1].startswith("/opt/hermes/.venv/bin:"), "the selfext gate needs the venv's python3"


def test_nothing_of_the_hermes_home_is_bound_but_the_scratch() -> None:
    argv = _argv()
    writable = [(s, t) for f, s, t in _binds(argv) if f == "--bind"]
    assert writable == [(f"{SCRATCH}/tmp", "/tmp"), (SCRATCH, SCRATCH)], writable
    for _, source, _target in _binds(argv):
        if source.startswith("/opt/data"):
            assert source.startswith(SCRATCH), f"{source} of the home is bound"
    assert "/opt/data" not in argv and "/opt/jarvis" not in argv and "/etc" not in argv
    assert ("--ro-bind", "/proc", "/proc") in _binds(argv)
    assert "--dev" in argv and argv[argv.index("--dev") + 1] == "/dev"


def test_the_toolchain_is_read_only() -> None:
    kinds = {t: f for f, _s, t in _binds(_argv())}
    assert kinds["/usr"] == "--ro-bind" and kinds["/bin"] == "--ro-bind"
    assert kinds["/opt/hermes"] == "--ro-bind-try", "the venv is optional on a probe image, read-only on ours"


def test_an_extra_bind_is_writable_and_may_hold_the_cwd() -> None:
    repo = "/opt/data/work/jarvis"
    argv = _argv(cwd=repo, extra_binds=[repo])
    assert ("--bind", repo, repo) in _binds(argv)
    assert argv[argv.index("--chdir") + 1] == repo


def test_a_cwd_outside_every_writable_bind_falls_back_to_the_scratch() -> None:
    """`terminal.cwd` is /opt/data/work, which does not exist behind the fence: a shell
    started there would fail on `cd` before running anything."""
    argv = _argv(cwd="/opt/data/work")
    assert argv[argv.index("--chdir") + 1] == SCRATCH
    argv = _argv(cwd="/opt/data/sandboxes/task-abc")  # a sibling scratch, not a prefix match
    assert argv[argv.index("--chdir") + 1] == SCRATCH


def test_the_command_follows_the_double_dash_untouched() -> None:
    argv = _argv(command=["python3", "-m", "pytest", "hermes/plugins/x", "-q"])
    assert argv[argv.index("--") + 1:] == ["python3", "-m", "pytest", "hermes/plugins/x", "-q"]


# --------------------------------------------------------------------------- #
# The one-shot runner, and the refusal
# --------------------------------------------------------------------------- #
def test_run_sandboxed_refuses_rather_than_running_unsandboxed(monkeypatch) -> None:
    monkeypatch.setattr(sandbox, "bwrap_path", lambda: None)
    calls: list = []
    monkeypatch.setattr(sandbox.subprocess, "run", lambda *a, **k: calls.append(a))
    with pytest.raises(sandbox.BwrapMissing):
        sandbox.run_sandboxed(["true"], cwd="/x")
    assert calls == [], "the command ran somewhere"


def _sandboxes_at(monkeypatch, tmp_path: Path) -> Path:
    """Point both readers of the sandbox root at tmp_path/sandboxes (Hermes' and the fallback)."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("TERMINAL_SANDBOX_DIR", str(tmp_path / "sandboxes"))
    return tmp_path / "sandboxes"


def test_run_sandboxed_uses_a_throwaway_scratch_and_removes_it(monkeypatch, tmp_path) -> None:
    sandboxes = _sandboxes_at(monkeypatch, tmp_path)
    monkeypatch.setattr(sandbox, "bwrap_path", lambda: "/usr/bin/bwrap")
    seen: dict = {}

    def _fake_run(argv, **kwargs):
        seen["argv"], seen["kwargs"] = argv, kwargs
        scratch = argv[argv.index("HOME") + 1]
        seen["scratch_existed"] = Path(scratch).is_dir() and (Path(scratch) / "tmp").is_dir()
        return subprocess.CompletedProcess(argv, 0, "ok", "")

    monkeypatch.setattr(sandbox.subprocess, "run", _fake_run)
    done = sandbox.run_sandboxed(["python3", "-m", "ruff", "check", "."], cwd="/repo",
                                 extra_binds=["/repo"], timeout=7, env={"LC_ALL": "C"})
    assert done.returncode == 0 and seen["scratch_existed"]
    assert seen["argv"][0] == "/usr/bin/bwrap" and seen["kwargs"]["timeout"] == 7
    assert ("--bind", "/repo", "/repo") in _binds(seen["argv"])
    assert seen["argv"][-5:] == ["python3", "-m", "ruff", "check", "."]
    assert list(sandboxes.iterdir()) == [], "the scratch outlived the call"


def test_scratch_root_is_under_the_home_by_default(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("TERMINAL_SANDBOX_DIR", raising=False)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    assert sandbox.scratch_root() == tmp_path / "sandboxes"


# --------------------------------------------------------------------------- #
# The provider and the environment (need Hermes)
# --------------------------------------------------------------------------- #
hermes = pytest.importorskip("agent.terminal_env_provider",
                             reason="the provider ABC ships with Hermes; skipped where none is installed")


class _Ctx:
    def __init__(self) -> None:
        self.providers: list = []

    def register_terminal_environment_provider(self, provider) -> None:
        self.providers.append(provider)


def test_register_hands_hermes_a_provider_with_the_declared_flags() -> None:
    ctx = _Ctx()
    plugin.register(ctx)
    (provider,) = ctx.providers
    assert isinstance(provider, hermes.TerminalEnvironmentProvider)
    assert provider.name == "aura-bwrap"
    assert provider.is_container and not provider.is_remote
    assert provider.skip_container_guards and provider.session_isolated_when_nonpersistent
    assert provider.strip_env_keys == frozenset() and provider.cache_path_base is None


def test_a_ctx_without_the_extension_point_gets_no_registration_and_no_traceback() -> None:
    class _Old:
        pass

    plugin.register(_Old())  # logs, returns


def test_the_provider_refuses_without_bwrap(monkeypatch) -> None:
    monkeypatch.setattr(sandbox, "bwrap_path", lambda: None)
    provider = plugin.provider_class()()
    assert provider.is_available() is False
    assert provider.probe()[0] == "needs_setup"
    with pytest.raises(sandbox.BwrapMissing):
        provider.create_environment(cwd="/opt/data/work", timeout=30, task_id="t1")


def test_the_environment_runs_bash_through_bwrap_and_cleanup_deletes_the_scratch(monkeypatch, tmp_path) -> None:
    sandboxes = _sandboxes_at(monkeypatch, tmp_path)
    # /bin/true stands in for bwrap: init_session's login shell "runs" and captures no
    # snapshot, which is the path Hermes takes when a snapshot fails, and the argv it
    # was handed is what this test reads.
    monkeypatch.setattr(sandbox, "bwrap_path", lambda: "/bin/true")
    work = tmp_path / "work"  # stands in for /opt/data/work, which this host does not have
    files = tmp_path / "files"; files.mkdir()  # the person's files area, shared too
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    env = plugin.provider_class()().create_environment(cwd=str(work), timeout=30,
                                                        task_id="session/abc:1")
    scratch = Path(env.scratch)
    assert scratch.parent == sandboxes and scratch.is_dir() and (scratch / "tmp").is_dir()
    assert "/" not in scratch.name and ":" not in scratch.name, "the task id is sanitised into one path segment"
    # The work directory is made if missing, bound writable at its own path, and is the
    # cwd: Hermes' command prelude does `cd` into it and the gateway reads files there.
    assert work.is_dir() and env.cwd == str(work) and env.get_temp_dir() == "/tmp"
    proc = env._run_bash("echo hi", login=False, stdin_data="in")
    proc.wait(timeout=10)
    argv = proc.args
    assert argv[0] == "/bin/true" and "--unshare-net" in argv and "--clearenv" in argv
    assert ("--bind", str(work), str(work)) in _binds(argv) and argv[argv.index("--chdir") + 1] == str(work)
    assert ("--bind", str(files), str(files)) in _binds(argv), "the files area is shared with the shell"
    assert not any(src == str(tmp_path) for _f, src, _t in _binds(argv)), "the home itself is not"
    assert argv[-3:] == ["/bin/bash", "-c", "echo hi"]
    env.cleanup()
    assert not scratch.exists(), "cleanup is the reset between tasks"
    env.cleanup()  # a second cleanup (Hermes' __del__) finds nothing and says nothing


def test_the_owners_notes_are_readable_and_the_journal_writable_behind_the_fence(monkeypatch, tmp_path) -> None:
    """SOUL tells the model to search the notes before saying there is no record, and the
    nightly journal writes notes/journal -- both with file tools, which run in here. With
    neither bound, both answered "Path not found: /opt/data/notes" (voice-question-004,
    2026-10-06). The notes are read-only: changing one of the owner's notes stays on the
    gated notes tools. The journal is the model's own and is bound writable over them."""
    _sandboxes_at(monkeypatch, tmp_path)
    monkeypatch.setattr(sandbox, "bwrap_path", lambda: "/bin/true")
    notes = tmp_path / "notes"; notes.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    env = plugin.provider_class()().create_environment(cwd=str(tmp_path / "work"), timeout=30, task_id="t")
    proc = env._run_bash("true", login=False)
    proc.wait(timeout=10)
    binds = _binds(proc.args)
    journal = notes / "journal"
    assert journal.is_dir(), "the journal directory is made so the first entry has somewhere to go"
    assert ("--ro-bind", str(notes), str(notes)) in binds
    assert ("--bind", str(journal), str(journal)) in binds
    assert binds.index(("--ro-bind", str(notes), str(notes))) < binds.index(("--bind", str(journal), str(journal))), \
        "the writable journal is mounted over the read-only notes, not under them"
    assert not any(src == str(tmp_path) for _f, src, _t in binds), "the home itself still is not"
    env.cleanup()


def test_a_read_only_bind_is_read_only() -> None:
    argv = _argv(readonly_binds=["/opt/data/notes"])
    assert ("--ro-bind", "/opt/data/notes", "/opt/data/notes") in _binds(argv)
    assert ("--bind", "/opt/data/notes", "/opt/data/notes") not in _binds(argv)
