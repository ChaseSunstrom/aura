"""A stored chat's title, for the approval card that names the chat about to be deleted (T-1325).

Read-only and best effort: the card must still show the id when Hermes' session store is not
importable (tests) or the row is gone. It does not say the id belongs to the caller's own
chat; ``aura-goals``' ``session_delete`` refuses that itself.
"""
from __future__ import annotations

from typing import Optional


def title_of(stored_id: str) -> Optional[str]:
    """The chat's title, "" when it has none, None when there is no such chat or no store."""
    try:
        from hermes_state import SessionDB

        db = SessionDB(read_only=True)
        try:
            if db.get_session(stored_id) is None:
                return None
            return (db.get_session_title(stored_id) or "").strip()
        finally:
            db.close()
    except Exception:
        return None
