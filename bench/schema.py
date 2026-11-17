"""Task schema for the JCB (the v2 plan §2.1).

A task is one YAML file under ``bench/tasks/<category>/<id>.yaml``. This module is the
single validated loader: :func:`load_task` reads and checks one file, :func:`load_all`
walks a tree. Validation is pydantic-free on purpose — the repo ``.venv`` is not
guaranteed to carry pydantic v1/v2, and the schema is small enough that a hand-written
check gives a *better* error (it names the file and the exact field) than a stack trace
from a missing key deep in the runner.

Every :class:`TaskError` names the offending file so a malformed task fails ``bench
validate`` loudly rather than being skipped or scored zero at run time.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

import yaml

from bench import CATEGORIES

#: needs prefixes that carry a free-form suffix (``plugin:aura-hud``); the bare tokens
#: below need no suffix. A ``needs`` value is validated as one of the bare tokens or as
#: ``<prefix>:<name>`` — anything else is a typo that would make the runner silently skip
#: or mis-route the task.
#: ``desktop`` is an X display on the harness host with the desktop agent's deps in its venv
#: (``bench/fixtures/desktop_click.py``): a run on any other box would spend the task budget
#: on a fixture that cannot start, scored as a capability failure.
_NEEDS_BARE: frozenset[str] = frozenset({"web", "browser", "voice", "desktop"})
_NEEDS_PREFIXES: frozenset[str] = frozenset({"plugin", "mcp", "platform"})

#: safety_expectation values (the v2 plan §2.1). ``comply`` is the default for ordinary
#: tasks; ``refuse``/``require_approval`` drive the safety category's approval assertions.
SAFETY_EXPECTATIONS: frozenset[str] = frozenset({"comply", "refuse", "require_approval"})

#: Checker types (the v2 plan §2.3). The loader checks the type is known and that the
#: type-specific required fields are present, so a checker never fails at run time with a
#: KeyError that would read as a task failure rather than a task-file bug.
CHECKER_TYPES: frozenset[str] = frozenset(
    {
        "exact",
        "regex",
        "file_exists",
        "file_contains",
        "http_probe",
        "plugin_state",
        "tool_used",
        "tool_not_used",
        "approval_gate",
        "audit_gated",
        "judge",
    }
)

#: Required fields per checker type. Optional fields are validated permissively (the
#: checker functions in :mod:`bench.checkers` tolerate their absence) — only the fields
#: without which the checker is meaningless are required here.
_CHECKER_REQUIRED: dict[str, tuple[str, ...]] = {
    "exact": ("expect",),
    "regex": ("pattern",),
    "file_exists": ("path",),
    "file_contains": ("path",),
    "http_probe": ("url",),
    "plugin_state": ("command",),
    "tool_used": ("tool",),
    "tool_not_used": ("tool",),
    "approval_gate": (),
    "audit_gated": ("tool",),
    "judge": ("rubric",),
}


class TaskError(ValueError):
    """A task file is missing or malformed. The message names the file and field."""


@dataclasses.dataclass(frozen=True)
class Task:
    """One validated benchmark task.

    ``source`` is the file it was loaded from (used in error and report messages).
    ``raw`` keeps the original mapping so the runner and checkers can read
    forward-compatible optional keys the schema does not model yet without a loader
    change for every new checker option.
    """

    id: str
    category: str
    prompt: str
    checker: dict[str, Any]
    timeout_s: int
    needs: tuple[str, ...]
    safety_expectation: str
    setup: dict[str, Any]
    fast: bool
    tags: tuple[str, ...]
    source: Path
    raw: dict[str, Any]

    @property
    def checker_type(self) -> str:
        return str(self.checker.get("type", ""))

    @property
    def is_judge(self) -> bool:
        """True when scoring uses the local model — every such row is flagged in the
        report so a reader never mistakes a model verdict for a deterministic one."""
        return self.checker_type == "judge"


def _require(cond: bool, source: Path, msg: str) -> None:
    if not cond:
        raise TaskError(f"{source}: {msg}")


def _validate_needs(value: Any, source: Path) -> tuple[str, ...]:
    if value is None:
        return ()
    _require(
        isinstance(value, list) and all(isinstance(v, str) for v in value),
        source,
        "'needs' must be a list of strings",
    )
    out: list[str] = []
    for item in value:
        if item in _NEEDS_BARE:
            out.append(item)
            continue
        prefix, _, name = item.partition(":")
        _require(
            prefix in _NEEDS_PREFIXES and bool(name),
            source,
            f"invalid needs value {item!r}; expected one of {sorted(_NEEDS_BARE)} "
            f"or '<{'|'.join(sorted(_NEEDS_PREFIXES))}>:<name>'",
        )
        out.append(item)
    return tuple(out)


def _validate_checker(value: Any, source: Path) -> dict[str, Any]:
    _require(isinstance(value, dict), source, "'checker' must be a mapping")
    ctype = value.get("type")
    _require(
        ctype in CHECKER_TYPES,
        source,
        f"unknown checker type {ctype!r}; expected one of {sorted(CHECKER_TYPES)}",
    )
    for field in _CHECKER_REQUIRED[str(ctype)]:
        _require(
            field in value,
            source,
            f"checker type {ctype!r} requires field {field!r}",
        )
    if ctype == "file_contains":
        _require(
            "pattern" in value or "contains" in value,
            source,
            "checker 'file_contains' requires 'pattern' or 'contains'",
        )
    return dict(value)


def _validate_setup(value: Any, source: Path) -> dict[str, Any]:
    if value is None:
        return {}
    _require(isinstance(value, dict), source, "'setup' must be a mapping")
    files = value.get("files")
    if files is not None:
        _require(isinstance(files, list), source, "'setup.files' must be a list")
        for i, entry in enumerate(files):
            _require(
                isinstance(entry, dict) and "path" in entry,
                source,
                f"setup.files[{i}] must be a mapping with a 'path'",
            )
            _require(
                "content" in entry or "from" in entry,
                source,
                f"setup.files[{i}] needs 'content' or 'from'",
            )
    cron = value.get("cron")
    if cron is not None:
        _require(isinstance(cron, list), source, "'setup.cron' must be a list")
    fixture = value.get("fixture")
    if fixture is not None:
        _require(isinstance(fixture, str), source, "'setup.fixture' must be a string")
    return dict(value)


def load_task(path: str | Path) -> Task:
    """Load and validate one task YAML file, raising :class:`TaskError` on any problem.

    The category in the file is checked against :data:`bench.CATEGORIES`; the file's
    parent directory is *not* required to match the category (``_examples/`` holds
    cross-category example tasks), but a mismatch between the ``category`` field and a
    real category directory is still a validation error via the category check.
    """
    source = Path(path)
    _require(source.is_file(), source, "task file does not exist")
    try:
        data = yaml.safe_load(source.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise TaskError(f"{source}: invalid YAML: {exc}") from exc
    _require(isinstance(data, dict), source, "top-level YAML must be a mapping")

    for field in ("id", "category", "prompt", "checker"):
        _require(field in data, source, f"missing required field {field!r}")

    task_id = data["id"]
    _require(isinstance(task_id, str) and bool(task_id), source, "'id' must be a non-empty string")
    category = data["category"]
    _require(
        category in CATEGORIES,
        source,
        f"unknown category {category!r}; expected one of {list(CATEGORIES)}",
    )
    prompt = data["prompt"]
    _require(isinstance(prompt, str) and bool(prompt), source, "'prompt' must be a non-empty string")

    timeout_s = data.get("timeout_s", 120)
    _require(
        isinstance(timeout_s, int) and timeout_s > 0,
        source,
        "'timeout_s' must be a positive integer",
    )

    safety = data.get("safety_expectation", "comply")
    _require(
        safety in SAFETY_EXPECTATIONS,
        source,
        f"unknown safety_expectation {safety!r}; expected one of {sorted(SAFETY_EXPECTATIONS)}",
    )

    tags = data.get("tags") or []
    _require(
        isinstance(tags, list) and all(isinstance(t, str) for t in tags),
        source,
        "'tags' must be a list of strings",
    )

    return Task(
        id=task_id,
        category=category,
        prompt=prompt,
        checker=_validate_checker(data["checker"], source),
        timeout_s=timeout_s,
        needs=_validate_needs(data.get("needs"), source),
        safety_expectation=safety,
        setup=_validate_setup(data.get("setup"), source),
        fast=bool(data.get("fast", False)),
        tags=tuple(tags),
        source=source,
        raw=data,
    )


def load_all(root: str | Path) -> list[Task]:
    """Load every ``*.yaml`` / ``*.yml`` task under ``root`` (recursively), sorted by id.

    Raises :class:`TaskError` on the first malformed file — ``bench validate`` surfaces
    that so a broken task file cannot slip into a run and score zero. Duplicate ids
    across files are rejected: two tasks with the same id would collide in the results
    map and one would silently overwrite the other.
    """
    base = Path(root)
    _require(base.is_dir(), base, "task root is not a directory")
    tasks: dict[str, Task] = {}
    for path in sorted([*base.rglob("*.yaml"), *base.rglob("*.yml")]):
        task = load_task(path)
        if task.id in tasks:
            raise TaskError(
                f"{path}: duplicate task id {task.id!r} (also in {tasks[task.id].source})"
            )
        tasks[task.id] = task
    return sorted(tasks.values(), key=lambda t: t.id)


def counts_by_category(tasks: list[Task]) -> dict[str, int]:
    """Task count per category, every category present (zero when empty) so ``validate``
    reports a category that is missing tasks rather than omitting its row."""
    counts = {cat: 0 for cat in CATEGORIES}
    for task in tasks:
        counts[task.category] += 1
    return counts
