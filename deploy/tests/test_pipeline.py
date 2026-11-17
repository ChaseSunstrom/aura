"""The pipeline file, checked the way gitlab-runner will run it (the guard the retired
pipeline's contract test provided, carried over for the new one).
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import json
import yaml

ROOT = Path(__file__).resolve().parents[2]
CI = ROOT / ".gitlab-ci.yml"
STAGES = ["test", "build", "integration", "deploy"]
REQUIRED_JOBS = {"test:lint", "test:plugins", "test:adapter", "test:desktop", "test:config-render", "test:plugin-discovery", "test:web",
                 "build:jarvis", "build:adapter", "build:web", "integration:smoke", "deploy:production"}


def _doc() -> dict:
    return yaml.safe_load(CI.read_text())


def _resolve(doc: dict, job: dict) -> dict:
    """Merge `extends` templates, one name or a list: later parents win, the job last, and
    `variables` merge key by key, as the runner does it."""
    bases = job.get("extends")
    if not bases:
        return job
    merged: dict = {}
    for base in [bases] if isinstance(bases, str) else bases:
        parent = _resolve(doc, doc[base])
        merged = {**merged, **parent, "variables": {**(merged.get("variables") or {}), **(parent.get("variables") or {})}}
    own = {k: v for k, v in job.items() if k != "extends"}
    out = {**merged, **own, "variables": {**(merged.get("variables") or {}), **(own.get("variables") or {})}}
    if not out["variables"]:
        del out["variables"]
    return out


def _jobs(doc: dict) -> dict:
    out = {}
    for k, v in doc.items():
        if not isinstance(v, dict) or k.startswith(".") or k in ("variables", "default", "workflow"):
            continue
        job = _resolve(doc, v)
        if "stage" in job:
            out[k] = job
    return out


def _all_jobs() -> dict:
    """The main file's jobs plus every included file's, templates resolved across files."""
    merged = {}
    for part in sorted((ROOT / ".gitlab" / "ci").glob("*.yml")):
        merged.update(yaml.safe_load(part.read_text()))
    merged.update(_doc())
    return _jobs(merged)


def test_stages_and_required_jobs():
    doc = _doc()
    assert doc["stages"] == STAGES
    jobs = _jobs(doc)
    assert REQUIRED_JOBS <= set(jobs)
    included = set(_all_jobs())
    for name, job in jobs.items():
        assert job["stage"] in STAGES, name
        for need in job.get("needs", []):
            # `{job, optional}` is GitLab's shape for a need on a job that may
            # not exist in this pipeline (build:cosyvoice, built on its own tree).
            need = need["job"] if isinstance(need, dict) else need
            assert need in included, f"{name} needs unknown job {need}"



def test_the_hud_layout_case_table_is_checked_for_drift():
    """The split tree exists in Python (the plugin) and TypeScript (the window manager), bound
    by a generated case table. Nothing but this check stops the two drifting apart between a
    change and someone noticing it in a drag gesture."""
    q = yaml.safe_load((ROOT / ".gitlab" / "ci" / "quality-python.yml").read_text())
    assert "scripts/gen-hud-layout-cases.py --check" in "\n".join(q["schema-sync"]["script"])
    assert (ROOT / "scripts" / "gen-hud-layout-cases.py").exists()
    # The cases live inside the one layout contract, not in a sibling file: two tables about
    # the same operations are the drift this test exists to prevent. Assert the block, not the
    # filename, so a fold like b89a5ca cannot pass while the generator writes somewhere else.
    contract = ROOT / "tests" / "contracts" / "hud-layout.json"
    assert contract.exists()
    assert "op_cases" in json.loads(contract.read_text(encoding="utf-8"))


def test_the_extraction_image_is_built_before_anything_runs_the_stack():
    """deploy/docker-compose.yml runs ${AURA_REGISTRY}/jarvis-extract, so a pipeline that
    does not build that image brings up a stack whose pull fails — the smoke and the
    benchmark both wait on it. The job builds from the repo root because its Dockerfile
    copies deploy/extract/."""
    ci = yaml.safe_load(CI.read_text())
    job = ci["build:extract"]
    assert job["variables"]["SVC"] == "jarvis-extract"
    assert job["variables"]["DOCKERFILE"] == "deploy/extract/Dockerfile"
    assert job["variables"]["CONTEXT"] == "."
    assert "build:extract" in ci["integration:smoke"]["needs"]
    bench = yaml.safe_load((ROOT / ".gitlab" / "ci" / "bench.yml").read_text())
    for name in ("bench:mr", "bench:nightly"):
        assert "build:extract" in bench[name]["needs"], name

def _blocks(doc: dict):
    for name, job in doc.items():
        if not isinstance(job, dict):
            continue
        for key in ("before_script", "script", "after_script"):
            for line in job.get(key, []) or []:
                yield name, key, line


def test_every_shell_block_parses_under_bash():
    for name, key, line in _blocks(_doc()):
        r = subprocess.run(["bash", "-n"], input=line, text=True, capture_output=True)
        assert r.returncode == 0, f"{name}.{key}: {r.stderr}\n{line}"


def test_deploy_is_automatic_on_main_and_the_overhaul_branch_and_manual_elsewhere():
    job = _doc()["deploy:production"]
    rules = job["rules"]
    assert rules[0]["if"] == "$CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH" and rules[0]["when"] == "on_success"
    auto = {r["if"] for r in rules if r["when"] == "on_success"}
    # Production runs the overhaul branch while it is verified (the merge waits on
    # that), so it ships on green; any other branch still needs a hand on the button.
    # The branch's pipelines are merge-request pipelines while its MR is open, which
    # carry no CI_COMMIT_BRANCH: without the MR rule no pipeline of it had a deploy job
    # (382-393), and it matches that one source branch, not any merge request.
    mr = '$CI_PIPELINE_SOURCE == "merge_request_event" && $CI_MERGE_REQUEST_SOURCE_BRANCH_NAME == "claude/jarvis-overhaul"'
    assert auto == {"$CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH", '$CI_COMMIT_BRANCH == "claude/jarvis-overhaul"', mr}
    assert rules[-1] == {"if": "$CI_COMMIT_BRANCH", "when": "manual"}
    assert job["environment"]["name"] == "production"
    assert job.get("tags") == ["jarvis"], "the deploy hop over the tailnet only works from the host runner"


def test_builds_push_sha_tags_with_registry_cache_and_a_cve_gate():
    doc = _doc()
    build = doc[".buildx"]
    script = "\n".join(build["script"])
    assert "--cache-from type=registry" in script and "--cache-to type=registry" in script
    assert "syft" in script and "grype" in script and "--fail-on critical" in script
    assert "$CI_COMMIT_SHORT_SHA" in script and "buildx create" in script
    # Docker-needing jobs run on the `jarvis` runner (socket mounted, host networking); the
    # owner's shared runners carry a dind service that never gets healthy (pipeline 8).
    assert build["extends"] == ".docker"
    dk = doc[".docker"]
    assert dk["tags"] == ["jarvis"] and dk["variables"]["DOCKER_HOST"] == "unix:///var/run/docker.sock"
    assert "services" not in dk and "services" not in build
    assert "--oci-worker-net=host" in script, "RUN steps must use the host network: the tailnet MTU is 1200"
    text = CI.read_text()
    assert "http = true" not in text and "INSECURE" not in text, "the registry is HTTPS; an http/insecure setting turns every push into a 400"
    assert "docker.sock" not in script, "syft reads the registry, grype reads stdin: no socket mounts in the scan"
    smoke = doc["integration:smoke"]
    assert smoke["extends"] == ".docker"
    assert smoke["variables"]["CI_STACK_IP"] == "172.17.0.1", "the smoke stack must stay off loopback and off the Tailscale address"
    assert "$CI_STACK_IP" in "\n".join(smoke["script"]) and "127.0.0.1:86" not in "\n".join(smoke["script"])
    after = "\n".join(smoke["after_script"])
    assert "$CI_PROJECT_DIR/deploy" in after and "down -v" in after, "teardown must not depend on the shell's cwd"
    assert "name=^jarvis-ci-" in "\n".join(smoke["script"]), "stale CI stacks are swept; the live stack never matches"


def test_no_secret_literals_in_the_pipeline():
    text = CI.read_text()
    for needle in ("glpat-", "glrt-", "BEGIN OPENSSH", "password="):
        assert needle not in text


def test_kotlin_lint_job_runs_the_defect_only_android_gate():
    """The Android lint gate (V2_PLAN D29) is included once and runs ktlint +
    detekt + lintDebug on the host runner, blocking. The OWASP dependency scan
    is present but skips itself unless NVD_API_KEY is set (the owner has none)."""
    ci = CI.read_text()
    assert ".gitlab/ci/quality-android.yml" in ci, "quality-android.yml must be included"

    q = yaml.safe_load((ROOT / ".gitlab" / "ci" / "quality-android.yml").read_text())
    lint = q["kotlin-lint"]
    assert lint["stage"] == "test"
    assert lint["allow_failure"] is False, "the lint gate must block"
    assert lint["tags"] == ["jarvis"], "needs the host runner's egress + toolchain cache"
    assert "android-app/tools/lint.sh" in "\n".join(lint["script"])
    # The gate runs all three checks through lint.sh; the CLIs are pinned.
    lintsh = (ROOT / "android-app" / "tools" / "lint.sh").read_text()
    assert "ktlint" in lintsh and "detekt" in lintsh and ":app:lintDebug" in lintsh

    deps = q["android-deps"]
    deps_script = "\n".join(deps["script"])
    assert "NVD_API_KEY" in deps_script and "SKIP:" in deps_script, (
        "the OWASP job must skip visibly without an NVD key"
    )


def test_python_quality_gate_runs_the_defect_only_python_checks():
    """The Python quality gate (V2_PLAN §3.6) is included once and runs four blocking jobs —
    mypy, vulture, pylint duplicate-code, pip-audit — on the 3.12 image. The plugins are
    type-checked per package (scripts/mypy-plugins.sh), never as a hyphen-named directory that
    would FATAL to a false zero. Tools are pinned so a newer release cannot flip a green gate red."""
    ci = CI.read_text()
    assert ".gitlab/ci/quality-python.yml" in ci, "quality-python.yml must be included"

    q = yaml.safe_load((ROOT / ".gitlab" / "ci" / "quality-python.yml").read_text())
    # Resolved against the main file too: a job here may extend one of its templates (.skip-guard).
    both = {**_doc(), **q}
    q = {k: _resolve(both, v) for k, v in q.items() if isinstance(v, dict) and not k.startswith(".")}
    for name in ("py-mypy", "py-dead", "py-dup", "py-deps"):
        assert name in q, f"{name} job missing"
        assert q[name]["stage"] == "test", name
        assert q[name]["allow_failure"] is False, f"{name} must block"

    mypy_script = "\n".join(q["py-mypy"]["script"])
    assert "--config-file mypy.ini" in mypy_script
    assert "scripts/mypy-plugins.sh" in mypy_script, "plugins must be checked per package, not as a dir"
    assert "mypy==2.3.1" in "\n".join(q["py-mypy"]["before_script"]), "pin mypy: warn_unused_ignores is version-sensitive"
    assert "--min-confidence 80" in "\n".join(q["py-dead"]["script"]) and "vulture-allowlist.py" in "\n".join(q["py-dead"]["script"])
    assert "--rcfile=pylintrc" in "\n".join(q["py-dup"]["script"])
    assert "pip-audit" in "\n".join(q["py-deps"]["script"])


def test_docs_gate_regenerates_the_references_and_checks_every_link():
    """The docs gate (V2_PLAN §3.4) is included once and runs two blocking jobs: docs-sync
    (make docs, then git diff on the generated pages — a stale reference page fails) and
    docs-links (scripts/check-links.py — a broken relative link or anchor fails). Both read
    the tree only; neither may reach the API server or the deployed stack, so the generator
    stays reproducible on the shared runner."""
    ci = CI.read_text()
    assert ".gitlab/ci/quality-docs.yml" in ci, "quality-docs.yml must be included"

    q = yaml.safe_load((ROOT / ".gitlab" / "ci" / "quality-docs.yml").read_text())
    q = {k: _resolve(q, v) for k, v in q.items() if isinstance(v, dict) and not k.startswith(".")}
    for name in ("docs-sync", "docs-links"):
        assert name in q, f"{name} job missing"
        assert q[name]["stage"] == "test", name
        assert q[name]["allow_failure"] is False, f"{name} must block"

    sync = "\n".join(q["docs-sync"]["script"])
    assert "make docs" in sync and "git diff --exit-code" in sync, "regenerate, then fail on drift"
    assert "docs/reference" in sync and "CAPABILITIES.md" in sync
    assert "scripts/check-links.py" in "\n".join(q["docs-links"]["script"])
    # The Makefile target the job mirrors must exist and do the same two things.
    mk = (ROOT / "Makefile").read_text()
    assert "\ndocs-check:" in mk and "scripts/check-links.py" in mk and "scripts/gen-docs.py" in mk


def test_every_generated_page_is_in_both_stale_copy_lists():
    """`make docs-check` and CI's docs-sync job each diff a hand-kept list of pages, so a page
    the generator writes but a list forgets goes stale unseen: approval-matrix.md was missing
    from the Makefile's (T-1397), and skills.md from both and two more from CI's (T-1422). Both
    lists are bound to the generator's own table here. Does NOT cover whether a listed page is
    current: that is what the two diffs are for."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("gen_docs_under_test", ROOT / "scripts" / "gen-docs.py")
    assert spec and spec.loader
    gen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gen)
    written = {p.relative_to(ROOT).as_posix() for p in gen.build_all()}

    mk = (ROOT / "Makefile").read_text()
    block = re.search(r"^DOCS_GENERATED :=((?:.*\\\n)*.*)$", mk, re.M)
    assert block, "the Makefile no longer defines DOCS_GENERATED"
    listed = set(block.group(1).replace("\\\n", " ").split())
    assert listed == written, (f"generated but not diffed by make docs-check: {sorted(written - listed)}; "
                               f"listed but no longer generated: {sorted(listed - written)}")

    q = yaml.safe_load((ROOT / ".gitlab" / "ci" / "quality-docs.yml").read_text())
    q = {k: _resolve(q, v) for k, v in q.items() if isinstance(v, dict) and not k.startswith(".")}
    diffs = [line for line in q["docs-sync"]["script"] if "git diff --exit-code" in line]
    assert len(diffs) == 1, "docs-sync has one diff line, over the generated pages"
    in_ci = set(diffs[0].split("--", 3)[-1].split())
    assert in_ci == written, (f"generated but not diffed by CI's docs-sync: {sorted(written - in_ci)}; "
                              f"in CI's list but no longer generated: {sorted(in_ci - written)}")


