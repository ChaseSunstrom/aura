"""Skill proposals: the review queue between "JARVIS did this by hand" and the loop.

The media brief's distillation loop (Part 7, M10): after a task that took a long
chain of tool calls and succeeded, JARVIS proposes the skill it would become —
what the task was, the working sequence, the dead ends, what should vary — and a
person approves, edits or drops it. ``skill_propose`` writes one row and shows one
``notice`` panel; nothing acts on the row until ``skill_proposals approve <id>``,
which runs the existing loop up to the scaffold: a branch carrying the plugin,
its test, a SKILL.md and a benchmark task, uncommitted, for the model to fill in.
The merge request stays behind ``selfext_open_mr`` and its approval gate, which
is the one boundary the brief asks for ("nothing self-modifies production
directly").

What this does NOT do: decide which chains deserve a proposal. That judgement is
the curator's (the weekly job in ``hermes/cron/jobs.tsv`` reads the sessions with
``session_search``) and the model's after any long task; this module records what
it is told and keeps the queue honest. A proposal is also not a skill: until it
is approved and its handler filled in, nothing reads it.
"""
from __future__ import annotations

import importlib.util
import json
import logging
import re
import sys
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType
from typing import Any

from aura_schema import from_env

from _shared import _matrix
from _shared._approval_hook import split_session

from . import git, scaffold

_LOGGER = logging.getLogger(__name__)

#: The plugin-state key the queue lives under (one list, so a read-modify-write is
#: one locked ``set``).
KEY = "proposals"
#: Fewer calls than this is not a chain worth a skill — the brief's "threshold of
#: tool calls". Two calls is a tool and its retry.
MIN_CHAIN = 3
#: aura-hud's module, by path: a sibling directory, not a package this one can
#: import (Hermes loads each plugin as ``hermes_plugins.<slug>``).
_HUD_SOURCE = Path(__file__).resolve().parents[1] / "aura-hud" / "hud.py"
#: The name a by-path load registers under. The same one aura-artifacts uses, so
#: the two plugins share one fallback copy and one panel table.
_HUD_FALLBACK = "jarvis_hud_hud"
#: The panel kind the queue uses. ``notice`` rather than a kind of its own: every
#: surface already renders it, and a proposal is a notice with an id in it.
PANEL_KIND = "notice"
STATUSES = ("open", "approved", "dropped")
USAGE = "usage: /skill_proposals [@<sid>] list | approve <id> | drop <id>"


class _Queue:
    """The rows, in Hermes' ``PluginState`` when ``register()`` was given one.

    ``backend`` is ``ctx.state`` (``get(key, default)`` / ``set(key, value)``, one
    JSON file under ``HERMES_HOME/plugin-data/jarvis-selfext/``) or None for an
    in-process list — the docs generator's ctx and the tests' have no state, and a
    docs build must never write a file.
    """

    def __init__(self) -> None:
        self.backend: Any = None
        self._memory: list[dict[str, Any]] = []
        self.lock = threading.Lock()

    def rows(self) -> list[dict[str, Any]]:
        raw = self.backend.get(KEY, None) if self.backend is not None else self._memory
        return [dict(r) for r in raw if isinstance(r, dict)] if isinstance(raw, list) else []

    def save(self, rows: list[dict[str, Any]]) -> None:
        if self.backend is not None:
            self.backend.set(KEY, rows)
        else:
            self._memory = rows


QUEUE = _Queue()


def attach_state(state: Any) -> None:
    """Keep the queue in plugin state when *state* has ``get``/``set``; else in memory."""
    if callable(getattr(state, "get", None)) and callable(getattr(state, "set", None)):
        QUEUE.backend = state


def _hud() -> ModuleType | None:
    """aura-hud's ``hud`` module: the loaded one when Hermes has it, else by path.

    The loaded copy first, because ``STORE`` is per process: a second copy shows a
    panel that routes to the client but is listed and dismissed by nothing. None
    when the plugin is absent, which costs the proposal its panel and nothing else.
    """
    for name, mod in list(sys.modules.items()):
        if name.endswith("aura_hud.hud") and hasattr(mod, "STORE"):
            return mod
    cached = sys.modules.get(_HUD_FALLBACK)
    if cached is not None or not _HUD_SOURCE.is_file():
        return cached
    spec = importlib.util.spec_from_file_location(_HUD_FALLBACK, _HUD_SOURCE)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    sys.modules[_HUD_FALLBACK] = module
    spec.loader.exec_module(module)
    return module


