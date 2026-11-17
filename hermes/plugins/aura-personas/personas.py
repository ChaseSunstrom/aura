"""The two personas, AURA and JARVIS, and which one is primary (ADR-0043; AURA, T-0861).

Ultron, Ada and Friday went at the owner's word (T-0936); a state file still naming one
reads as the default, AURA, like any other id that is not a persona.

What a persona owns is a short list: a soul (name, manner, greeting style),
a voice (its reference clip on the CosyVoice shim), a hue. Memory, tools,
skills, goals, history are shared — switching changes who is speaking, not what
they know — so this module owns exactly one piece of state, ``state/persona.json``
under ``HERMES_HOME``, and touches nothing else on the volume.

How the switch reaches the model: not by rewriting SOUL.md. The entrypoint
refreshes SOUL.md from the image only while it is byte-identical to what was
seeded; a plugin that rewrote it would freeze the house rules at whatever the
image shipped the day of the first switch. Instead the active persona's soul is
a *system prompt section* (``register_system_prompt_section``), rendered into
each new session's prompt, and — because that prompt is frozen for the session
— a ``pre_llm_call`` hook adds one line to the next turn of any session whose
prompt was rendered under a different persona. SOUL.md holds only the operating rules that
no persona may change and names no one: it once opened "You are JARVIS", so every AURA prompt
named two identities and she called herself JARVIS to people she wrote to (T-0944).

How it reaches the screens: ``persona.switch`` on every live HUD session's
socket (the same ``_emit`` the HUD panels use), and ``GET /api/status`` on the
adapter reads the same file for a client that connects later. The two readers
are held to the file's shape by ``tests/contracts/persona.json``.

Everything Hermes-specific is imported lazily, so this module and its tests run
without Hermes on the path — the rule the sibling plugins follow.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from _shared._broadcast import broadcast

from . import drafts

_LOGGER = logging.getLogger(__name__)

TOOLSET = "aura-personas"

#: The theme packs as the plugin needs them (generated from themes/*/theme.json by
#: design/build.py, T-0861): who each theme is, her voice and wake phrases.
THEMES: dict[str, Any] = json.loads((Path(__file__).resolve().parent / "themes.json").read_text(encoding="utf-8"))

#: Lower-case: what the state file, the voice and the wake word use. AURA first: the default.
PERSONAS: tuple[str, ...] = ("aura", "jarvis")
#: How each is written where a person reads it.
DISPLAY: dict[str, str] = {"aura": "AURA", "jarvis": "JARVIS"}
#: The default theme's persona, so a missing state file is AURA (design/AURA/PROMPT.md §2).
DEFAULT = THEMES["default"]
#: The pack a persona with no theme of her own wears.
SOUL_IDENTITY = "jarvis"

#: Relative to ``HERMES_HOME``, beside the kill switch; ``tests/contracts/persona.json``.
STATE_REL = "state/persona.json"
SCHEMA = "jarvis.persona.v1"

#: The souls the plugin ships; ``HERMES_HOME/personas/<name>.md`` overrides one.
SOULS_DIR = Path(__file__).resolve().parent / "souls"

_lock = threading.Lock()
#: Which persona each session's frozen prompt was rendered under (session id ->
#: persona). The pre_llm_call hook compares it with the active one and speaks up
#: when they differ; a session absent from here was rendered before this process
#: started, and its prompt carries SOUL.md's default.
_rendered: dict[str, str] = {}


def _hermes_home() -> Path:
    """Resolved per call — the tests point ``HERMES_HOME`` at a tmp dir."""
    return Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes")))


def state_path() -> Path:
    return _hermes_home() / STATE_REL


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def normalise(name: Any) -> Optional[str]:
    """``"Jarvis"``, ``" aura "`` -> the persona, or None when it is not one.

    None rather than a guess: a voice command mishearing "switch to Jarvis's"
    should leave the primary alone, not empty the pool.
    """
    key = str(name or "").strip().lower()
    return key if key in PERSONAS else None


def read_state(path: Optional[Path] = None) -> dict[str, Any]:
    """The persona record. Never raises; anything unreadable is the default.

    Unlike the kill switch, whose unreadable file fails towards *engaged*, an
    unreadable persona file fails towards AURA, the default theme's persona: the
    worst outcome is the wrong voice. ``theme`` is a drafted theme applied over
    the persona's own (drafts.py), or empty.
    """
    p = path or state_path()
    try:
        raw = p.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {"active": DEFAULT, "since": "", "previous": "", "theme": ""}
    except OSError as exc:
        return {"active": DEFAULT, "since": "", "previous": "", "theme": "", "unreadable": exc.__class__.__name__}
    try:
        data = json.loads(raw) if raw.strip() else {}
    except ValueError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    active = normalise(data.get("active")) or DEFAULT
    return {
        "active": active,
        "since": str(data.get("since") or ""),
        "previous": normalise(data.get("previous")) or "",
        "theme": str(data.get("theme") or "") if drafts.THEME_ID.fullmatch(str(data.get("theme") or "")) else "",
    }


def active() -> str:
    return read_state()["active"]


def soul_of(name: str) -> str:
    """The persona's soul: the operator's copy under HERMES_HOME first, then the plugin's."""
    for candidate in (_hermes_home() / "personas" / f"{name}.md", SOULS_DIR / f"{name}.md"):
        try:
            return candidate.read_text(encoding="utf-8").strip()
        except OSError:
            continue
    return f"# {DISPLAY.get(name, name)}\n\nYou are {DISPLAY.get(name, name)}."


