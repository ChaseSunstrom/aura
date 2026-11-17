"""Every git call the self-extension loop makes, and the rules that bound them.

The loop's whole risk is here: this is the one module in JARVIS that can put code
on a remote. It is written so that the dangerous shapes are impossible rather than
merely discouraged.

* :func:`run` is the only place ``git`` is executed. It never uses a shell, always
  runs with ``-C <repo>``, and always carries a timeout, so a wedged credential
  prompt cannot hang the turn forever (``GIT_TERMINAL_PROMPT=0`` makes git fail
  instead of waiting for a password that no one is there to type).
* :func:`assert_pushable` refuses the four pushes that would be a disaster: onto
  the target branch, from a branch without the configured prefix, with a dirty
  tree outside the capability's own files, and a force push (there is no code path
  that passes ``--force``; the check exists so adding one is a visible act).
* :func:`open_merge_request` uses **GitLab push options** rather than the REST API.
  That is deliberate and not a workaround: the loop then needs only a credential
  that can push, never an API call, and the model's context never holds one. The
  push authenticates with ``GITLAB_TOKEN`` from the gateway's environment, handed to
  a staging repo the model never wrote, by a credential helper scoped to that host
  (:func:`_token_push`). GitLab prints the merge request URL on the push's stderr;
  :func:`parse_mr_url` reads it back.

What this does NOT cover: it cannot merge, and nothing here approves anything. The
merge request waits for CI and for a person. It also does not sign commits, and it
does not stop a *reviewer* from merging something bad — the gate is human review,
which is the point of the design and not a gap in it.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from aura_schema import from_env

#: GitLab prints ``remote:   https://…/-/merge_requests/12`` after a push that
#: created or updated one. Matching on the path is what makes this survive the
#: surrounding banner text changing between GitLab releases.
_MR_URL_RE = re.compile(r"https?://\S+/-/merge_requests/\d+")

#: ``scheme://user:password@host`` in anything git echoes back.
_USERINFO_RE = re.compile(r"(?P<scheme>[a-zA-Z][a-zA-Z0-9+.-]*://)[^/@\s]*@")


def redact(text: str) -> str:
    """Strip credentials out of anything git printed.

    This repository's remote is ssh, so there is no credential in a URL today. But
    git echoes the remote in push output and in the message of a failed command, and
    a checkout configured with an https remote written ``https://user:token@host``
    would put that token into a tool result — which reaches the model, the transcript
    and the HUD. Positional, so it does not need to know what the token looks like.
    """
    return _USERINFO_RE.sub(lambda m: m.group("scheme"), text)


class GitError(RuntimeError):
    """A git command failed, or a rule in this module refused to run one."""


def repo_path() -> Path:
    return Path(from_env().selfext_repo_path)


def available() -> bool:
    """Whether the configured checkout is a git work tree.

    Gates every tool. On a deployment where the loop is not wired up (no checkout
    in the volume) the tools simply do not appear, rather than appearing and
    failing on first use — which is the difference between "not configured" and
    "broken" for whoever is reading the tool list.
    """
    root = repo_path()
    return (root / ".git").exists()


def bwrap() -> Any:
    """aura-bwrap's ``sandbox.py``, loaded by path.

    Not as ``hermes_plugins.aura_bwrap``: that name exists only while the plugin is
    enabled, and what runs in the sandbox must not depend on the operator's plugin list
    to stay there. ``sandbox.py`` imports nothing of its own package.
    """
    import importlib.util
    import sys

    module = sys.modules.get("jarvis_bwrap_sandbox")
    if module is None:
        source = Path(__file__).resolve().parents[1] / "aura-bwrap" / "sandbox.py"
        spec = importlib.util.spec_from_file_location("jarvis_bwrap_sandbox", source)
        if spec is None or spec.loader is None:
            raise OSError(f"aura-bwrap is missing beside this plugin ({source})")
        module = importlib.util.module_from_spec(spec)
        sys.modules["jarvis_bwrap_sandbox"] = module
        spec.loader.exec_module(module)
    return module


def _identity() -> dict[str, str]:
    """Who commits: the gateway's GIT_AUTHOR_*/GIT_COMMITTER_* or its global user.name/email."""
    named = {k: v for k, v in os.environ.items() if k.startswith(("GIT_AUTHOR_", "GIT_COMMITTER_"))}
    for key, envs in (("user.name", ("GIT_AUTHOR_NAME", "GIT_COMMITTER_NAME")),
                      ("user.email", ("GIT_AUTHOR_EMAIL", "GIT_COMMITTER_EMAIL"))):
        if all(e in named for e in envs):
            continue
        got = subprocess.run(["git", "config", "--global", key], capture_output=True, text=True,
                             timeout=10, env=dict(_sealed_env(os.path.expanduser("~")),
                                                  GIT_CONFIG_GLOBAL=os.path.expanduser("~/.gitconfig")))
        for e in envs:
            named.setdefault(e, got.stdout.strip() or "jarvis")
    return named


