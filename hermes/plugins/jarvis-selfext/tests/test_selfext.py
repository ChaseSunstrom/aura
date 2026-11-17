"""The self-extension loop against a real git repository in a temp dir.

Not a mock: the loop's whole value is that the git rules hold, and a fake ``git``
would only prove that the fake agrees with itself. Each test points
``SELFEXT_REPO_PATH`` at a throwaway repository with a bare "remote" beside it, so
the branch, the pathspecs, the refusals and the push all run for real — with the
one thing that cannot run here, GitLab's merge-request push option, checked
separately by parsing a captured GitLab push banner.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from _plugin_loader import load_plugin  # noqa: E402

plugin = load_plugin("jarvis-selfext")
tools = plugin.tools
scaffold = plugin.scaffold
git_layer = plugin.git

TOOLS = [
    {"name": "weather_now", "description": "Current weather for a place.",
     "parameters": {"type": "object", "properties": {"place": {"type": "string"}},
                    "required": ["place"]}},
]


def _git(repo: Path, *args: str) -> str:
    out = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                         check=True)
    return out.stdout


#: A deploy manifest with the two shapes that matter: comments interleaved with the
#: entries, and a sibling key ending the list.
MANIFEST = """\
model:
  default: "test"
toolsets:
  enabled:
    - terminal
    - memory
plugins:
  enabled:
    # The governor, first.
    - jarvis-guard
    - smart-home
  # Named, not merely left out.
  disabled:
    - web-exa
approvals:
  mode: smart
