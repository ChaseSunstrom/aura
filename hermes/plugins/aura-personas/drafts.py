"""Themes AURA makes by voice: draft, validate, preview, then apply with the person's yes (T-0861).

"Aura, make a theme that feels like a sunrise" -> the model picks a base pack and the colours,
``theme_draft`` holds the result to the validator's rules (theme_rules.py: the schema and AA
contrast), keeps it under HERMES_HOME/themes/drafts/ and sends ``theme.preview`` so every
screen shows it without wearing it. ``theme_apply`` (asked: the matrix's ``theme:apply``)
moves the draft to HERMES_HOME/themes/<id>.json, records it in the persona file and sends
``theme.apply``; the adapter serves the pack at ``/api/themes/<id>``. A persona switch goes
back to that persona's own pack.

Not covered: new fonts, icons, voices or wake heads. A draft keeps its base pack's, because
those are files that have to be vendored, trained or licensed, which THEMES.md walks through.
"""
from __future__ import annotations

import copy
import json
import os
import re
from pathlib import Path
from typing import Any, Callable, Optional

from .theme_rules import contrast_problems, schema_problems

THEME_ID = re.compile(r"[a-z][a-z0-9-]{1,23}")
HEX = re.compile(r"#[0-9a-fA-F]{6}")


def drafts_dir(home: Path) -> Path:
    return home / "themes" / "drafts"


def applied_path(home: Path, theme_id: str) -> Path:
    return home / "themes" / f"{theme_id}.json"


def build(themes: dict[str, Any], theme_id: str, name: str, base: str, color: dict[str, Any],
          renderer: str = "", description: str = "") -> tuple[dict[str, Any], list[str]]:
    """The draft pack and what is wrong with it; an empty list means it may be previewed."""
    packs = themes["packs"]
    problems: list[str] = []
    if not THEME_ID.fullmatch(theme_id or ""):
        problems.append("id must be 2-24 lower-case letters, digits or dashes, starting with a letter")
    elif theme_id in packs:
        problems.append(f"{theme_id!r} is a shipped theme; pick a new id")
    if base not in packs:
        return {}, problems + [f"base must be one of {sorted(packs)}"]
    pack = copy.deepcopy(packs[base])
    pack["id"], pack["name"] = theme_id, (name or theme_id).strip()[:40]
    pack["description"] = (description or f"Drafted by voice from {packs[base]['name']}.").strip()[:200]
    for role, value in (color or {}).items():
        if role not in pack["color"]:
            problems.append(f"color: {role!r} is not a role (one of {', '.join(sorted(pack['color']))})")
        elif not HEX.fullmatch(str(value)):
            problems.append(f"color: {role} must be #rrggbb, not {value!r}")
        else:
            pack["color"][role] = str(value).lower()
    if renderer:
        pack["core"]["renderer"] = renderer
    problems += schema_problems(pack, themes["schema"])
    if not problems:
        problems += contrast_problems(pack)
    return pack, problems


def _write(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def draft(home: Path, themes: dict[str, Any], emitter: Callable[[str, dict], int], **args: Any) -> dict[str, Any]:
    pack, problems = build(themes, str(args.get("id") or ""), str(args.get("name") or ""),
                           str(args.get("base") or themes["default"]), args.get("color") or {},
                           str(args.get("renderer") or ""), str(args.get("description") or ""))
    if problems:
        return {"ok": False, "problems": problems, "previewed": False}
    _write(drafts_dir(home) / f"{pack['id']}.json", pack)
    reached = emitter("theme.preview", {"id": pack["id"], "name": pack["name"], "pack": pack})
    return {"ok": True, "id": pack["id"], "previewed": True, "delivered": reached,
            "next": "Ask the person whether to apply it; theme_apply needs their yes."}


def apply(home: Path, themes: dict[str, Any], theme_id: str, write_choice: Callable[[Optional[str]], None],
          emitter: Callable[[str, dict], int]) -> dict[str, Any]:
    """Wear a draft (or go back to a shipped pack by its id). Raises ValueError for neither."""
    if theme_id in themes["packs"]:
        write_choice(None)
        emitter("theme.apply", {"id": theme_id, "pack": None})
        return {"ok": True, "id": theme_id, "custom": False}
    if not THEME_ID.fullmatch(theme_id or ""):
        raise ValueError(f"not a theme id: {theme_id!r}")
    source = drafts_dir(home) / f"{theme_id}.json"
    try:
        pack = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"no draft {theme_id!r}: draft it first ({exc.__class__.__name__})") from exc
    # Held to the rules again: the file is what is worn, and it may have changed since its
    # preview (the approval card names the id the person said yes to; the path is governed).
    problems = ([] if isinstance(pack, dict) and pack.get("id") == theme_id else ["its id is not the file's"])
    problems = problems or schema_problems(pack, themes["schema"]) or contrast_problems(pack)
    if problems:
        raise ValueError(f"draft {theme_id!r} no longer passes: {'; '.join(problems[:3])}")
    _write(applied_path(home, theme_id), pack)
    source.unlink(missing_ok=True)
    write_choice(theme_id)
    reached = emitter("theme.apply", {"id": theme_id, "pack": pack})
    return {"ok": True, "id": theme_id, "custom": True, "delivered": reached}