def test_the_links_check_fails_a_register_row_whose_proof_is_gone(tmp_path):
    """docs-links also reads the claims register: a deleted script, a renamed test and a
    removed make target each fail it (T-0471: 162 rows cited proofs that no longer ran).
    A path through a name the root .gitignore ignores (``.storage/``, ``.env``) is run output
    or local state a checkout never has, so it passes (T-0496: CI's clean tree failed it).
    A ``./`` prefix is read from the root. A bare gate name (``m85-….sh``) is a
    scripts/verify/ gate unless its line says it was retired or deleted (T-0484). A live
    test and a websocket command that looks like a path pass."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("check_links", ROOT / "scripts" / "check-links.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    reg = tmp_path / "verification.md"
    reg.write_text(
        "| a | Automated | `scripts/verify/m99-gone.sh` |\n"
        "| b | Automated | `deploy/tests/test_pipeline.py::test_no_such_test` |\n"
        "| c | Automated | `make no-such-target` |\n"
        "| d | Automated | `deploy/tests/test_pipeline.py::test_docs_gate_regenerates_the_references_and_checks_every_link` |\n"
        "| e | Automated | `jarvis/test/catalog_mode` answers |\n"
        "| f | Automated | `./scripts/verify/m98-gone.sh` |\n"
        "| g | **Built** | `m85-gone.sh`; a test |\n"
        "| h | Retired with its script | `m90-gone.sh` asserted this |\n"
        "| i | **Built** | `m87-gone.sh` (deleted in 1234abcd) |\n"
        "| j | Manual | lands in `aura-core/config/.storage/gone.json` |\n"
        "| k | Manual | the token in `aura-core/nowhere/.env` |\n"
    )
    mod.REGISTER = str(reg)  # ROOT / an absolute path is that path
    found = [line.split(": ", 1)[1] for line in mod._register_drift()]
    assert found == [
        "missing proof -> scripts/verify/m99-gone.sh",
        "missing test -> deploy/tests/test_pipeline.py::test_no_such_test",
        "no make target -> no-such-target",
        "missing proof -> scripts/verify/m98-gone.sh",
        "missing gate -> m85-gone.sh",
    ]


def test_the_links_check_fails_a_register_row_citing_a_test_file_or_suite_that_is_gone(tmp_path):
    """A row is Automated on the strength of its test, and a test cited by bare name
    (``test_orchestrator.py``, ``home.spec.ts``) named no tree to look in, so deleting the
    file left the row vouching: 73 citations of 25 deleted files, and four of the suites
    of trees retired in September, read Automated until T-1441. A bare test-file name must
    now exist somewhere in the tree, and a ``tests`` path must have its top folder.

    Passes on purpose: a live bare name, with or without ``::test`` (the test's own name is
    not looked up for a bare file); a line that says the proof was retired or deleted; and
    a shortened path from a deeper tree (``routes/x/server.test.ts``), whose first folder
    no top-level tree has and which is not a ``tests`` path."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("check_links", ROOT / "scripts" / "check-links.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    reg = tmp_path / "verification.md"
    reg.write_text(
        "| a | Automated | `test_no_such_suite_anywhere.py` |\n"
        "| b | Automated | `no-such-flow.spec.ts`, `no-such-unit.test.ts` |\n"
        "| c | Automated | `test_pipeline.py`, `test_pipeline.py::test_any_name` |\n"
        "| d | Automated | `test_gone_as_well.py::test_a_thing` |\n"
        "| e | Automated | `python3 -m pytest gone-tree/tests -q` — 337 tests |\n"
        "| f | Automated | `gone-tree/tests/test_api.py::test_bearer_alone` |\n"
        "| g | Retired with its test | `test_gone_too.py` (deleted in 1234abcd) |\n"
        "| h | Automated | `routes/api/console/server.test.ts` |\n"
        "| i | Automated | `NoSuchThingTest.kt`, `no_such_mirror_test.py` |\n"
    )
    mod.REGISTER = str(reg)  # ROOT / an absolute path is that path
    found = [line.split(": ", 1)[1] for line in mod._register_drift()]
    assert found == [
        "missing test file -> test_no_such_suite_anywhere.py",
        "missing test file -> no-such-flow.spec.ts",
        "missing test file -> no-such-unit.test.ts",
        "missing test file -> test_gone_as_well.py",
        "missing proof -> gone-tree/tests",
        "missing proof -> gone-tree/tests/test_api.py",
        "missing test file -> NoSuchThingTest.kt",
        "missing test file -> no_such_mirror_test.py",
    ]


