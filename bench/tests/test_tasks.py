"""Structural tests for the JCB task corpus (the v2 plan §2.4).

These never run a task against the stack — they check the *files*: that the corpus has the
category counts the plan requires, unique ids, a fast subset that touches every category,
well-formed checkers, correctly flagged judge tasks, safety tasks that never expect
compliance, and that every fixture a task points at (audio clips, served images/docs)
actually exists on disk. A task that references a missing fixture would fail opaquely at
run time; here it fails loudly with the id and the missing path.

The loader is :mod:`bench.schema` when present (the harness owns it); a small local loader
stands in if it is not, so this file is useful before the harness lands. Only the ten real
category directories are scanned — ``bench/tasks/_examples/`` holds the harness's
per-checker-type samples, which are not part of the counted corpus.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parents[1]
_TASKS = _REPO / "bench" / "tasks"
_FIXTURES = _REPO / "bench" / "fixtures"

CATEGORIES: tuple[str, ...] = (
    "web-research",
    "browser-action",
    "homelab-ops",
    "coding-selfext",
    "personal-data",
    "proactive-scheduled",
    "multimodal",
    "voice",
    "safety",
    "intelligence",
)

#: The §2.4 plan (66 tasks total, ≥60 required), plus the tasks later phases add for the
#: capabilities they build — the brief's rule that every new capability carries at least two
#: benchmark tasks means this table grows, and it stays pinned so a task cannot be *removed*
#: to make a category look better. Phase 5 added three to intelligence (memory-store-010,
#: delegate-batch-010, routing-table-009). Phase 4 added nine to homelab-ops (n8n list and
#: activate, jellyfin, mail read and send, calendar, contacts, files, grafana dashboards —
#: a read task and, where the service can be changed, an approval-gated write) and three to
#: coding-selfext (selfext plan, scaffold-and-verify, and the gated merge request). Phase 6
#: added two to proactive-scheduled (alert-vram-007 and brief-scheduled-006) ; the two HUD-layout tasks sit in
#: bench/tasks/hud/ outside the ten category directories, so this table does not count them. The media
#: work (JARVIS_MEDIA_BRIEF, 2026-09-18) added five to multimodal: media-fetch-007, media-image-008,
#: media-doc-009, media-handoff-010 and media-gap-011 — the store, the panels and the routing gate, each
#: asserted on the tool-call trace (see test_media_tasks_assert_the_brief_s_gates). Equality rather than a floor is the point: a task cannot be removed
#: to flatter a category, and adding one makes the author say here what they added and why.
EXPECTED_COUNTS: dict[str, int] = {
    "web-research": 8,
    "browser-action": 7,
    "homelab-ops": 17,
    "coding-selfext": 9,
    "personal-data": 6,
    "proactive-scheduled": 7,
    "multimodal": 11,
    "voice": 5,
    "safety": 8,
    "intelligence": 11,
}

CHECKER_TYPES = {
    "exact", "regex", "file_exists", "file_contains", "http_probe",
    "plugin_state", "tool_used", "tool_not_used", "approval_gate", "judge",
}

# Fields a checker is useless without — mirrors bench.schema so this test still guards the
# corpus if a future schema.py loosens a rule the plan relies on.
CHECKER_REQUIRED: dict[str, tuple[str, ...]] = {
    "exact": ("expect",),
    "regex": ("pattern",),
    "file_exists": ("path",),
    "file_contains": ("path",),   # plus one of pattern/contains, checked below
    "http_probe": ("url",),
    "plugin_state": ("command",),
    "tool_used": ("tool",),
    "tool_not_used": ("tool",),
    "approval_gate": (),
    "judge": ("rubric",),
}


class _Task:
    """Minimal task view used whether the schema loader or the local fallback produced it."""

    def __init__(self, data: dict, source: Path):
        self.id = data.get("id")
        self.category = data.get("category")
        self.prompt = data.get("prompt")
        self.checker = data.get("checker") or {}
        self.setup = data.get("setup") or {}
        self.needs = tuple(data.get("needs") or ())
        self.safety_expectation = data.get("safety_expectation", "comply")
        self.tags = tuple(data.get("tags") or ())
        self.fast = bool(data.get("fast", False))
        self.source = source

    @property
    def checker_type(self) -> str:
        return str(self.checker.get("type", ""))


def _load_corpus() -> list[_Task]:
    """Load the ten category directories, preferring bench.schema, else a local YAML load."""
    import yaml

    try:
        from bench.schema import load_task as _schema_load

        def load_one(path: Path) -> _Task:
            t = _schema_load(path)
            return _Task(t.raw, path)
    except Exception:  # noqa: BLE001 - the local loader must work with no bench package
        def load_one(path: Path) -> _Task:
            return _Task(yaml.safe_load(path.read_text(encoding="utf-8")), path)

    tasks: list[_Task] = []
    for category in CATEGORIES:
        cat_dir = _TASKS / category
        assert cat_dir.is_dir(), f"missing category directory {cat_dir}"
        for path in sorted([*cat_dir.glob("*.yaml"), *cat_dir.glob("*.yml")]):
            tasks.append(load_one(path))
    return tasks


CORPUS = _load_corpus()


def test_corpus_loads_and_is_large_enough():
    assert len(CORPUS) >= 60, f"only {len(CORPUS)} tasks; the brief requires at least 60"


def test_category_counts_match_plan():
    counts = {cat: 0 for cat in CATEGORIES}
    for task in CORPUS:
        assert task.category in CATEGORIES, f"{task.source}: bad category {task.category!r}"
        counts[task.category] += 1
    assert counts == EXPECTED_COUNTS, f"counts {counts} != plan {EXPECTED_COUNTS}"


def test_ids_are_unique_and_present():
    seen: dict[str, Path] = {}
    for task in CORPUS:
        assert task.id, f"{task.source}: missing id"
        assert task.id not in seen, f"duplicate id {task.id} in {task.source} and {seen[task.id]}"
        seen[task.id] = task.source


def test_task_dir_matches_category():
    for task in CORPUS:
        assert task.source.parent.name == task.category, (
            f"{task.source}: lives under {task.source.parent.name} but category is {task.category}")


@pytest.mark.parametrize("task", CORPUS, ids=lambda t: t.id)
def test_required_scalar_fields(task: _Task):
    assert isinstance(task.prompt, str) and task.prompt.strip(), f"{task.id}: empty prompt"
    assert task.safety_expectation in {"comply", "refuse", "require_approval"}, task.id


@pytest.mark.parametrize("task", CORPUS, ids=lambda t: t.id)
def test_checker_well_formed(task: _Task):
    ctype = task.checker_type
    assert ctype in CHECKER_TYPES, f"{task.id}: unknown checker type {ctype!r}"
    for field in CHECKER_REQUIRED[ctype]:
        assert field in task.checker, f"{task.id}: checker {ctype!r} needs field {field!r}"
    if ctype == "file_contains":
        assert "pattern" in task.checker or "contains" in task.checker, (
            f"{task.id}: file_contains needs 'pattern' or 'contains'")
    if ctype == "http_probe":
        assert str(task.checker["url"]).startswith("http"), f"{task.id}: http_probe url not a URL"


@pytest.mark.parametrize("task", CORPUS, ids=lambda t: t.id)
def test_needs_tokens_valid(task: _Task):
    for need in task.needs:
        if need in {"web", "browser", "voice"}:
            continue
        prefix, _, name = need.partition(":")
        assert prefix in {"plugin", "mcp", "platform"} and name, f"{task.id}: bad needs {need!r}"


def test_judge_tasks_are_flagged():
    for task in CORPUS:
        if task.checker_type == "judge":
            assert "judge" in task.tags, f"{task.id}: judge checker but not tagged 'judge'"
            assert "rubric" in task.checker, f"{task.id}: judge checker without a rubric"


def test_every_safety_task_expects_no_compliance():
    safety = [t for t in CORPUS if t.category == "safety"]
    assert safety, "no safety tasks"
    for task in safety:
        assert task.safety_expectation in {"refuse", "require_approval"}, (
            f"{task.id}: safety task must not expect 'comply'")


def test_fast_subset_size_and_coverage():
    fast = [t for t in CORPUS if t.fast]
    assert 12 <= len(fast) <= 18, f"fast subset is {len(fast)} tasks; want 12–18"
    for task in fast:
        assert "fast" in task.tags, f"{task.id}: fast task not tagged 'fast'"
    covered = {t.category for t in fast}
    missing = set(CATEGORIES) - covered
    assert not missing, f"fast subset misses categories: {sorted(missing)}"


def test_voice_tasks_reference_an_existing_audio_clip():
    for task in CORPUS:
        if task.category != "voice":
            continue
        audio = task.setup.get("audio")
        assert audio, f"{task.id}: voice task has no setup.audio"
        path = (_REPO / audio).resolve()
        assert path.is_file(), f"{task.id}: audio fixture {audio} not found at {path}"


def _referenced_asset_paths(task: _Task) -> list[tuple[str, Path]]:
    """Every /assets/<f> or /docs/<f> the task's prompt or checker names, mapped to disk."""
    blob = " ".join([
        task.prompt or "",
        str(task.checker.get("url", "")),
        str(task.checker.get("command", "")),
    ])
    out: list[tuple[str, Path]] = []
    for rel in re.findall(r"/assets/([\w.\-]+)", blob):
        out.append((f"/assets/{rel}", _FIXTURES / "assets" / rel))
    for rel in re.findall(r"/docs/([\w.\-]+)", blob):
        out.append((f"/docs/{rel}", _FIXTURES / "documents" / rel))
    return out


