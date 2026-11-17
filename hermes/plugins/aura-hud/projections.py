"""hud_project: the model projects a visual out of the core (generative UI).

A projection is a typed visual (a chart, a map, a table, a scene, …) the console
draws with its design system, out of the core and back into it. The tool checks
the payload against ``projections.json`` — this plugin's copy of
``tests/contracts/projections.json``, kept because the image copies the plugin,
not the repo; ``tests/test_project.py`` fails when it drifts — and sends one
``hud.projection`` event to the session's surfaces. The console's validator
(``aura-web/src/lib/ds/gen/contract.ts``) gives the same first error for the
same payload, so what is refused here is what would have drawn an error there.

Nothing is stored: a projection lives on the screen that drew it, and the same
``id`` replaces it there. Does not cover what the strings say: they are model
output (``origin: "agent"``), drawn as text; only ``custom_html`` runs anything,
and the console runs it in a sandboxed frame with no network.
"""
from __future__ import annotations

import json
import logging
import math
import re
import uuid
from pathlib import Path
from typing import Any

from . import hud

_LOGGER = logging.getLogger(__name__)

CONTRACT: dict = json.loads(Path(__file__).with_name("projections.json").read_text(encoding="utf-8"))
EVENT: str = CONTRACT["event"]
ORIGIN: str = CONTRACT["origin"]
LIMITS: dict = CONTRACT["limits"]
TYPES: dict = CONTRACT["types"]
_ID_RE = re.compile(LIMITS["id_pattern"])

_NAMES = {
    "string": "a string",
    "number": "a number",
    "integer": "an integer",
    "boolean": "true or false",
    "array": "an array",
    "object": "an object",
}


def _is(v: Any, kind: str) -> bool:
    # bool is an int in Python and not a number in JSON's sense: true is not 1 here.
    if kind == "string":
        return isinstance(v, str)
    if kind == "number":
        return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
    if kind == "integer":
        if isinstance(v, bool):
            return False
        return isinstance(v, int) or (isinstance(v, float) and v.is_integer())
    if kind == "boolean":
        return isinstance(v, bool)
    if kind == "array":
        return isinstance(v, list)
    return isinstance(v, dict)


def _num(n: float) -> str:
    """A bound as JavaScript prints it: 1, not 1.0."""
    return str(int(n)) if float(n).is_integer() else repr(n)


def _items(n: int) -> str:
    return f"{n} item" if n == 1 else f"{n} items"


def _units(s: str) -> int:
    """UTF-16 code units, which is what the console's ``.length`` counts."""
    return len(s.encode("utf-16-le", "surrogatepass")) // 2


def _check(v: Any, spec: dict, path: str, kind_name: str) -> str | None:
    kinds = spec["type"] if isinstance(spec["type"], list) else [spec["type"]]
    kind = next((k for k in kinds if _is(v, k)), None)
    if kind is None:
        return f"{path} must be " + " or ".join(_NAMES[k] for k in kinds)
    if isinstance(v, str):
        if "enum" in spec and v not in spec["enum"]:
            return f"{path} must be one of " + ", ".join(spec["enum"])
        # fullmatch: `$` in search() also matches before a final newline; JavaScript's does not.
        if "pattern" in spec and not re.fullmatch(spec["pattern"], v):
            return f"{path} has characters it may not"
        if "prefix" in spec and not any(v.startswith(p) for p in spec["prefix"]):
            return f"{path} must start with " + " or ".join(spec["prefix"])
        if "chars" in spec and _units(v) > spec["chars"]:
            return f"{path} is longer than {spec['chars']} characters"
        return None
    if kind in ("number", "integer"):
        if "min" in spec and v < spec["min"]:
            return f"{path} must be at least {_num(spec['min'])}"
        if "max" in spec and v > spec["max"]:
            return f"{path} must be at most {_num(spec['max'])}"
        return None
    if isinstance(v, list):
        if "max" in spec and len(v) > spec["max"]:
            return f"{path} has more than {_items(spec['max'])}"
        if "min" in spec and len(v) < spec["min"]:
            return f"{path} needs at least {_items(spec['min'])}"
        if "items" in spec:
            for i, item in enumerate(v):
                error = _check(item, spec["items"], f"{path}[{i}]", kind_name)
                if error:
                    return error
        return None
    if isinstance(v, dict) and "fields" in spec:
        return _check_fields(v, spec["fields"], path, kind_name)
    return None