def run(*args: str, timeout: float | None = None, check: bool = True) -> str:
    """Run one git command in the checkout and return its stdout.

    ``check`` False returns stdout even on a non-zero exit (used where the exit
    code is the answer, e.g. ``rev-parse --verify``). stderr is folded into the
    raised message because git says the useful half there.

    Inside the bwrap sandbox wherever bwrap exists (the image): the checkout is the
    model's to write, and its config runs commands -- core.fsmonitor on a status, a
    filter driver on an add, a hook on a commit -- which in the gateway would run with
    every credential it holds (security review, 2026-10-06). Without bwrap (a
    developer's machine) git runs here with an environment that holds no secret.
    """
    cfg = from_env()
    repo = str(repo_path())
    argv = ["git", "-C", repo, *args]
    env = {"LC_ALL": "C", "GIT_TERMINAL_PROMPT": "0", **_identity()}
    try:
        sandbox = bwrap()
        if sandbox.bwrap_path() is not None:
            proc = sandbox.run_sandboxed(argv, cwd=repo, extra_binds=[repo],
                                         timeout=timeout or cfg.selfext_timeout, env=env)
        else:
            proc = subprocess.run(argv, capture_output=True, text=True,
                                  timeout=timeout or cfg.selfext_timeout,
                                  env=dict(_sealed_env(os.devnull), **env))
    except (OSError, subprocess.SubprocessError) as exc:
        raise GitError(f"git {args[0]}: {exc.__class__.__name__}: {exc}") from None
    if check and proc.returncode != 0:
        raise GitError(redact(f"git {' '.join(args)} failed ({proc.returncode}): "
                              f"{(proc.stderr or proc.stdout).strip()[:800]}"))
    return proc.stdout


def current_branch() -> str:
    return run("rev-parse", "--abbrev-ref", "HEAD").strip()


def porcelain() -> list[tuple[str, str]]:
    """``[(status, path), …]`` for every changed or untracked path in the checkout.

    ``--untracked-files=all`` is load-bearing, not tidiness: git's default collapses
    a wholly-untracked directory to one entry ending in ``/``, so a scaffold that
    creates ``bench/tasks/coding-selfext/x.yaml`` in a checkout with no ``bench/``
    yet is reported as ``bench/`` — which matches none of the allowed prefixes, and
    the loop refuses to push its own output. Listing each file makes the prefix
    check mean what it says.
    """
    out: list[tuple[str, str]] = []
    for line in run("status", "--porcelain", "-z", "--untracked-files=all").split("\0"):
        if len(line) > 3:
            out.append((line[:2].strip(), line[3:]))
    return out


def _is_under(path: str, prefixes: tuple[str, ...]) -> bool:
    return any(path == p or path.startswith(p.rstrip("/") + "/") for p in prefixes)


