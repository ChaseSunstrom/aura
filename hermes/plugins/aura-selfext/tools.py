"""The five self-extension handlers, and the lint/test run that stands between them.

``selfext_plan`` writes nothing — it turns a capability request into the spec the
rest of the loop consumes, so the *shape* of what will be built is reviewable
before a file exists. ``selfext_scaffold`` writes. ``selfext_verify`` runs ruff and
pytest over the new plugin **inside the checkout**, which is the only evidence the
merge request can carry that the thing loads. ``selfext_open_mr`` is the outward
act and is the only one behind the approval gate. ``selfext_status`` answers "what
is on the branch right now" without changing anything.

Every handler returns a JSON string and never raises: a plugin handler that raises
takes the tool call down with an opaque traceback, and this plugin's failures are
exactly the ones the operator most needs stated plainly.
"""
from __future__ import annotations

import json
import posixpath
import re
import subprocess
from typing import Any, Callable

from aura_schema import from_env

from . import git, proposals, scaffold

#: The trailers this repository's commits carry. The loop writes them so a commit it
#: made is attributable in ``git log`` without reading the branch name.
TRAILERS = (
    "Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>\n"
    "Claude-Session: https://claude.ai/code/session_01GRfzG31HaDPMKvgpkK5UbL"
)


def _safe(fn: Callable[..., dict[str, Any]], *, session: bool = False) -> Callable[..., str]:
    """Never raise, and hand *fn* the session only when it asked (``session=True``):
    a proposal's panel goes to the session that made it; nothing else here has one."""
    def handler(params: dict, **kwargs) -> str:
        extra = {"session_id": kwargs.get("session_id")} if session else {}
        try:
            return json.dumps(fn(params or {}, **extra))
        except (git.GitError, scaffold.ScaffoldError) as exc:
            return json.dumps({"ok": False, "error": str(exc)})
        except (OSError, ValueError, TypeError, KeyError) as exc:
            return json.dumps({"ok": False, "error": f"{exc.__class__.__name__}: {exc}"})
    return handler


def _tools_arg(params: dict) -> list[dict[str, Any]]:
    tools = params.get("tools")
    if isinstance(tools, str):
        # The model sometimes sends the array as a JSON string. Accepting it is
        # cheaper than a failed turn, and a genuinely malformed string still errors.
        tools = json.loads(tools)
    if not isinstance(tools, list):
        raise scaffold.ScaffoldError("tools must be a list of {name, description, parameters}")
    return [t for t in tools if isinstance(t, dict)]


#: What the plan says to do next, per interface class. An MCP server is the one
#: that does not scaffold a plugin at all: a server that already exists should be
#: configured, and generating a second implementation of it is the duplication
#: this harness is supposed to prevent.
_NEXT_BY_INTERFACE: dict[str, list[str]] = {
    "mcp": ["hermes mcp add", "selfext_verify"],
    "webhook": ["create the n8n workflow", "selfext_verify"],
}


def plan(params: dict) -> dict[str, Any]:
    """The spec the rest of the loop consumes, and where each piece will land.

    When `url` or `command` is given, discovery runs first and its answer is part
    of the plan: what the service is, how sure that is, and what should be
    generated for it. A `guess` is carried through rather than smoothed over —
    the owner reading the plan should see that the shape was inferred from a
    content type and not from a spec.
    """
    name = str(params.get("name") or "").strip()
    tools = _tools_arg(params)
    scaffold._validate(name, str(params.get("toolset") or name.replace("-", "_")), tools)

    found: dict[str, Any] | None = None
    url = str(params.get("url") or "").strip()
    command = str(params.get("command") or "").strip()
    if url or command:
        from . import discover

        found = discover.discover(url=url, command=command)

    interface = (found or {}).get("interface", "")
    out = {
        "ok": True,
        "capability": name,
        "why": str(params.get("why") or ""),
        "toolset": str(params.get("toolset") or name.replace("-", "_")),
        "tools": [{"name": t["name"], "description": t["description"]} for t in tools],
        "needs_approval": bool(params.get("approval")),
        "writes": list(scaffold.allowed_paths(name)),
        "branch": from_env().selfext_branch_prefix + name,
        "target": from_env().selfext_target_branch,
        "next": _NEXT_BY_INTERFACE.get(
            interface, ["selfext_scaffold", "selfext_verify", "selfext_open_mr"]
        ),
    }
    if found:
        from . import discover

        out["interface"] = found["interface"]
        out["confidence"] = found["confidence"]
        out["generate"] = found["generate"]
        out["discovery"] = discover.summary(found)
    return out


