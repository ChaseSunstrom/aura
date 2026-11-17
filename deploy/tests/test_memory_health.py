"""deploy/memory_health.py: tool lessons are retired when a deploy changes the tool (T-0208)."""
from __future__ import annotations

import importlib.util
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location("memory_health", Path(__file__).resolve().parents[1] / "memory_health.py")
assert _SPEC and _SPEC.loader
mh = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(mh)

MATRIX = '''RULES = (
    Rule(("phone_action",), FREE, "acts on the owner's phone"),
    Rule(("hud_show", "hud_dismiss"), FREE, "panels"),
    Rule(("gpu_status",), FREE, "reads the cards"),
    Rule(("memory",), ASK, "removes or rewrites what JARVIS remembers"),
)
'''

# The holographic store's own schema, triggers included (they keep the FTS index in step).
SCHEMA = '''
CREATE TABLE facts (fact_id INTEGER PRIMARY KEY AUTOINCREMENT, content TEXT NOT NULL UNIQUE,
    category TEXT DEFAULT 'general', tags TEXT DEFAULT '', trust_score REAL DEFAULT 0.5,
    retrieval_count INTEGER DEFAULT 0, helpful_count INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
CREATE VIRTUAL TABLE facts_fts USING fts5(content, tags, content=facts, content_rowid=fact_id);
CREATE TRIGGER facts_au AFTER UPDATE ON facts BEGIN
    INSERT INTO facts_fts(facts_fts, rowid, content, tags) VALUES ('delete', old.fact_id, old.content, old.tags);
    INSERT INTO facts_fts(rowid, content, tags) VALUES (new.fact_id, new.content, new.tags);
END;
CREATE TRIGGER facts_ai AFTER INSERT ON facts BEGIN
    INSERT INTO facts_fts(rowid, content, tags) VALUES (new.fact_id, new.content, new.tags);
END;
'''


def _plugin(root: Path, name: str, source: str) -> None:
    (root / name).mkdir(parents=True, exist_ok=True)
    (root / name / "plugin.yaml").write_text(f"name: {name}\n")
    (root / name / "__init__.py").write_text(source)


def _home(tmp_path: Path) -> tuple[Path, Path]:
    seed, home = tmp_path / "seed", tmp_path / "home"
    (seed / "_shared").mkdir(parents=True)
    (seed / "_shared" / "_matrix.py").write_text(MATRIX)
    for root in (seed, home / "plugins"):
        _plugin(root, "jarvis-hud", 'register(name="phone_action")\nregister(name="hud_show")\n')
        _plugin(root, "gpu-telemetry", 'register(name="gpu_status")\n')
    db = sqlite3.connect(home / "memory_store.db")
    db.executescript(SCHEMA)
    db.executemany("INSERT INTO facts (content, category, trust_score, tags) VALUES (?, ?, ?, ?)", [
        ("From cron sessions, phone_action fails with 'no phone is connected'.", "tool", 0.5, ""),
        ("gpu_status is DEFERRED: call it through tool_call.", "tool", 0.5, "cron"),
        ("tool_call takes exactly one entry per local tool.", "tool", 0.5, ""),
        ("Sir likes the phone_action confirmations short.", "user_pref", 0.8, ""),
        ("A memory rewrite is gated with no approver.", "tool", 0.5, ""),
        ("phone_action and hud_show were both slow on 09-30.", "tool", 0.2, ""),
    ])
    db.commit()
    db.close()
    return seed, home


def _facts(home: Path) -> dict[int, tuple[float, str]]:
    db = sqlite3.connect(home / "memory_store.db")
    try:
        return {fid: (trust, tags) for fid, trust, tags in db.execute("SELECT fact_id, trust_score, tags FROM facts")}
    finally:
        db.close()


def test_a_lesson_about_a_changed_plugins_tool_drops_below_recall(tmp_path):
    seed, home = _home(tmp_path)
    (seed / "jarvis-hud" / "__init__.py").write_text('register(name="phone_action")  # T-0361\nregister(name="hud_show")\n')
    assert mh.changed_plugins(home / "plugins", seed) == ["jarvis-hud"]
    assert mh.main([str(home), str(seed)], today="2026-10-09") == 0
    facts = _facts(home)
    assert facts[1] == (mh.DEMOTED, "stale:jarvis-hud@2026-10-09"), "phone_action's plugin changed"
    assert facts[1][0] < mh.MIN_TRUST
    assert facts[2] == (0.5, "cron"), "gpu-telemetry did not change"
    assert facts[3] == (0.5, ""), "tool_call is Hermes' own, not a plugin's"
    assert facts[4] == (0.8, ""), "the owner's preferences are never touched"
    assert facts[5] == (0.5, ""), "'memory' is a plain word as well as a tool: only snake_case names match"
    assert facts[6] == (0.2, ""), "already below recall: left as it is"
    # The FTS index followed the update (the store's trigger), so search still finds it.
    db = sqlite3.connect(home / "memory_store.db")
    assert db.execute("SELECT rowid FROM facts_fts WHERE facts_fts MATCH 'stale'").fetchall() == [(1,)]
    db.close()