def stray_changes(allowed: tuple[str, ...]) -> list[str]:
    """Changed paths that are not under *allowed*.

    The failure this prevents is the worst one available to this plugin: several
    agents (and a person) share this checkout, so ``git commit -a`` — or an
    ``add`` with a loose pathspec — would sweep somebody else's half-finished work
    into a merge request that nobody reviewed for it. The loop refuses to push
    while any such path exists rather than trying to be clever about which ones
    are safe.
    """
    return sorted(path for _status, path in porcelain() if not _is_under(path, allowed))


_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def changed_lines(path: str) -> tuple[list[tuple[int, str]], list[str]]:
    """``([(line_number, text), …], [removed_text, …])`` for *path*'s diff against HEAD.

    Line numbers are into the *new* file, taken from each hunk header, because where
    an added line landed is half of what :func:`assert_manifest_edit_is_only` has to
    know: ``- disk-space`` is a permitted line inside ``plugins.enabled`` and an
    arbitrary edit anywhere else.

    Against HEAD rather than the index, so it sees the change whether or not anything
    has been staged yet; :func:`assert_pushable` runs before the commit.
    """
    diff = run("diff", "--no-color", "--unified=0", "HEAD", "--", path, check=False)
    added: list[tuple[int, str]] = []
    removed: list[str] = []
    cursor = 0
    for line in diff.splitlines():
        hunk = _HUNK_RE.match(line)
        if hunk:
            cursor = int(hunk.group(1))
            continue
        if line.startswith(("+++", "---")):
            continue
        if line.startswith("+"):
            added.append((cursor, line[1:]))
            cursor += 1
        elif line.startswith("-"):
            removed.append(line[1:])
    return added, removed


def enabled_block(text: str) -> tuple[int, int] | None:
    """1-based line range the ``plugins.enabled`` list occupies, or ``None``.

    ``(first, last)`` where ``first`` is the ``enabled:`` key itself and ``last`` the
    final line belonging to it, so a permitted entry sits at ``first < n <= last``.

    One function so the writer and the checker cannot disagree about where the list
    is: :func:`scaffold.name_in_manifest` appends inside this range and
    :func:`assert_manifest_edit_is_only` refuses an added line outside it. Two
    implementations of "where does the list start" would be one edit away from a hole
    that neither reviewer of either function would see.
    """
    lines = [line.rstrip() for line in text.splitlines()]
    try:
        plugins = lines.index("plugins:")
    except ValueError:
        return None
    enabled = None
    for index in range(plugins + 1, len(lines)):
        if lines[index] == "  enabled:":
            enabled = index
            break
        if lines[index] and not lines[index].startswith(" "):
            return None  # the plugins block ended without an enabled list
    if enabled is None:
        return None
    for index in range(enabled + 1, len(lines)):
        # A line at the list's own level or shallower ends it: `  disabled:`, or the
        # next top-level key. Entries and their comments are indented further.
        if lines[index] and not lines[index].startswith("    "):
            return (enabled + 1, index)
    return (enabled + 1, len(lines))


def target_ref() -> str:
    """The ref a new capability is new *relative to* — the remote target, else HEAD.

    Resolved rather than assumed because :func:`names_governed` has to ask "does this
    name already mean something here?" of the branch being merged into, not of the
    working tree. In the working tree a capability's own scaffold has already taken
    its name, so a working-tree check would refuse the very push it exists to allow.

    The HEAD fallback refuses wrongly in one case — a capability that has already
    committed its own scaffold, in a checkout with no remote target and no local one
    — and it is left that way on purpose. The obvious repair is to let the loop record
    the base it branched from and ask that instead, and the obvious repair is the hole:
    anything the loop can record, the model can write. A state file under the writable
    roots is a free ``write_file`` in the approval matrix; a git tag or note is a plain
    ``terminal`` call. A recorded base would be attacker-controlled, which is strictly
    worse than a refusal the operator can clear by fetching the target branch. Asking a
    ref the loop did not author is the only shape that holds, so the effort goes into
    the message (see :func:`scaffold.assert_name_is_free`) rather than the semantics.
    """
    cfg = from_env()
    base = f"{cfg.selfext_remote}/{cfg.selfext_target_branch}"
    if run("rev-parse", "--verify", "--quiet", base, check=False).strip():
        return base
    local = run("rev-parse", "--verify", "--quiet", cfg.selfext_target_branch,
                check=False).strip()
    return cfg.selfext_target_branch if local else "HEAD"


