"""Every message Jarvis sends for Sir says that Jarvis wrote it.

The owner, 2026-10-08: "it needs to tell every message it sends that it was made by
jarvis". Until then it rested on the model remembering to open with "this is Jarvis",
which a routine's text and a quick reply often did not. Applied where the payload is
built, before the phone's approval prompt is drawn, so the text Sir approves is the
text that goes.

The name is the worn persona's (T-0927: the owner wore AURA and every text said Jarvis),
read from aura-personas' state file when the text is signed.

Not covered: a message Sir types himself, and a body that already names the persona,
which is left as it is rather than signed twice. The line is plain ASCII on purpose: an
em dash would put a text in UCS-2 and halve what fits in a part.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

#: aura-personas' record of who is worn (its STATE_REL); a missing or unreadable one is
#: AURA, the default theme's persona, as it is there. Read here, not imported: _shared
#: cannot import a hyphenated plugin directory.
_STATE_REL = "state/persona.json"
_DEFAULT = "aura"


def persona() -> str:
    """The worn persona's name as a text writes it: "Aura", "Jarvis"."""
    home = Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes")))
    try:
        name = json.loads((home / _STATE_REL).read_text(encoding="utf-8")).get("active")
    except (OSError, ValueError, AttributeError):
        name = None
    # Letters only: the name goes into a text, and anything else in the file is not a name.
    return (name if isinstance(name, str) and name.isalpha() else _DEFAULT).title()


def signed(body: str) -> str:
    """[body] with the signature as its last line, unless it already names the persona."""
    text = str(body or "").rstrip()
    name = persona()
    if not text or re.search(rf"\b{name}\b", text, re.I):
        return text
    return f"{text}\n- sent by {name}, an AI assistant"