def _scaffold(params: dict) -> dict[str, Any]:
    name = str(params.get("name") or "").strip()
    return scaffold.scaffold(
        name,
        str(params.get("description") or "").strip() or f"{name} capability",
        str(params.get("toolset") or name.replace("-", "_")),
        _tools_arg(params),
        approval=bool(params.get("approval")),
        bench_prompt=str(params.get("bench_prompt") or ""),
    )


def _run_sandboxed(argv: list[str], *, cwd: str, timeout: float) -> subprocess.CompletedProcess[str]:
    """The checkers run under bubblewrap (aura-bwrap) with the checkout bound writable.

    pytest executes the plugin's own test file, and any conftest.py beside it, so the
    gate is where model-written code first runs: not in the gateway process, whose
    environment holds every credential. The helper refuses (``BwrapMissing``, an
    OSError) rather than running the command unsandboxed. git in the checkout runs
    sandboxed too (:func:`git.run`); only the push, which needs the network, does not.

    """
    return git.bwrap().run_sandboxed(argv, cwd=cwd, extra_binds=[cwd], timeout=timeout, env={"LC_ALL": "C"})


def _run_in_repo(argv: list[str]) -> dict[str, Any]:
    """Run a checker in the checkout and report its exit code and tail.

    Only the tail: a full pytest log is thousands of tokens and the model needs the
    verdict plus the failure, not the passing lines.
    """
    cfg = from_env()
    try:
        proc = _run_sandboxed(argv, cwd=str(git.repo_path()), timeout=cfg.selfext_timeout)
    except subprocess.TimeoutExpired:
        return {"command": " ".join(argv), "ok": False,
                "output": f"timed out after {cfg.selfext_timeout:.0f}s"}
    except OSError as exc:
        return {"command": " ".join(argv), "ok": False, "output": f"{exc.__class__.__name__}: {exc}"}
    tail = (proc.stdout + proc.stderr).strip()[-2000:]
    return {"command": " ".join(argv), "ok": proc.returncode == 0, "exit": proc.returncode,
            "output": tail}


def _gate_commands(target: str) -> list[list[str]]:
    """What CI runs for a plugin, in the order a person would want to read failures.

    Merge request !2 — the loop's first real one — was rejected by ``py-mypy`` for two
    type errors in code this loop had written and then pronounced ready, because
    ``verify`` ran only ruff and pytest. A self-check weaker than the gate its own
    merge request must pass will keep producing merge requests that fail for reasons it
    could have caught, so this runs the same four checks the pipeline does:

    * ``ruff``            → the ``test:lint`` job's ``ruff check .``
    * per-plugin ``mypy`` → the ``py-mypy`` job's ``scripts/mypy-plugins.sh``, which
      aliases every hyphenated plugin dir under a temp root so relative and
      ``_shared`` imports resolve the way the runtime loads them. A bare
      ``mypy hermes/plugins`` FATALs on the first hyphen and prints nothing.
    * ``vulture``         → the ``py-dead`` job, with the in-repo allow-list
    * the corpus counts   → ``bench/tests/test_tasks.py``, which pins the per-category
      task count by equality, so adding a benchmark task without moving the number is
      a red pipeline

    ``docs-sync`` is reported by :func:`_docs_name_the_capability` rather than run
    here: regenerating writes files outside this loop's allowed paths.
    """
    return [
        ["python3", "-m", "ruff", "check", target],
        ["python3", "-m", "pytest", target, "-q"],
        ["bash", "scripts/mypy-plugins.sh"],
        ["python3", "-m", "vulture", "--min-confidence", "80", target, "vulture-allowlist.py"],
        ["python3", "-m", "pytest", "bench/tests/test_tasks.py", "-q"],
    ]