def names_bundled_by_hermes(ref: str, contract: str) -> set[str]:
    """The plugin names Hermes itself ships, as recorded in the repository at *ref*.

    Read out of the checkout rather than off the filesystem so the answer is the same
    in the gateway container, on a dev box and in a test. Absent or unreadable means
    an empty set: the file is a *notification* mechanism kept honest by
    ``deploy/tests/test_plugin_manifest.py``, and a loop that refused to run because a
    contract file had moved would be a worse failure than the collision it prevents.
    """
    text = run("show", f"{ref}:{contract}", check=False)
    try:
        return set(json.loads(text).get("plugins") or [])
    except (ValueError, AttributeError):
        return set()


def names_governed(ref: str, plugins_subdir: str, manifests: tuple[str, ...],
                   contract: str = "", skills_subdir: str = "") -> set[str]:
    """Every plugin name *ref* already ships or already names in a manifest list.

    And, when *skills_subdir* is given, every skill it ships: ``allowed_paths``
    opens ``hermes/skills/<name>/`` too, so a capability called ``refactor`` would
    otherwise be handed the shipped refactor skill to rewrite and push.

    Both halves matter and for different reasons. The directory listing stops a
    capability taking the name of a plugin that exists — ``aura-guard`` above all,
    since ``allowed_paths`` would then hand the loop write access to the plugin that
    registers the approval hook, and no manifest check would ever see that edit
    because it is not in a manifest.

    A third half, when *contract* is given: the names Hermes itself bundles. Those are
    not in this repository at all, so neither of the other two sees them, and
    ``disk-cleanup`` being bundled while ``disk-space`` is ours says how close the
    collision already is.

    The manifest entries stop a capability taking a name the manifest already governs
    for a different reason. The disabled list is not decoration: those cloud web
    backends are named there so that one "must take a deliberate edit here to come
    back, not an environment variable someone pastes into .env". A capability called
    ``web-exa`` would append ``- web-exa`` to ``enabled`` while it is still listed
    under ``disabled``, and every shape check would pass, because the added line does
    equal the capability's own name. Read out of the file rather than hard-coded, so
    it keeps holding as the disabled list grows.
    """
    names: set[str] = set(names_bundled_by_hermes(ref, contract)) if contract else set()
    for subdir in (plugins_subdir, skills_subdir):
        if not subdir:
            continue
        listing = run("ls-tree", "--name-only", f"{ref}:{subdir}", check=False)
        names.update(part for part in listing.split() if part and not part.startswith("_"))
    for rel in manifests:
        text = run("show", f"{ref}:{rel}", check=False)
        names.update(re.findall(r"^\s+- ([a-z0-9][a-z0-9._-]*)\s*$", text, re.M))
    return names


