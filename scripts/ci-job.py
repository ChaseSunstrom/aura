#!/usr/bin/env python3
"""Run one pipeline job's own lines, in its own image, on a clean clone.

A job that is red in the pipeline and green on a desk is green on the desk for a reason the
desk cannot see: the local venv holds every suite's packages, the host has tools the slim image
lacks, and a Hermes install sits beside the checkout (T-1442 found one of each, by hand). This
does what the runner does, for the jobs where that is only a script: it reads the job's
``before_script`` and ``script`` from ``.gitlab-ci.yml`` and the files it includes, and runs
exactly those in a fresh container of the job's image, as root, on a shallow clone of ``HEAD``.

    make ci-job JOB=test:adapter        # one job
    make ci-jobs                        # the untagged Python test-stage jobs, one after another
    make ci-jobs-all                    # those and the Node and Hermes-image ones (longer)
    python3 scripts/ci-job.py --print test:plugins   # the lines it would run, no container

What it does not do. It runs committed work only (the runner clones commits; a dirty tree is
said, not included). It copies no artifacts, caches or services between jobs, so a job that
`needs` another, has a service, or is tagged for the deploy host's runner is refused by name.
It does not know the runner's own variables (the registry, the commit ref), a ``rules:`` block,
or a ``!reference`` tag, which this pipeline does not use (a file that gains one is refused, not
half-read). A green run here is the job's script passing in its image, not the pipeline passing.

Exit 0 when every job ran green, 1 when one failed, 2 when a job was refused, Docker is absent,
or a pipeline's job containers are running on this machine (``--during-pipeline`` starts anyway).
"""
from __future__ import annotations

import argparse
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
#: Where the runner puts the checkout, and so where the job's lines start.
BUILD_DIR = "/builds/p"
#: Marks the end of the tool's preamble in a generated script; the job's own lines follow it.
OWN_LINES = "# --- the job's own lines, from the pipeline file"
_VAR_RE = re.compile(r"\$\{?(\w+)\}?")
_NOT_JOBS = {"stages", "include", "variables", "default", "workflow"}


class CannotRun(Exception):
    """A job this tool cannot reproduce, with the reason in words."""


def load(root: Path) -> tuple[dict[str, dict], dict[str, str], dict]:
    """(every job and template by name, the pipeline's variables, its ``default`` block)."""
    main = _read(root / ".gitlab-ci.yml")
    docs = [main]
    for inc in main.get("include") or []:
        if isinstance(inc, dict) and "local" in inc:
            docs.append(_read(root / str(inc["local"]).lstrip("/")))
    jobs: dict[str, dict] = {}
    variables: dict[str, str] = {}
    for doc in docs:
        jobs.update({k: v for k, v in doc.items() if isinstance(v, dict) and k not in _NOT_JOBS})
        variables.update(_plain(doc.get("variables")))
    default = main.get("default")
    return jobs, variables, default if isinstance(default, dict) else {}


def _read(path: Path) -> dict:
    """One pipeline file with the safe loader. A ``!reference`` tag ends here, in words: this
    resolves none, and the loader's traceback would read as the tool being broken."""
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        why = " ".join(str(exc).split())[:200]
        raise CannotRun(f"anything: {path.name} does not load as plain YAML ({why})") from exc
    return doc if isinstance(doc, dict) else {}


def _plain(variables: object) -> dict[str, str]:
    """``NAME: value`` and ``NAME: {value: …}`` both as strings."""
    out: dict[str, str] = {}
    for key, value in (variables or {}).items() if isinstance(variables, dict) else []:
        out[str(key)] = str(value.get("value", "") if isinstance(value, dict) else value)
    return out


def resolve(jobs: dict[str, dict], name: str, _seen: tuple[str, ...] = ()) -> dict:
    """A job with its ``extends`` merged: later parents and the job itself win key by key,
    and ``variables`` merge rather than replace, as the runner does it."""
    body = jobs[name]
    parents = body.get("extends") or []
    out: dict = {}
    for parent in [parents] if isinstance(parents, str) else parents:
        if parent in jobs and parent not in _seen:
            merged = resolve(jobs, parent, _seen + (name,))
            out = {**out, **merged, "variables": {**out.get("variables", {}), **_plain(merged.get("variables"))}}
    own = {k: v for k, v in body.items() if k != "extends"}
    return {**out, **own, "variables": {**out.get("variables", {}), **_plain(own.get("variables"))}}


def _flat(lines: object) -> list[str]:
    """A script block as lines: GitLab lets an entry be a list of its own."""
    if lines is None:
        return []
    if not isinstance(lines, list):
        return [str(lines)]
    out: list[str] = []
    for line in lines:
        out += _flat(line)
    return out


def _expand(text: str, env: dict[str, str]) -> str:
    """``$NAME`` and ``${NAME}`` replaced where the pipeline defines NAME, left alone where not."""
    return _VAR_RE.sub(lambda m: env.get(m.group(1), m.group(0)), text)


