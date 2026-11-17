"""The Hermes memory layers, as rows the console's graph can lay out.

The scene brief wants the mid field built from *Hermes'* memory -- "SOUL/USER/
MEMORY files, the structured memory provider or retrieval store, session history,
the skills library". The console already has a memory feed, but it reads
``jarvis/memory/list``, which is jarvis-core's memory integration and a different
store entirely; on this deployment it is empty, so the graph had nothing to draw.

The adapter is where this belongs. It already mounts the Hermes home at
``/opt/data`` (the volume ``jarvis-hermes-home``), so it can read the layers
without a new mount, a new service, or a tool call through the agent -- and a
read-only file walk is the kind of thing a thin adapter is allowed to do, where
proxying an agent turn to fetch its own memory would not be.

Everything here is read-only and defensive: a missing file, a locked database or
a half-written JSON file is a smaller graph, never a failed request. The console
draws whatever came back.
"""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any


#: Where the Hermes home is mounted in this container.
#:
#: A constant rather than a setting, and not read from the environment: the
#: adapter's own conformance test forbids `os.environ` here, and this is not
#: configuration anyway. The path is fixed by the compose file's
#: `jarvis-hermes-home:/opt/data` mount, so making it settable would invite a
#: value that does not match the mount. `collect()` takes an override for tests.
HOME = Path("/opt/data")

#: Hermes separates MEMORY.md entries with a lone section mark.
_ENTRY_SEP = re.compile(r"^\s*§\s*$", re.M)

#: Sessions are 600+ and would swamp the graph; the recent ones are the ones a
#: person is actually looking for. Raise it when the layout proves it can carry
#: more (the brief's own target is tens of thousands, measured with a fixture).
SESSION_LIMIT = 120

#: A tag on every row from one layer, so the console can colour by kind.
_LAYER_TAGS = {
    "soul": ["identity", "soul"],
    "user": ["identity", "user"],
    "memory": ["durable"],
    "fact": ["fact"],
    "skill": ["skill"],
    "session": ["session"],
}