def assert_manifest_edit_is_only(name: str, manifests: tuple[str, ...]) -> None:
    """Raise unless each manifest's diff does nothing but name *name* in a list.

    ``allowed`` has to include the deploy manifests for the loop to name its own
    capability there (:func:`scaffold.name_in_manifest`), and that widens
    :func:`stray_changes` — which until now was the only thing standing between an
    agent-authored edit and a push. This narrows it straight back: the one edit
    permitted in those files is the addition of list entries reading exactly
    ``- <name>`` for the capability being pushed. A changed key, a deleted line,
    another plugin's name, a comment reworded on the way past — all refused, and the
    refusal quotes the line so whoever reads it can see what was attempted.

    Deliberately not a YAML comparison. Comparing parsed documents would accept a
    diff that reorders the file, rewrites its comments or reflows a block while
    landing on the same data, and the comments in that block are the manifest's
    explanation of itself, which is most of what makes it worth keeping honest.
    """
    entry = f"- {name}"
    for rel in manifests:
        added, removed = changed_lines(rel)
        # Added lines first: a *changed* line is a removal and an addition, and the new
        # text is what a reader needs to see. Reporting it as "a line was removed" would
        # name the old value and hide what it was changed to.
        offending = [text for _n, text in added if text.strip() != entry]
        if offending:
            raise GitError(
                f"{rel} was edited beyond naming {name!r} under plugins.enabled; refusing to "
                f"push: {[line.strip()[:60] for line in offending[:5]]}"
            )
        if removed:
            raise GitError(
                f"{rel} has lines removed, and this loop may only add its own name there: "
                f"{[line.strip()[:60] for line in removed[:5]]}"
            )
        if not added:
            continue
        # Where it landed, not only what it says. `    - disk-space` is a permitted
        # line inside plugins.enabled and an arbitrary one pasted into, say, the
        # approvals block or a comment further down the file.
        block = enabled_block((repo_path() / rel).read_text(encoding="utf-8"))
        if block is None:
            raise GitError(f"{rel} has no plugins.enabled block to have written into")
        first, last = block
        outside = [n for n, _text in added if not first < n <= last]
        if outside:
            raise GitError(
                f"{rel} gained {entry!r} at line(s) {outside}, outside the plugins.enabled "
                f"block at lines {first}-{last}; refusing to push"
            )


def assert_pushable(branch: str, allowed: tuple[str, ...], name: str,
                    manifests: tuple[str, ...]) -> None:
    """Raise unless pushing *branch* is one of the shapes this loop is allowed to make."""
    cfg = from_env()
    prefix = cfg.selfext_branch_prefix
    if not prefix:
        raise GitError("selfext.branch_prefix is empty; refusing to push an unmarked branch")
    if not branch.startswith(prefix):
        raise GitError(f"branch {branch!r} does not start with {prefix!r}; refusing to push")
    if branch == cfg.selfext_target_branch:
        raise GitError(f"refusing to push the target branch {branch!r}")
    stray = stray_changes(allowed)
    if stray:
        raise GitError(
            "the checkout carries changes outside this capability's files, which would be "
            f"swept into the merge request: {stray[:20]}"
        )
    # Required arguments, not optional ones: `allowed` is permanently wide now, so a
    # caller that forgot the narrowing would get the widened allowlist with nothing
    # narrowing it and no error. The assertion covers the emptier version of the same
    # mistake — passing "" or () to satisfy the signature.
    reachable = tuple(rel for rel in manifests if _is_under(rel, allowed))
    if reachable and not name:
        raise GitError(
            f"assert_pushable was given manifests inside the allowlist ({list(reachable)}) "
            f"with no capability name to narrow them to; refusing to push")
    if name and manifests:
        assert_manifest_edit_is_only(name, manifests)


def create_branch(branch: str) -> None:
    """Branch off the configured target, or switch to the branch if it already exists."""
    cfg = from_env()
    existing = run("rev-parse", "--verify", "--quiet", branch, check=False).strip()
    if existing:
        run("checkout", branch)
        return
    base = f"{cfg.selfext_remote}/{cfg.selfext_target_branch}"
    have_base = run("rev-parse", "--verify", "--quiet", base, check=False).strip()
    run("checkout", "-b", branch, *( [base] if have_base else [] ))