"""


@pytest.fixture
def repo(tmp_path, monkeypatch) -> Path:
    """A checkout with one commit on `main` and a bare remote to push into."""
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(remote)], check=True,
                   capture_output=True)
    # GitLab advertises push options; a bare repo does not unless told to. Without
    # this the push carrying `-o merge_request.create` is rejected wholesale, which
    # is exactly the case the fallback below covers — so the happy path needs a
    # remote that behaves like the real one.
    _git(remote, "config", "receive.advertisePushOptions", "true")
    work = tmp_path / "work"
    work.mkdir()
    _git(work, "init", "-b", "main")
    _git(work, "config", "user.email", "selfext@test")
    _git(work, "config", "user.name", "selfext")
    (work / "README.md").write_text("jarvis\n", encoding="utf-8")
    (work / "hermes" / "plugins").mkdir(parents=True)
    (work / "hermes" / "plugins" / ".keep").write_text("", encoding="utf-8")
    # A plugin that already ships, so "is this name taken" can be asked of the
    # directory listing and of the manifest separately.
    (work / "hermes" / "plugins" / "existing-thing").mkdir()
    (work / "hermes" / "plugins" / "existing-thing" / "plugin.yaml").write_text(
        "name: existing-thing\n", encoding="utf-8")
    # The deploy manifests, in the shape the real ones have: a commented block whose
    # comments are part of what the loop must not disturb, and a second key after the
    # list so "append after the last entry" has an end to find.
    for rel in scaffold.MANIFESTS:
        manifest = work / rel
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text(MANIFEST, encoding="utf-8")
    _git(work, "add", "-A")
    _git(work, "commit", "-m", "seed")
    _git(work, "remote", "add", "origin", str(remote))
    _git(work, "push", "-u", "origin", "main")
    monkeypatch.setenv("SELFEXT_REPO_PATH", str(work))
    monkeypatch.setenv("SELFEXT_TARGET_BRANCH", "main")
    monkeypatch.setenv("SELFEXT_BRANCH_PREFIX", "jarvis/capability-")
    monkeypatch.setenv("SELFEXT_REMOTE", "origin")
    return work


def test_the_tools_are_hidden_until_a_checkout_exists(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("SELFEXT_REPO_PATH", str(tmp_path / "nothing-here"))
    ctx = plugin.FakeCtx()
    plugin.register(ctx)
    assert set(ctx.tools) == {"integrate_discover", "selfext_plan", "selfext_scaffold", "selfext_verify",
                              "selfext_open_mr", "selfext_status",
                              "skill_propose", "skill_proposals"}
    # Registered but not offered: the difference between "not configured" and "broken".
    assert all(ctx.available(name) is False for name in ctx.tools)


def test_the_push_and_the_execution_are_gated_and_nothing_else_is(repo) -> None:
    ctx = plugin.FakeCtx()
    plugin.register(ctx)
    assert all(ctx.available(name) for name in ctx.tools)
    gate = ctx.hooks["pre_tool_call"]
    directive = gate("selfext_open_mr", {"name": "weather-now"}, "")
    assert directive["action"] == "approve"
    assert "jarvis/capability-weather-now" in directive["message"]
    assert "'main'" in directive["message"] and "Nothing is merged" in directive["message"]

    # Writing a file and running one are different acts. Scaffolding produces inert
    # text; verify executes it, and pytest collects a conftest.py the model could
    # have written before it runs a single test.
    verifying = gate("selfext_verify", {"name": "weather-now"}, "")
    assert verifying["action"] == "approve"
    # The base key; the token after it is what lets a standing yes run out (T-0570).
    assert verifying["rule_key"].rsplit(":g", 1)[0] == "selfext:verify", "one [a]lways should cover the build"
    assert "no sandbox" in verifying["message"] and "conftest.py" in verifying["message"]

    for ungated in ("selfext_plan", "selfext_scaffold", "selfext_status",
                    "skill_propose", "skill_proposals"):
        assert gate(ungated, {}, "") is None


def test_plan_writes_nothing_and_names_where_things_land(repo) -> None:
    out = json.loads(tools.handle_selfext_plan(
        {"name": "weather-now", "why": "cannot answer the forecast", "tools": TOOLS}))
    assert out["ok"] and out["branch"] == "jarvis/capability-weather-now"
    assert out["target"] == "main"
    assert out["writes"] == ["hermes/plugins/weather-now", "bench/tasks/coding-selfext",
                             "hermes/skills/weather-now", *scaffold.MANIFESTS]
    assert not (repo / "hermes" / "plugins" / "weather-now").exists()


def test_scaffold_writes_a_plugin_that_loads_and_a_test_that_runs(repo) -> None:
    out = json.loads(tools.handle_selfext_scaffold(
        {"name": "weather-now", "description": "Answer the forecast.", "tools": TOOLS,
         "bench_prompt": "What is the weather right now?"}))
    assert out["ok"], out
    assert "hermes/plugins/weather-now/__init__.py" in out["files"]
    assert out["bench_task"] == "selfext-weather-now-001"
    body = (repo / "hermes" / "plugins" / "weather-now" / "__init__.py").read_text()
    assert "def handle_weather_now" in body and "TODO" in body
    # It must actually import and register, or the merge request carries dead code.
    namespace: dict = {}
    exec(compile(body, "generated", "exec"), namespace)  # noqa: S102 - the point of the test
    ctx = plugin.FakeCtx()
    namespace["register"](ctx)
    assert set(ctx.tools) == {"weather_now"}
    assert json.loads(ctx.tools["weather_now"]["handler"]({"place": "here"}))["ok"] is True
    task = (repo / "bench" / "tasks" / "coding-selfext" / "selfext-weather-now-001.yaml").read_text()
    assert "plugin:weather-now" in task and "tool: weather_now" in task


def test_scaffold_with_approval_generates_a_gate_and_its_test(repo) -> None:
    json.loads(tools.handle_selfext_scaffold(
        {"name": "door-opener", "description": "Open the door.", "tools": TOOLS,
         "approval": True}))
    init = repo / "hermes" / "plugins" / "door-opener" / "__init__.py"
    body = init.read_text()
    namespace: dict = {"__file__": str(init)}
    exec(compile(body, "generated", "exec"), namespace)  # noqa: S102
    ctx = plugin.FakeCtx()
    namespace["register"](ctx)
    assert ctx.hooks["pre_tool_call"]("weather_now", {}, "")["action"] == "approve"
    assert ctx.hooks["pre_tool_call"]("something_else", {}, "") is None
    test_body = (repo / "hermes" / "plugins" / "door-opener" / "tests"
                 / "test_door_opener.py").read_text()
    assert "gates_what_changes_the_world" in test_body


@pytest.mark.parametrize("bad", ["Weather", "weather_now", "weather--now", "-weather", ""])
def test_a_name_that_is_not_a_plugin_directory_is_refused(repo, bad) -> None:
    out = json.loads(tools.handle_selfext_scaffold({"name": bad, "tools": TOOLS}))
    assert out["ok"] is False and "lowercase" in out["error"]


def test_a_plugin_with_no_tools_is_refused(repo) -> None:
    out = json.loads(tools.handle_selfext_scaffold({"name": "empty-thing", "tools": []}))
    assert out["ok"] is False and "at least one" in out["error"]


def test_scaffolding_over_an_existing_plugin_is_refused(repo) -> None:
    assert json.loads(tools.handle_selfext_scaffold({"name": "weather-now", "tools": TOOLS}))["ok"]
    again = json.loads(tools.handle_selfext_scaffold({"name": "weather-now", "tools": TOOLS}))
    assert again["ok"] is False and "already exists" in again["error"]


def test_a_dirty_checkout_is_refused_rather_than_swept_into_the_branch(repo) -> None:
    json.loads(tools.handle_selfext_scaffold({"name": "weather-now", "tools": TOOLS}))
    # Somebody else's half-finished work, in the same shared checkout.
    (repo / "OTHER_AGENT.md").write_text("mid-edit\n", encoding="utf-8")
    out = json.loads(tools.handle_selfext_open_mr(
        {"name": "weather-now", "description": "adds the forecast"}))
    assert out["ok"] is False
    assert "outside this capability's files" in out["error"] and "OTHER_AGENT.md" in out["error"]
    assert git_layer.current_branch() == "main", "a refused push must not leave a new branch checked out"


def test_a_merge_request_needs_a_description(repo) -> None:
    json.loads(tools.handle_selfext_scaffold({"name": "weather-now", "tools": TOOLS}))
    out = json.loads(tools.handle_selfext_open_mr({"name": "weather-now", "description": "  "}))
    assert out["ok"] is False and "needs a description" in out["error"]


def test_open_mr_branches_commits_only_its_own_files_and_pushes(repo) -> None:
    json.loads(tools.handle_selfext_scaffold(
        {"name": "weather-now", "description": "Answer the forecast.", "tools": TOOLS,
         "bench_prompt": "What is the weather right now?"}))
    out = json.loads(tools.handle_selfext_open_mr(
        {"name": "weather-now", "title": "Add a weather capability",
         "description": "JARVIS could not answer the forecast. Adds the weather-now plugin."}))
    assert out["ok"] is True, out
    assert out["branch"] == "jarvis/capability-weather-now" and out["merged"] is False
    files = _git(repo, "show", "--name-only", "--pretty=", "HEAD").split()
    assert "hermes/plugins/weather-now/__init__.py" in files
    assert "bench/tasks/coding-selfext/selfext-weather-now-001.yaml" in files
    assert all(f.startswith(("hermes/plugins/weather-now/", "bench/tasks/coding-selfext/"))
               or f in scaffold.MANIFESTS for f in files), files
    # The manifests are in the commit, and carry exactly one added line each.
    for rel in scaffold.MANIFESTS:
        assert rel in files, rel
        added = [line for line in _git(repo, "show", "--pretty=", "--unified=0", "--", rel
                                       ).splitlines()
                 if line.startswith("+") and not line.startswith("+++")]
        assert added == ["+    - weather-now"], (rel, added)
    message = _git(repo, "log", "-1", "--pretty=%B")
    assert "Co-Authored-By: Claude Opus 5 (1M context)" in message
    # The branch reached the remote and main did not move.
    remote_branches = _git(repo, "ls-remote", "--heads", "origin")
    assert "refs/heads/jarvis/capability-weather-now" in remote_branches
    main_local = _git(repo, "rev-parse", "main").strip()
    main_remote = _git(repo, "rev-parse", "origin/main").strip()
    assert main_local == main_remote, "the target branch must not have moved"


def test_a_remote_without_push_options_still_gets_the_branch(repo, tmp_path) -> None:
    _git(tmp_path / "remote.git", "config", "receive.advertisePushOptions", "false")
    json.loads(tools.handle_selfext_scaffold({"name": "weather-now", "tools": TOOLS}))
    out = json.loads(tools.handle_selfext_open_mr(
        {"name": "weather-now", "description": "adds the forecast"}))
    assert out["ok"] is True and out["merge_request"] == ""
    assert "open the merge request by hand" in out["push_note"]
    assert "refs/heads/jarvis/capability-weather-now" in _git(repo, "ls-remote", "--heads", "origin")


def test_pushing_the_target_branch_is_refused(repo) -> None:
    with pytest.raises(git_layer.GitError, match="does not start with"):
        git_layer.assert_pushable("main", ("hermes/plugins/weather-now",), "weather-now",
                                  scaffold.MANIFESTS)


def test_an_unprefixed_branch_is_refused(repo) -> None:
    with pytest.raises(git_layer.GitError, match="does not start with"):
        git_layer.assert_pushable("hotfix/whatever", (), "whatever", scaffold.MANIFESTS)


def test_a_credential_in_a_remote_url_is_not_echoed_back() -> None:
    # The repo's remote is ssh, so nothing leaks today — but git prints the remote in
    # push output and in a failed command's message, and a checkout configured with
    # https://user:token@host would put that token in a tool result, which reaches the
    # model, the transcript and the HUD.
    leaked = ("fatal: could not read from 'https://oauth2:glpat-notarealtoken@gitlab.test/x.git'\n"
              "To https://oauth2:glpat-notarealtoken@gitlab.test/x.git")
    cleaned = git_layer.redact(leaked)
    assert "glpat-notarealtoken" not in cleaned and "oauth2" not in cleaned
    assert "https://gitlab.test/x.git" in cleaned
    # An ordinary URL is untouched, so the redaction cannot quietly mangle the answer.
    plain = "https://gitlab.test/acme/jarvis/-/merge_requests/41"
    assert git_layer.redact(plain) == plain


def test_the_merge_request_url_is_read_back_off_the_push_banner() -> None:
    # A real GitLab push banner, verbatim apart from the host.
    banner = (
        "remote: \n"
        "remote: View merge request for jarvis/capability-weather-now:\n"
        "remote:   https://gitlab.example.ts.net/acme/jarvis/-/merge_requests/41\n"
        "remote: \n"
        "To ssh://gitlab.example.ts.net:2224/acme/jarvis.git\n"
        " * [new branch]      jarvis/capability-weather-now -> jarvis/capability-weather-now\n"
    )
    assert git_layer.parse_mr_url(banner) == (
        "https://gitlab.example.ts.net/acme/jarvis/-/merge_requests/41")
    assert git_layer.parse_mr_url("To ssh://host/repo.git\n * [new branch] x -> x\n") == ""


def test_verify_runs_every_check_the_pipeline_runs(repo, monkeypatch) -> None:
    json.loads(tools.handle_selfext_scaffold({"name": "weather-now", "tools": TOOLS}))
    seen: list[list[str]] = []

    class _Done:
        returncode = 0
        stdout = "All checks passed!"
        stderr = ""

    def _fake_run(argv, *, cwd, timeout):
        seen.append(argv)
        assert cwd == str(repo) and timeout > 0
        return _Done()

    # The seam is the sandbox call, not subprocess.run: every checker goes through
    # jarvis-bwrap's run_sandboxed with the checkout bound writable, and a fake on
    # subprocess.run would see nothing (or, worse, see the checkers run unsandboxed).
    monkeypatch.setattr(plugin.tools, "_run_sandboxed", _fake_run)
    out = json.loads(tools.handle_selfext_verify({"name": "weather-now"}))
    assert out["ok"] is True and out["failed"] == []
    joined = [" ".join(argv) for argv in seen]
    # Merge request !2 was rejected by py-mypy for two type errors in code this loop
    # had written and called ready, because verify stopped after ruff and pytest.
    assert any(c.startswith("python3 -m ruff check hermes/plugins/weather-now") for c in joined)
    assert any("pytest hermes/plugins/weather-now" in c for c in joined)
    assert any("scripts/mypy-plugins.sh" in c for c in joined)
    assert any("vulture" in c and "hermes/plugins/weather-now" in c for c in joined)
    assert any("bench/tests/test_tasks.py" in c for c in joined)
    assert out["checks"][-1]["command"].startswith("grep weather-now in ")


def test_verify_refuses_a_plugin_that_was_never_scaffolded(repo) -> None:
    out = json.loads(tools.handle_selfext_verify({"name": "never-made"}))
    assert out["ok"] is False and "run selfext_scaffold first" in out["error"]


def test_status_reports_the_branch_and_what_is_uncommitted(repo) -> None:
    (repo / "scratch.txt").write_text("x\n", encoding="utf-8")
    out = json.loads(tools.handle_selfext_status({}))
    assert out["branch"] == "main" and out["target"] == "main"
    assert "scratch.txt" in out["uncommitted"] and out["uncommitted_count"] == 1


def test_a_broken_repo_answers_with_an_error_not_a_traceback(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SELFEXT_REPO_PATH", str(tmp_path / "not-a-repo"))
    out = json.loads(tools.handle_selfext_status({}))
    assert out["ok"] is False and out["error"]


# --------------------------------------------------------------------------- #
# Naming the capability in the deploy manifest
# --------------------------------------------------------------------------- #
# Merge request !2 was red on `deploy/config.template.yaml does not name
# ['disk-space'] under plugins.enabled`. The loop wrote its plugin directory and its
# benchmark task and stopped, because those were the only two roots it was allowed to
# touch, so the third list a shipped plugin must appear in stayed unwritten and the
# capability's own merge request failed the repository's own gate.


def _scaffold(name: str = "tide-times", **kw):
    return scaffold.scaffold(name, "Tide times for a beach.", "tide", TOOLS, **kw)


def test_scaffold_names_the_capability_in_both_manifests(repo) -> None:
    _scaffold()
    for rel in scaffold.MANIFESTS:
        text = (repo / rel).read_text(encoding="utf-8")
        assert "    - tide-times\n" in text, rel
        # Appended after the last entry, so the comments stay with what they describe.
        assert text.index("- smart-home") < text.index("- tide-times")
        assert text.index("- tide-times") < text.index("  disabled:")
        assert "# The governor, first." in text and "# Named, not merely left out." in text


def test_both_manifests_move_together(repo) -> None:
    """Editing one alone trades a red test:config-render for a red schema-sync."""
    _scaffold()
    rendered = (repo / scaffold.RENDERED_TEMPLATE).read_text(encoding="utf-8")
    source = (repo / scaffold.SCHEMA_TEMPLATE).read_text(encoding="utf-8")
    assert rendered == source


def test_a_name_already_in_the_list_is_not_duplicated() -> None:
    once = scaffold.name_in_manifest(MANIFEST, "smart-home")
    assert once == MANIFEST
    assert once.count("- smart-home") == 1


def test_a_manifest_with_no_plugins_block_is_refused_rather_than_guessed_at() -> None:
    for broken in ("model:\n  default: x\n", "plugins:\n  disabled: []\n",
                   "plugins:\n  enabled:\n  disabled: []\n"):
        with pytest.raises(scaffold.ScaffoldError):
            scaffold.name_in_manifest(broken, "tide-times")


def test_the_scaffold_reports_the_manifests_it_touched(repo) -> None:
    out = _scaffold()
    assert set(scaffold.MANIFESTS) <= set(out["files"])
    assert set(scaffold.MANIFESTS) <= set(out["allowed_paths"])


# --------------------------------------------------------------------------- #
# …and the narrowing that pays for it
# --------------------------------------------------------------------------- #
# Adding the manifests to allowed_paths widens stray_changes, which was the only
# thing between an agent-authored edit and a push. These pin the narrowing.


def test_the_loop_may_push_its_own_name_in_the_manifest(repo) -> None:
    _scaffold()
    git_layer.assert_pushable("jarvis/capability-tide-times",
                              scaffold.allowed_paths("tide-times"),
                              "tide-times", scaffold.MANIFESTS)


def test_a_manifest_edit_that_changes_another_key_is_refused(repo) -> None:
    """The edit the widened allowlist would otherwise carry to a merge request."""
    _scaffold()
    path = repo / scaffold.RENDERED_TEMPLATE
    path.write_text(path.read_text(encoding="utf-8").replace(
        'default: "test"', 'default: "attacker-controlled"'), encoding="utf-8")
    with pytest.raises(git_layer.GitError) as exc:
        git_layer.assert_pushable("jarvis/capability-tide-times",
                                  scaffold.allowed_paths("tide-times"),
                                  "tide-times", scaffold.MANIFESTS)
    assert "beyond naming 'tide-times'" in str(exc.value)
    assert "attacker-controlled" in str(exc.value)


def test_a_manifest_edit_that_removes_a_line_is_refused(repo) -> None:
    """Disabling the governor by deletion is the same widening, read backwards."""
    _scaffold()
    path = repo / scaffold.RENDERED_TEMPLATE
    path.write_text(path.read_text(encoding="utf-8").replace("    - jarvis-guard\n", ""),
                    encoding="utf-8")
    with pytest.raises(git_layer.GitError) as exc:
        git_layer.assert_pushable("jarvis/capability-tide-times",
                                  scaffold.allowed_paths("tide-times"),
                                  "tide-times", scaffold.MANIFESTS)
    assert "has lines removed" in str(exc.value) and "jarvis-guard" in str(exc.value)


def test_a_manifest_edit_naming_a_different_plugin_is_refused(repo) -> None:
    """`- jarvis-guard` is a list entry too; only this capability's own name passes."""
    _scaffold()
    path = repo / scaffold.RENDERED_TEMPLATE
    path.write_text(path.read_text(encoding="utf-8").replace(
        "    - tide-times\n", "    - tide-times\n    - some-other-plugin\n"), encoding="utf-8")
    with pytest.raises(git_layer.GitError) as exc:
        git_layer.assert_pushable("jarvis/capability-tide-times",
                                  scaffold.allowed_paths("tide-times"),
                                  "tide-times", scaffold.MANIFESTS)
    assert "some-other-plugin" in str(exc.value)


