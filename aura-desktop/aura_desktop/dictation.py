"""Hold-to-dictate: the words the desktop app heard, typed into the focused window (T-1258).

The app records while the owner holds a key, has the adapter transcribe it on release, and sends
the result here as one ``dictate`` frame on its loopback socket (:mod:`aura_desktop.ipc`). This
types it with the same ``type_text`` the model's computer use goes through, so the typing is
visible, stoppable by the owner's Esc and held back while they have taken the machine.

What makes the *key press* the authority rather than the model:

* ``dictate`` is a frame on the shell's token-checked socket, never an action in the registry, so
  no command from the house and no tool call can name it. ``type_text`` stays Tier 3: a model that
  wants to type still asks the owner every time.
* It runs only when the owner has switched input automation on (``input_automation.enabled``),
  exactly as ``type_text`` does. The key is a second way into that switch, not a way around it.
* Newlines and tabs become spaces. A transcript is speech, and a "new line" the recogniser wrote
  as a line break would press Enter in a terminal or submit a form the owner never meant to send.

What this does **not** cover: which window has the focus when the words arrive. Release the key
with the focus somewhere else and the words go there. The audit line records how many characters
were typed, never what they were: a spoken password is still a password.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any

from .actions.base import ActionResult, Status
from .audit import AuditEntry, AuditLog
from .policy import ActionTier, Decision

_LOGGER = logging.getLogger(__name__)

__all__ = ["Dictation", "clean"]

#: Every control character, newline and tab included, is a gap between two words.
#: C0, DEL and the C1 range (an 0x9b is a terminal's escape introducer).
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]+")
#: Zero-width and bidi controls draw nothing, so they are dropped without a gap: a right-to-left
#: override would make the typed line read as something other than what lands.
_INVISIBLE = re.compile(r"[​-‏‪-‮⁠-⁤⁦-⁯﻿]+")
_BLANKS = re.compile(r"\s+")


def clean(text: Any) -> str | None:
    """The line to type, or None when there is nothing to type (not text, or only blanks)."""
    if not isinstance(text, str):
        return None
    line = _BLANKS.sub(" ", _CONTROL.sub(" ", _INVISIBLE.sub("", text))).strip()
    return line or None


class Dictation:
    """Types what the shell dictates, one frame at a time."""

    def __init__(self, registry: Any, audit: AuditLog | None = None) -> None:
        self._registry = registry
        self._audit = audit
        # One at a time: two dictations typed together would interleave their letters.
        self._lock = asyncio.Lock()
        self._tasks: set[asyncio.Task[ActionResult]] = set()

    def attach(self, server: Any) -> None:
        """Take the shell socket's ``dictate`` frames."""
        server.on_dictate = self.handle

    def handle(self, text: Any) -> None:
        """Called on the loop with the frame's ``text``; schedules the typing, never blocks."""
        line = clean(text)
        if line is None:
            return
        task = asyncio.ensure_future(self.type_out(line))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def type_out(self, line: str) -> ActionResult:
        started = time.monotonic()
        async with self._lock:
            action = self._registry.get("type_text")
            ctx = self._registry.ctx
            if action is None:
                result = ActionResult.unsupported("type_text is not registered")
            elif not action.available(ctx):
                result = ActionResult.unsupported(action.unavailable_reason(ctx) or "not available")
            else:
                try:
                    result = await asyncio.wait_for(
                        asyncio.to_thread(action.run, ctx, {"text": line}), timeout=action.timeout_s
                    )
                except asyncio.TimeoutError:
                    result = ActionResult.failed(f"typing timed out after {action.timeout_s:g}s")
                except Exception as exc:  # noqa: BLE001 - one bad dictation must not end the loop
                    _LOGGER.warning("dictation failed", exc_info=True)
                    # The type only: the message can hold what the typing library saw.
                    result = ActionResult.failed(type(exc).__name__)
        await self._record(line, result, started)
        return result

    async def _record(self, line: str, result: ActionResult, started: float) -> None:
        if self._audit is None:
            return
        status = result.status.value if isinstance(result.status, Status) else str(result.status)
        await self._audit.record_async(
            AuditEntry(
                action_id="dictate",
                tier=ActionTier.CONFIRM,
                decision=Decision.ALLOW,
                # Length only: the words may be anything the owner said aloud.
                params={"characters": len(line)},
                status=status,
                ok=result.ok,
                error=result.error,
                source="dictation",
                duration_ms=int((time.monotonic() - started) * 1000),
                note="the owner held the dictation key; the key press authorised the typing",
            )
        )