def _stage(job: dict) -> str:
    return str(job.get("stage", "test"))


def job_script(root: Path, name: str) -> tuple[str, str]:
    """(image, bash) for one job, or :class:`CannotRun`."""
    return _script(load(root), name)


def _script(loaded: tuple[dict[str, dict], dict[str, str], dict], name: str) -> tuple[str, str]:
    jobs, variables, default = loaded
    if name not in jobs or name.startswith(".") or "script" not in resolve(jobs, name):
        raise CannotRun(f"{name}: no job of that name in the pipeline file or the files it includes")
    job = resolve(jobs, name)
    if job.get("tags") or default.get("tags"):
        raise CannotRun(f"{name}: carries a runner tag ({', '.join(job.get('tags') or default['tags'])}); it runs on "
                        "that runner's own machine, with what is mounted there")
    if job.get("services") or default.get("services"):
        raise CannotRun(f"{name}: starts a service container beside it, which this does not")
    if job.get("needs") or job.get("dependencies"):
        raise CannotRun(f"{name}: needs another job's artifacts ({job.get('needs') or job.get('dependencies')})")
    env = {**variables, **job["variables"], "CI": "true", "CI_PROJECT_DIR": BUILD_DIR, "CI_JOB_ID": "1",
           "CI_JOB_NAME": name}
    for _ in range(3):                                   # a variable may name another
        env = {k: _expand(v, env) for k, v in env.items()}
    image = job.get("image", default.get("image"))
    image = image.get("name") if isinstance(image, dict) else image
    image = _expand(str(image or ""), env)
    if not image or "$" in image:
        raise CannotRun(f"{name}: its image ({image or 'none'}) is not a name this can pull")
    before = job["before_script"] if "before_script" in job else default.get("before_script")
    lines = ["set -eo pipefail", f"cd {BUILD_DIR}"]
    # A value still holding a `$` names something only the runner knows; it is left unset.
    lines += [f"export {k}={shlex.quote(v)}" for k, v in env.items() if re.fullmatch(r"\w+", k) and "$" not in v]
    lines += [OWN_LINES] + _flat(before) + _flat(job["script"])
    return image, "\n".join(lines) + "\n"


def test_jobs(root: Path, python_only: bool = False) -> list[str]:
    """The test-stage jobs this can run, in the pipeline file's order: any runner takes them, no
    service, no other job's artifacts, an image with a name. ``python_only`` keeps those in a
    Python image, the short set; the rest install the console's packages or run in the Hermes
    image, and take minutes each."""
    loaded = load(root)
    jobs = loaded[0]
    out = []
    for name in jobs:
        if name.startswith(".") or "script" not in resolve(jobs, name) or _stage(resolve(jobs, name)) != "test":
            continue
        try:
            image, _text = _script(loaded, name)
        except CannotRun:
            continue
        if image.startswith("python:") or not python_only:
            out.append(name)
    return out


def python_test_jobs(root: Path) -> list[str]:
    """The test-stage jobs any runner takes that are a script in a Python image."""
    return test_jobs(root, python_only=True)


def docker_argv(image: str, src: Path, script: Path, name: str | None = None) -> list[str]:
    """The container: the clone and the script read-only, and nothing else of the host's.
    The copy inside belongs to the container's own user, as the runner's checkout does. The
    image's entrypoint is replaced by the shell, as the runner replaces it."""
    return ["docker", "run", "--rm", *(["--name", name] if name else []),
            "-v", f"{src}:/src:ro", "-v", f"{script}:/job.sh:ro", "--entrypoint", "bash", image,
            "-c", f"mkdir -p /builds && cp -r /src {BUILD_DIR} && bash /job.sh"]