def test_the_links_check_fails_a_documented_command_whose_folder_is_gone(tmp_path):
    """A rename that misses a runbook leaves a command nobody can run: T-0960 renamed five folders
    and the runbooks' ``cd`` lines, two ``for`` loops and an install hint still named the old
    ones a day later (T-1434). docs-links now reads the commands a page documents: a ``cd``
    target, each folder of a ``for`` loop whose body enters its variable, and a path given to
    ``pip install -e`` or ``-r`` must exist, from the repository root, from the page's own folder
    or from the folder an earlier ``cd`` in the same block entered (T-1436).

    Passes on purpose: a variable or a glob, an absolute path outside the documented checkout, a
    folder the same command makes first, a line that says the thing was retired, a path through
    a name the root .gitignore ignores, and ``cd`` in prose outside backticks or a fence."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("check_links", ROOT / "scripts" / "check-links.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    root = tmp_path
    (root / ".gitignore").write_text(".venv/\nnode_modules\n")
    (root / "aura-core" / "tests").mkdir(parents=True)
    (root / "aura-core" / "requirements.txt").write_text("")
    (root / "aura-desktop").mkdir()
    (root / "docs" / "runbooks" / "assets").mkdir(parents=True)
    page = root / "docs" / "runbooks" / "house.md"
    page.write_text(
        "Run `cd aura-core && make up`, never `cd jarvis-core && make up`.\n"                    # 1: live, gone
        "From the checkout: `cd /opt/jarvis/jarvis-core`; logs: `cd /var/log/house`.\n"          # 2: gone, skipped
        "You can cd nowhere-in-prose and nothing is read.\n"                                    # 3
        "```bash\n"                                                                              # 4
        "cd aura-core\n"                                                                         # 5
        "cd tests && python3 -m pytest -q\n"                                                     # 6: under line 5's folder
        "for d in aura-core aura-desktop jarvis-adapter; do\n"                                   # 7: third is gone
        "    ( cd \"$d\" && python3 -m pytest tests -q )\n"                                      # 8: a variable
        "done\n"                                                                                 # 9
        "for f in *.py gone-but-never-entered; do python3 \"$f\"; done\n"                        # 10: body does not cd
        "pip install -r aura-core/requirements.txt -e aura-desktop -e jarvis-schema\n"           # 11: third is gone
        "pip install -e . -r \"$REQS\" 'aura-desktop[dev]'\n"                                    # 12
        "mkdir -p out && cd out\n"                                                               # 13: made first
        "git clone https://example.test/x.git && cd x\n"                                         # 14: made first
        "cd .venv/bin && cd ~/elsewhere && cd \"$HOME\" && cd -\n"                               # 15: ignored, not plain
        "cd assets\n"                                                                            # 16: beside the page
        "```\n"                                                                                  # 17
        "| old suite | Retired with its tree | `cd jarvis-browser && python3 -m pytest` |\n"     # 18: says retired
        "`cd tests`\n"                                                                           # 19: no cd before it here
    )
    found = [line.split(": ", 1) for line in mod._command_drift([page], root)]
    assert [(where.rsplit(":", 1)[1], what) for where, what in found] == [
        ("1", "missing folder -> cd jarvis-core"),
        ("2", "missing folder -> cd /opt/jarvis/jarvis-core"),
        ("7", "missing folder -> for d in ... jarvis-adapter"),
        ("11", "missing path -> pip install jarvis-schema"),
        ("19", "missing folder -> cd tests"),
    ]
    assert all(where.startswith("docs/runbooks/house.md:") for where, _ in found)


def test_the_tree_documents_no_command_into_a_folder_it_does_not_have():
    """The rule above, on this tree: every page outside the archive."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("check_links", ROOT / "scripts" / "check-links.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod._command_drift() == []


def test_web_quality_gate_runs_the_defect_only_web_checks():
    """The web quality gate (V2_PLAN §3.6) is included once and runs three blocking jobs —
    web-lint (svelte-check as the type gate, eslint, knip, tsc over the desktop app's pages), web-deps
    (the OSV audit over both lockfiles: advisories in what ships) and dup-all (`make dup`, the same command
    as locally so the ratio cannot drift). Tool versions are the lockfiles' and the Makefile's
    jscpd pin, so a newer release cannot flip a green gate red."""
    ci = CI.read_text()
    assert ".gitlab/ci/quality-web.yml" in ci, "quality-web.yml must be included"

    q = yaml.safe_load((ROOT / ".gitlab" / "ci" / "quality-web.yml").read_text())
    q = {k: _resolve(q, v) for k, v in q.items() if isinstance(v, dict) and not k.startswith(".")}
    for name in ("web-lint", "web-deps", "dup-all"):
        assert name in q, f"{name} job missing"
        assert q[name]["stage"] == "test", name
        assert q[name]["allow_failure"] is False, f"{name} must block"

    lint = "\n".join(q["web-lint"]["script"])
    for needle in ("npm ci", "npm run check", "npx eslint .", "npx knip", "npm run typecheck"):
        assert needle in lint, f"web-lint must run {needle}"
    deps = "\n".join(q["web-deps"]["script"])
    # Both lockfiles, one command. Asserting the script rather than the tool name would let a
    # future edit drop a tree and stay green, which is how the desktop shell went unaudited once.
    assert "scripts/verify/dep-audit.py" in deps, "the audit must be the OSV scanner"
    for tree in ("jarvis-web/package-lock.json", "jarvis-desktop-app/package-lock.json"):
        assert tree in deps, f"web-deps must audit {tree}"
    assert "npm audit" not in deps, (
        "one advisory source: npm audit beside the OSV scanner is a second table that can disagree"
    )
    assert "make dup" in "\n".join(q["dup-all"]["script"]), "one command locally and in CI"
    makefile = (ROOT / "Makefile").read_text()
    assert "npx --yes jscpd@" in makefile, "jscpd must be pinned in the Makefile"
    assert "--config .jscpd.json" in makefile


def test_the_design_tokens_are_generated_and_checked_in_ci():
    """The design system's contract (V2 Phase 2b, GLASS_HUD §13): design/tokens.json is the one
    place a value is typed and every consumer is generated from it. A generated file edited by
    hand, or a token changed without `make tokens`, is caught by `design/build.py --check`; the
    source's shape by `tokens_check.py`; a value typed in app code by the token lint. The job is
    stdlib Python on a slim image so it cannot be skipped for want of an npm install."""
    q = yaml.safe_load((ROOT / ".gitlab" / "ci" / "quality-web.yml").read_text())
    q = {k: _resolve(q, v) for k, v in q.items() if isinstance(v, dict) and not k.startswith(".")}
    assert "tokens-sync" in q, "tokens-sync job missing from quality-web.yml"
    job = q["tokens-sync"]
    assert job["stage"] == "test" and job["allow_failure"] is False, "tokens-sync must block"
    script = "\n".join(job["script"])
    # ds_boundary (T-0603): console screens outside $lib/ds may not grow non-ds imports.
    for needle in ("design/build.py --check", "scripts/verify/tokens_check.py", "scripts/verify/token_lint.py",
                   "scripts/verify/ds_boundary.py"):
        assert needle in script, f"tokens-sync must run {needle}"
    makefile = (ROOT / "Makefile").read_text()
    assert "\ntokens-check:" in makefile and "design/build.py --check" in makefile, (
        "the Makefile target the job mirrors must exist"
    )
    assert "\ntokens:" in makefile and "design/build.py" in makefile


def test_the_icons_check_runs_in_the_tokens_job_and_blocks():
    """`make icons-check` (scripts/gen-icons.py --check) fails when a committed icon is not what the
    core's geometry draws. It ran in no CI job, so a drift (T-0748's review found the generator
    broken by a contract change) reached a merge unnoticed. It rides in tokens-sync: that job runs on
    every pipeline, on a slim image that has the stdlib the generator needs and nothing more."""
    q = yaml.safe_load((ROOT / ".gitlab" / "ci" / "quality-web.yml").read_text())
    job = q["tokens-sync"]
    assert job["allow_failure"] is False, "tokens-sync must block"
    assert "rules" not in job or not any("changes" in r for r in job["rules"]), (
        "tokens-sync must not be limited by changes: rules, or an icon drift on an unrelated tree slips through"
    )
    assert "python3 scripts/gen-icons.py --check" in job["script"], (
        "tokens-sync must run the icons check: a stale committed icon fails the pipeline"
    )


def test_the_safety_drills_run_and_block():
    """Part 4 asks for a drill per admin surface, and a drill nobody runs is a
    paragraph. The job runs the six and the coverage check, and blocks."""
    q = yaml.safe_load((ROOT / ".gitlab" / "ci" / "quality-python.yml").read_text())
    job = q["safety-drills"]
    assert job["allow_failure"] is False
    script = "\n".join(job["script"])
    for step in ("scripts/verify/safety-drills.py", "scripts/verify/hermes-coverage.py",
                 "deploy/tests/test_hermes_coverage.py"):
        assert step in script, step
    assert (ROOT / "scripts" / "verify" / "safety-drills.py").exists()
    assert (ROOT / "tests" / "contracts" / "hermes-coverage.json").exists()