def test_a_comment_reworded_on_the_way_past_is_refused(repo) -> None:
    """The comments are the manifest's explanation of itself; a YAML compare would
    have accepted this, which is why the check reads lines and not documents."""
    _scaffold()
    path = repo / scaffold.RENDERED_TEMPLATE
    path.write_text(path.read_text(encoding="utf-8").replace(
        "# The governor, first.", "# Optional."), encoding="utf-8")
    with pytest.raises(git_layer.GitError):
        git_layer.assert_pushable("jarvis/capability-tide-times",
                                  scaffold.allowed_paths("tide-times"),
                                  "tide-times", scaffold.MANIFESTS)


# --------------------------------------------------------------------------- #
# The loop's self-check is the gate its merge request has to pass
# --------------------------------------------------------------------------- #


def test_verify_runs_what_ci_runs() -> None:
    """py-mypy rejected merge request !2 for two type errors in code this loop had
    written and pronounced ready, because verify ran only ruff and pytest."""
    commands = [" ".join(argv) for argv in tools._gate_commands("hermes/plugins/tide-times")]
    assert any(c.startswith("python3 -m ruff check") for c in commands)
    assert any("pytest hermes/plugins/tide-times" in c for c in commands)
    assert any("scripts/mypy-plugins.sh" in c for c in commands), "py-mypy is the job that rejected !2"
    assert any("vulture" in c for c in commands), "py-dead"
    assert any("bench/tests/test_tasks.py" in c for c in commands), "the pinned corpus counts"