def test_a_second_start_with_nothing_changed_changes_nothing(tmp_path):
    seed, home = _home(tmp_path)
    (seed / "gpu-telemetry" / "__init__.py").write_text('register(name="gpu_status")\n# v2\n')
    mh.main([str(home), str(seed)], today="2026-10-09")
    first = _facts(home)
    (home / "plugins" / "gpu-telemetry" / "__init__.py").write_text((seed / "gpu-telemetry" / "__init__.py").read_text())
    mh.main([str(home), str(seed)], today="2026-10-10")
    assert _facts(home) == first
    assert first[2] == (mh.DEMOTED, "cron,stale:gpu-telemetry@2026-10-09")


def test_bytecode_and_a_new_plugin_are_not_changes(tmp_path):
    seed, home = _home(tmp_path)
    (home / "plugins" / "jarvis-hud" / "__pycache__").mkdir()
    (home / "plugins" / "jarvis-hud" / "__pycache__" / "x.pyc").write_bytes(b"\0")
    _plugin(seed, "jarvis-new", 'register(name="phone_action")\n')
    assert mh.changed_plugins(home / "plugins", seed) == []


def test_a_missing_or_broken_store_never_fails_the_start(tmp_path, capsys):
    seed, home = _home(tmp_path)
    (seed / "jarvis-hud" / "__init__.py").write_text("# changed\n")
    (home / "memory_store.db").unlink()
    assert mh.main([str(home), str(seed)]) == 0
    (home / "memory_store.db").write_bytes(b"not a database")
    assert mh.main([str(home), str(seed)]) == 0
    assert "memory_health:" in capsys.readouterr().err
    assert mh.main([]) == 0


_DEPLOY = Path(__file__).resolve().parents[1]


def test_the_entrypoint_sets_lessons_aside_before_it_replaces_the_plugins(tmp_path):
    """After the copy the two trees are equal and nothing would ever be set aside."""
    text = (_DEPLOY / "entrypoint.sh").read_text()
    start = text.index("if [ -f /opt/jarvis/bin/memory_health.py ]")
    block = text[start:text.index("\nfi\n", start) + 4]
    assert start < text.index('for d in "$SEED"/plugins/*/; do')
    assert 's6-setuidgid hermes python3 /opt/jarvis/bin/memory_health.py "$HERMES_HOME" "$SEED/plugins"' in block
    assert block.count("python3 /opt/jarvis/bin/memory_health.py") == 2, "as root it runs only through s6-setuidgid"
    assert "COPY deploy/memory_health.py /opt/jarvis/bin/memory_health.py" in (_DEPLOY / "Dockerfile").read_text()



def _run_block(tmp_path: Path, uid: int, s6: bool) -> tuple[float, str]:
    """The entrypoint's own block against a changed plugin, as `uid`, with or without
    s6-setuidgid on PATH: (fact 1's trust after, whom s6-setuidgid was asked to run as).
    `id` is faked so the branch does not depend on who runs the suite (CI runs as root)."""
    text = (_DEPLOY / "entrypoint.sh").read_text()
    start = text.index("if [ -f /opt/jarvis/bin/memory_health.py ]")
    block = text[start:text.index("\nfi\n", start) + 4]
    seed, home = _home(tmp_path)
    (seed / "jarvis-hud" / "__init__.py").write_text("# changed\nregister(name=\"phone_action\")\n")
    (tmp_path / "image").mkdir()
    seed.rename(tmp_path / "image" / "plugins")  # the image's layout: $SEED/plugins
    fake = tmp_path / "bin"
    fake.mkdir()
    (fake / "id").write_text(f"#!/bin/sh\necho {uid}\n")
    if s6:  # drops the user argument and runs the rest, noting whom it was asked for
        (fake / "s6-setuidgid").write_text('#!/bin/sh\necho "$1" > "$S6_LOG"\nshift\nexec "$@"\n')
    for f in fake.iterdir():
        f.chmod(0o755)
    log = tmp_path / "s6.log"
    script = block.replace("/opt/jarvis/bin/memory_health.py", str(_DEPLOY / "memory_health.py"))
    subprocess.run(["bash", "-euo", "pipefail", "-c", script], check=True, env={
        "HERMES_HOME": str(home), "SEED": str(tmp_path / "image"), "S6_LOG": str(log),
        "PATH": f"{fake}:{os.path.dirname(sys.executable)}:/usr/bin:/bin"})
    return _facts(home)[1][0], log.read_text().strip() if log.exists() else ""


def test_the_entrypoint_block_runs_as_hermes_and_never_as_root(tmp_path):
    """CI runs the suite as root: the first version of this test assumed a non-root shell."""
    assert _run_block(tmp_path / "user", uid=10000, s6=False) == (mh.DEMOTED, "")
    assert _run_block(tmp_path / "root", uid=0, s6=True) == (mh.DEMOTED, "hermes")
    assert _run_block(tmp_path / "bare", uid=0, s6=False) == (0.5, ""), "root without s6-setuidgid skips"