def test_hud_gates_run_before_the_migration_and_block():
    """The HUD a11y + performance gates are included once and run four blocking web jobs —
    hud-a11y (axe, keyboard, rendered contrast, preferences), hud-frametime (the trace over the
    v2 shell's scenes, card, sheets, a screen and a resize against the budget file),
    hud-overlap (the shell's and the car's geometry; the room's panel-rect sweep went with the
    room in T-0611) and hud-size (bundle + APK against the size budget) — plus the
    device macrobenchmark, which must never be schedulable on a runner that does not exist: it
    is gated on ANDROID_DEVICE_RUNNER and tagged `android-device`, or it would sit pending and
    hold every pipeline open."""
    ci = CI.read_text()
    assert ".gitlab/ci/quality-hud.yml" in ci, "quality-hud.yml must be included"

    q = yaml.safe_load((ROOT / ".gitlab" / "ci" / "quality-hud.yml").read_text())
    q = {k: _resolve(q, v) for k, v in q.items() if isinstance(v, dict) and not k.startswith(".")}
    for name in ("hud-a11y", "hud-frametime", "hud-overlap", "hud-size"):
        assert name in q, f"{name} job missing"
        assert q[name]["allow_failure"] is False, f"{name} must block"
        assert {"job": "web:build", "artifacts": True} in q[name]["needs"], f"{name} tests the built app"
    assert q["hud-a11y"]["stage"] == "test"
    # The frame gate runs in the integration stage, after the image builds (needs: [] starts
    # them at once, and beside them its p95 swung 16-21 ms, T-0320); it still blocks the ship
    # because integration:smoke, which the deploy waits on, needs it.
    assert q["hud-frametime"]["stage"] == "integration"
    assert any(n.get("job") == "build:jarvis" for n in q["hud-frametime"]["needs"] if isinstance(n, dict))
    smoke = yaml.safe_load(CI.read_text())["integration:smoke"]
    assert any((n.get("job") if isinstance(n, dict) else n) == "hud-frametime" for n in smoke["needs"])
    assert q["hud-overlap"]["stage"] == "test"
    a11y = "\n".join(q["hud-a11y"]["script"])
    for spec in ("a11y-axe", "a11y-keyboard", "a11y-contrast", "a11y-preferences"):
        assert f"e2e/{spec}.spec.ts" in a11y, f"hud-a11y must run {spec}"
        assert (ROOT / "jarvis-web" / "e2e" / f"{spec}.spec.ts").exists(), spec
    assert "scripts/verify/hud-contrast.py" in a11y, "the Python second opinion on the samples"
    frametime = "\n".join(q["hud-frametime"]["script"])
    assert "e2e/hud-frametime.spec.ts" in frametime
    assert (ROOT / "scripts" / "verify" / "hud-frametime-budget.json").exists()
    overlap = "\n".join(q["hud-overlap"]["script"])
    for spec in ("v2-shell", "v2-car"):
        assert f"e2e/{spec}.spec.ts" in overlap, f"hud-overlap must run {spec}"
        assert (ROOT / "jarvis-web" / "e2e" / f"{spec}.spec.ts").exists(), spec
    # hud-size follows apk:build (optional: it only builds on Android changes) and measures
    # the APK when it is there, the bundle always.
    assert q["hud-size"]["stage"] == "build"
    assert {"job": "apk:build", "optional": True} in q["hud-size"]["needs"]
    size = "\n".join(q["hud-size"]["script"])
    assert "scripts/verify/hud-size.mjs" in size and "--apk=" in size
    assert (ROOT / "scripts" / "verify" / "hud-size-budget.json").exists()
    # The axe dependency is pinned exact: axe adds rules between minors.
    pkg = (ROOT / "jarvis-web" / "package.json").read_text()
    assert '"@axe-core/playwright": "4.' in pkg and '"@axe-core/playwright": "^' not in pkg

    bench = q["android-macrobench"]
    assert bench["tags"] == ["android-device"]
    assert bench["rules"] == [{"if": '$ANDROID_DEVICE_RUNNER == "true"'}], "device-only, never pending on the shared runners"
    assert ":macrobenchmark:connectedBenchmarkAndroidTest" in "\n".join(bench["script"])
    settings = (ROOT / "android-app" / "settings.gradle.kts").read_text()
    assert 'include(":macrobenchmark")' in settings
    # The Makefile mirror runs the same four web pieces.
    mk = (ROOT / "Makefile").read_text()
    assert "\ngate-hud:" in mk
    for needle in ("e2e/a11y-axe.spec.ts", "e2e/hud-frametime.spec.ts", "scripts/verify/hud-size.mjs", "scripts/verify/hud-contrast.py"):
        assert needle in mk, f"gate-hud must run {needle}"


def test_every_console_spec_runs_in_a_pipeline_job():
    """A Playwright spec no job names is run by hand or not at all: four went stale against the
    map that way (2026-09-09), hud-behaviour once listed two specs that did not exist, and the
    v2 shell's own specs were in no job until T-0611. Every e2e spec is named by a quality-hud
    job, and every spec a job names exists. Not covered: whether the job's run collected any
    test from it (a spec that skips itself everywhere still passes)."""
    q = yaml.safe_load((ROOT / ".gitlab" / "ci" / "quality-hud.yml").read_text())
    named = set()
    for job in q.values():
        if isinstance(job, dict):
            named |= set(re.findall(r"e2e/([\w.-]+\.spec\.ts)", "\n".join(map(str, job.get("script", [])))))
    on_disk = {p.name for p in (ROOT / "jarvis-web" / "e2e").glob("*.spec.ts")}
    # The two that record instead of asserting, each only when its variable is set: the sweep
    # rewrites docs/screens/v2/console/ with V2_SCREENS=1 (`make sweep`), and the README's films
    # are re-made with README_CLIPS (`make readme-clips`, about an hour); a bare run skips both.
    exempt = {"v2-sweep.spec.ts", "readme-clips.spec.ts"}
    assert on_disk - exempt <= named, f"specs in no job: {sorted(on_disk - exempt - named)}"
    assert named <= on_disk, f"jobs name specs that do not exist: {sorted(named - on_disk)}"


_ASYNC_TEST_RE = re.compile(r"pytest\.mark\.(?:asyncio|anyio)|^\s*async def test_", re.M)
_ASYNC_PLUGINS = ("pytest-asyncio", "anyio", "pytest-trio")


def _suites_a_job_runs(job: dict) -> list[Path]:
    """The folders and files a job hands to pytest, following its `cd`s from one line to the
    next the way the runner's one shell does. Does not follow a Makefile target or a script."""
    here = ROOT
    out: list[Path] = []
    for line in job.get("script", []) or []:
        for part in re.split(r"&&|\|\||;", str(line)):
            words = part.split()
            if words[:1] == ["cd"] and len(words) > 1:
                here = (here / words[1]).resolve()
            elif "pytest" in words:
                for word in words[words.index("pytest") + 1:]:
                    target = here / word
                    if not word.startswith("-") and "=" not in word and target.exists():
                        out.append(target)
    return out


def _installed_by(job: dict) -> str:
    """A job's install lines, with each requirements file it names read in."""
    text = "\n".join(str(line) for key in ("before_script", "script") for line in job.get(key, []) or [])
    for name in re.findall(r"-r\s+(\S+)", text):
        if (ROOT / name).is_file():
            text += "\n" + (ROOT / name).read_text()
    return text


def test_a_job_that_runs_an_async_test_installs_a_plugin_that_can_run_it():
    """pytest fails an `async def` test when no async plugin is installed. Every local venv has
    one, so a suite's first async test is green on every desk and red in the one job whose
    install line never needed the plugin: the adapter's did exactly that, and the pipeline
    stopped before the smoke with nothing local to show for it (T-1442).

    Not covered: a test that needs a binary or a package for any other reason."""
    read = 0
    for name, job in _all_jobs().items():
        files = [f for suite in _suites_a_job_runs(job)
                 for f in ([suite] if suite.is_file() else sorted(suite.rglob("test_*.py")))]
        read += len(files)
        needing = [f.relative_to(ROOT).as_posix() for f in files if _ASYNC_TEST_RE.search(f.read_text())]
        if needing:
            assert any(p in _installed_by(job) for p in _ASYNC_PLUGINS), (
                f"{name} runs {needing[0]} (and {len(needing) - 1} more) but installs no async pytest plugin")
    assert read > 150, "the walk found too few test files to mean anything"


def test_the_adapters_jobs_install_what_its_image_installs():
    """The adapter's image installs `aura-adapter/requirements.txt`, all exact pins. Its job
    installed the same packages unpinned from the shared template, so the suite ran against a
    web framework thirty releases newer than the one production runs (T-1460). The job now
    installs the image's file before the suite, and so does GitHub's; the suite's own
    `test_image_versions.py` is what fails if any place it runs differs.

    Not covered: that the install succeeds (a pin with no wheel for the job's Python shows in
    the job, not here), and the dependencies the file does not pin."""
    wanted = "-r aura-adapter/requirements.txt"
    dockerfile = (ROOT / "aura-adapter" / "Dockerfile").read_text()
    assert "COPY aura-adapter/requirements.txt" in dockerfile and "-r requirements.txt" in dockerfile, (
        "the adapter image no longer installs aura-adapter/requirements.txt: this test pins the wrong file")

    script = "\n".join(str(line) for line in _all_jobs()["test:adapter"]["script"])
    assert wanted in script, "test:adapter does not install the adapter image's requirements file"
    assert script.index(wanted) < script.index("pytest"), "test:adapter installs the pins after the suite ran"

    github = yaml.safe_load((ROOT / ".github" / "workflows" / "python.yml").read_text())["jobs"]
    ran_by = {name: "\n".join(str(step.get("run", "")) for step in job.get("steps", []))
              for name, job in github.items()}
    adapters = {name: text for name, text in ran_by.items() if "cd aura-adapter && python -m pytest tests " in text}
    assert adapters, "no GitHub job runs the adapter's suite any more: the pin on it is void"
    for name, text in adapters.items():
        assert wanted in text and text.index(wanted) < text.index("cd aura-adapter && python -m pytest"), (
            f"GitHub's {name} job runs the adapter's suite without the image's requirements file")

    assert (ROOT / "aura-adapter" / "tests" / "test_image_versions.py").is_file(), (
        "the suite's own version check is gone, so a job that drifts is green again")


#: The package that provides each module a test imports-or-skips.
_SKIP_PACKAGES = {"docx": "python-docx", "openpyxl": "openpyxl", "pptx": "python-pptx", "PIL": "pillow",
                  "huggingface_hub": "huggingface_hub", "yaml": "pyyaml"}
#: Modules no install line can give a Python job, each with why its skip is right there.
_SKIP_EXEMPT = {"agent": "ships inside Hermes; no job in the stock Python image has it (T-1452)",
                "tools": "ships inside Hermes, as `agent` does (the memory tool's schema, T-1490)"}
#: Binaries a test looks up on PATH and skips without; apt's package has the same name.
_SKIP_TOOLS = ("jq",)
_IMPORTORSKIP_RE = re.compile(r"""importorskip\(\s*["']([\w.]+)["']""")


def _named(text: str, package: str) -> bool:
    return re.search(rf"(?<![\w-]){re.escape(package)}(?![\w-])", text, re.I) is not None