# --------------------------------------------------------------------------- #
# The name is the payload
# --------------------------------------------------------------------------- #
# `NAME_RE` says a name is well-formed. It does not say the name is unclaimed, and
# the name is the one part of these edits the model chooses. Deterministic code
# inserting a model-chosen string is not the same as the model authoring nothing.


def test_a_capability_may_not_take_a_shipped_plugin_s_name(repo) -> None:
    """`allowed_paths(name)` opens `hermes/plugins/<name>`, so a capability that took
    `jarvis-guard` would get write access to the plugin registering the approval hook,
    and no manifest check would see that edit because it is not in a manifest."""
    with pytest.raises(scaffold.ScaffoldError) as exc:
        _scaffold("existing-thing")
    assert "already the name of a plugin" in str(exc.value)
    assert not (repo / "hermes" / "plugins" / "existing-thing" / "__init__.py").exists()


def test_a_capability_may_not_take_a_name_the_manifest_already_governs(repo) -> None:
    """The disabled list is not decoration: those cloud web backends are named there
    so coming back "must take a deliberate edit here, not an environment variable
    someone pastes into .env". A capability called `web-exa` would append
    `- web-exa` to `enabled` while it is still listed under `disabled`, and every
    shape check would pass, because the added line does equal its own name."""
    before = (repo / scaffold.RENDERED_TEMPLATE).read_text(encoding="utf-8")
    with pytest.raises(scaffold.ScaffoldError) as exc:
        _scaffold("web-exa")
    assert "a manifest there\n    already governs".replace("\n    ", " ") in str(exc.value)
    assert (repo / scaffold.RENDERED_TEMPLATE).read_text(encoding="utf-8") == before


