"""A spoken turn runs at a reasoning level of its own (T-0641); equal to the typed one by default (T-0814).

The first word of a voice reply waits for the model's reasoning tokens
(``bench/results/model-first-token.md``: vLLM's first token is under 0.25 s, the
first visible delta 0.85-1.84 s). Hermes' HUD ``prompt.submit`` takes no effort
per turn, but ``config.set reasoning <level>`` with a ``session_id`` sets that
session's effort live (``tui_gateway/methods_config_set.py::_set_reasoning``,
session-scoped, never written to config.yaml). So the adapter, the one place every
prompt passes through, sends that frame ahead of the prompt:

* a prompt that carries ``voice: true`` (the console mic, the phone and the car's
  console page set it on every spoken turn, whatever the speaker gate said) or a
  ``speaker_ticket`` gets ``voice.reasoning_effort``;
* the next prompt without one, on a session we lowered, gets ``model.reasoning_effort``
  back, so typed turns run exactly as they did.

Not covered: ``/v1/chat/completions`` (no app speaks through it), a client that
speaks without sending ``voice`` and with no ticket, and a turn
submitted while another is still running on the same session (Hermes applies the
level to the agent at once). Nor the cost of a switch: any level but medium or none
changes the top of the model's prompt, so the first turn after a switch re-reads the
whole conversation (bench/results/reasoning-effort.md); that is why the default is equal.
"""
from __future__ import annotations

import json
from collections import OrderedDict
from collections.abc import Callable

#: Ids of our own ``config.set`` frames; their replies are dropped, not relayed.
ID_PREFIX = "adapter-effort-"

#: Sessions remembered at most; oldest out. Mirrors ``speaker.MAX_SESSIONS``.
MAX_SESSIONS = 512


def is_ours(frame: str) -> bool:
    """Is this upstream frame the reply to one of our ``config.set`` calls?"""
    return ID_PREFIX in frame and _id_of(frame).startswith(ID_PREFIX)


def _id_of(frame: str) -> str:
    try:
        msg = json.loads(frame)
    except ValueError:
        return ""
    return str(msg.get("id") or "") if isinstance(msg, dict) else ""


class Efforts:
    """Which sessions are running at the voice effort, and the frames that change it."""

    def __init__(self, read: Callable[[], tuple[str, str]]) -> None:
        # read() -> (voice effort, typed effort); asked per prompt so a settings_set
        # by voice applies on the next turn without a restart.
        self._read = read
        self._lowered: OrderedDict[str, str] = OrderedDict()
        self._n = 0

    def before(self, frame: str) -> list[str]:
        """``config.set`` frames to send ahead of ``frame``, if it is a prompt that needs one.

        Call it before :func:`speaker.rewrite_prompt`, which strips the ticket.
        """
        try:
            msg = json.loads(frame)
        except ValueError:
            return []
        if not isinstance(msg, dict) or msg.get("method") != "prompt.submit":
            return []
        params = msg.get("params")
        if not isinstance(params, dict):
            return []
        session_id = str(params.get("session_id") or "")
        if not session_id:
            return []
        voice, typed = self._read()
        # ``voice`` is how the turn arrived (the app says so whatever the gate did); the
        # ticket is who spoke. Either makes it a spoken turn. ``is True``: a string or a 1
        # from a sloppy client must not lower a typed turn.
        want = voice if params.get("voice") is True or params.get("speaker_ticket") else typed
        have = self._lowered.get(session_id, typed)
        if want == have:
            return []
        if want == typed:
            self._lowered.pop(session_id, None)
        else:
            self._lowered[session_id] = want
            self._lowered.move_to_end(session_id)
            while len(self._lowered) > MAX_SESSIONS:
                self._lowered.popitem(last=False)
        self._n += 1
        return [json.dumps({"jsonrpc": "2.0", "id": f"{ID_PREFIX}{self._n}", "method": "config.set",
                            "params": {"session_id": session_id, "key": "reasoning", "value": want}})]