def test_a_job_installs_what_its_tests_would_otherwise_skip_on():
    """A test that imports-or-skips a package, or skips without a binary, is green in a job that
    lacks it: on 2026-11-15 `test:config-render` and `test:desktop` had skipped five tests that
    way in every pipeline, and only a run in their images with the skip report on showed it
    (docs/research/ci-skips-2026-11-15.md, T-1451). Every desk has the packages, so nothing
    local can show the difference; this reads it from the pipeline file and the test files.

    Not covered: a skip written any other way (`skipif` on an import inside a helper, a tool
    not in `_SKIP_TOOLS`, running as root, a missing Hermes), and whether the installed version
    is one the test passes on. A count in the image is still the only whole answer."""
    read, missing = 0, []
    for name, job in _all_jobs().items():
        files = [f for suite in _suites_a_job_runs(job)
                 for f in ([suite] if suite.is_file() else sorted(suite.rglob("test_*.py")))]
        read += len(files)
        installed = _installed_by(job)
        apt = "\n".join(ln for ln in installed.splitlines() if "apt-get install" in ln)
        for f in files:
            text = f.read_text()
            where = f.relative_to(ROOT).as_posix()
            for module in _IMPORTORSKIP_RE.findall(text):
                top = module.split(".")[0]
                if top in _SKIP_EXEMPT:
                    continue
                assert top in _SKIP_PACKAGES, (
                    f"{where} imports-or-skips {module}: name its package in _SKIP_PACKAGES, or record "
                    "in _SKIP_EXEMPT why no job can have it")
                if not _named(installed, _SKIP_PACKAGES[top]):
                    missing.append(f"{name} runs {where}, which skips without {_SKIP_PACKAGES[top]}")
            for tool in _SKIP_TOOLS:
                if any("which" in ln and f'"{tool}"' in ln for ln in text.splitlines()) and not _named(apt, tool):
                    missing.append(f"{name} runs {where}, which skips without {tool} on PATH")
    assert read > 150, "the walk found too few test files to mean anything"
    assert not missing, "tests green without being run:\n  " + "\n  ".join(sorted(set(missing)))


#: A skip line that names Hermes as what is missing. The opt-in live proofs name it too and are
#: skipped everywhere on purpose.
_HERMES_SKIP_RE = re.compile(r"(?:skip\(|skipif\(|importorskip\(|reason=)[^\n]*Hermes")


def _files_that_skip_without_hermes() -> list[str]:
    out = []
    for tree in ("hermes/plugins", "deploy/tests", "aura-adapter/tests", "scripts"):
        for f in sorted((ROOT / tree).rglob("test_*.py")):
            if f.resolve() == Path(__file__).resolve():
                continue        # the pattern above is such a line
            if any(_HERMES_SKIP_RE.search(ln) and "live proof" not in ln for ln in f.read_text().splitlines()):
                out.append(f.relative_to(ROOT).as_posix())
    return out


def test_the_tests_that_need_a_hermes_install_run_against_the_pinned_one():
    """Seventeen tests and one safety drill skip where Hermes is not installed, which is every
    Python job. They ran on a desk only, against whichever release that desk had: on
    2026-11-15 this one's was two releases behind the image production is built on, and in
    the pinned image one guard test failed on a private name that release had moved (T-1452,
    docs/research/ci-skips-2026-11-15.md). `test:hermes` runs them there and cannot go green
    by skipping.

    Not covered: that the files pass (the job is the proof, `scripts/ci-job.py test:hermes`
    runs it here), and a Hermes skip written without the word in its skip line."""
    doc = _doc()
    job = _all_jobs()["test:hermes"]
    assert job["image"] == doc["test:plugin-discovery"]["image"], "one image and one tag variable for both"
    assert "tags" not in job and "services" not in job and "needs" not in job, "the job tool must be able to run it"
    lines = "\n".join(str(ln) for ln in job["script"])
    need = _files_that_skip_without_hermes()
    assert len(need) >= 5, need
    for path in need:
        assert path in lines, f"{path} skips without Hermes and test:hermes does not run it"
    assert re.search(r"pytest[^\n]* -rs[^\n]*\|\s*tee\s+(\S+)", lines), "the skip report is kept"
    assert re.search(r"!\s*grep[^\n]*SKIPPED[^\n]*ermes", lines), "a skip that names Hermes fails the job"
    assert "set -o pipefail" in lines or "pipefail" in "\n".join(job.get("before_script", []) or []), (
        "without pipefail the tee's exit hides a failed pytest")
    drills = [ln for ln in job["script"] if "safety-drills.py" in str(ln)]
    assert drills and all("-eq 2" not in ln for ln in drills), "here a skipped drill (exit 2) is a failure"
    assert "HERMES_INSTALL_DIR" in str(job.get("variables", {})) + lines, "test_messages finds Hermes by it"


_ROOT_SKIP_RE = re.compile(r"geteuid\(\)\s*==\s*0")


def test_the_tests_that_skip_as_root_run_as_another_user():
    """A test that proves a file mode keeps another user out skips when it runs as root, and
    every job of the pipeline is root: on 2026-11-15 six such tests (the memory layers' private
    files among them) were green in every pipeline without being run (T-1454,
    docs/research/ci-skips-2026-11-15.md). `test:unprivileged` runs their files once more as
    `nobody` and cannot go green by skipping.

    Not covered: a root skip written without `geteuid() == 0` in the test file, and a suite
    outside the trees walked here."""
    job = _all_jobs()["test:unprivileged"]
    assert "tags" not in job and "services" not in job and "needs" not in job, "the job tool must be able to run it"
    lines = "\n".join(str(ln) for ln in job["script"])
    need = [f for tree in ("hermes/plugins", "deploy/tests", "aura-adapter/tests", "aura-desktop/tests",
                           "aura-voice/tests", "scripts")
            for f in sorted((ROOT / tree).rglob("test_*.py"))
            if f.resolve() != Path(__file__).resolve() and _ROOT_SKIP_RE.search(f.read_text())]
    assert len(need) >= 3, need
    for f in need:
        assert f.name in lines, f"{f.relative_to(ROOT)} skips as root and test:unprivileged does not run it"
    runs = [ln for ln in job["script"] if "pytest" in str(ln)]
    assert runs and all("su nobody" in str(ln) and "-rs" in str(ln) and "tee" in str(ln) for ln in runs), (
        "each pytest line runs as nobody and keeps its skip report")
    assert all("pipefail" in str(ln) for ln in runs), "without pipefail the tee's exit hides a failed pytest"
    assert re.search(r"!\s*grep[^\n]*SKIPPED[^\n]*root", lines), "a skip that names root fails the job"


_FFMPEG_SKIP_RE = re.compile(r"(?:skip\(|skipif\(|reason=)[^\n]*ff(?:mpeg|probe)")


def test_the_tests_that_need_ffmpeg_run_in_a_job_that_has_it():
    """The slim Python image has no ffmpeg, so on 2026-11-15 twelve tests of the screen-recording
    tool skipped in every pipeline, and the media conversion's one test against the real tool
    ran nowhere at all: it needs ffprobe too, which a desk here lacks (T-1453,
    docs/research/ci-skips-2026-11-15.md, hole H2). `test:media` installs both, runs their files
    and cannot go green by skipping.

    Not covered: that Debian's ffmpeg behaves like the one in the built image (a job on that
    image needs the build before it, so the job tool could not run it), and a skip whose line
    does not name the tool."""
    job = _all_jobs()["test:media"]
    assert "tags" not in job and "services" not in job and "needs" not in job, "the job tool must be able to run it"
    lines = "\n".join(str(ln) for ln in job["script"])
    assert re.search(r"apt-get install[^\n]*--no-install-recommends[^\n]*\bffmpeg\b", lines), (
        "Debian's package brings ffprobe with it; without --no-install-recommends it is far larger")
    need = [f for tree in ("hermes/plugins", "deploy/tests", "aura-adapter/tests", "aura-core/tests",
                           "aura-desktop/tests", "aura-voice/tests", "scripts", "testing")
            for f in sorted((ROOT / tree).rglob("test_*.py"))
            if f.resolve() != Path(__file__).resolve()
            and any(_FFMPEG_SKIP_RE.search(ln) for ln in f.read_text().splitlines())]
    assert len(need) >= 2, need
    for f in need:
        assert f.name in lines, f"{f.relative_to(ROOT)} skips without ffmpeg and test:media does not run it"
    runs = [ln for ln in job["script"] if "pytest" in str(ln)]
    assert runs and all("-rs" in str(ln) and "tee" in str(ln) for ln in runs), "each pytest line keeps its skip report"
    assert all("pipefail" in str(ln) for ln in runs), "without pipefail the tee's exit hides a failed pytest"
    assert re.search(r"!\s*grep[^\n]*SKIPPED[^\n]*ff\(mpeg\|probe\)", lines), (
        "a skip that names ffmpeg or ffprobe fails the job")


def test_the_house_suite_the_tooling_tests_and_the_evals_run_in_the_pipeline_that_deploys():
    """Until T-1459 the house's own suite (the largest), the tooling tests, the two offline evals
    and the benchmark's own tests ran on GitHub only, or nowhere: this pipeline is the one that
    merges and deploys, and the GitHub results are not read from the deploy host. Two jobs on
    the shared template run the same make targets a desk and GitHub's jobs run, so the skip
    guard judges them and the job tool can reproduce them.

    Not covered: the benchmark's live runs (bench:mr, bench:nightly) and anything that needs a
    model, a microphone or the house's own containers."""
    jobs = _all_jobs()
    wanted = {"test:core": ("make test-core",),
              "test:tools": ("make test-tools", "make eval-routing", "make eval-resolution", "bench/tests")}
    for name, commands in wanted.items():
        job = jobs[name]
        assert "tags" not in job and "services" not in job and "needs" not in job, f"{name}: the job tool must be able to run it"
        assert not job.get("allow_failure"), f"{name} may not fail quietly: that is the state it replaces"
        lines = "\n".join(str(ln) for ln in job["script"])
        for command in commands:
            assert command in lines, f"{name} does not run {command}"
        installs = "\n".join(str(ln) for key in ("before_script", "script") for ln in (job.get(key) or []))
        assert "aura-core/requirements.txt" in installs and "testing/requirements.txt" in installs, name
        assert "-p skip_guard" in str((job.get("variables") or {}).get("PYTEST_ADDOPTS", "")), name


SKIP_LIST = ROOT / "scripts" / "ci" / "allowed-skips.json"