def _list_arg(value: Any, what: str) -> list[str]:
    if isinstance(value, str):
        # The model sometimes sends an array as a JSON string; a bare sentence is
        # one item, not a parse error.
        try:
            value = json.loads(value)
        except ValueError:
            value = [value]
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{what} must be a list of strings")
    return [str(v).strip() for v in value if str(v).strip()]


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


#: Words too common in tool and skill names to say two of them do the same thing.
_STOPWORDS = frozenset({"a", "an", "and", "as", "at", "by", "for", "from", "in", "into", "is",
                        "it", "my", "of", "on", "or", "the", "to", "with", "jarvis"})
#: Where shipped skills live besides the checkout: this tree's own ``hermes/skills``.
_SKILLS_HERE = Path(__file__).resolve().parents[2] / "skills"


def _words(name: str) -> set[str]:
    return {w for w in re.split(r"[^a-z0-9]+", name.lower()) if w and w not in _STOPWORDS}


def _overlaps(name: str, skills: list[str], tools: list[str]) -> list[str]:
    """Skills and tools whose names share two or more words with *name*.

    Word overlap on names only: deterministic and cheap, so the owner sees the same
    list every time. It does NOT catch an overlap named differently (a ``backup``
    skill against a ``snapshot`` proposal); that is what the case against is for.
    """
    mine = _words(name)
    return ([f"skill {s}" for s in sorted(set(skills)) if len(mine & _words(s)) >= 2]
            + [f"tool {t}" for t in sorted(set(tools)) if len(mine & _words(t)) >= 2])


def _existing() -> tuple[list[str], list[str]]:
    """Shipped skill directory names (checkout and this tree) and every matrix tool."""
    skills: list[str] = []
    for root in (git.repo_path() / scaffold.SKILLS_SUBDIR, _SKILLS_HERE):
        if root.is_dir():
            skills += [p.name for p in root.iterdir() if p.is_dir()]
    # The matrix names every tool (a tool with no row is asked about each time).
    return skills, sorted(_matrix.known_tools())


def _plain(text: str) -> str:
    """One line: the case against can carry stranger-influenced text onto a panel."""
    return " ".join(text.split())


def _find(rows: list[dict[str, Any]], pid: str) -> dict[str, Any]:
    for row in rows:
        if row.get("id") == pid:
            return row
    raise ValueError(f"no proposal {pid!r}; `skill_proposals list` shows the open ones")


# --------------------------------------------------------------------------- #
# The panel
# --------------------------------------------------------------------------- #
def _panel_text(row: dict[str, Any]) -> str:
    chain = ", ".join(row["chain"])
    overlaps = ", ".join(row.get("overlaps") or []) or "none found by name"
    return (f"Skill proposal {row['id']}: would become {row['name']}, proven by a benchmark "
            f"task. For: {_plain(row['task'])} Done by hand in {len(row['chain'])} calls "
            f"({chain}). Against: {row.get('against') or 'none given'} "
            f"Overlaps: {overlaps}. "
            f"/skill_proposals approve {row['id']} or drop {row['id']}.")


def _show(row: dict[str, Any], session_id: str | None) -> dict[str, str]:
    """Show the proposal's notice; ``{state_key: panel_id}`` for what was shown.

    On the caller's session first. When nobody is on it — a cron turn has no HUD
    client — on every live session instead, so the weekly curator's proposals land
    where the person is rather than under a key nothing lists. Never raises: a
    screen that cannot be reached is not a reason the row was not written.
    """
    try:
        hud = _hud()
    except Exception:  # noqa: BLE001 - a broken sibling plugin must not fail the proposal
        _LOGGER.exception("aura-hud failed to load for a skill proposal")
        return {}
    if hud is None:
        return {}
    caller = hud._state_key(session_id)
    keys = [caller]
    if hud._resolve_tui_sid(caller) is None:
        # ponytail: fan-out to every live session; one "review" session key would
        # replace it if the HUD ever grows a queue surface of its own.
        keys += [str(entry.get("session_key") or sid)
                 for sid, entry in list((hud._sessions_table() or {}).items())
                 if isinstance(entry, dict)]
    payload = {"level": "info", "text": _panel_text(row), "proposal_id": row["id"],
               "name": row["name"], "chain": row["chain"], "actions": ["approve", "drop"]}
    shown: dict[str, str] = {}
    for key in dict.fromkeys(keys):
        try:
            record, _delivered = hud.STORE.show(key, PANEL_KIND, f"Skill proposal: {row['name']}",
                                                payload, panel_id="p_" + row["id"],
                                                origin="system")
        except Exception:  # noqa: BLE001 - a rejected panel is logged, not raised
            _LOGGER.exception("the HUD refused the proposal panel on %s", key)
            continue
        shown[key] = str(record["id"])
    return shown