def theme_of(name: str) -> str:
    """The theme pack a persona wears: her own, or JARVIS's for the personas that have none."""
    return name if name in THEMES["themes"] else SOUL_IDENTITY


def record(name: str) -> dict[str, Any]:
    """What a switch returns and what the event carries: enough to re-tint a screen."""
    return {
        "active": name,
        "display": DISPLAY[name],
        # The voice is the persona's name: the adapter says it in the theme's voice when she has
        # a pack (aura -> af_heart), else the reference clip <name>.wav on the voice server.
        "voice": name,
        "wake_word": name,
        "theme": theme_of(name),
        "pool": [p for p in PERSONAS if p != name],
    }


def switch(name: Any, emitter: Optional[Callable[[str, dict], int]] = None) -> dict[str, Any]:
    """Make ``name`` primary. Returns the record; raises ValueError for a non-persona.

    ``emitter(event, payload) -> clients reached`` is injected so the tests run
    without a gateway; the default broadcasts on every live HUD session.
    """
    key = normalise(name)
    if key is None:
        raise ValueError(f"not a persona: {name!r} (one of {', '.join(DISPLAY[p] for p in PERSONAS)})")
    with _lock:
        before = read_state()
        changed = before["active"] != key
        if changed:
            # Written whole, so a drafted theme applied over the old persona is dropped: the new
            # one comes in her own pack.
            path = state_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            data = {"schema": SCHEMA, "active": key, "since": _now(), "previous": before["active"]}
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
            os.replace(tmp, path)
    out = record(key)
    out["previous"] = before["active"]
    out["changed"] = changed
    payload = {**out, "at": _now()}
    reached = (emitter or broadcast)("persona.switch", payload) if changed else 0
    out["delivered"] = reached
    return out


def status() -> dict[str, Any]:
    st = read_state()
    out = record(st["active"])
    out["since"] = st["since"]
    out["previous"] = st["previous"]
    out["theme"] = st["theme"] or out["theme"]
    return out


# --------------------------------------------------------------------------- #
# The prompt: a section per new session, a line for a session rendered earlier
# --------------------------------------------------------------------------- #
def render_section(session_info: Any = None) -> str:
    """The active persona's soul, for the prompt of a session starting now: the one place the
    prompt says who she is. Does not reach a volume's USER.md or memory, which may still call
    the assistant JARVIS from before there were personas; the section names that as history.
    """
    name = active()
    sid = ""
    try:
        sid = str((session_info or {}).get("session_id") or "")
    except Exception:
        sid = ""
    if sid:
        _rendered[sid] = name
    return f"{soul_of(name)}\n\n{_identity(name)}"


def _identity(name: str) -> str:
    """Who she is, said once, after her soul: what she answers to and signs with."""
    others = " or ".join(DISPLAY[n] for n in DISPLAY if n != name)
    return (
        f"You are {DISPLAY[name]}: say so when asked who you are, and sign or introduce anything "
        f"you send for the owner as {DISPLAY[name]}, never {others}. Notes written before (USER.md, "
        f"memory, old messages) may call the assistant {others}: that is history, not your name. "
        f"Your wake word is \"{name}\"; the operating rules in SOUL.md stand unchanged."
    )


def pre_llm_call(**kw: Any) -> Optional[dict[str, str]]:
    """One line, on the turn after a switch, for a session whose prompt predates it.

    Nothing for a session rendered under the active persona. A session rendered before this
    process (a restart) is told once too: its prompt may predate the section.
    """
    sid = str(kw.get("session_id") or "")
    name = active()
    if _rendered.get(sid) == name:
        return None
    _rendered[sid] = name
    return {"context": f"Persona in effect from this turn: {DISPLAY[name]}.\n\n{soul_of(name)}\n\n{_identity(name)}"}


