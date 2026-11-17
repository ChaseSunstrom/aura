"""The small JSON files jarvis-hud's tools keep under ``$HERMES_HOME/plugin-data``, and their replies.

Five modules (later, loops, ledger, watches, routines) each carried the same read, atomic
write and ok/err helpers, and pylint's duplicate check (CI's py-dup) failed on the copies.
Imported as ``_shared._jsonstore``, never relatively: the cron gate scripts load those
modules by file path with the plugins directory on ``sys.path``.

Not covered: concurrent writers. Each caller holds its own lock around read-modify-write;
the write here is only atomic against a reader seeing half a file, and each process
writes its own temp file; two processes saving at once still lose one save.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def read_json(path: Path) -> dict:
    """The file's object, or {} when it is missing, unreadable or not JSON."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def write_json(path: Path, data: dict) -> None:
    """Write through a temp file and a rename, so a crash never leaves half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    # Per process: two processes saving one file shared a temp name and wrote into each other.
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def ok(**kw: Any) -> str:
    return json.dumps({"ok": True, **kw}, ensure_ascii=False)


def err(message: str) -> str:
    return json.dumps({"ok": False, "error": message}, ensure_ascii=False)