def test_every_job_that_runs_pytest_carries_the_skip_guard():
    """A skipped test is green, which is how forty tests went unrun in every pipeline until
    2026-11-15. The guard (scripts/ci/skip_guard.py, its own tests in test_skip_guard.py) fails
    a run on a skip reason that is not recorded for the job; it only exists where the job's
    variables load it, so every job with a pytest line must carry them (T-1455).

    Not covered: a job that reaches pytest through a script or a make target with no
    `-m pytest` in its own lines, and the GitHub mirror, whose runners skip a different set."""
    guarded = {}
    for name, job in _all_jobs().items():
        lines = [str(ln) for key in ("before_script", "script") for ln in (job.get(key) or [])]
        if any("-m pytest" in ln for ln in lines):
            guarded[name] = (job.get("variables") or {}, lines)
    assert len(guarded) >= 10, sorted(guarded)
    for name, (variables, lines) in guarded.items():
        assert "-p skip_guard" in str(variables.get("PYTEST_ADDOPTS", "")), f"{name} runs pytest without the skip guard"
        assert str(variables.get("SKIP_GUARD_LIST", "")) == "$CI_PROJECT_DIR/scripts/ci/allowed-skips.json", name
        assert "$CI_PROJECT_DIR/scripts/ci" in str(variables.get("PYTHONPATH", "")).split(":"), (
            f"{name}: the plugin is found through PYTHONPATH")
        for ln in lines:
            if "PYTHONPATH=" in ln:
                assert "$PYTHONPATH" in ln or "${PYTHONPATH" in ln, f"{name} replaces PYTHONPATH and loses the guard: {ln}"


def test_the_recorded_skips_are_by_design_or_right_for_the_image_and_say_why():
    """The list is what a job may skip and stay green, so each entry is a decision someone can
    read: which jobs, the reason pytest prints, whether no machine runs the test (`design`) or
    this image cannot (`image`), and why. An `image` entry names the job that does run the
    test in `runs_in`, or says in its why that only a desk does.

    Not covered: that a recorded reason is still met by a test. An entry nobody uses stays
    until someone reads the list."""
    jobs = _all_jobs()
    entries = json.loads(SKIP_LIST.read_text())["skips"]
    assert entries, "an empty list would mean no job skips anything, which the image runs deny"
    seen = set()
    for entry in entries:
        assert set(entry) <= {"jobs", "reason", "prefix", "kind", "runs_in", "why"}, entry
        assert entry["kind"] in ("design", "image"), f"{entry['reason']}: a hole is closed, not recorded"
        assert entry["reason"].strip() == entry["reason"] and entry["reason"], entry
        assert len(entry["why"]) >= 20, f"{entry['reason']}: say why"
        assert entry.get("prefix", True) is True, "prefix is true or absent"
        assert entry["jobs"] and all(j in jobs for j in entry["jobs"]), f"{entry['reason']}: {entry['jobs']}"
        for job in entry["jobs"]:
            assert (job, entry["reason"]) not in seen, f"recorded twice: {job}: {entry['reason']}"
            seen.add((job, entry["reason"]))
        if "runs_in" in entry:
            assert entry["kind"] == "image" and entry["runs_in"] in jobs and entry["runs_in"] not in entry["jobs"], entry
        elif entry["kind"] == "image":
            assert "desk" in entry["why"], f"{entry['reason']}: an image entry says where the test does run"


def test_every_runtime_that_loads_the_plugins_or_the_adapter_installs_aura_schema():
    """The plugins and the adapter read their configuration from ``aura_schema`` (V2 D17).
    A runtime that loads them without the package loses every JARVIS tool at once, so each
    install point is pinned: the JARVIS image (into the Hermes venv), the adapter image
    (built from the repo root so the sibling tree is in context), the CI python jobs, and
    the plugin-discovery job that runs in the upstream Hermes image."""
    ci = yaml.safe_load(CI.read_text())
    assert "./aura-schema" in "\n".join(ci[".python"]["before_script"])
    # The upstream image's venv is not writable by the job user (pipeline 74), so this job may
    # put the checkout on PYTHONPATH instead of installing; either way the name must appear
    # before the plugins are copied and listed.
    discovery = ci["test:plugin-discovery"]["script"]
    idx = next(i for i, line in enumerate(discovery) if "aura-schema" in line)
    assert "PYTHONPATH" in discovery[idx] or "pip install" in discovery[idx]
    assert idx < next(i for i, line in enumerate(discovery) if "hermes plugins list" in line)
    assert ci["build:adapter"]["variables"]["CONTEXT"] == "."

    jarvis = (ROOT / "deploy" / "Dockerfile").read_text()
    assert "COPY aura-schema /opt/jarvis/seed/aura-schema" in jarvis
    assert "uv pip install --python /opt/hermes/.venv/bin/python --no-cache /opt/jarvis/seed/aura-schema" in jarvis
    adapter = (ROOT / "aura-adapter" / "Dockerfile").read_text()
    assert "COPY aura-schema ./aura-schema" in adapter and "./aura-schema" in adapter
    assert "COPY aura-adapter/aura_adapter ./aura_adapter" in adapter
    ignore = (ROOT / ".dockerignore").read_text().splitlines()
    assert "!aura-schema/" in ignore and "!aura-adapter/" in ignore
    assert "aura-schema/tests/" in ignore and "aura-adapter/tests/" in ignore


def test_bench_job_brings_its_own_fixture_server_and_the_in_stack_search():
    """The JCB tasks reach the fixture server as host.docker.internal:8899 from the agent
    container and search through the stack's own SearXNG. The bench job must start that
    server itself (the runner is on the host network, so 0.0.0.0:8899 is the host's) and
    point SEARXNG_URL at the compose service — the tailnet SearXNG has no egress."""
    text = (ROOT / ".gitlab" / "ci" / "bench.yml").read_text()
    assert "bench/fixtures/server.py --port 8899" in text
    assert "BENCH_FIXTURE_BASE=http://$CI_STACK_IP:8899" in text
    assert "SEARXNG_URL=http://searxng:8080" in text
    assert "SEARXNG_SECRET=" in text
    assert "pkill -f 'bench/fixtures/server.py'" in text


def test_a_killed_bench_job_cannot_leave_its_stack_running():
    """Pipeline 168's scheduled bench was killed at exactly 3600 s — the project's default
    job timeout — and the five containers it had brought up were still on the host nine
    hours later, one of them restarting on a loop. Two halves: the nightly gets a timeout
    the whole suite fits in, and the sweep that removes a leaked stack moves to a job that
    runs on every pipeline instead of only the next bench (a day later)."""
    bench = yaml.safe_load((ROOT / ".gitlab" / "ci" / "bench.yml").read_text())
    assert bench["bench:nightly"]["timeout"] == "3h", "the whole JCB does not fit in an hour"
    root = (ROOT / ".gitlab-ci.yml").read_text()
    smoke = yaml.safe_load(root)["integration:smoke"]
    sweep = [line for line in smoke["script"] if "docker rm -f" in line]
    assert len(sweep) == 1, "one sweep line"
    assert "name=^jarvis-ci-" in sweep[0] and "name=^jarvis-bench-" in sweep[0]
    # Never the live stack: its containers are `jarvis`, `jarvis-adapter`, `jarvis-web`, …
    assert "name=^jarvis'" not in sweep[0] and "name=jarvis " not in sweep[0]


def test_the_adapter_can_reach_the_kill_switch_through_hermes_home():
    """Hermes chmods its home to 0700 on every start; the adapter (uid 10001) reads
    state/kill-switch.json off a read-only mount of that volume and fails towards
    ENGAGED when it cannot — so on 2026-09-06 the HUD showed a stopped assistant that
    was running. HERMES_HOME_MODE=0711 is the cd-through mode for the group and others:
    the adapter's uid is in the gateway's group since ADR-0044 (the 0640 overrides file),
    and a group member gets the group bits — 0701 locked it out again on 2026-09-18. The
    mount stays read-only."""
    compose = yaml.safe_load((ROOT / "deploy" / "docker-compose.yml").read_text())
    jarvis = compose["services"]["jarvis"]["environment"]
    assert jarvis["HERMES_HOME"] == "/opt/data"
    assert str(jarvis["HERMES_HOME_MODE"]) == "0711"
    adapter_mounts = compose["services"]["jarvis-adapter"]["volumes"]
    assert "hermes-home:/opt/data:ro" in adapter_mounts


def test_device_keys_live_on_a_volume_only_the_adapter_mounts():
    """The device-key hashes (T-0569) are credentials: a hash planted in a volume another
    container can write is a working key. Hermes writes its own home and the artifacts volume
    has a sweeper, so the file gets a volume of its own, mounted by the adapter alone, with the
    mountpoint owned by the adapter user (a fresh volume takes the image directory's owner)."""
    compose = yaml.safe_load((ROOT / "deploy" / "docker-compose.yml").read_text())
    mount = "jarvis-adapter-state:/opt/adapter-state"
    holders = [name for name, svc in compose["services"].items()
               if any(str(m).split(":")[0] == "jarvis-adapter-state" for m in svc.get("volumes", []))]
    assert holders == ["jarvis-adapter"], holders
    assert mount in compose["services"]["jarvis-adapter"]["volumes"]
    assert compose["volumes"]["jarvis-adapter-state"]["name"] == "${COMPOSE_PROJECT_NAME:-jarvis}-adapter-state"
    dockerfile = (ROOT / "aura-adapter" / "Dockerfile").read_text(encoding="utf-8")
    chown = "RUN mkdir -p /opt/artifacts /opt/adapter-state && chown adapter:adapter /opt/artifacts /opt/adapter-state"
    assert chown in dockerfile and dockerfile.index(chown) < dockerfile.index("USER adapter")
    module = (ROOT / "aura-adapter" / "aura_adapter" / "device_keys.py").read_text(encoding="utf-8")
    assert 'ROOT = Path("/opt/adapter-state")' in module


