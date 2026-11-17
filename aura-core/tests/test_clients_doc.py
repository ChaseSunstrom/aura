"""docs/clients.md names only websocket commands the house still registers.

One direction on purpose: a route retired from the code must not stay
documented. It does NOT require every registered command to be documented (the
doc is a guide, not a reference), and it reads only the command table: the
first cell of each row from `ping` to `assist_pipeline/stop`.
"""

from __future__ import annotations

import re
from pathlib import Path

from aura.api.websocket import WebSocketHandler

DOC = Path(__file__).parent.parent / "docs" / "clients.md"


def doc_commands(text: str) -> set[str]:
    rows = [ln for ln in text.splitlines() if ln.startswith("| `")]
    start = next(i for i, ln in enumerate(rows) if ln.startswith("| `ping`"))
    end = next(i for i, ln in enumerate(rows) if "`assist_pipeline/stop`" in ln.split("|")[1])
    names: set[str] = set()
    for row in rows[start : end + 1]:
        base = ""
        for tok in re.findall(r"`([^`]+)`", row.split("|")[1]):
            # `config/x/list` · `/set`: a leading slash reuses the previous prefix.
            name = base.rsplit("/", 1)[0] + tok if tok.startswith("/") else tok
            names.add(name)
            base = name
    return names


def test_clients_doc_names_only_registered_commands():
    names = doc_commands(DOC.read_text())
    assert len(names) > 100, "the command table was not read"
    assert not names - set(WebSocketHandler._HANDLERS)


def test_clients_doc_check_catches_a_retired_command():
    text = DOC.read_text()
    row = "| `jarvis/retired/thing` | gone |\n"
    marker = "| `assist_pipeline/stop`"
    names = doc_commands(text.replace(marker, row + marker, 1))
    assert names - set(WebSocketHandler._HANDLERS) == {"jarvis/retired/thing"}