def commit(paths: tuple[str, ...], subject: str, body: str) -> str:
    """Stage exactly *paths* and commit them. Returns the new commit's short sha.

    Explicit pathspecs, never ``-a`` and never ``.``: see :func:`stray_changes` for
    the failure that rule exists to prevent.

    A path in *paths* that does not exist is dropped rather than passed to git: the
    tuple is the capability's *permitted* blast radius, not a list of things it must
    have written, and a scaffold asked for no benchmark task would otherwise die on
    ``pathspec 'bench/tasks/coding-selfext' did not match any files``.
    """
    root = repo_path()
    present = tuple(p for p in paths if (root / p).exists())
    if not present:
        raise GitError(f"none of {list(paths)} exists in the checkout; nothing to commit")
    run("add", "--", *present)
    staged = run("diff", "--cached", "--name-only").strip()
    if not staged:
        raise GitError("nothing staged: the scaffold wrote no files under the allowed paths")
    message = f"{subject}\n\n{body}\n"
    run("commit", "-m", message)
    return run("rev-parse", "--short", "HEAD").strip()


def parse_mr_url(text: str) -> str:
    """The merge request URL GitLab echoed on the push, or "" when it printed none."""
    match = _MR_URL_RE.search(text)
    return match.group(0) if match else ""


def _sealed_env(home: str) -> dict[str, str]:
    """An environment for git that holds no secret and reads no global or system config."""
    return {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": home, "LC_ALL": "C",
            "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "", "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull}


def trusted_push_url(remote: str) -> str:
    """The checkout's remote URL, credentials stripped, when it is the configured GitLab.

    The checkout is the model's to write (its terminal binds work/), so its remote is
    not trusted with a token on its own say-so: the host must be GITLAB_API_URL's, over
    https. GitError otherwise -- a push that would hand the token elsewhere does not run.
    """
    api = urlsplit(from_env().gitlab_api_url)
    proc = subprocess.run(["git", "-C", str(repo_path()), "remote", "get-url", remote],
                          capture_output=True, text=True, timeout=30, env=_sealed_env(os.devnull))
    url = urlsplit(proc.stdout.strip())
    if proc.returncode or url.scheme != "https" or not api.hostname or url.hostname != api.hostname:
        raise GitError(f"the checkout's remote '{remote}' is not the configured GitLab "
                       f"({api.hostname or 'GITLAB_API_URL unset'}); not pushing with its token")
    # The host alone is not enough: the token may push to other projects there too.
    project = configured_project()
    if url.path.strip("/").removesuffix(".git") != project:
        raise GitError(f"the checkout's remote '{remote}' is not the configured GitLab project "
                       f"({project or 'GITLAB_PROJECT_ID unset'}); not pushing with its token")
    netloc = url.hostname + (f":{url.port}" if url.port else "")
    return f"https://{netloc}/{project}.git"


def configured_project() -> str:
    """GITLAB_PROJECT_ID's ``path_with_namespace``, asked of GitLab; "" when it cannot say."""
    cfg = from_env()
    api, pid = cfg.gitlab_api_url.rstrip("/"), cfg.gitlab_project_id
    if not api or not pid:
        return ""
    import urllib.request
    req = urllib.request.Request(f"{api}/projects/{pid}",
                                 headers={"PRIVATE-TOKEN": cfg.gitlab_token})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310 - the configured API
            return str(json.load(resp).get("path_with_namespace") or "")
    except (OSError, ValueError):
        return ""