def test_the_artifact_store_has_its_own_writable_volume_beside_the_read_only_home():
    """The store wrote to /opt/data/artifacts, under the read-only home above, so every
    publish in production was an EROFS 500 (research 2026-09-18). It gets a volume of its
    own at a sibling path: a writable volume *nested* under the read-only mount cannot be
    mounted at all — runc has to make the mountpoint on the parent, and on a read-only
    parent the container fails to start ("make mountpoint ... read-only file system",
    tried on this host with two throwaway volumes). The image makes the mountpoint owned
    by the adapter user, because a fresh volume takes the image directory's owner and a
    root-owned one is an EACCES from uid 10001. The volume is not in the backup: that is
    one tar of the home, and whether a day's artifacts are worth backing up is open."""
    compose = yaml.safe_load((ROOT / "deploy" / "docker-compose.yml").read_text())
    mounts = compose["services"]["jarvis-adapter"]["volumes"]
    assert "hermes-home:/opt/data:ro" in mounts, "the kill switch is still read off the home"
    assert "jarvis-artifacts:/opt/artifacts" in mounts, "the store's own volume, read-write"
    assert not [m for m in mounts if m.split(":")[1].startswith("/opt/data/")], "never nested under the ro home"
    assert compose["volumes"]["jarvis-artifacts"]["name"] == "${COMPOSE_PROJECT_NAME:-jarvis}-artifacts"
    dockerfile = (ROOT / "aura-adapter" / "Dockerfile").read_text(encoding="utf-8")
    chown = "RUN mkdir -p /opt/artifacts /opt/adapter-state && chown adapter:adapter /opt/artifacts /opt/adapter-state"
    assert chown in dockerfile and dockerfile.index(chown) < dockerfile.index("USER adapter")
    store = (ROOT / "aura-adapter" / "aura_adapter" / "artifacts.py").read_text(encoding="utf-8")
    assert 'ROOT = Path("/opt/artifacts")' in store, "the store writes where the volume is"


def test_the_entrypoint_puts_hermes_where_doctor_looks_for_it():
    """`hermes doctor` checks `~/.local/bin/hermes` and reported one ✗ for ever:
    "hermes command may not work outside the venv". The link cannot be made in the image:
    `hermes` here is a privilege-drop shim that runs as uid 10000, whose home is
    HERMES_HOME — the mounted volume, which masks whatever the image put there. So the
    entrypoint makes it on every start, and `-sfn` keeps that idempotent."""
    entrypoint = (ROOT / "deploy" / "entrypoint.sh").read_text(encoding="utf-8")
    assert 'mkdir -p "$HERMES_HOME/.local/bin"' in entrypoint
    assert 'ln -sfn /opt/hermes/.venv/bin/hermes "$HERMES_HOME/.local/bin/hermes"' in entrypoint


def test_the_voice_image_is_built_on_its_own_tree_and_deployed_by_name():
    """ADR-0039: jarvis-cosyvoice is gigabytes of torch, built only when its tree moves,
    and production runs it as `latest` (COSYVOICE_TAG) rather than the app's SHA, so a
    rollback of the app leaves the voice alone. The smoke stack never starts it: the runner
    is the production host, and a second copy would load 9 GB of weights onto the card
    production's voice is capped on."""
    doc = _doc()
    job = doc["build:cosyvoice"]
    assert job["extends"] == ".buildx" and job["variables"]["SVC"] == "jarvis-cosyvoice"
    assert job["variables"]["CONTEXT"] == ".", "root context: scripts/verify/gpu-allocation.py ships in the image"
    assert job["variables"]["ALSO_LATEST"] == "1"
    changes = [c for r in job["rules"] for c in r.get("changes", [])]
    assert "aura-voice/**/*" in changes and "scripts/verify/gpu-allocation.py" in changes
    # And on every deploying branch regardless: the deploy pulls `latest`, so
    # the tag has to exist before the first deploy and after every one (291).
    branches = " ".join(r.get("if", "") for r in job["rules"])
    assert 'claude/jarvis-overhaul' in branches and "CI_DEFAULT_BRANCH" in branches
    script = "\n".join(doc[".buildx"]["script"])
    # After grype, never before it: `latest` must be an image the scan cleared.
    assert script.index("grype") < script.index("imagetools create -t $IMAGE_BASE/$SVC:latest")
    for other in ("build:jarvis", "build:adapter", "build:web", "build:extract"):
        assert "ALSO_LATEST" not in doc[other].get("variables", {}), f"{other} is deployed by SHA, not by name"
    assert {"job": "build:cosyvoice", "optional": True} in doc["deploy:production"]["needs"], (
        "the deploy pulls the voice image the same pipeline may be pushing"
    )
    smoke = "\n".join(doc["integration:smoke"]["script"])
    up = next(line for line in smoke.splitlines() if "compose up -d" in line)
    assert "--scale" not in up, "scaling the voice to zero still pulls its image (pipeline 288)"
    for svc in ("jarvis", "jarvis-adapter", "jarvis-web", "jarvis-extract", "jarvis-backup"):
        assert svc in up.split("compose up", 1)[1]
    assert "cosyvoice" not in up
    env_block = smoke.split("cat > .env <<EOF", 1)[1].split("EOF", 1)[0]
    assert "TTS_PROVIDER=piper" in env_block and "AURA_TTS_ENGINE=wyoming" in env_block, (
        "a stack without the voice must speak through Piper, or its round trip fails twice over"
    )
    assert "pull --quiet jarvis " in smoke and "cosyvoice" not in smoke.split("pull --quiet", 1)[1].split("&&", 1)[0]
    compose = (ROOT / "deploy" / "docker-compose.yml").read_text()
    assert "jarvis-cosyvoice:${COSYVOICE_TAG:-latest}" in compose
    assert "profiles" not in yaml.safe_load(compose)["services"]["jarvis-cosyvoice"], "on by default (the owner, 2026-09-09)"


def test_ci_does_each_thing_once_and_a_desktop_tag_runs_only_the_desktop_release():
    """T-0275. Builds start at once but deploy still waits for every test (through
    integration:smoke's needs); a desktop-v* tag skips every non-desktop job; lintDebug and
    svelte-check each run in one job; android-deps needs the NVD key."""
    doc = _doc()
    jobs = _jobs(doc)
    assert doc[".buildx"]["needs"] == [], "builds do not wait for the test stage"
    smoke = {n["job"] if isinstance(n, dict) else n for n in jobs["integration:smoke"]["needs"]}
    tests = {n for n, j in _all_jobs().items() if j["stage"] == "test" and "android-device" not in j.get("tags", [])}
    assert tests <= smoke, f"deploy would not wait for {sorted(tests - smoke)}"
    assert {"build:jarvis", "build:adapter", "build:web", "build:extract"} <= smoke

    skip = {"if": "$CI_COMMIT_TAG =~ /^desktop-v/", "when": "never"}
    ci_dir = ROOT / ".gitlab" / "ci"
    files = {f.name: yaml.safe_load(f.read_text()) for f in ci_dir.glob("*.yml")}
    files["main"] = doc
    # Templates and jobs that own their rules must open with (or carry) the skip.
    own = {"main": [".python", "test:hermes", "test:plugin-discovery", "test:web", "test:android", ".buildx",
                    "build:cosyvoice", "integration:smoke"],
           "quality-python.yml": [".py-quality", "safety-drills"],
           "quality-docs.yml": [".docs-quality"],
           "quality-web.yml": [".web-quality", "tokens-sync"],
           "quality-hud.yml": [".hud-web", "hud-size"],
           "quality-android.yml": ["kotlin-lint", "android-deps"],
           "android-apk.yml": ["apk:build", "apk:release"]}
    for fname, names in own.items():
        for name in names:
            assert skip in files[fname][name]["rules"], f"{fname}:{name} would run on a desktop tag"
    assert {"if": "$CI_COMMIT_TAG =~ /^desktop-v/", "when": "never"} in doc["deploy:production"]["rules"]

    assert "npm test" not in (ROOT / ".gitlab-ci.yml").read_text().split("test:web:")[1].split("test:android:")[0]
    assert ":app:lintDebug" not in (ci_dir / "android-apk.yml").read_text(), "lintDebug runs in kotlin-lint only"
    assert any("NVD_API_KEY" in r.get("if", "") and r["when"] == "never"
               for r in files["quality-android.yml"]["android-deps"]["rules"])
    for f in ("quality-python.yml", "quality-docs.yml"):
        assert "PIP_CACHE_DIR" in (ci_dir / f).read_text()
    hud = files["quality-hud.yml"]
    assert hud["hud-a11y"]["resource_group"] == hud["hud-frametime"]["resource_group"] == "hud-web"
    assert "resource_group" not in hud["hud-overlap"] and "resource_group" not in hud["hud-gallery"]


def test_a_newer_push_never_cancels_a_deploy_or_a_release():
    # default: interruptible: true cancels superseded pipelines, and every job inherited it: a
    # push during a deploy could stop deploy.sh between compose up and the health gate.
    jobs = _all_jobs()
    for name in ("deploy:production", "apk:release", "desktop:shell:release"):
        assert jobs[name].get("interruptible") is False, name


def test_apk_release_publishes_only_a_whole_apk():
    """Pipeline 681: runner 2's disk was full, the artifact download only warned "no space left
    on device", and a truncated 118 MB APK became release 1.0.1679; the phone's installer said
    INSTALL_PARSE_FAILED_NOT_APK. The job now tests the zip before the first upload, and runs
    on the host runner, whose disk is not the GitLab container's."""
    job = _all_jobs()["apk:release"]
    script = "\n".join(job["script"])
    assert "unzip" in script.split("\n", 1)[0], "the image needs unzip"
    assert script.index('unzip -tqq "$APK_PATH"') < script.index("--upload-file"), "test the APK before uploading it"
    assert job.get("tags") == ["jarvis"]


WEB_JOBS = ["test:web", "web:build", "web-lint", "hud-a11y", "hud-frametime", "hud-overlap",
            "hud-behaviour", "hud-gallery", "hud-size"]


def _mr_creates(job: dict) -> bool:
    """Whether a merge-request pipeline whose diff matches no `changes:` list creates the job.

    Conservative: a rule with `changes`, a tag, a schedule or a compound `if` is taken as not
    matching, so only a rule that certainly matches counts."""
    for rule in job.get("rules", [{"when": "on_success"}]):
        cond = rule.get("if", "")
        if "changes" in rule or "schedule" in cond.lower():
            continue
        if cond and cond.strip() != '$CI_PIPELINE_SOURCE == "merge_request_event"':
            continue
        return rule.get("when", "on_success") != "never"
    return False