def _clone(root: Path, into: Path, ref: str | None) -> str:
    """A shallow clone of a branch, a tag or HEAD; returns its short commit."""
    argv = ["git", "clone", "-q", "--depth", "1", *(["--branch", ref] if ref else []), f"file://{root}", str(into)]
    subprocess.run(argv, check=True)
    return subprocess.run(["git", "-C", str(into), "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                          check=True).stdout.strip()


def _say(line: str) -> None:
    """A progress line, out now. Sent to a file or a pipe, Python holds stdout until exit, and a
    whole-set run then shows nothing for its ten minutes."""
    print(line, flush=True)


def _pipeline_containers() -> list[str]:
    """The runner's job containers on this machine, by name. The runner names them
    ``runner-<id>-project-…``; this tool's own are ``ci-job-…`` and the runner's daemon is not
    one. Does not see a job on another machine's runner; a ``docker ps`` that fails is read as
    nothing running, and the ``docker run`` that follows reports the daemon."""
    out = subprocess.run(["docker", "ps", "--format", "{{.Names}}"], capture_output=True, text=True, check=False)
    return [name for name in (out.stdout or "").split() if name.startswith("runner-")]


def run(root: Path, names: list[str], ref: str | None = None, limit_s: int = 2400, keep: bool = False,
        during_pipeline: bool = False) -> int:
    scripts = {}
    for name in names:                                   # refuse before anything is cloned or pulled
        try:
            scripts[name] = job_script(root, name)
        except CannotRun as exc:
            print(f"ci-job: cannot run {exc}", file=sys.stderr)
            return 2
    if not shutil.which("docker"):
        print("ci-job: docker is not on this machine", file=sys.stderr)
        return 2
    busy = [] if during_pipeline else _pipeline_containers()
    if busy:
        # The runner shares these cores: a suite started beside its jobs fails their frame budgets.
        print(f"ci-job: not started: {len(busy)} container(s) of a pipeline's job are running on this machine "
              "(docker ps, names starting runner-), and this load fails that pipeline's timed jobs. "
              "Run it when they are gone, or pass --during-pipeline.", file=sys.stderr)
        return 2
    work = Path(tempfile.mkdtemp(prefix="ci-job-"))
    worst = 0
    try:
        commit = _clone(root, work / "src", ref)
        dirty = subprocess.run(["git", "-C", str(root), "status", "--porcelain"], capture_output=True, text=True).stdout
        _say(f"ci-job: commit {commit}" + (f" (not included: {len(dirty.splitlines())} uncommitted change(s))"
                                            if dirty.strip() and not ref else ""))
        for i, (name, (image, text)) in enumerate(scripts.items()):
            script, log = work / f"job-{i}.sh", work / f"job-{i}.log"
            script.write_text(text, encoding="utf-8")
            began = time.monotonic()
            container = f"ci-job-{work.name.removeprefix('ci-job-')}-{i}".lower()
            with log.open("w", encoding="utf-8") as out:
                try:
                    code = subprocess.run(docker_argv(image, work / "src", script, container), stdout=out,
                                          stderr=subprocess.STDOUT, timeout=limit_s, check=False).returncode
                except subprocess.TimeoutExpired:
                    # Stopping the client leaves the container running; it is ended by name.
                    subprocess.run(["docker", "kill", container], capture_output=True, check=False)
                    code = 124
            took = time.monotonic() - began
            tail = [ln for ln in log.read_text(encoding="utf-8", errors="replace").splitlines() if ln.strip()]
            if code == 0:
                _say(f"ok    {name}  ({took:.0f} s, {image}): {tail[-1][:120] if tail else ''}")
            else:
                worst = 1
                _say(f"FAIL  {name}  (exit {code}, {took:.0f} s, {image})")
                named = [ln for ln in tail if ln.startswith(("FAILED", "ERROR"))][:40]
                for ln in named or tail[-25:]:
                    _say(f"      {ln[:300]}")
        if keep:
            _say(f"ci-job: logs kept in {work}")
    finally:
        if not keep:
            shutil.rmtree(work, ignore_errors=True)
    return worst


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("jobs", nargs="*", help="job names, as the pipeline file spells them")
    ap.add_argument("--python-tests", action="store_true", help="every untagged Python test-stage job")
    ap.add_argument("--all-tests", action="store_true",
                    help="every test-stage job this can run: the Python ones and those in other images")
    ap.add_argument("--list", action="store_true",
                    help="name the jobs --python-tests would run (with --all-tests, that set)")
    ap.add_argument("--print", action="store_true", dest="show", help="print each job's lines; start nothing")
    ap.add_argument("--ref", help="a branch or tag to clone instead of HEAD")
    ap.add_argument("--keep", action="store_true", help="keep the clone and the logs")
    ap.add_argument("--limit", type=int, default=2400, help="seconds one job may take (default 2400)")
    ap.add_argument("--during-pipeline", action="store_true",
                    help="start even while a pipeline's job containers run on this machine")
    args = ap.parse_args(argv)
    try:
        chosen = args.all_tests or args.python_tests or args.list
        names = list(args.jobs) + (test_jobs(ROOT, python_only=not args.all_tests) if chosen else [])
    except CannotRun as exc:
        print(f"ci-job: cannot run {exc}", file=sys.stderr)
        return 2
    if args.list:
        print("\n".join(names))
        return 0
    if not names:
        ap.error("name a job (make ci-job JOB=test:adapter) or pass --python-tests or --all-tests")
    if args.show:
        for name in names:
            try:
                image, text = job_script(ROOT, name)
            except CannotRun as exc:
                print(f"ci-job: cannot run {exc}", file=sys.stderr)
                return 2
            print(f"# {name}\n# image: {image}\n{text}")
        return 0
    return run(ROOT, names, ref=args.ref, limit_s=args.limit, keep=args.keep, during_pipeline=args.during_pipeline)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