def _dismiss(row: dict[str, Any]) -> None:
    hud = _hud()
    if hud is None:
        return
    for key, pid in dict(row.get("panels") or {}).items():
        try:
            hud.STORE.dismiss(key, pid, origin="user")
        except Exception:  # noqa: BLE001 - the row's fate does not depend on the panel
            _LOGGER.exception("could not dismiss proposal panel %s on %s", pid, key)


# --------------------------------------------------------------------------- #
# The verbs
# --------------------------------------------------------------------------- #
def propose(params: dict, *, session_id: str | None = None) -> dict[str, Any]:
    """Write one row and show its notice. Builds nothing."""
    task = str(params.get("task") or "").strip()
    if not task:
        raise ValueError("a proposal needs the task: what was done by hand, in a sentence")
    chain = _list_arg(params.get("chain"), "chain")
    if len(chain) < MIN_CHAIN:
        raise ValueError(f"a chain of {len(chain)} call(s) is not a skill; {MIN_CHAIN} or more")
    name = str(params.get("name") or "").strip()
    tool = str(params.get("tool") or name.replace("-", "_")).strip()
    scaffold._validate(name, tool, [{"name": tool, "description": task}])
    bench_prompt = str(params.get("bench_prompt") or "").strip()
    if not bench_prompt:
        raise ValueError("a proposal needs bench_prompt: the request that would prove the skill")
    # The owner approves a judgement, not a rubber stamp: the proposer argues the
    # other side in the same call, and a restated task is not an argument.
    against = _plain(str(params.get("against") or ""))
    if not against or _words(against) == _words(task):
        raise ValueError("a proposal needs against: the strongest case for not building it "
                         "(cost, an existing skill or tool, too rare to be worth it)")
    rows = QUEUE.rows()
    for row in rows:
        if row.get("name") == name and row.get("status") == "open":
            return {"ok": True, "proposal": row, "already": True,
                    "note": f"{name} is already in the queue as {row['id']}; nothing added"}
    row = {
        "id": uuid.uuid4().hex[:8], "created_at": _now(), "status": "open",
        "task": task, "chain": chain, "name": name, "tool": tool,
        "bench_prompt": bench_prompt, "against": against,
        "overlaps": _overlaps(f"{name} {tool}", *_existing()),
        "dead_ends": _list_arg(params.get("dead_ends"), "dead_ends"),
        "parameters": _list_arg(params.get("parameters"), "parameters"),
        "session": str(session_id or ""), "panels": {},
    }
    row["panels"] = _show(row, session_id)
    with QUEUE.lock:
        QUEUE.save(QUEUE.rows() + [row])
    return {"ok": True, "proposal": row, "already": False, "shown_on": len(row["panels"]),
            "note": "In the review queue. Nothing is built until it is approved."}


def render_skill_md(row: dict[str, Any]) -> str:
    """The SKILL.md a proposal becomes: frontmatter Hermes indexes, then the chain."""
    steps = "\n".join(f"{i}. `{tool}`" for i, tool in enumerate(row["chain"], 1))
    dead = "\n".join(f"- {d}" for d in row["dead_ends"]) or "- None recorded."
    vary = ("\n".join(f"- {p}" for p in row["parameters"])
            or "- Nothing recorded; the chain ran with fixed arguments.")
    return (
        "---\n"
        f"name: {row['name']}\n"
        f"description: {json.dumps(row['task'])}\n"
        "version: 0.1.0\n"
        "author: JARVIS distillation loop\n"
        "license: MIT\n"
        "platforms: [linux]\n"
        "metadata:\n"
        "  hermes:\n"
        f"    tags: [distilled, proposal-{row['id']}]\n"
        "---\n\n"
        f"# {row['task']}\n\n"
        f"Distilled from a chain JARVIS ran by hand (proposal {row['id']}, {row['created_at']}). "
        f"The `{row['tool']}` tool in `hermes/plugins/{row['name']}/` is the script this became; "
        "until its handler is filled in, the sequence below is the working procedure.\n\n"
        f"## The working sequence\n\n{steps}\n\n"
        f"## Dead ends worth avoiding\n\n{dead}\n\n"
        f"## What should vary\n\n{vary}\n\n"
        "## Proof\n\n"
        f"`bench/tasks/coding-selfext/selfext-{row['name']}-001.yaml`: {json.dumps(row['bench_prompt'])}\n"
    )