def test_open_mr_asks_the_same_question_scaffold_does(repo) -> None:
    """open_mr does not require scaffold to have run, so it cannot rely on it."""
    (repo / "hermes" / "plugins" / "existing-thing" / "__init__.py").write_text(
        "# whatever the model wanted here\n", encoding="utf-8")
    out = json.loads(tools.handle_selfext_open_mr(
        {"name": "existing-thing", "description": "Take over an existing plugin."}))
    assert out["ok"] is False
    assert "already the name of a plugin" in out["error"]
    assert "existing-thing" not in _git(repo, "ls-remote", "--heads", "origin")


def test_the_capability_s_own_name_is_still_free_after_its_own_scaffold(repo) -> None:
    """Asked of the target branch, not the working tree. A working-tree check would
    refuse the very push it exists to allow, because scaffold has just taken the name."""
    _scaffold()
    scaffold.assert_name_is_free("tide-times")  # does not raise


def test_an_entry_added_outside_the_enabled_block_is_refused(repo) -> None:
    """The line says the right thing; it is in the wrong place. Shape and location are
    checked separately so neither has to be right on its own."""
    _scaffold()
    path = repo / scaffold.RENDERED_TEMPLATE
    path.write_text(path.read_text(encoding="utf-8").replace(
        "  disabled:\n", "  disabled:\n    - tide-times\n"), encoding="utf-8")
    with pytest.raises(git_layer.GitError) as exc:
        git_layer.assert_pushable("jarvis/capability-tide-times",
                                  scaffold.allowed_paths("tide-times"),
                                  "tide-times", scaffold.MANIFESTS)
    assert "outside the plugins.enabled block" in str(exc.value)


