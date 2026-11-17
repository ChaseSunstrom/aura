"""The console's Board: the jarvis-board plugin's counts, with their age (T-0733).

Hermes keeps state.db 0600 and this process is another uid, so the numbers come from the
file the plugin writes inside the jarvis container (hermes/plugins/jarvis-board), readable by
group hermes. The route adds what changes by the second: llama-swap's models and the manager's
cards. Each source says ok, stale (with its age) or unavailable (with the reason), so a panel
never draws an old or missing number as if it were current.

Not covered: what the plugin could not read (its own ``errors``, passed through as-is).
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from .config import HERMES_HOME

BOARD_FILE = Path(HERMES_HOME) / "board" / "board.json"
SCHEMA = 1
#: The plugin rewrites the file after model and tool calls, at most every 10 s; an idle Hermes
#: writes nothing, so past this the numbers are shown as of their age, not as now.
STALE_S = 15 * 60


def source(status: str, age_s: float | None = None, why: str | None = None) -> dict[str, Any]:
    return {"status": status, "age_s": age_s, "why": why}


def read(now: float | None = None) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """(the plugin's board or None, its source status)."""
    now = time.time() if now is None else now
    try:
        data = json.loads(BOARD_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, source("unavailable", why="not written yet: the jarvis-board plugin writes it after a turn")
    except (OSError, ValueError) as exc:
        return None, source("unavailable", why=f"unreadable: {exc}")
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        return None, source("unavailable", why=f"schema {data.get('schema') if isinstance(data, dict) else '?'}, "
                                                f"not {SCHEMA}: the plugin and the adapter are from different builds")
    try:
        age = max(0.0, round(now - float(data.get("at") or 0), 1))
    except (TypeError, ValueError):
        return None, source("unavailable", why=f"unreadable: at is {data.get('at')!r}")
    return data, source("stale" if age >= STALE_S else "ok", age)


def running(payload: Any) -> list[dict[str, str]]:
    """llama-swap's /running, cut to each model and its state (not its command line)."""
    entries = payload.get("running", []) if isinstance(payload, dict) else []
    return [{"model": str(e.get("model") or ""), "state": str(e.get("state") or "unknown")}
            for e in entries if isinstance(e, dict)]
