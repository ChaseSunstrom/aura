"""The sessions a deploy's own check turns ran in, kept for the Board (T-1142).

aura-hud's habit counter is where a turn is recognised as a probe (the adapter's surface
record says ``probe``); aura-board reads the list to report those sessions as their own kind
instead of console turns. Hermes chose the session ids, so nothing in state.db says which they
are. Imported as ``_shared._probes``, never relatively: two plugins share it.

Only session ids are stored, newest :data:`CAP` kept: no message, title or tool argument.

Not covered: a probe turn recorded while the adapter could not answer (the habit counter fails
open, so it stays a console turn); two processes saving at once lose one id, as in
``_jsonstore``; a session that was a probe before this file existed.
"""
from __future__ import annotations

import os
import threading
from pathlib import Path

from ._jsonstore import read_json, write_json

CAP = 500
_LOCK = threading.Lock()


def path() -> Path:
    """Beside the board's own file, in the Hermes home the board already writes to."""
    return Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes"))) / "board" / "probe-sessions.json"


def _load() -> list[str]:
    raw = read_json(path()).get("ids")
    return [s for s in raw if isinstance(s, str) and s] if isinstance(raw, list) else []


def ids() -> set[str]:
    """The recorded session ids; empty when the file is missing, unreadable or malformed."""
    return set(_load())


def record(session_id: str) -> None:
    """Remember a probe session, oldest dropped past :data:`CAP`. A session already held writes nothing."""
    if not isinstance(session_id, str) or not session_id:
        return
    with _LOCK:
        held = _load()
        if session_id in held:
            return
        write_json(path(), {"ids": (held + [session_id])[-CAP:]})