# --------------------------------------------------------------------------- #
# Tools and the command
# --------------------------------------------------------------------------- #
SCHEMAS: dict[str, dict[str, Any]] = {
    "persona_switch": {
        "name": "persona_switch",
        "description": (
            "Make one of the two personas the primary identity: AURA or JARVIS. "
            "Use it when the person asks to switch, talk to, or hear a different one ('switch to "
            "JARVIS', 'let me talk to AURA', 'switch to the JARVIS theme'). The voice, the name, the "
            "wake word and every screen's look (AURA and JARVIS each have a whole theme) all follow; "
            "memory, tools and history are shared and unchanged. This tool, never hud_set_mode or a "
            "settings write."
        ),
        "parameters": {
            "type": "object",
            "properties": {"name": {"type": "string", "enum": [DISPLAY[p] for p in PERSONAS],
                                    "description": "Which persona becomes primary."}},
            "required": ["name"],
        },
    },
    "persona_status": {
        "name": "persona_status",
        "description": "Who is primary right now, since when, which theme is worn, and who is in the companion pool.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    "theme_draft": {
        "name": "theme_draft",
        "description": (
            "Draft a new look when the person asks for one ('make a theme that feels like a sunrise'): "
            "start from a shipped theme (base) and give new colour roles as #rrggbb. Text roles "
            "(text, text.secondary, text.label, accent, ok, attention, danger) must reach 4.5:1 "
            "contrast over bg.void and bg.deep; on.accent over accent. The draft is checked, then "
            "every screen previews it without wearing it. Fix and draft again if it returns problems. "
            "Then ask whether to apply it; never apply without a yes. Fonts, icons, voice and wake "
            "word stay the base theme's."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "a new short id, lower-case with dashes: 'sunrise'"},
                "name": {"type": "string", "description": "what the theme is called: 'Sunrise'"},
                "base": {"type": "string", "enum": sorted(THEMES["packs"]),
                         "description": "the shipped theme it starts from"},
                "color": {"type": "object", "additionalProperties": {"type": "string"},
                          "description": "colour role -> #rrggbb, only the roles that change",
                          "properties": {r: {"type": "string"} for r in sorted(THEMES["packs"][DEFAULT]["color"])}},
                "renderer": {"type": "string", "enum": ["galaxy", "reactor"], "description": "the core: galaxy or reactor"},
                "description": {"type": "string", "description": "one line on the feel"},
            },
            "required": ["id", "name", "base", "color"],
        },
    },
    "theme_apply": {
        "name": "theme_apply",
        "description": (
            "Wear a drafted theme on every screen, after the person said yes to its preview; or go "
            "back to a shipped theme by its id (aura, jarvis). Needs their approval."
        ),
        "parameters": {
            "type": "object",
            "properties": {"id": {"type": "string", "description": "the draft's id, or aura / jarvis"}},
            "required": ["id"],
        },
    },
}


def _tool_switch(name: str = "", **_: Any) -> str:
    try:
        out = switch(name)
    except ValueError as exc:
        return json.dumps({"ok": False, "error": str(exc)})
    return json.dumps({"ok": True, **out})


def _tool_status(**_: Any) -> str:
    return json.dumps({"ok": True, **status()})


def write_theme(theme_id: Optional[str]) -> None:
    """Record a drafted theme worn over the active persona (None: her own pack again)."""
    with _lock:
        path = state_path()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        data.setdefault("schema", SCHEMA)
        data.setdefault("active", read_state()["active"])
        if theme_id:
            data["theme"] = theme_id
        else:
            data.pop("theme", None)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, path)


def _tool_theme_draft(**args: Any) -> str:
    return json.dumps(drafts.draft(_hermes_home(), THEMES, broadcast, **args))


def _tool_theme_apply(id: str = "", **_: Any) -> str:  # noqa: A002 — the tool's parameter name
    try:
        return json.dumps(drafts.apply(_hermes_home(), THEMES, str(id).strip().lower(), write_theme, broadcast))
    except ValueError as exc:
        return json.dumps({"ok": False, "error": str(exc)})


HANDLERS: dict[str, Callable[..., str]] = {
    "persona_switch": _tool_switch,
    "persona_status": _tool_status,
    "theme_draft": _tool_theme_draft,
    "theme_apply": _tool_theme_apply,
}


def handle_command(rest: str) -> str:
    """``/persona`` — ``status`` | ``<name>``. The session prefix is not needed: a switch is global."""
    arg = (rest or "").strip()
    if not arg or arg.lower() == "status":
        st = status()
        since = f" since {st['since']}" if st["since"] else ""
        return f"{st['display']} is primary{since}; pool: {', '.join(DISPLAY[p] for p in st['pool'])}."
    try:
        out = switch(arg)
    except ValueError as exc:
        return str(exc)
    if not out["changed"]:
        return f"{out['display']} is already primary."
    return f"{out['display']} is primary now (was {DISPLAY[out['previous']]}); {out['delivered']} HUD client(s) told."
