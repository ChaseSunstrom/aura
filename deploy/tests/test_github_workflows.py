"""The GitHub Actions workflows mirror the GitLab gates (T-0627).

The owner wants the repository public on GitHub, where the gates must run on hosted
runners with no secret. Three things are pinned here so the two CIs cannot drift apart
without a red test: the workflows are safe (SHA-pinned actions, read-only token, no
secrets, no self-hosted runner), every GitLab job is either mirrored command for command
or named in GITLAB_ONLY with the reason it cannot run on GitHub, and every command a
workflow runs is one GitLab runs, a Makefile target, or install plumbing.

What this does not prove: that a command passes on a GitHub runner. Nothing here runs
Actions; the first push to a fork does.
"""
from __future__ import annotations

import posixpath
import re

import yaml

from test_pipeline import ROOT, _all_jobs

WORKFLOWS = sorted((ROOT / ".github" / "workflows").glob("*.yml"))

# Every GitLab job that stays GitLab-only, and why. A new GitLab job must land in the
# mirrored set or here: test_every_gitlab_job_is_mirrored_or_named_gitlab_only fails otherwise.
GITLAB_ONLY = {
    "test:plugin-discovery": "runs inside the nousresearch/hermes-agent image",
    "test:hermes": "runs inside the nousresearch/hermes-agent image: the tests that need a Hermes install",
    "test:unprivileged": "re-runs the root-skipped tests as nobody; GitHub's runner is not root, so its "
                         "own suite steps already run them",
    "test:media": "installs Debian's ffmpeg to run the tests that skip without it; the mirror's suite "
                  "steps run those files on whatever GitHub's runner image carries",
    "test:hermes-imports": "runs inside the image build:jarvis pushed to the private registry",
    "build:jarvis": "private-registry image build",
    "build:adapter": "private-registry image build",
    "build:web": "private-registry image build",
    "build:extract": "private-registry image build",
    "build:cosyvoice": "private-registry image build",
    "build:models": "private-registry image build",
    "integration:smoke": "brings the stack up on the production host, with the model",
    "deploy:production": "deploys to the production host",
    "deploy:console": "drives the deployed console",
    "bench:mr": "benchmark against the host's models",
    "bench:nightly": "benchmark against the host's models",
    "apk:build": "release signing and the GitLab package registry; its JVM unit tests run in android.yml",
    "apk:release": "APK signing and release",
    "desktop:dist": "signed desktop release",
    "desktop:agent:windows": "signed desktop release",
    "desktop:shell": "signed desktop release",
    "desktop:shell:macos": "signed desktop release",
    "desktop:shell:release": "signed desktop release",
    "android-deps": "needs the owner's NVD_API_KEY secret",
    "hud-frametime": "frame-budget gate calibrated on this host's runner",
    "hud-overlap": "serialised beside the frame-budget jobs on this host",
    "hud-behaviour": "serialised beside the frame-budget jobs on this host",
    "hud-gallery": "serialised beside the frame-budget jobs on this host",
    "hud-size": "needs the APK apk:build produces",
    "android-macrobench": "needs a runner with a phone on adb",
}

# Makefile gate targets a workflow must run, or the GitLab job whose commands stand in for it.
GATE_TARGETS = {
    "test-core": None,
    "test-tools": None,
    "test-android": None,
    "eval-routing": None,
    "eval-resolution": None,
    "check-shell": None,
    "check-markers": None,
    "dup": None,
    "lint-web": "web-lint",
    "lint-python": "py-mypy",
    "lint-android": "kotlin-lint",
    "schema-check": "schema-sync",
    "tokens-check": "tokens-sync",
    "icons-check": "tokens-sync",
    "docs-check": "docs-sync",
}

SETUP = ("pip install", "python -m pip install", "sudo apt-get", "apt-get", "export ", "mkdir ", "chmod ",
         "echo ", "curl -fsSL -o", "command -v", "bash android-app/tools/bootstrap-toolchain.sh",
         "bash scripts/install-age.sh",
         "set -eu", "for d in", "npx playwright install", "npm ci", "make --version")
SKIP_GL = SETUP + ("if ", "fi", "done")


def _flat(lines, cwd: str = "."):
    """(cwd, command) per command, following `cd` the way one shell would."""
    out = []
    for raw in lines:
        raw = raw.strip()
        if not raw or raw.startswith("#"):
            continue
        for part in raw.split(" && "):
            part = re.sub(r" --junitxml=\S+", "", part.strip())
            if part.startswith("cd "):
                cwd = posixpath.normpath(posixpath.join(cwd, part[3:].strip()))
            else:
                out.append((cwd, part))
    return out


def _lines(value) -> list[str]:
    if not value:
        return []
    return [ln for item in ([value] if isinstance(value, str) else value) for ln in str(item).splitlines()]


def _gitlab_pairs(job: dict) -> tuple[set, set]:
    """(script commands to mirror, every command incl. before_script)."""
    before = _flat(_lines(job.get("before_script")))
    cwd = "."
    for c, _ in before:
        cwd = c
    script = _flat(_lines(job.get("script")), cwd)
    keep = lambda pairs: {(c, p) for c, p in pairs if not p.startswith(SKIP_GL)}  # noqa: E731
    return keep(script), keep(before + script)


def _workflow_pairs() -> list[tuple[str, str, str]]:
    """(workflow, cwd, command) for every run step."""
    out = []
    for wf in WORKFLOWS:
        for job in yaml.safe_load(wf.read_text())["jobs"].values():
            for step in job["steps"]:
                if "run" in step:
                    base = step.get("working-directory", ".")
                    out += [(wf.name, c, p) for c, p in _flat(step["run"].splitlines(), base)]
    return out