def approve(pid: str) -> dict[str, Any]:
    """Plan and scaffold the proposal on its branch; commit and push stay with the loop."""
    from . import tools  # the plan is selfext_plan's, verbatim; tools imports this module

    rows = QUEUE.rows()
    row = _find(rows, pid)
    if row["status"] != "open":
        raise ValueError(f"proposal {pid} is {row['status']}, not open")
    if not git.available():
        raise git.GitError("no self-extension checkout (selfext.repo_path); a proposal can "
                           "be approved only where the loop can write")
    name = row["name"]
    props = {p: {"type": "string"} for p in row["parameters"]}
    tool_specs = [{"name": row["tool"], "description": row["task"],
                   "parameters": {"type": "object", "properties": props, "required": []}}]
    plan = tools.plan({"name": name, "why": row["task"], "tools": tool_specs})
    # Refused before the branch exists, so a taken name leaves the checkout where
    # it was; scaffold asks again, which is cheap.
    scaffold.assert_name_is_free(name)
    git.create_branch(plan["branch"])
    made = scaffold.scaffold(name, row["task"], plan["toolset"], tool_specs,
                             bench_prompt=row["bench_prompt"], skill_md=render_skill_md(row))
    row.update(status="approved", approved_at=_now(), branch=plan["branch"], files=made["files"])
    _dismiss(row)
    with QUEUE.lock:
        QUEUE.save([row if r.get("id") == pid else r for r in QUEUE.rows()])
    return {
        "ok": True, "proposal": pid, "plan": plan, "branch": plan["branch"],
        "files": made["files"], "bench_task": made["bench_task"],
        "next": [f"fill in handle_{row['tool']} in hermes/plugins/{name}/__init__.py",
                 "selfext_verify", "selfext_open_mr"],
        "note": ("Written to the checkout on the branch, uncommitted. The merge request "
                 "is selfext_open_mr's and asks first; nothing is merged."),
    }


def drop(pid: str) -> dict[str, Any]:
    rows = QUEUE.rows()
    row = _find(rows, pid)
    if row["status"] != "open":
        raise ValueError(f"proposal {pid} is {row['status']}, not open")
    row.update(status="dropped", dropped_at=_now())
    _dismiss(row)
    with QUEUE.lock:
        QUEUE.save([row if r.get("id") == pid else r for r in QUEUE.rows()])
    return {"ok": True, "proposal": pid, "status": "dropped"}


def review(params: dict) -> dict[str, Any]:
    """The ``skill_proposals`` tool: list (open rows), approve <id>, drop <id>."""
    action = str(params.get("action") or "list").strip().lower()
    if action == "list":
        rows = QUEUE.rows()
        wanted = [r for r in rows if r.get("status") == "open"] if not params.get("all") else rows
        return {"ok": True, "count": len(wanted), "proposals": wanted,
                "branch_prefix": from_env().selfext_branch_prefix}
    pid = str(params.get("id") or "").strip()
    if not pid:
        raise ValueError(f"{action} needs the proposal id")
    if action == "approve":
        return approve(pid)
    if action == "drop":
        return drop(pid)
    raise ValueError("action must be list, approve or drop")


def handle_command(raw_args: str) -> str:
    """``/skill_proposals [@<sid>] list | approve <id> | drop <id>``; the sid is ignored."""
    _sid, rest = split_session(raw_args)
    head, _, tail = rest.partition(" ")
    action = head.strip().lower() or "list"
    if action not in ("list", "approve", "drop"):
        return USAGE
    return json.dumps(review({"action": action, "id": tail.strip()}))