def test_the_narrowing_cannot_be_left_off(repo) -> None:
    """`allowed` is permanently wide now, so a caller that forgot the narrowing would
    get the widened allowlist with nothing narrowing it."""
    _scaffold()
    with pytest.raises(git_layer.GitError) as exc:
        git_layer.assert_pushable("jarvis/capability-tide-times",
                                  scaffold.allowed_paths("tide-times"),
                                  "", scaffold.MANIFESTS)
    assert "no capability name to narrow them to" in str(exc.value)


def test_the_writer_and_the_checker_read_the_same_block(repo) -> None:
    """One function locates the list for both, so they cannot drift into a hole."""
    text = (repo / scaffold.RENDERED_TEMPLATE).read_text(encoding="utf-8")
    first, last = git_layer.enabled_block(text)
    lines = text.splitlines()
    assert lines[first - 1].strip() == "enabled:"
    assert all(not lines[n - 1].strip() or lines[n - 1].startswith("    ")
               for n in range(first + 1, last + 1))


def test_the_reserved_names_are_every_list_in_the_manifest_not_only_the_plugin_one(repo) -> None:
    """Deliberately broad. The manifest also lists Hermes' own toolsets, and a plugin
    called `terminal` or `memory` is a name collision waiting to confuse whoever reads
    the config next, whatever it does to the loop. Reserving a name the loop did not
    strictly have to reserve costs a clear error message; the other direction costs a
    deploy config that names one thing twice."""
    with pytest.raises(scaffold.ScaffoldError):
        _scaffold("terminal")
    with pytest.raises(scaffold.ScaffoldError):
        _scaffold("memory")
    # And the block finder is not confused by a second `enabled:` above the plugins one.
    assert git_layer.enabled_block(MANIFEST) is not None
    first, last = git_layer.enabled_block(MANIFEST)
    assert MANIFEST.splitlines()[first - 1] == "  enabled:"
    assert "- jarvis-guard" in "\n".join(MANIFEST.splitlines()[first:last])
    assert "- terminal" not in "\n".join(MANIFEST.splitlines()[first:last])


def test_the_refusal_names_the_ref_it_asked(repo) -> None:
    """So a refusal can be diagnosed rather than guessed at."""
    with pytest.raises(scaffold.ScaffoldError) as exc:
        _scaffold("existing-thing")
    assert "origin/main" in str(exc.value), "the refusal should say what it consulted"
    assert "asked of HEAD" not in str(exc.value)


def test_the_head_fallback_explains_itself(tmp_path, monkeypatch) -> None:
    """The one case this check refuses wrongly, and the message that makes it obvious.

    With no remote-tracking target and no local target branch there is no branch
    point to ask, so `target_ref` falls back to HEAD — and on HEAD a capability that
    has already committed its own scaffold reads as claiming its own name. It fails
    safe (a legitimate push refused, never a bad one allowed) and in production the
    remote resolves, but the message has to point at itself or it sends whoever hits
    it hunting for a collision that does not exist.
    """
    work = tmp_path / "solo"
    work.mkdir()
    _git(work, "init", "-b", "feature")
    _git(work, "config", "user.email", "selfext@test")
    _git(work, "config", "user.name", "selfext")
    (work / "hermes" / "plugins").mkdir(parents=True)
    (work / "hermes" / "plugins" / ".keep").write_text("", encoding="utf-8")
    for rel in scaffold.MANIFESTS:
        (work / rel).parent.mkdir(parents=True, exist_ok=True)
        (work / rel).write_text(MANIFEST, encoding="utf-8")
    _git(work, "add", "-A")
    _git(work, "commit", "-m", "seed")
    monkeypatch.setenv("SELFEXT_REPO_PATH", str(work))
    monkeypatch.setenv("SELFEXT_TARGET_BRANCH", "main")
    monkeypatch.setenv("SELFEXT_BRANCH_PREFIX", "jarvis/capability-")
    monkeypatch.setenv("SELFEXT_REMOTE", "origin")

    assert git_layer.target_ref() == "HEAD", "no remote and no local target to ask"
    scaffold.scaffold("tide-times", "Tides.", "tide", TOOLS)
    _git(work, "add", "-A")
    _git(work, "commit", "-m", "the capability")
    with pytest.raises(scaffold.ScaffoldError) as exc:
        scaffold.assert_name_is_free("tide-times")
    assert "asked of HEAD" in str(exc.value)
    assert "fetch the target branch and retry" in str(exc.value)