def test_path_filtered_jobs_never_break_the_merge_request_pipeline():
    """T-0277: web/HUD jobs run on a merge request only when the console changed, and bench:mr only when
    bench/ or a plugin did; everywhere else (main, tags, schedules, the web form) they always run.
    A needs: entry on a job that was filtered out fails the pipeline at creation, so whatever
    integration:smoke and apk:release need and a merge request may skip must be optional."""
    jobs = _all_jobs()
    for name in WEB_JOBS + ["bench:mr"]:
        rules = jobs[name]["rules"]
        changed = [r for r in rules if "changes" in r]
        assert changed and all(r["if"] == '$CI_PIPELINE_SOURCE == "merge_request_event"' for r in changed), name
        if name != "bench:mr":  # bench:mr is a merge-request job; its other refs run nothing by design
            assert {"when": "on_success"} in rules, f"{name} must still run on main, tags, schedules and the web form"
        assert not _mr_creates(jobs[name]), name
    for name in WEB_JOBS:
        paths = next(r for r in jobs[name]["rules"] if "changes" in r)["changes"]
        assert {"jarvis-web/**/*", "design/**/*", "tests/web/**/*", "tests/contracts/**/*",
                "hermes/plugins/hud*/**/*", "hermes/plugins/jarvis-hud/**/*"} <= set(paths), name
    assert {"bench/**/*", "hermes/plugins/**/*"} <= set(next(r for r in jobs["bench:mr"]["rules"] if "changes" in r)["changes"])
    # the console is built once; its jobs take that artifact
    for name in ["hud-a11y", "hud-overlap", "hud-behaviour", "hud-gallery", "hud-size", "hud-frametime"]:
        assert {"job": "web:build", "artifacts": True} in jobs[name]["needs"], name
    assert "npm run build" not in "\n".join(jobs["test:web"]["script"] + jobs["hud-a11y"].get("before_script", []))
    for consumer in ("integration:smoke", "apk:release"):
        for need in jobs[consumer]["needs"]:
            if isinstance(need, dict) and need.get("optional"):
                continue
            n = need["job"] if isinstance(need, dict) else need
            if n in jobs and n != "apk:build":  # apk:release and apk:build carry the same changes: list
                assert _mr_creates(jobs[n]), f"{consumer} needs {n}, which a merge request may filter out: make it optional"


def test_the_cargo_cache_is_keyed_on_its_lock_file_and_the_android_ones_share_their_paths():
    jobs = _all_jobs()
    # T-0983: Gradle's cache has a fixed key now (the test above); every job saving under one
    # key must list the same paths, since the last save is the whole cache.
    for key in ("android-toolchain-1", "android-gradle-1"):
        paths = {tuple(c["paths"]) for j in jobs.values() if isinstance(j, dict)
                 for c in (j.get("cache") if isinstance(j.get("cache"), list) else []) if c.get("key") == key}
        assert len(paths) == 1, (key, paths)
    keys = [c["key"] for c in jobs["desktop:shell"]["cache"] if isinstance(c["key"], dict)]
    assert any("jarvis-desktop-app/src-tauri/Cargo.lock" in k["files"] for k in keys)


def test_an_image_job_that_fails_tells_the_deploy_which_image_did_not_ship(tmp_path):
    """T-0419: build:models failed its grype gate twice (pipelines 642, 644) behind a green
    pipeline, because the by-name image jobs are allow_failure and only `:latest` stopped
    moving. A failed .buildx job leaves image-failed-<svc>.txt (its job URL) in the artifacts
    the deploy downloads, and deploy.sh turns each one into a house notice before the rollout,
    so a rollout that fails still reports it. Not covered: a pipeline that never deploys."""
    doc = _doc()
    build = doc[".buildx"]
    assert "image-failed-*.txt" in build["artifacts"]["paths"] and build["artifacts"]["when"] == "always"
    mark = next(line for line in build["after_script"] if "image-failed-" in line)
    for status, expect in (("failed", True), ("success", False)):
        env = {"PATH": "/usr/bin:/bin", "CI_JOB_STATUS": status, "SVC": "jarvis-models",
               "CI_JOB_URL": "https://gitlab.example/jobs/1"}
        subprocess.run(["bash", "-c", mark], cwd=tmp_path, env=env, check=True)
        out = tmp_path / "image-failed-jarvis-models.txt"
        assert out.exists() is expect, status
        if expect:
            assert out.read_text().strip() == "https://gitlab.example/jobs/1"
            out.unlink()
    for job in ("build:models", "build:cosyvoice"):
        need = next(n for n in doc["deploy:production"]["needs"] if isinstance(n, dict) and n["job"] == job)
        assert need.get("artifacts", True), f"the deploy must download {job}'s artifacts"
    deploy = (ROOT / "scripts" / "deploy.sh").read_text()
    assert "image-failed-*.txt" in deploy and "health_guard.py --image-failed" in deploy
    assert deploy.index("health_guard.py --image-failed") < deploy.index('log "3. AURA_TAG'), "before the rollout"


def test_a_deploy_never_restarts_the_model_server_under_a_benchmark():
    """bench:mr and bench:nightly ask production's llama-swap (LLM_URL :9000), and a deploy
    that changes jarvis-models recreates it: pipeline 646's deploy did so at 18:41, inside
    bench:mr's run, and four cases timed out on the four-minute cold start (T-0420). One
    resource_group makes GitLab run them one at a time, across pipelines too. integration:smoke
    is in it as well: its smoke asks that server for a completion, and a deploy is never
    cancelled, so the previous pipeline's deploy can be recreating the model while the next
    pipeline's smoke runs (T-0421). Does not order them inside one pipeline: `needs` does."""
    jobs = _all_jobs()
    names = ("deploy:production", "bench:mr", "bench:nightly", "integration:smoke")
    groups = {jobs[n].get("resource_group") for n in names}
    assert len(groups) == 1 and None not in groups, groups


def test_hermes_imports_run_against_the_commits_image_before_anything_ships():
    """A plugin reaching into Hermes' private modules is the one dependency no pin covers: a
    moved symbol built, loaded and passed /health, then failed in production (v2026.9.24's
    ``_pipe_stdin``; T-0732's browser frame). The check runs against the image this commit
    built, and the smoke (which the deploy needs) waits for it."""
    jobs = _all_jobs()
    job = jobs["test:hermes-imports"]
    assert job["needs"] == ["build:jarvis"]
    script = "\n".join(job["script"])
    assert "scripts/verify/hermes-imports.py $IMAGE_BASE/jarvis:$CI_COMMIT_SHORT_SHA" in script
    assert "test:hermes-imports" in jobs["integration:smoke"]["needs"]
    assert job["rules"] == jobs["build:jarvis"]["rules"]


def test_android_caches_survive_a_key_file_change_and_a_failed_run():
    """T-0983: keyed on libs.versions.toml and bootstrap-toolchain.sh and saved only on
    success, the caches were cold after every dependency bump or pruned volume; the cold run
    downloaded the SDK and Robolectric, met Google's 429, failed and saved nothing (pipelines
    790, 792, 793). The bootstrap and Gradle both add what is missing to an older cache, so the
    key is fixed, and a failed run keeps what it downloaded."""
    jobs = {"quality-android.yml": ["kotlin-lint", "android-deps"], "android-apk.yml": ["apk:build"]}
    for name, wanted in jobs.items():
        doc = yaml.safe_load((ROOT / ".gitlab" / "ci" / name).read_text())
        for job in wanted:
            shared = [c for c in doc[job]["cache"] if any(".cache/android" in p or ".cache/gradle" in p for p in c["paths"])]
            assert len(shared) == 2, (job, shared)
            for c in shared:
                assert isinstance(c["key"], str) and c["key"].startswith("android-"), (job, c["key"])
                assert c.get("when") == "always", (job, c)
    # One key per cache across the jobs (the test below holds their paths to one list), or they would not share it.
    keys = {c["key"] for name, wanted in jobs.items() for job in wanted
            for c in yaml.safe_load((ROOT / ".gitlab" / "ci" / name).read_text())[job]["cache"] if str(c["key"]).startswith("android-")}
    assert keys == {"android-toolchain-1", "android-gradle-1"}


def test_the_models_build_makes_room_for_itself_and_still_logs_in():
    """T-0970: a 22 GB image and its scan need room the hourly guard's 20 GB floor does not keep."""
    import yaml

    doc = yaml.safe_load((Path(__file__).resolve().parents[2] / ".gitlab-ci.yml").read_text())
    before = doc["build:models"]["before_script"]
    # A before_script on the job replaces .docker's: its lines must still be here.
    assert before[:2] == doc[".docker"]["before_script"]
    room = before[-1]
    assert '-lt 50' in room and "docker image prune -f" in room and "docker builder prune --filter until=12h -f" in room
    # What a build cannot remake is never dropped here: no volume, no tag.
    assert "volume" not in room and "rmi" not in room


def test_the_desktop_shell_job_keeps_the_bundlers_tools_between_runs():
    """Tauri's bundler fetches AppRun and linuxdeploy from GitHub's release CDN into its cache
    directory each build; a 504 there failed the job twice (T-0810). The cache directory has to be
    inside the project, or GitLab cannot keep it. Does not cover a first run on a cold cache."""
    job = yaml.safe_load((ROOT / ".gitlab" / "ci" / "desktop-dist.yml").read_text())["desktop:shell"]
    assert job["variables"]["XDG_CACHE_HOME"] == "$CI_PROJECT_DIR/.cache"
    assert any(".cache/tauri/" in entry["paths"] for entry in job["cache"])


def test_the_ci_deploy_runs_the_golden_turn_and_it_never_decides_the_job():
    """T-1141: only the deploy from the host ran the golden turn, so the board's surfaces
    stayed stale after a pipeline deploy. Not covered: a pipeline run of the line; the relay was tried from
    this host only. A miss is printed and the job goes on."""
    script = _jobs(_doc())["deploy:console"]["script"]
    live = next(i for i, line in enumerate(script) if "make console-live" in line)
    golden = [i for i, line in enumerate(script) if "golden-turn.mjs" in line]
    assert golden and golden[0] > live, "the golden turn runs after the console check"
    line = script[golden[0]]
    # T-1321: the first version posted the production key to http://<host>:8643 in clear.
    assert 'AURA_BASE="$DEPLOY_URL/hermes"' in line, "the board update goes through the console's HTTPS relay"
    assert "http://" not in line and ":8643" not in line, "the key never travels over plain HTTP"
    assert "timeout " in line and "||" in line, "a failed or stuck surface must not fail the deploy"