def _check_fields(obj: dict, fields: dict, path: str, kind_name: str) -> str | None:
    def at(key: str) -> str:
        return f"{path}.{key}" if path else key

    # Unknown keys first: a misspelt field is the likeliest mistake and the clearest to fix.
    for key in obj:
        if key not in fields:
            return f"{at(str(key))} is not a field of {kind_name}"
    for key, spec in fields.items():
        if key not in obj:
            if spec.get("required"):
                return f"{at(key)} is required"
            continue
        error = _check(obj[key], spec, at(key), kind_name)
        if error:
            return error
    return None


def validate(kind: Any, data: Any) -> str | None:
    """The first thing wrong with a projection, or None when the console can draw it.

    The size cap is the JSON's UTF-8 length; the console measures its own
    serialisation, which can differ by a few bytes of escaping at the boundary.
    """
    if not isinstance(kind, str) or kind not in TYPES:
        return f"{str(kind)[:40]} is not a projection type"
    if not isinstance(data, dict):
        return "data must be an object"
    try:
        size = len(json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    except (TypeError, ValueError, RecursionError):
        return "data must be plain JSON"
    if size > LIMITS["data_bytes"]:
        return f"data is larger than {LIMITS['data_bytes']} bytes"
    return _check_fields(data, {**CONTRACT["common_fields"], **TYPES[kind]["fields"]}, "", kind)


def _signature(fields: dict) -> str:
    """A type's fields as the model reads them: `bars[{label, value, picked?}], unit?`."""
    out = []
    for name, spec in fields.items():
        kinds = spec["type"] if isinstance(spec["type"], list) else [spec["type"]]
        shape = name
        if "array" in kinds:
            item = spec.get("items", {})
            inner = _signature(item["fields"]) if "fields" in item else ""
            shape += f"[{{{inner}}}]" if inner else "[]"
        elif "fields" in spec:
            shape += f"{{{_signature(spec['fields'])}}}"
        elif "enum" in spec and spec["enum"] != CONTRACT["tones"]:  # the tones are said once
            shape += ":" + "|".join(spec["enum"])
        out.append(shape if spec.get("required") else f"{shape}?")
    return ", ".join(out)


HUD_PROJECT = {
    "name": "hud_project",
    "description": (
        "Project a visual out of the core onto this session's HUD: a chart, a map, a table, a "
        "picture. Use it when seeing beats hearing; say the answer too. Pick the type that fits; "
        "when Sir asks for a visual and none fits, draw a `scene`; `custom_html` is the last "
        "resort (sandboxed, no network, gone after "
        f"{LIMITS['custom_html_seconds']} s). The same `id` replaces a projection. Every string "
        "is shown as plain text. Tones: accent, violet (memory, models), warn (needs Sir), ok, "
        "danger (errors). A refused payload comes back with the first thing wrong; fix that and "
        f"call again. `data` is at most {LIMITS['data_bytes']} bytes. Types and their `data` "
        "fields (? optional; every type also takes title?):\n"
        + "\n".join(f"- {name}: {spec['about']} {_signature(spec['fields'])}" for name, spec in TYPES.items())
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "type": {"type": "string", "enum": list(TYPES)},
            "data": {"type": "object", "description": "The type's fields, as listed above."},
            "id": {
                "type": "string",
                "description": "Optional, 1 to 64 of A-Z a-z 0-9 _ . : -; reuse one to replace that projection.",
            },
        },
        "required": ["type", "data"],
    },
}


def handle_hud_project(args: dict, **kwargs: Any) -> str:
    """Send one projection to the session's surfaces and the owner's other screens; the same ``id`` replaces it there."""
    key = hud._state_key(kwargs.get("session_id"))
    pid = args.get("id")
    if pid is None or pid == "":
        pid = f"j_{uuid.uuid4().hex[:12]}"
    elif not isinstance(pid, str) or not _ID_RE.fullmatch(pid):
        return hud._err("id must be 1 to 64 letters, digits or _ . : -")
    kind = args.get("type")
    delivered, error = send(key, pid, kind, args.get("data"))
    if error:
        return hud._err(f"hud_project: {error}")
    return json.dumps({"ok": True, "delivered": delivered, "id": pid, "type": kind})


def send(state_key: str, pid: str, kind: Any, data: Any) -> tuple[bool, str]:
    """Check one projection against the contract and send it to the session and the owner's
    other screens: (whether a screen took it, what is wrong with it). A refused one is not sent.

    The one way a projection leaves the plugin, for ``hud_project`` and for a tool that draws
    its own answer: the lists and weather tools each built the frame themselves and sent it to
    the asking session only, so what was asked for by voice (a session of its own) never
    reached the screen being looked at (T-1471). Who gets it is ``HudStore.share``'s rule.
    """
    error = validate(kind, data)
    if error:
        return False, error
    payload = {"id": pid, "type": kind, "data": data, "origin": ORIGIN}
    return bool(hud.STORE.share(state_key, EVENT, payload)), ""