#: The generated pages that gain a row when a plugin ships. `docs-sync` regenerates
#: all of them and fails on any diff; these are the ones a new capability moves.
DOCS_PAGES: tuple[str, ...] = (
    "CAPABILITIES.md", "docs/reference/plugins.md", "docs/reference/tools.md",
)


def _docs_name_the_capability(name: str) -> dict[str, Any]:
    """Which generated pages do not mention *name* yet. Reads only; writes nothing.

    Not ``make docs`` and a diff, which is what the ``docs-sync`` job does: that
    regenerates files outside this capability's allowed paths, and a loop that writes
    them would either be refused by :func:`git.stray_changes` or have to restore them
    afterwards in a checkout it shares with other people. Searching for the
    capability's own name is weaker than regenerating — it would not notice a page
    stale for some unrelated reason — and it catches the case that actually arises,
    which is a plugin that shipped without its references being rebuilt.
    """
    root = git.repo_path()
    missing = [page for page in DOCS_PAGES
               if (root / page).is_file() and name not in (root / page).read_text(
                   encoding="utf-8", errors="replace")]
    return {
        "command": f"grep {name} in {', '.join(DOCS_PAGES)}",
        "ok": not missing,
        "output": ("every generated reference names it" if not missing
                   else f"not named in {missing}"),
        "advisory": ("" if not missing else
                     "run `make docs` and include the regenerated files in the same commit"),
    }


def verify(params: dict) -> dict[str, Any]:
    """Run over the generated plugin what CI will run over it."""
    name = str(params.get("name") or "").strip()
    if not scaffold.NAME_RE.match(name):
        raise scaffold.ScaffoldError(f"plugin name {name!r} is not a plugin directory name")
    target = f"{scaffold.PLUGINS_SUBDIR}/{name}"
    if not (git.repo_path() / target).is_dir():
        raise scaffold.ScaffoldError(f"{target} does not exist; run selfext_scaffold first")
    checks = [_run_in_repo(argv) for argv in _gate_commands(target)]
    checks.append(_docs_name_the_capability(name))
    failed = [c["command"] for c in checks if not c["ok"]]
    return {"ok": not failed, "plugin": name, "checks": checks, "failed": failed}


#: Where a merge request may carry files beyond its own capability (the owner, 2026-10-06:
#: "yes" to merge requests for any change). A self-extension branch's MR pipeline runs that
#: branch's own CI file and builds images on the production host before anyone reviews it, so
#: "any change" stops at files a person reads: CI files, deploy/, scripts/, build files and the
#: plugins that govern JARVIS itself would run, or loosen the gate, unreviewed.
REVIEWABLE_PREFIXES = ("docs/", "hermes/skills/", "hermes/home/", "bench/tasks/", "hermes/plugins/")
#: Plugins that are the gate, the sandbox, the matrix and this loop: never reachable from here.
GOVERNING_PLUGINS = ("_shared", "aura-guard", "aura-bwrap", "aura-selfext")
#: A build or CI input wherever it sits, even inside an allowed directory.
_BUILT = re.compile(r"(^|/)(Dockerfile[^/]*|Makefile|[^/]*\.gradle(\.kts)?|conftest\.py|\.gitlab-ci\.yml"
                    r"|package\.json|pyproject\.toml|setup\.py|\.env[^/]*)$")


#: The most extra files one merge request may carry (each is named on the approval card).
MAX_EXTRA_PATHS = 12


