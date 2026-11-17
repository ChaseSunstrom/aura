"""Tool lessons are retired when a deploy changes the tool they are about (T-0208, mem-02).

    memory_health.py <hermes_home> <seed_plugins>   # the entrypoint, before it copies the plugins

The nightly self-review writes what it learns about failing tools into the holographic fact
store (category ``tool``), and prefetch puts a matching fact in front of the model on every
turn. Nothing retired one: an unattended session cannot update or remove a fact (the matrix
asks, and nobody is there), so on 2026-10-09 production still told the model that
phone_action always fails from a cron session -- false since the house route went first.

A deploy is when such a lesson can stop being true. Before the entrypoint replaces each
shipped plugin in the volume, this compares the two trees; for each plugin that changed, a
``tool`` fact naming one of its tools is demoted below the store's recall threshold (prefetch
and search skip it) and tagged ``stale:<plugin>@<date>``. Nothing is deleted:
``fact_feedback`` or an UPDATE restores it, and a failure that persists is learned again.

A plugin's tools are the approval matrix's names (every tool has a row) that appear quoted in
its sources; only snake_case names count, so "memory" or "terminal" in a sentence is not a
tool. Does not cover Hermes' own tools (a Hermes bump changes tool_call, and nothing here
sees it), a change in ``_shared/`` (the helpers several plugins import belong to no one
plugin's tools), lessons in MEMORY.md, or a fact that names the tool in other words. Any error is
printed and skipped: a start never fails here.
"""
from __future__ import annotations

import datetime as dt
import filecmp
import re
import sqlite3
import sys
from pathlib import Path

#: holographic's min_trust_threshold default; prefetch and search drop facts under it.
MIN_TRUST = 0.3
#: Under the threshold, and recognisably ours.
DEMOTED = 0.25

_RULE = re.compile(r"Rule\(\(\s*([^)]*?)\)")
_NAME = re.compile(r"""["']([a-z][a-z0-9]*(?:_[a-z0-9]+)+)["']""")
_IGNORE = ["__pycache__", "tests"]


def _differs(a: Path, b: Path) -> bool:
    cmp = filecmp.dircmp(a, b, ignore=_IGNORE)

    def walk(c: filecmp.dircmp) -> bool:
        if c.left_only or c.right_only or c.diff_files or c.funny_files:
            return True
        # dircmp compares by stat signature; same size and mtime but new bytes is a change too.
        if filecmp.cmpfiles(c.left, c.right, c.same_files, shallow=False)[1]:
            return True
        return any(walk(sub) for sub in c.subdirs.values())
    return walk(cmp)


def changed_plugins(volume: Path, seed: Path) -> list[str]:
    """Shipped plugins whose copy in the volume differs; one new to the volume has no lessons yet."""
    return sorted(d.name for d in seed.iterdir()
                  if (d / "plugin.yaml").is_file() and (volume / d.name).is_dir() and _differs(volume / d.name, d))


def matrix_tools(seed: Path) -> set[str]:
    text = (seed / "_shared" / "_matrix.py").read_text()
    return {n for group in _RULE.findall(text) for n in _NAME.findall(group)}


def plugin_tools(plugin: Path, known: set[str]) -> set[str]:
    found: set[str] = set()
    for f in plugin.rglob("*"):
        if f.suffix in (".py", ".yaml") and not set(_IGNORE) & set(f.relative_to(plugin).parts):
            found.update(_NAME.findall(f.read_text(errors="replace")))
    return found & known


def demote(db_path: Path, tools: dict[str, set[str]], today: str) -> list[int]:
    """Demote the tool lessons that name a changed plugin's tool; the fact ids it touched."""
    if not db_path.is_file():
        return []
    db = sqlite3.connect(db_path, timeout=5)
    try:
        rows = db.execute("SELECT fact_id, content, tags FROM facts WHERE category = 'tool' AND trust_score >= ?",
                          (MIN_TRUST,)).fetchall()
        touched = []
        for fact_id, content, tags in rows:
            plugin = next((p for p, names in sorted(tools.items())
                           if any(re.search(rf"(?<!\w){re.escape(n)}(?!\w)", content) for n in names)), None)
            if plugin:
                tag = f"stale:{plugin}@{today}"
                db.execute("UPDATE facts SET trust_score = ?, tags = ? WHERE fact_id = ?",
                           (DEMOTED, f"{tags},{tag}" if tags else tag, fact_id))
                touched.append(fact_id)
        db.commit()
        return touched
    finally:
        db.close()


def main(argv: list[str], today: str | None = None) -> int:
    try:
        home, seed = Path(argv[0]), Path(argv[1])
        changed = changed_plugins(home / "plugins", seed)
        if not changed:
            return 0
        known = matrix_tools(seed)
        tools = {p: plugin_tools(seed / p, known) for p in changed}
        touched = demote(home / "memory_store.db", tools, today or dt.date.today().isoformat())
        if touched:
            print(f"memory_health: {len(touched)} tool lesson(s) set aside, their plugin changed "
                  f"({', '.join(changed)}): facts {touched}", file=sys.stderr)
    except Exception as err:  # noqa: BLE001 - the gateway starts whatever happens here
        print(f"memory_health: skipped ({err.__class__.__name__}: {err})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