def test_workflows_exist():
    assert {w.name for w in WORKFLOWS} >= {"python.yml", "web.yml", "android.yml", "static.yml"}


def test_github_workflows_parse_pin_and_hold_no_secrets():
    for wf in WORKFLOWS:
        text = wf.read_text()
        doc = yaml.safe_load(text)
        assert doc["permissions"] == {"contents": "read"}, wf.name
        code = "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))
        assert not re.search(r"\bsecrets\s*[.:\[]|GITHUB_TOKEN", code), f"{wf.name} reads a secret"
        for name, job in doc["jobs"].items():
            assert "self-hosted" not in str(job["runs-on"]), f"{wf.name}:{name}"
            assert job.get("timeout-minutes"), f"{wf.name}:{name} has no timeout"
        for line in text.splitlines():
            m = re.search(r"\buses:\s*(\S+)(.*)", line)
            if m:
                assert re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", m.group(1)), f"{wf.name}: {line.strip()}"
                assert re.search(r"#\s*v\d", m.group(2)), f"{wf.name}: pin needs its version in a comment"


def test_every_gitlab_job_is_mirrored_or_named_gitlab_only():
    jobs = _all_jobs()
    assert set(GITLAB_ONLY) <= set(jobs), f"stale GITLAB_ONLY: {set(GITLAB_ONLY) - set(jobs)}"
    have = {(c, p) for _, c, p in _workflow_pairs()}
    missing = {}
    for name, job in jobs.items():
        if name in GITLAB_ONLY:
            continue
        gone = _gitlab_pairs(job)[0] - have
        if gone:
            missing[name] = sorted(gone)
    assert not missing, f"GitLab commands no workflow runs (mirror them or add to GITLAB_ONLY): {missing}"


def test_makefile_gate_targets_are_mirrored():
    makefile = (ROOT / "Makefile").read_text()
    made = {p.split()[1] for _, _, p in _workflow_pairs() if p.startswith("make ") and len(p.split()) > 1}
    jobs = _all_jobs()
    have = {(c, p) for _, c, p in _workflow_pairs()}
    for target, stand_in in GATE_TARGETS.items():
        assert re.search(rf"^{re.escape(target)}:", makefile, re.M), f"{target} left the Makefile"
        if stand_in is None:
            assert target in made, f"no workflow runs `make {target}`"
        else:
            assert _gitlab_pairs(jobs[stand_in])[0] <= have, f"`make {target}` has no mirror of {stand_in}"


def test_github_workflow_steps_run_what_gitlab_or_make_runs():
    makefile = (ROOT / "Makefile").read_text()
    allowed = set()
    for job in _all_jobs().values():
        allowed |= _gitlab_pairs(job)[1]
    allowed_cmds = {p for _, p in allowed}
    for wf, cwd, cmd in _workflow_pairs():
        if cmd.startswith(SETUP):
            continue
        m = re.match(r"make (?:--no-print-directory )?([\w-]+)", cmd)
        if m:
            assert re.search(rf"^{m.group(1)}:", makefile, re.M), f"{wf}: no Makefile target in `{cmd}`"
            continue
        assert (cwd, cmd) in allowed or cmd in allowed_cmds, f"{wf}: `{cmd}` (in {cwd}) is not a GitLab or make command"


def test_ci_docs_name_the_workflows_and_the_gitlab_only_jobs():
    docs = (ROOT / "docs" / "ci.md").read_text()
    assert ".github/workflows" in docs
    for name in ("deploy:production", "hud-frametime", "bench:nightly", "apk:release"):
        assert name in docs, f"docs/ci.md does not list {name} as GitLab-only"
    head = (ROOT / ".github" / "workflows" / "python.yml").read_text().split("\n\n", 1)[0]
    assert "GitLab-only" in head


def test_the_desktop_end_to_end_suite_is_a_gate_and_cannot_skip_its_way_green():
    """`aura-desktop/tests_e2e` ran in no gate until T-1440, and one of its tests was red for seven
    weeks while `docs/verification.md` cited it as proof. It runs in `make test` and in both
    pipelines' desktop jobs, each time with `AURA_E2E_REQUIRED=1`: without it the suite skips when
    its harness cannot be imported, and a job that skipped everything is green having run nothing.

    Does not cover: that the suite passes (the gates do that), or the switch itself
    (`aura-desktop/tests_e2e/test_support.py`)."""
    want = "AURA_E2E_REQUIRED=1 python -m pytest tests_e2e "
    needs = ("-r aura-core/requirements.txt", "-r testing/requirements.txt")

    job = _all_jobs()["test:desktop"]
    assert any(cwd == "aura-desktop" and cmd.startswith(want) for cwd, cmd in _gitlab_pairs(job)[0]), job["script"]
    assert any(all(n in line for n in needs) for line in _lines(job.get("before_script"))), job.get("before_script")

    steps = [(cwd, cmd) for wf, cwd, cmd in _workflow_pairs() if wf == "python.yml"]
    assert any(cwd == "aura-desktop" and cmd.startswith(want) for cwd, cmd in steps)
    desktop = yaml.safe_load((ROOT / ".github" / "workflows" / "python.yml").read_text())["jobs"]["desktop"]
    assert any(all(n in step.get("run", "") for n in needs) for step in desktop["steps"])

    makefile = (ROOT / "Makefile").read_text()
    recipe = makefile.split("\ntest-desktop-e2e:", 1)[1].split("\n\n", 1)[0]
    assert "AURA_E2E_REQUIRED=1 python3 -m pytest tests_e2e " in recipe, recipe
    prerequisites = re.search(r"^test-python:([^#\n]*)", makefile, re.M).group(1).split()
    assert "test-desktop-e2e" in prerequisites, prerequisites