def reviewable_paths(paths: Any) -> tuple[str, ...]:
    """The extra paths, normalised, or ScaffoldError naming the first that may not ride along."""
    if not paths:
        return ()
    if not isinstance(paths, list) or not all(isinstance(p, str) for p in paths):
        raise scaffold.ScaffoldError("paths must be a list of repository-relative file paths")
    if len(paths) > MAX_EXTRA_PATHS:
        # The approval card names every path; a longer list is one nobody reads to the end.
        raise scaffold.ScaffoldError(f"a merge request carries at most {MAX_EXTRA_PATHS} extra paths")
    out: list[str] = []
    for raw in paths:
        rel = posixpath.normpath(raw.strip())
        top = rel.split("/", 2)
        governing = rel.startswith("hermes/plugins/") and len(top) > 2 and top[2].split("/")[0] in GOVERNING_PLUGINS
        readable = rel.endswith(".md") and "/" not in rel or rel.startswith(REVIEWABLE_PREFIXES)
        if raw.strip().startswith("/") or ".." in raw.split("/") or governing or not readable or _BUILT.search(rel):
            raise scaffold.ScaffoldError(
                f"a merge request may not carry {raw!r}: only files a person reads (markdown at the "
                f"top level, docs/, hermes/skills/, hermes/home/, bench/tasks/ and ordinary plugins) "
                f"may, never CI, deploy, scripts, build files or the plugins that govern JARVIS")
        out.append(rel)
    return tuple(out)


def open_mr(params: dict) -> dict[str, Any]:
    """Branch, commit the capability's own files, push, and let GitLab open the request."""
    cfg = from_env()
    name = str(params.get("name") or "").strip()
    if not scaffold.NAME_RE.match(name):
        raise scaffold.ScaffoldError(f"plugin name {name!r} is not a plugin directory name")
    title = str(params.get("title") or "").strip() or f"Add the {name} capability"
    body = str(params.get("description") or "").strip()
    if not body:
        raise scaffold.ScaffoldError(
            "a merge request needs a description: what was missing, what this adds, and what "
            "it does not cover. A reviewer cannot judge a diff with no statement of intent.")
    # Before allowed_paths is consulted, not after: `allowed_paths("aura-guard")`
    # includes hermes/plugins/aura-guard, so a capability that took the governor's
    # name would get write access to the plugin registering the approval hook, and no
    # manifest check would see it because that edit is not in a manifest. open_mr
    # asks as well as scaffold, because open_mr does not require scaffold to have run.
    scaffold.assert_name_is_free(name)
    branch = cfg.selfext_branch_prefix + name
    extra = reviewable_paths(params.get("paths"))
    allowed = scaffold.allowed_paths(name) + extra
    # `allowed` now includes the deploy manifests so the loop can name its own
    # capability there; the extra arguments narrow that widening back to exactly
    # that one edit (git.assert_manifest_edit_is_only).
    git.assert_pushable(branch, allowed, name, scaffold.MANIFESTS)
    started_on = git.current_branch()
    git.create_branch(branch)
    sha = git.commit(allowed, title, f"{body}\n\n{TRAILERS}")
    result = git.open_merge_request(branch, title, body)
    return {**result, "commit": sha, "was_on": started_on, "target": cfg.selfext_target_branch,
            "merged": False,
            "note": "Nothing is merged. The merge request waits for CI and for the owner."}


def integrate_discover(params: dict) -> dict[str, Any]:
    """How a service can be reached (`discover.py`). Read-only, decides nothing."""
    from . import discover

    url = str(params.get("url") or "").strip()
    command = str(params.get("command") or "").strip()
    if not url and not command:
        return {"ok": False, "error": "integrate_discover needs a url or a command"}
    found = discover.discover(url=url, command=command)
    return {"ok": True, **found, "summary": discover.summary(found)}


def status(params: dict) -> dict[str, Any]:
    """Branch, head, and whether anything outside a capability's files is uncommitted."""
    del params
    cfg = from_env()
    branch = git.current_branch()
    head = git.run("log", "-1", "--pretty=%h %s").strip()
    dirty = [path for _s, path in git.porcelain()]
    return {"ok": True, "repo": str(git.repo_path()), "branch": branch, "head": head,
            "target": cfg.selfext_target_branch, "branch_prefix": cfg.selfext_branch_prefix,
            "remote": cfg.selfext_remote, "uncommitted": dirty[:40],
            "uncommitted_count": len(dirty)}


handle_selfext_plan = _safe(plan)
handle_selfext_scaffold = _safe(_scaffold)
handle_selfext_verify = _safe(verify)
handle_selfext_open_mr = _safe(open_mr)
handle_selfext_status = _safe(status)
handle_integrate_discover = _safe(integrate_discover)
handle_skill_propose = _safe(proposals.propose, session=True)
handle_skill_proposals = _safe(proposals.review)