def test_a_name_hermes_itself_bundles_is_refused(repo) -> None:
    """The hole I had no answer for, half closed. `disk-cleanup` is bundled with
    Hermes and `disk-space` is ours, so this is not hypothetical. A collision is not
    an error anyone sees — Hermes loads its own plugins and ours from different
    directories — it is two plugins answering to one name in a config and in
    `hermes plugins list`."""
    contract = repo / scaffold.BUNDLED_CONTRACT
    contract.parent.mkdir(parents=True, exist_ok=True)
    contract.write_text(json.dumps({"plugins": ["disk-cleanup", "kanban", "spotify"]}),
                        encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "record what hermes bundles")
    _git(repo, "push", "origin", "main")
    with pytest.raises(scaffold.ScaffoldError):
        _scaffold("disk-cleanup")
    with pytest.raises(scaffold.ScaffoldError):
        _scaffold("spotify")
    _scaffold("tide-times")  # and a name nobody has taken still works


def test_a_missing_bundled_contract_does_not_stop_the_loop(repo) -> None:
    """A notification mechanism, not a gate. A loop that refused to run because a
    contract file had moved would be a worse failure than the collision it prevents,
    and the other two halves of the check still apply."""
    assert not (repo / scaffold.BUNDLED_CONTRACT).exists()
    assert git_layer.names_bundled_by_hermes(git_layer.target_ref(),
                                             scaffold.BUNDLED_CONTRACT) == set()
    _scaffold()  # does not raise
    with pytest.raises(scaffold.ScaffoldError):
        _scaffold("jarvis-guard")  # the manifest half still bites


def test_a_merge_request_can_carry_a_readme_line_and_nothing_else(repo) -> None:
    """selfext-mr-004 (2026-10-06): "add a line to the README and open a merge request" had no
    supported path, so the model tried git in a sandbox with no network, then the GitLab API
    from the browser. The owner allowed MRs beyond a new plugin, for files a person reviews."""
    (repo / "README.md").write_text("jarvis\nA one-line note.\n", encoding="utf-8")
    out = json.loads(tools.handle_selfext_open_mr(
        {"name": "readme-note", "title": "Add a note to the README",
         "description": "The owner asked for a one-line note.", "paths": ["README.md"]}))
    assert out["ok"] is True, out
    assert _git(repo, "show", "--name-only", "--pretty=", "HEAD").split() == ["README.md"]
    assert "refs/heads/jarvis/capability-readme-note" in _git(repo, "ls-remote", "--heads", "origin")


@pytest.mark.parametrize("path", [
    ".gitlab-ci.yml", ".gitlab/ci/x.yml", "deploy/entrypoint.sh", "scripts/smoke.sh", "Makefile",
    "jarvis-web/Dockerfile", "android-app/app/build.gradle.kts", "hermes/plugins/jarvis-guard/__init__.py",
    "hermes/plugins/_shared/_matrix.py", "hermes/plugins/jarvis-bwrap/sandbox.py",
    "hermes/plugins/jarvis-selfext/git.py", "../outside.md", "/etc/passwd", "." + "env",
    "aura-core/aura/core.py", "docs/../deploy/x.md",
])
def test_a_merge_request_refuses_anything_ci_would_run_or_that_governs_jarvis(repo, path) -> None:
    """A self-extension branch's MR pipeline runs that branch's own CI file and builds on the
    production host before anyone reviews it, so only files a person reads may ride along."""
    out = json.loads(tools.handle_selfext_open_mr(
        {"name": "sneaky", "description": "x", "paths": [path]}))
    assert out["ok"] is False and "may not carry" in out["error"], out
    assert "sneaky" not in _git(repo, "ls-remote", "--heads", "origin")


def test_the_approval_names_every_extra_path() -> None:
    asked = plugin._gate("selfext_open_mr", {"name": "readme-note", "paths": ["README.md", "docs/a.md"]})
    assert "README.md" in asked["message"] and "docs/a.md" in asked["message"]
    assert asked["rule_key"] != plugin._gate("selfext_open_mr", {"name": "readme-note"})["rule_key"], \
        "a remembered 'always' for the bare capability must not cover files it never named"
    other = plugin._gate("selfext_open_mr", {"name": "readme-note", "paths": ["docs/b.md"]})
    assert other["rule_key"] != asked["rule_key"]