#: The media brief's five gates, as the checker each task carries. Pinned by id so a
#: task cannot be quietly re-pointed at a weaker assertion (a `tool_used` on `terminal`
#: would pass media-fetch-007 for the very shell download the brief retires).
MEDIA_GATES: dict[str, tuple[str, object]] = {
    "media-fetch-007": ("tool_used", "media_fetch"),
    "media-image-008": ("tool_used", ["image_generate", "comfyui_submit"]),
    "media-doc-009": ("tool_used", "artifact_publish"),
    "media-handoff-010": ("tool_used", "artifact_send"),
    "media-gap-011": ("tool_not_used", ["terminal", "execute_code"]),
}


def test_media_tasks_assert_the_brief_s_gates():
    by_id = {t.id: t for t in CORPUS}
    for task_id, (ctype, tool) in MEDIA_GATES.items():
        task = by_id.get(task_id)
        assert task is not None, f"{task_id} missing from bench/tasks/multimodal/"
        assert task.category == "multimodal", task_id
        assert task.checker_type == ctype, f"{task_id}: checker {task.checker_type!r}, want {ctype!r}"
        assert task.checker["tool"] == tool, f"{task_id}: tool {task.checker['tool']!r}, want {tool!r}"
        assert "plugin:jarvis-artifacts" in task.needs, f"{task_id}: must gate on the artifacts plugin"
        # A negative on a runtime install is empty without the verb it bans.
        if ctype == "tool_not_used":
            assert task.checker.get("args_contains") == "install", task_id


def test_referenced_fixture_assets_exist():
    for task in CORPUS:
        for ref, path in _referenced_asset_paths(task):
            assert path.is_file(), f"{task.id}: references {ref} but {path} is missing"


def test_fixture_server_and_creds_present():
    assert (_FIXTURES / "server.py").is_file(), "fixture server missing"
    creds = _FIXTURES / "creds.json"
    assert creds.is_file(), "fixture creds.json missing"
    import json

    data = json.loads(creds.read_text(encoding="utf-8"))
    assert data.get("username") and data.get("password"), "creds.json needs username+password"
    # A real GitLab/OpenSSH secret must never live in the fixtures.
    blob = creds.read_text(encoding="utf-8")
    assert "glpat-" not in blob and "BEGIN OPENSSH" not in blob, "creds.json looks like a real secret"
