"""Schema loader: a good task loads, every malformed shape raises a TaskError naming the
file and field, and load_all rejects duplicate ids."""

from __future__ import annotations

import textwrap

import pytest

from bench.schema import Task, TaskError, counts_by_category, load_all, load_task

_GOOD = textwrap.dedent(
    """
    id: web-research-001
    category: web-research
    prompt: "What is the current stable version of Hermes Agent? Cite the source."
    checker:
      type: regex
      pattern: "v?\\\\d+\\\\.\\\\d+\\\\.\\\\d+"
      must_cite: true
    timeout_s: 90
    needs: [web]
    safety_expectation: comply
    fast: true
    tags: [smoke, versions]
    """
).strip()


def _write(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def test_load_good_task(tmp_path):
    task = load_task(_write(tmp_path, "t.yaml", _GOOD))
    assert isinstance(task, Task)
    assert task.id == "web-research-001"
    assert task.category == "web-research"
    assert task.checker_type == "regex"
    assert task.checker["must_cite"] is True
    assert task.timeout_s == 90
    assert task.needs == ("web",)
    assert task.fast is True
    assert task.tags == ("smoke", "versions")
    assert task.is_judge is False


def test_defaults_applied(tmp_path):
    text = textwrap.dedent(
        """
        id: t-1
        category: intelligence
        prompt: hello
        checker: {type: exact, expect: hi}
        """
    ).strip()
    task = load_task(_write(tmp_path, "t.yaml", text))
    assert task.timeout_s == 120
    assert task.safety_expectation == "comply"
    assert task.needs == ()
    assert task.fast is False


@pytest.mark.parametrize(
    "text,fragment",
    [
        ("id: x\ncategory: web-research\nprompt: p", "missing required field 'checker'"),
        ("id: x\ncategory: nope\nprompt: p\nchecker: {type: exact, expect: a}", "unknown category"),
        ("id: x\ncategory: voice\nprompt: p\nchecker: {type: bogus}", "unknown checker type"),
        ("id: x\ncategory: voice\nprompt: p\nchecker: {type: regex}", "requires field 'pattern'"),
        (
            "id: x\ncategory: voice\nprompt: p\nchecker: {type: exact, expect: a}\nneeds: [bogus]",
            "invalid needs value",
        ),
        (
            "id: x\ncategory: voice\nprompt: p\nchecker: {type: exact, expect: a}\nsafety_expectation: maybe",
            "unknown safety_expectation",
        ),
        (
            "id: x\ncategory: voice\nprompt: p\nchecker: {type: file_contains, path: /a}",
            "requires 'pattern' or 'contains'",
        ),
        ("id: x\ncategory: voice\nprompt: p\nchecker: {type: exact, expect: a}\ntimeout_s: 0", "positive integer"),
    ],
)
def test_bad_tasks_raise(tmp_path, text, fragment):
    with pytest.raises(TaskError) as exc:
        load_task(_write(tmp_path, "bad.yaml", textwrap.dedent(text).strip()))
    assert fragment in str(exc.value)
    assert "bad.yaml" in str(exc.value)


def test_invalid_yaml_raises(tmp_path):
    with pytest.raises(TaskError) as exc:
        load_task(_write(tmp_path, "broken.yaml", "id: [unclosed"))
    assert "invalid YAML" in str(exc.value)


def test_needs_prefixed_forms(tmp_path):
    text = textwrap.dedent(
        """
        id: t
        category: homelab-ops
        prompt: p
        checker: {type: tool_used, tool: gpu}
        needs: [plugin:jarvis-hud, mcp:notes, platform:telegram, browser]
        """
    ).strip()
    task = load_task(_write(tmp_path, "t.yaml", text))
    assert task.needs == ("plugin:jarvis-hud", "mcp:notes", "platform:telegram", "browser")


def test_load_all_and_duplicate_ids(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    _write(tmp_path / "a", "one.yaml", _GOOD)
    _write(
        tmp_path / "b",
        "two.yaml",
        "id: safety-1\ncategory: safety\nprompt: p\nchecker: {type: exact, expect: no}",
    )
    tasks = load_all(tmp_path)
    assert [t.id for t in tasks] == ["safety-1", "web-research-001"]
    counts = counts_by_category(tasks)
    assert counts["web-research"] == 1
    assert counts["safety"] == 1
    assert counts["voice"] == 0

    _write(tmp_path / "b", "dup.yaml", _GOOD)  # same id as a/one.yaml
    with pytest.raises(TaskError) as exc:
        load_all(tmp_path)
    assert "duplicate task id" in str(exc.value)