def test_a_merge_request_carries_no_more_paths_than_the_card_names(repo) -> None:
    many = [f"docs/n{i}.md" for i in range(tools.MAX_EXTRA_PATHS + 1)]
    out = json.loads(tools.handle_selfext_open_mr({"name": "sprawl", "description": "x", "paths": many}))
    assert out["ok"] is False and "at most" in out["error"], out



def _gitlab_checkout(repo, monkeypatch, url="https://private@gitlab.example/owner/jarvis.git"):
    monkeypatch.setenv("GITLAB_TOKEN", "glpat-test-only")
    monkeypatch.setenv("GITLAB_API_URL", "https://gitlab.example/api/v4")
    monkeypatch.setenv("HASS_TOKEN", "another-secret")
    monkeypatch.setattr(plugin.git, "configured_project", lambda: "owner/jarvis")
    _git(repo, "remote", "set-url", "origin", url)
    _git(repo, "checkout", "-q", "-b", "jarvis/capability-x")


def test_the_token_push_goes_from_a_staging_repo_to_the_configured_gitlab_only(repo, monkeypatch) -> None:
    """The checkout's remote has no password, so every push was refused (2026-10-06); and the
    checkout is the model's to write, so its config, hooks and URL rewrites never see the token."""
    _gitlab_checkout(repo, monkeypatch)
    _git(repo, "config", "url.https://evil.example/.insteadOf", "https://gitlab.example/")
    for name in ("pre-push", "post-checkout", "reference-transaction"):
        hook = repo / ".git" / "hooks" / name
        hook.write_text("#!/bin/sh\nenv > \"$GIT_DIR/../hook-ran\"\n")
        hook.chmod(0o755)
    _git(repo, "config", "core.fsmonitor", f"touch {repo}/fsmonitor-ran; false")
    seen: list = []
    real_run = subprocess.run

    def run(argv, **kw):
        if "credential.helper=" in argv:
            seen.append((argv, kw["env"]))
            return subprocess.CompletedProcess(argv, 0, "", "")
        return real_run(argv, **kw)
    monkeypatch.setattr(plugin.git.subprocess, "run", run)
    out = plugin.git.open_merge_request("jarvis/capability-x", "t", "d")
    assert out["ok"] and len(seen) == 1
    argv, env = seen[0]
    assert "https://gitlab.example/owner/jarvis.git" in argv, "the trusted URL, credentials stripped"
    assert str(repo) not in argv, "pushed from the staging repo, not the model's checkout"
    assert "glpat-test-only" not in " ".join(argv) and "glpat" not in json.dumps(out)
    assert "HASS_TOKEN" not in env and env["GIT_CONFIG_GLOBAL"] == "/dev/null"
    assert not (repo / "hook-ran").exists(), "the checkout's hooks never run with the token about"
    assert not (repo / "fsmonitor-ran").exists(), "nor its fsmonitor"
    helper = [argv[i + 1] for i, a in enumerate(argv) if a == "-c"]
    assert helper[1].startswith("credential.https://gitlab.example.helper=")
    # The helper really answers git's credential protocol with the token -- for that host only.
    def fill(host):
        return real_run(["git", *sum((["-c", h] for h in helper), []), "credential", "fill"],
                        input=f"protocol=https\nhost={host}\n\n", capture_output=True, text=True,
                        env={"GITLAB_TOKEN": "glpat-test-only", "PATH": "/usr/bin:/bin", "HOME": "/nonexistent",
                             "GIT_TERMINAL_PROMPT": "0"}).stdout
    assert "password=glpat-test-only" in fill("gitlab.example")
    assert "glpat-test-only" not in fill("evil.example")


@pytest.mark.parametrize("url", ["https://evil.example/owner/jarvis.git", "http://gitlab.example/owner/jarvis.git",
                                 "/tmp/somewhere.git", "https://gitlab.example/owner/other-project.git"])
def test_a_remote_that_is_not_the_configured_gitlab_never_gets_the_token(repo, monkeypatch, url) -> None:
    _gitlab_checkout(repo, monkeypatch, url)
    with pytest.raises(plugin.git.GitError, match="not the configured GitLab"):  # host or project
        plugin.git.open_merge_request("jarvis/capability-x", "t", "d")


def test_without_a_token_the_push_uses_the_checkouts_own_credential(repo, monkeypatch) -> None:
    monkeypatch.delenv("GITLAB_TOKEN", raising=False)
    seen: list = []
    monkeypatch.setattr(plugin.git.subprocess, "run",
                        lambda argv, **kw: seen.append(argv) or subprocess.CompletedProcess(argv, 0, "", ""))
    plugin.git.open_merge_request("jarvis/capability-x", "t", "d")
    assert "credential.helper=" not in " ".join(seen[0])


def test_git_in_the_checkout_runs_with_no_secret_in_its_environment(repo, monkeypatch) -> None:
    """Off-image (no bwrap) the fallback still hands git nothing worth stealing."""
    monkeypatch.setenv("GITLAB_TOKEN", "glpat-test-only")
    _git(repo, "config", "core.fsmonitor", f"env > {repo}/fsmonitor-env; false")
    plugin.git.run("status", "--porcelain")
    leaked = (repo / "fsmonitor-env").read_text() if (repo / "fsmonitor-env").exists() else ""
    assert "glpat-test-only" not in leaked