def _entry(
    ident: str, text: str, tags: list[str], created: float, source: str, **extra: Any
) -> dict[str, Any]:
    """One row in the shape the console's ``MemoryEntry`` already declares."""
    row: dict[str, Any] = {
        "id": ident,
        "text": text.strip(),
        "tags": tags,
        "created": int(created),
        "source": source,
    }
    row.update({k: v for k, v in extra.items() if v is not None})
    return row


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _read_layer_file(path: Path, kind: str, split: bool) -> list[dict[str, Any]]:
    """SOUL.md, USER.md and MEMORY.md.

    Only MEMORY.md is split: Hermes writes it as separate entries divided by a
    section mark, and each of those is a thing the agent chose to remember. SOUL
    and USER are single documents that happen to be long, and chopping them into
    paragraphs would put a dozen meaningless nodes where one node belongs.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        # Not written yet: an empty layer, not an unreadable one. Anything
        # else (a 0600 MEMORY.md seen from another uid) is `collect`'s to name.
        return []
    when = _mtime(path)
    if not split:
        return [_entry(f"{kind}:{path.name}", text, _LAYER_TAGS[kind], when, "hermes-layer")]
    out: list[dict[str, Any]] = []
    for i, chunk in enumerate(_ENTRY_SEP.split(text)):
        if chunk.strip():
            out.append(_entry(f"{kind}:{i}", chunk, _LAYER_TAGS[kind], when, "hermes-layer"))
    return out


def _read_facts(db: Path) -> list[dict[str, Any]]:
    """The structured retrieval store.

    Opened read-only through a URI so a live writer is never blocked and this can
    never be the reason a fact is lost.
    """
    if not db.exists():
        return []
    try:  # OSError: `collect` names the layer unreadable (see _read_sessions)
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=2.0)
    except sqlite3.Error as exc:
        raise OSError(str(exc)) from exc
    try:
        rows = conn.execute(
            "select fact_id, content, category, tags, created_at from facts"
        ).fetchall()
    except sqlite3.Error as exc:
        raise OSError(str(exc)) from exc
    finally:
        conn.close()

    out: list[dict[str, Any]] = []
    for fact_id, content, category, tags, created_at in rows:
        extra = [t for t in str(tags or "").split(",") if t]
        out.append(
            _entry(
                f"fact:{fact_id}",
                str(content or ""),
                [*_LAYER_TAGS["fact"], *([str(category)] if category else []), *extra],
                _epoch(created_at),
                "hermes-facts",
            )
        )
    return out


def _epoch(value: Any) -> float:
    """`created_at` is an ISO-ish string in this schema, not a number."""
    if isinstance(value, (int, float)):
        return float(value)
    try:
        from datetime import datetime

        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return 0.0


def _read_skills(root: Path) -> list[dict[str, Any]]:
    """The skills library, with its usage so significance can weight it."""
    if not root.is_dir():
        return []
    try:
        usage = json.loads((root / ".usage.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        usage = {}
    out: list[dict[str, Any]] = []
    for child in sorted(root.iterdir()):
        skill = child / "SKILL.md"
        # A directory with only a DESCRIPTION.md is a category, not a skill.
        if not skill.is_file():
            continue
        meta = usage.get(child.name) if isinstance(usage, dict) else None
        first = ""
        try:
            for line in skill.read_text(encoding="utf-8").splitlines():
                if line.strip() and not line.startswith(("---", "name:", "description:")):
                    first = line.strip("# ").strip()
                    break
        except OSError:
            pass
        out.append(
            _entry(
                f"skill:{child.name}",
                f"{child.name} — {first}" if first else child.name,
                _LAYER_TAGS["skill"],
                _mtime(skill),
                "hermes-skills",
                uses=(meta or {}).get("use_count") if isinstance(meta, dict) else None,
            )
        )
    return out


def _read_sessions(db: Path, limit: int = SESSION_LIMIT) -> list[dict[str, Any]]:
    """Recent session history, so the graph can say two facts share a conversation."""
    if not db.exists():
        return []
    # Hermes keeps state.db 0600 and this is another uid, so in production the open fails on
    # every read. Raised as OSError so `collect` names the layer unreadable like the rest,
    # rather than a warning per read beside a layer that looks empty.
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=2.0)
    except sqlite3.Error as exc:
        raise OSError(str(exc)) from exc
    try:
        rows = conn.execute(
            "select id, title, started_at, message_count from sessions "
            "where coalesce(hidden, 0) = 0 order by started_at desc limit ?",
            (limit,),
        ).fetchall()
    except sqlite3.Error as exc:
        raise OSError(str(exc)) from exc
    finally:
        conn.close()
    return [
        _entry(
            f"session:{sid}",
            str(title or sid),
            _LAYER_TAGS["session"],
            _epoch(started),
            "hermes-sessions",
            conversation_id=str(sid),
            messages=count,
        )
        for sid, title, started, count in rows
    ]


def collect(home: Path | None = None) -> dict[str, Any]:
    """Every layer, as one payload.

    Shaped as ``{entries, notes, counts}``: ``entries`` is what the console's
    graph builder already consumes, ``notes`` stays empty because the layers are
    not documents, and ``counts`` is there so a caller can tell an empty store
    from a failed read without parsing the rows.
    """
    root = home or HOME
    entries: list[dict[str, Any]] = []
    unreadable: list[dict[str, str]] = []

    # Every layer is read on its own, and a layer that cannot be read costs
    # that layer and nothing else. In production the adapter is uid 10001 on a
    # read-only mount of a home that is 0701 -- cd-through, no listing, and
    # Hermes keeps MEMORY.md and skills/.usage.json 0600 -- so `skills/` can be
    # entered but not listed, and the one PermissionError from `iterdir` took
    # the whole endpoint to a 500 (2026-09-09, the first call in production).
    # What cannot be read is named in `unreadable`, so the console can say
    # "three of six layers" rather than draw an empty graph as the truth.
    def layer(name: str, read: Any) -> None:
        try:
            entries.extend(read())
        except OSError as exc:
            unreadable.append({"layer": name, "error": exc.strerror or str(exc)})

    layer("soul", lambda: _read_layer_file(root / "SOUL.md", "soul", split=False))
    layer("user", lambda: _read_layer_file(root / "memories" / "USER.md", "user", split=False))
    layer("memory", lambda: _read_layer_file(root / "memories" / "MEMORY.md", "memory", split=True))
    layer("facts", lambda: _read_facts(root / "memory_store.db"))
    layer("skills", lambda: _read_skills(root / "skills"))
    layer("sessions", lambda: _read_sessions(root / "state.db"))

    counts: dict[str, int] = {}
    for row in entries:
        counts[row["source"]] = counts.get(row["source"], 0) + 1
    return {"entries": entries, "notes": [], "counts": counts, "home": str(root), "unreadable": unreadable}