def _token_push(target: str, branch: str, options: list[str], timeout: float) -> tuple[int, str]:
    """Push *branch* to *target* with GITLAB_TOKEN, from a staging repo the model never wrote.

    The checkout's remote has no password (https://private@), so every push was refused,
    "HTTP Basic: Access denied" (bench selfext-mr-004, 2026-10-06). Pushing straight from
    the checkout with a token would trust config the model can edit: an insteadOf rewrite,
    a URL-scoped credential helper or a hook would all receive it. So the branch is first
    fetched into a fresh bare repo -- git upload-pack reads the checkout's objects and
    runs none of its hooks, filters or fsmonitor -- with no secret in the environment,
    and only that repo, whose config is empty, pushes, with a helper scoped to the one
    host that reads the token at push time. The value is never on a command line or in a
    tool result.
    """
    with tempfile.TemporaryDirectory(prefix="selfext-push-") as stage:
        sealed = _sealed_env(stage)
        subprocess.run(["git", "init", "--bare", "-q", stage], env=sealed, check=True,
                       capture_output=True, timeout=30)
        copy = subprocess.run(
            ["git", "-C", stage, "fetch", "-q", "--no-tags", "--no-write-fetch-head",
             f"file://{repo_path()}", f"+refs/heads/{branch}:refs/heads/{branch}"],
            capture_output=True, text=True, timeout=timeout, env=sealed)
        if copy.returncode:
            return copy.returncode, redact(f"{copy.stdout}\n{copy.stderr}".strip())
        host = target.split("/", 3)[2]
        helper = '!f() { echo username=oauth2; echo "password=$GITLAB_TOKEN"; }; f'
        proc = subprocess.run(
            ["git", "-c", "credential.helper=", "-c", f"credential.https://{host}.helper={helper}",
             "-C", stage, "push", *options, target, f"{branch}:{branch}"],
            capture_output=True, text=True, timeout=timeout,
            env=dict(sealed, GITLAB_TOKEN=from_env().gitlab_token))
        return proc.returncode, redact(f"{proc.stdout}\n{proc.stderr}".strip())


def open_merge_request(branch: str, title: str, description: str) -> dict[str, Any]:
    """Push *branch* and ask GitLab to open a merge request for it, via push options.

    Returns the push output and the merge request URL when GitLab printed one. A
    push that GitLab accepts but for which it opens no merge request (the option
    is unsupported, or one is already open) still succeeds here — the URL comes
    back empty and the caller says so, rather than the branch being pushed and the
    tool reporting a failure the owner would then have to untangle by hand.
    """
    cfg = from_env()
    # Newlines in a push option are not transmissible; GitLab takes the description
    # as one line. Keeping the whole body on one line is the honest shape rather
    # than silently truncating at the first newline.
    one_line = " ".join(description.split())
    options = [
        "-o", "merge_request.create",
        "-o", f"merge_request.target={cfg.selfext_target_branch}",
        "-o", f"merge_request.title={title}",
        "-o", f"merge_request.description={one_line}",
        "-o", "merge_request.remove_source_branch",
    ]
    target = trusted_push_url(cfg.selfext_remote) if cfg.gitlab_token else None

    def _push(with_options: bool) -> tuple[int, str]:
        if target is not None:
            return _token_push(target, branch, options if with_options else [], cfg.selfext_timeout)
        proc = subprocess.run(
            ["git", "-C", str(repo_path()), "push", "--set-upstream",
             *(options if with_options else []), cfg.selfext_remote, f"{branch}:{branch}"],
            capture_output=True, text=True, timeout=cfg.selfext_timeout,
            env=dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_ASKPASS="", LC_ALL="C"),
        )
        return proc.returncode, redact(f"{proc.stdout}\n{proc.stderr}".strip())

    code, combined = _push(True)
    push_note = ""
    if code != 0 and "does not support push options" in combined:
        # A remote with push options switched off (or a plain git server) rejects the
        # whole push, not just the options — so the branch would not exist and the
        # capability would be stranded. Push it plainly and say the merge request has
        # to be opened by hand, which is recoverable; a bare "push failed" is not.
        code, combined = _push(False)
        push_note = ("the remote does not accept push options, so the branch was pushed without "
                     "one: open the merge request by hand")
    if code != 0:
        raise GitError(f"push failed ({code}): {combined[:800]}")  # already redacted
    return {"ok": True, "branch": branch, "merge_request": parse_mr_url(combined),
            "push_note": push_note, "push_output": combined[-2000:]}
