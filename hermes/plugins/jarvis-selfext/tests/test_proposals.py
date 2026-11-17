"""The distillation loop's review queue, against a real checkout in a temp dir.

The media brief's gate: *"a synthetic long tool chain produces a skill proposal in
the review queue; approving it results in a script, a test, a SKILL.md and a
benchmark task on a branch."* The checkout is faked the way ``test_selfext.py``
fakes it — a real git repository — minus the bare remote, because approving never
pushes: the merge request stays ``selfext_open_mr``'s and is proven there.
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
proposals = plugin.proposals
scaffold = plugin.scaffold
git_layer = plugin.git

MANIFEST = "plugins:\n  enabled:\n    - jarvis-guard\n    - smart-home\n  disabled:\n    - web-exa\n"

#: A chain long enough to be a skill: what the brief calls "a synthetic long tool chain".
CHAIN = ["proxmox_guests", "proxmox_guest_action", "terminal", "artifact_publish"]
PROPOSAL = {
    "task": "Snapshot every Proxmox guest and publish the report.",
    "chain": CHAIN, "name": "guest-snapshots",
    "bench_prompt": "Snapshot every guest and show me the report.",
    "dead_ends": ["proxmox_guest_action on a stopped guest fails; skip those"],
    "parameters": ["which guests", "the snapshot name"],
    "against": "Proxmox already snapshots on a schedule; this may be a weekly job, not a skill.",
}


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                          check=True).stdout


@pytest.fixture
def repo(tmp_path, monkeypatch) -> Path:
    work = tmp_path / "work"
    work.mkdir()
    _git(work, "init", "-b", "main")
    _git(work, "config", "user.email", "selfext@test")
    _git(work, "config", "user.name", "selfext")
    (work / "hermes" / "plugins").mkdir(parents=True)
    (work / "hermes" / "plugins" / ".keep").write_text("", encoding="utf-8")
    # A skill that already ships, so a proposal may not take its name and rewrite it.
    (work / "hermes" / "skills" / "existing-skill").mkdir(parents=True)
    (work / "hermes" / "skills" / "existing-skill" / "SKILL.md").write_text("shipped\n")
    for rel in scaffold.MANIFESTS:
        (work / rel).parent.mkdir(parents=True, exist_ok=True)
        (work / rel).write_text(MANIFEST, encoding="utf-8")
    _git(work, "add", "-A")
    _git(work, "commit", "-m", "seed")
    monkeypatch.setenv("SELFEXT_REPO_PATH", str(work))
    monkeypatch.setenv("SELFEXT_TARGET_BRANCH", "main")
    monkeypatch.setenv("SELFEXT_BRANCH_PREFIX", "jarvis/capability-")
    monkeypatch.setenv("SELFEXT_REMOTE", "origin")
    return work


@pytest.fixture(autouse=True)
def fresh_queue(monkeypatch):
    """An empty in-memory queue and an empty panel table per test."""
    monkeypatch.setattr(proposals, "QUEUE", proposals._Queue())
    hud = proposals._hud()
    assert hud is not None, "jarvis-hud must sit beside this plugin for the queue's panel"
    monkeypatch.setattr(hud, "STORE", hud.HudStore(emitter=lambda *_a: False))
    return hud


def _ctx():
    ctx = plugin.FakeCtx()
    plugin.register(ctx)
    return ctx


def _propose(ctx, session="hud-session", **overrides) -> dict:
    return json.loads(ctx.tools["skill_propose"]["handler"]({**PROPOSAL, **overrides},
                                                             session_id=session))


def _panels(hud, session="hud-session") -> list[dict]:
    return hud.STORE.list(hud._state_key(session))


def test_a_repeated_tool_chain_becomes_a_proposal_in_the_queue(repo, fresh_queue) -> None:
    ctx = _ctx()
    out = _propose(ctx)
    assert out["ok"] and out["already"] is False, out
    row = out["proposal"]
    assert len(row["id"]) == 8 and row["status"] == "open" and row["chain"] == CHAIN
    assert row["tool"] == "guest_snapshots", "the tool name defaults from the plugin name"

    listed = json.loads(ctx.tools["skill_proposals"]["handler"]({"action": "list"}))
    assert [r["id"] for r in listed["proposals"]] == [row["id"]]

    # The review queue the person sees: one notice on the session that made it.
    panels = _panels(fresh_queue)
    assert len(panels) == 1 and panels[0]["kind"] == "notice"
    assert panels[0]["id"] == "p_" + row["id"] and panels[0]["origin"] == "system"
    assert panels[0]["payload"]["proposal_id"] == row["id"]
    assert f"approve {row['id']}" in panels[0]["payload"]["text"]

    # Proposing the same skill twice is one row, not two panels.
    again = _propose(ctx)
    assert again["already"] is True and again["proposal"]["id"] == row["id"]
    assert len(_panels(fresh_queue)) == 1


def test_a_short_chain_is_not_a_skill(repo) -> None:
    out = _propose(_ctx(), chain=["terminal", "terminal"])
    assert out["ok"] is False and "not a skill" in out["error"]


def test_approving_a_proposal_scaffolds_the_four_files_on_a_branch(repo, fresh_queue) -> None:
    ctx = _ctx()
    pid = _propose(ctx)["proposal"]["id"]
    out = json.loads(ctx.tools["skill_proposals"]["handler"]({"action": "approve", "id": pid}))
    assert out["ok"], out
    assert out["branch"] == "jarvis/capability-guest-snapshots"
    assert git_layer.current_branch() == out["branch"], "the files land on the capability branch"

    # The four files the brief names: a script, a test, a SKILL.md and a benchmark task.
    script = repo / "hermes" / "plugins" / "guest-snapshots" / "__init__.py"
    test = repo / "hermes" / "plugins" / "guest-snapshots" / "tests" / "test_guest_snapshots.py"
    skill = repo / "hermes" / "skills" / "guest-snapshots" / "SKILL.md"
    task = repo / "bench" / "tasks" / "coding-selfext" / "selfext-guest-snapshots-001.yaml"
    for path in (script, test, skill, task):
        assert path.is_file(), path
        assert str(path.relative_to(repo)) in out["files"]
    assert "def handle_guest_snapshots" in script.read_text() and "TODO" in script.read_text()
    body = skill.read_text()
    assert "name: guest-snapshots" in body
    for index, tool in enumerate(CHAIN, 1):
        assert f"{index}. `{tool}`" in body, "the working sequence, in order"
    assert "stopped guest" in body and "which guests" in body
    assert PROPOSAL["bench_prompt"] in task.read_text()
    # The plan is selfext_plan's, and the loop's next steps are the loop's.
    assert out["plan"]["ok"] and out["plan"]["capability"] == "guest-snapshots"
    assert out["next"][-2:] == ["selfext_verify", "selfext_open_mr"]

    # Uncommitted and unpushed: the merge request is a separate, gated act.
    assert _git(repo, "rev-list", "--count", "HEAD").strip() == "1"
    assert _git(repo, "status", "--porcelain").strip()
    # A push from here would carry exactly those files: the skill dir is in the allowlist.
    assert git_layer.stray_changes(scaffold.allowed_paths("guest-snapshots")) == []

    # The row is settled and its panel is gone.
    assert _panels(fresh_queue) == []
    listed = json.loads(ctx.tools["skill_proposals"]["handler"]({"action": "list", "all": True}))
    assert listed["proposals"][0]["status"] == "approved"
    assert json.loads(ctx.tools["skill_proposals"]["handler"]({"action": "list"}))["count"] == 0
    twice = json.loads(ctx.tools["skill_proposals"]["handler"]({"action": "approve", "id": pid}))
    assert twice["ok"] is False and "approved, not open" in twice["error"]


def test_a_dropped_proposal_leaves_no_panel_behind(repo, fresh_queue) -> None:
    ctx = _ctx()
    pid = _propose(ctx)["proposal"]["id"]
    assert len(_panels(fresh_queue)) == 1
    out = json.loads(ctx.tools["skill_proposals"]["handler"]({"action": "drop", "id": pid}))
    assert out["ok"] and out["status"] == "dropped"
    assert _panels(fresh_queue) == []
    assert json.loads(ctx.tools["skill_proposals"]["handler"]({"action": "list"}))["count"] == 0
    assert not (repo / "hermes" / "plugins" / "guest-snapshots").exists()
    refused = json.loads(ctx.tools["skill_proposals"]["handler"]({"action": "approve", "id": pid}))
    assert refused["ok"] is False and "dropped, not open" in refused["error"]


def test_a_proposal_may_not_take_a_shipped_skill_s_name(repo) -> None:
    ctx = _ctx()
    pid = _propose(ctx, name="existing-skill")["proposal"]["id"]
    out = json.loads(ctx.tools["skill_proposals"]["handler"]({"action": "approve", "id": pid}))
    assert out["ok"] is False and "already the name of a plugin" in out["error"]
    assert git_layer.current_branch() == "main", "a refused approve makes no branch"
    assert (repo / "hermes" / "skills" / "existing-skill" / "SKILL.md").read_text() == "shipped\n"


def test_the_queue_lives_in_plugin_state_when_there_is_one(repo, monkeypatch) -> None:
    """A cron turn writes the row; a HUD session, in another process, reads it."""
    file: dict = {}

    class _State:  # Hermes' PluginState: get(key, default) / set(key, value)
        def get(self, key, default=None):
            return file.get(key, default)

        def set(self, key, value):
            file[key] = json.loads(json.dumps(value))

    ctx = plugin.FakeCtx()
    ctx.state = _State()
    plugin.register(ctx)
    pid = _propose(ctx, session=None)["proposal"]["id"]
    assert [r["id"] for r in file["proposals"]] == [pid]
    # A second queue over the same state sees the row: nothing is held in memory.
    other = proposals._Queue()
    other.backend = ctx.state
    assert other.rows()[0]["id"] == pid


def test_the_command_lists_approves_and_drops(repo) -> None:
    ctx = _ctx()
    pid = _propose(ctx)["proposal"]["id"]
    command = ctx.commands["skill_proposals"]
    assert json.loads(command("@abc123 list"))["count"] == 1
    assert json.loads(command(""))["count"] == 1
    assert command("merge " + pid) == proposals.USAGE
    assert json.loads(command(f"drop {pid}"))["status"] == "dropped"
    assert json.loads(command("list"))["count"] == 0
    assert command("approve nope").startswith("skill_proposals error: no proposal")


@pytest.mark.parametrize("against", [None, "", "   ",
                                     "snapshot every proxmox guest and publish the report"])
def test_a_proposal_without_a_case_against_it_is_refused(repo, fresh_queue, against) -> None:
    params = {k: v for k, v in PROPOSAL.items() if k != "against"}
    if against is not None:
        params["against"] = against
    ctx = _ctx()
    out = json.loads(ctx.tools["skill_propose"]["handler"](params, session_id="hud-session"))
    assert out["ok"] is False and "against" in out["error"], out
    assert proposals.QUEUE.rows() == [] and _panels(fresh_queue) == []


def test_the_panel_and_review_weigh_for_against_and_overlaps(repo, fresh_queue) -> None:
    # A shipped skill sharing two words with the proposal, and one sharing only one.
    (repo / "hermes" / "skills" / "proxmox-guest-notes").mkdir(parents=True)
    (repo / "hermes" / "skills" / "guest-wifi").mkdir(parents=True)
    ctx = _ctx()
    row = _propose(ctx, name="proxmox-guest-report",
                   against="It is\nthree calls the model\tmakes fine by hand.")["proposal"]
    # One plain line: the case against can be stranger-influenced text.
    assert row["against"] == "It is three calls the model makes fine by hand."
    # proxmox_guest_action is a matrix row sharing "proxmox" and "guest";
    # proxmox_guests shares only "proxmox" ("guests" is not "guest").
    assert "skill proxmox-guest-notes" in row["overlaps"]
    assert "tool proxmox_guest_action" in row["overlaps"]
    assert not any(o.endswith(("guest-wifi", "proxmox_guests")) for o in row["overlaps"])

    text = _panels(fresh_queue)[0]["payload"]["text"]
    assert "For: Snapshot every Proxmox guest" in text
    assert "Against: It is three calls the model makes fine by hand." in text
    assert "Overlaps: " in text and "proxmox-guest-notes" in text and "\n" not in text

    listed = json.loads(ctx.tools["skill_proposals"]["handler"]({"action": "list"}))
    shown = listed["proposals"][0]
    assert shown["against"] == row["against"] and shown["overlaps"] == row["overlaps"]


def test_overlap_words_ignore_stopwords_against_one_shared_word() -> None:
    assert proposals._words("the-guest_of-report") == {"guest", "report"}
    assert proposals._overlaps("the-guest-of-honour", ["guest-of-the-house"], []) == []
    assert proposals._overlaps("guest-report", ["guest-report-weekly"], ["guest_report"]) == [
        "skill guest-report-weekly", "tool guest_report"]
