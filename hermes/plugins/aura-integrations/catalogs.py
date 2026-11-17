"""Adapter over the three Hermes catalogues (plugins, skills, MCP servers).

The apps and the agent browse, inspect, install, update, enable/disable and
remove integrations that Hermes already knows how to fetch. Hermes exposes each
catalogue only through its CLI (``hermes plugins|skills|mcp …``); the Python
that backs those commands lives in ``hermes_cli`` — 100–300 KB command handlers
that import the whole gateway and are not on the path when this plugin's tests
run (the sibling plugins all load without Hermes present). So this module treats
the CLI as the stable contract and shells out to it under a timeout, preferring
the machine-readable ``--json`` where a command offers it (``plugins list`` /
``plugins search`` / ``skills search``) and parsing the human table where it
does not (``skills list`` and every ``mcp`` command emit only a rendered table).

Every subprocess call goes through :func:`_run`, so the tests replace that one
function with canned CLI output and never touch the network. The functions
return rows normalised to the shape the contract fixes
(``tests/contracts/hermes-integrations.json`` → ``row_fields``): ``id``, ``name``,
``description``, ``source``, ``trust``, ``installed``, ``version`` (plus extra
keys where a kind carries more, e.g. a skill's ``enabled`` flag).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from aura_schema import from_env

#: The seven fields every normalised row carries; the row may hold more.
ROW_FIELDS: list[str] = ["id", "name", "description", "source", "trust", "installed", "version"]

#: Timeouts (seconds), from the one settings schema. Reads are quick; an install
#: may clone a repo or run npx, so it gets minutes — the timeout is the ceiling,
#: not the expected duration. Read once at import: a bad value fails the plugin
#: load with the variable named rather than the first catalogue call.
READ_TIMEOUT: float = from_env().integrations_read_timeout
WRITE_TIMEOUT: float = from_env().integrations_write_timeout

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
# Box-drawing cell separators Rich uses (│ light, ┃ heavy); a data row is split
# on either, and a line made only of border glyphs is dropped.
_CELL_SEP_RE = re.compile(r"[│┃|]")
_BORDER_RE = re.compile(r"^[\s─━│┃┏┓┗┛┡┩├┤┼╇╈╉╊┏┳┫┣┻╋┠┨╍╌—\-=_·]*$")


class CatalogError(RuntimeError):
    """A catalogue command failed (non-zero exit, timeout, or missing binary)."""


def hermes_bin() -> str:
    """Absolute path to the ``hermes`` CLI, or the bare name as a last resort.

    ``HERMES_BIN`` wins (the deploy image may put it somewhere non-standard),
    then ``PATH``, then the dev install location. Named so a caller can log the
    exact binary a failed call used.
    """
    override = os.environ.get("HERMES_BIN", "").strip()
    if override:
        return override
    found = shutil.which("hermes")
    if found:
        return found
    dev = Path.home() / ".local" / "bin" / "hermes"
    return str(dev) if dev.exists() else "hermes"


def _run(args: list[str], *, timeout: float, stdin: str | None = None) -> tuple[int, str, str]:
    """Run ``hermes <args>`` and return (returncode, stdout, stderr).

    The single choke point every catalogue call passes through, so the tests
    stub exactly here. ``COLUMNS`` is widened and colour disabled so Rich renders
    a stable, un-truncated, un-coloured table for the parsers; a timeout or a
    missing binary is turned into a returncode rather than raised, so a wedged
    CLI never takes down the tool call.
    """
    env = dict(os.environ)
    env["COLUMNS"] = "200"
    env["NO_COLOR"] = "1"
    env["TERM"] = "dumb"
    try:
        proc = subprocess.run(
            [hermes_bin(), *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            input=stdin,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return 124, "", f"timed out after {timeout:g}s"
    except FileNotFoundError:
        return 127, "", f"hermes binary not found ({hermes_bin()!r})"
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def _json_out(args: list[str], *, timeout: float) -> Any:
    """Run a ``--json`` command and parse its stdout, or raise CatalogError."""
    rc, out, err = _run(args, timeout=timeout)
    if rc != 0:
        raise CatalogError(f"`hermes {' '.join(args)}` exited {rc}: {(err or out).strip()[:400]}")
    text = out.strip()
    if not text:
        return []
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        # A json command that printed a banner first: recover the JSON body.
        body = _first_json_blob(text)
        if body is not None:
            return body
        raise CatalogError(f"`hermes {' '.join(args)}` did not return JSON: {exc}") from exc


def _first_json_blob(text: str) -> Any | None:
    """The first ``{...}`` or ``[...]`` that parses, or None (banner tolerance)."""
    for opener, closer in (("[", "]"), ("{", "}")):
        start = text.find(opener)
        end = text.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                continue
    return None


# --------------------------------------------------------------------------- #
# Table parsing
# --------------------------------------------------------------------------- #
def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def parse_rich_table(text: str) -> list[dict[str, str]]:
    """Parse a Rich box table (``skills list``, ``mcp list``) to dicts.

    The header is the first row split by a cell separator; each later separated,
    non-border line is a record keyed by the header. Border/rule lines (all box
    glyphs) are skipped. Truncated cells keep their trailing ``…`` — a browse can
    live with a clipped description; the id and name columns come first and do
    not clip at ``COLUMNS=200``.
    """
    rows: list[dict[str, str]] = []
    header: list[str] | None = None
    for raw in _strip_ansi(text).splitlines():
        line = raw.rstrip()
        if not line.strip() or _BORDER_RE.match(line):
            continue
        if not _CELL_SEP_RE.search(line):
            continue
        cells = [c.strip() for c in _CELL_SEP_RE.split(line)]
        cells = [c for c in cells if c != ""]
        if not cells:
            continue
        if header is None:
            header = [c.lower() for c in cells]
            continue
        if len(cells) < len(header):
            cells += [""] * (len(header) - len(cells))
        rows.append(dict(zip(header, cells[: len(header)])))
    return rows


#: Underline glyphs a header rule may use: ASCII ``-`` (``mcp catalog``) and the
#: box-drawing horizontals (``─``/``━``, ``mcp list``).
_DASHES = set("-─━")


def parse_table(text: str) -> list[dict[str, str]]:
    """Best parse of a CLI table: Rich cells first, else fixed-width columns.

    ``skills list`` draws ``│`` cell separators (Rich); ``mcp catalog`` and
    ``mcp list`` are space-padded with a dashed underline and no separators.
    Trying the separated form first and the underline form second parses all
    three without the caller knowing which a given command emits.
    """
    rows = parse_rich_table(text)
    return rows if rows else parse_fixed_width_table(text)


def parse_fixed_width_table(text: str) -> list[dict[str, str]]:
    """Parse a space-padded table (header + dashed underline) to dicts.

    The underline row (runs of dash glyphs separated by single spaces) fixes each
    column's character span; every following non-empty line is sliced on those
    spans. This is why the parser needs the underline, not just whitespace: a
    description holds spaces, so column starts cannot be inferred from a data row.
    """
    lines = [ln for ln in _strip_ansi(text).splitlines()]
    dash_idx = None
    for i, ln in enumerate(lines):
        s = ln.strip()
        if s and set(s) <= (_DASHES | {" "}) and any(c in _DASHES for c in s):
            dash_idx = i
            break
    if dash_idx is None or dash_idx == 0:
        return []
    spans: list[tuple[int, int | None]] = []
    for m in re.finditer(r"[-─━]+", lines[dash_idx]):
        spans.append((m.start(), m.end()))
    if not spans:
        return []
    # The last column runs to the end of the line (descriptions overflow the dashes).
    spans[-1] = (spans[-1][0], None)
    header_line = lines[dash_idx - 1]
    headers = [_slice(header_line, s, e).strip().lower() for s, e in spans]
    rows: list[dict[str, str]] = []
    for ln in lines[dash_idx + 1:]:
        if not ln.strip():
            continue
        cells = [_slice(ln, s, e).strip() for s, e in spans]
        if not any(cells):
            continue
        rows.append(dict(zip(headers, cells)))
    return rows


def _slice(line: str, start: int, end: int | None) -> str:
    return line[start:] if end is None else line[start:end]


# --------------------------------------------------------------------------- #
# Normalisation
# --------------------------------------------------------------------------- #
def _row(
    *,
    id: str,
    name: str = "",
    description: str = "",
    source: str = "",
    trust: str = "",
    installed: bool = False,
    version: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    row = {
        "id": id,
        "name": name or id,
        "description": description,
        "source": source,
        "trust": trust,
        "installed": installed,
        "version": version,
    }
    row.update(extra)
    return row


# ---- plugins ----------------------------------------------------------------
def _plugin_trust(source: str) -> str:
    # Trust for a plugin is what we can honestly say about where its code came
    # from: bundled ships with Hermes; anything else is unreviewed by us.
    return "bundled" if source == "bundled" else "unaudited"


def plugin_search(query: str, *, timeout: float = READ_TIMEOUT) -> list[dict[str, Any]]:
    data = _json_out(["plugins", "search", query, "--json"], timeout=timeout)
    results = data.get("results", []) if isinstance(data, dict) else (data or [])
    rows = []
    for r in results:
        if not isinstance(r, dict):
            continue
        pid = str(r.get("name") or r.get("id") or r.get("repo") or "")
        src = str(r.get("source") or (data.get("source") if isinstance(data, dict) else "") or "index")
        rows.append(_row(
            id=pid, name=str(r.get("name") or pid), description=str(r.get("description") or ""),
            source=src, trust=_plugin_trust(src), installed=bool(r.get("installed", False)),
            version=(str(r["version"]) if r.get("version") else None),
        ))
    return rows


def plugin_list(*, timeout: float = READ_TIMEOUT) -> list[dict[str, Any]]:
    data = _json_out(["plugins", "list", "--json"], timeout=timeout)
    rows = []
    for r in (data or []):
        if not isinstance(r, dict):
            continue
        pid = str(r.get("name") or "")
        src = str(r.get("source") or "")
        status = str(r.get("status") or "")
        rows.append(_row(
            id=pid, name=pid, description=str(r.get("description") or ""), source=src,
            trust=_plugin_trust(src), installed=True,
            version=(str(r["version"]) if r.get("version") else None),
            status=status, enabled=(status == "enabled"),
        ))
    return rows


def plugin_inspect(pid: str, *, timeout: float = READ_TIMEOUT) -> dict[str, Any]:
    rc, out, err = _run(["plugins", "show", pid], timeout=timeout)
    detail = (out or err).strip()
    return {"ok": rc == 0, "kind": "plugin", "id": pid,
            "row": _row(id=pid, name=pid, source="", trust="", installed=rc == 0), "detail": detail}


# ---- skills -----------------------------------------------------------------
def skill_search(query: str, *, limit: int = 20, timeout: float = READ_TIMEOUT) -> list[dict[str, Any]]:
    data = _json_out(["skills", "search", query, "--json", "--limit", str(limit)], timeout=timeout)
    rows = []
    for r in (data or []):
        if not isinstance(r, dict):
            continue
        ident = str(r.get("identifier") or r.get("id") or r.get("name") or "")
        rows.append(_row(
            id=ident, name=str(r.get("name") or ident), description=str(r.get("description") or ""),
            source=str(r.get("source") or ""), trust=str(r.get("trust_level") or r.get("trust") or ""),
            installed=bool(r.get("installed", False)),
            version=(str(r["version"]) if r.get("version") else None),
        ))
    return rows


def skill_list(*, timeout: float = READ_TIMEOUT) -> list[dict[str, Any]]:
    rc, out, err = _run(["skills", "list"], timeout=timeout)
    if rc != 0:
        raise CatalogError(f"`hermes skills list` exited {rc}: {(err or out).strip()[:400]}")
    rows = []
    for r in parse_table(out):
        name = r.get("name", "")
        if not name or name.lower() == "name":
            continue
        status = r.get("status", "")
        rows.append(_row(
            id=name, name=name, description="", source=r.get("source", ""),
            trust=r.get("trust", ""), installed=True, version=None,
            category=r.get("category", ""), status=status, enabled=(status == "enabled"),
        ))
    return rows


def skill_inspect(sid: str, *, timeout: float = READ_TIMEOUT) -> dict[str, Any]:
    rc, out, err = _run(["skills", "inspect", sid], timeout=timeout)
    detail = (out or err).strip()
    return {"ok": rc == 0, "kind": "skill", "id": sid,
            "row": _row(id=sid, name=sid, installed=False), "detail": detail}


# ---- mcp --------------------------------------------------------------------
def mcp_catalog(*, timeout: float = READ_TIMEOUT) -> list[dict[str, Any]]:
    rc, out, err = _run(["mcp", "catalog"], timeout=timeout)
    if rc != 0:
        raise CatalogError(f"`hermes mcp catalog` exited {rc}: {(err or out).strip()[:400]}")
    rows = []
    for r in parse_fixed_width_table(out):
        name = r.get("name", "")
        if not name:
            continue
        status = r.get("status", "")
        rows.append(_row(
            id=name, name=name, description=r.get("description", ""), source="nous-catalog",
            trust="nous-approved", installed=("installed" in status.lower()), version=None,
            status=status,
        ))
    return rows


def mcp_search(query: str, *, timeout: float = READ_TIMEOUT) -> list[dict[str, Any]]:
    q = query.lower().strip()
    rows = mcp_catalog(timeout=timeout)
    if not q:
        return rows
    return [r for r in rows if q in (r["name"] + " " + r["description"]).lower()]


def mcp_list(*, timeout: float = READ_TIMEOUT) -> list[dict[str, Any]]:
    rc, out, err = _run(["mcp", "list"], timeout=timeout)
    if rc != 0:
        raise CatalogError(f"`hermes mcp list` exited {rc}: {(err or out).strip()[:400]}")
    rows = []
    for r in parse_table(out):
        name = r.get("name", "")
        if not name or name.lower() == "name":
            continue
        status = r.get("status", "")
        rows.append(_row(
            id=name, name=name, description="", source="configured", trust="configured",
            installed=True, version=None,
            transport=r.get("transport", ""), enabled=("enabled" in status.lower()),
        ))
    return rows


def mcp_inspect(mid: str, *, timeout: float = READ_TIMEOUT) -> dict[str, Any]:
    # There is no `mcp show`; the catalog row is the closest to an inspection.
    match = next((r for r in mcp_catalog(timeout=timeout) if r["name"] == mid), None)
    if match is None:
        return {"ok": False, "kind": "mcp", "id": mid,
                "error": f"no MCP catalog entry named {mid!r}", "row": _row(id=mid)}
    return {"ok": True, "kind": "mcp", "id": mid, "row": match,
            "detail": f"{match['name']}: {match['description']} ({match['status']})"}


# --------------------------------------------------------------------------- #
# Mutations (each returns a result dict; the caller gates + audits)
# --------------------------------------------------------------------------- #
def _mutate(args: list[str], *, kind: str, mid: str, action: str,
            stdin: str | None = None, timeout: float = WRITE_TIMEOUT) -> dict[str, Any]:
    rc, out, err = _run(args, timeout=timeout, stdin=stdin)
    tail = (out or err).strip()
    return {
        "ok": rc == 0, "kind": kind, "id": mid, "action": action, "exit_code": rc,
        "output": tail[-1200:],
        **({} if rc == 0 else {"error": (err or out).strip()[:400] or f"exit {rc}"}),
    }


def _unsupported(kind: str, mid: str, action: str, hint: str) -> dict[str, Any]:
    return {"ok": False, "kind": kind, "id": mid, "action": action,
            "error": f"{action} is not supported for a {kind} via this API; {hint}"}


def install(kind: str, mid: str, *, timeout: float = WRITE_TIMEOUT) -> dict[str, Any]:
    if kind == "plugin":
        # Install disabled so a fetch never enables new gateway code without a
        # second, explicit enable step; --no-enable also skips the CLI's prompt.
        return _mutate(["plugins", "install", mid, "--no-enable"], kind=kind, mid=mid,
                       action="install", timeout=timeout)
    if kind == "skill":
        return _mutate(["skills", "install", mid, "--yes"], kind=kind, mid=mid,
                       action="install", timeout=timeout)
    if kind == "mcp":
        return _mutate(["mcp", "install", mid], kind=kind, mid=mid, action="install",
                       stdin="y\n", timeout=timeout)
    return _unsupported(kind, mid, "install", "kind must be plugin|skill|mcp")


def update(kind: str, mid: str, *, timeout: float = WRITE_TIMEOUT) -> dict[str, Any]:
    if kind == "plugin":
        return _mutate(["plugins", "update", mid], kind=kind, mid=mid, action="update", timeout=timeout)
    if kind == "skill":
        return _mutate(["skills", "update", mid], kind=kind, mid=mid, action="update", timeout=timeout)
    if kind == "mcp":
        return _unsupported(kind, mid, "update",
                            "MCP servers are not versioned here — remove and re-add to change one")
    return _unsupported(kind, mid, "update", "kind must be plugin|skill|mcp")


def remove(kind: str, mid: str, *, timeout: float = WRITE_TIMEOUT) -> dict[str, Any]:
    if kind == "plugin":
        return _mutate(["plugins", "remove", mid], kind=kind, mid=mid, action="remove",
                       stdin="y\n", timeout=timeout)
    if kind == "skill":
        return _mutate(["skills", "uninstall", mid, "--yes"], kind=kind, mid=mid,
                       action="remove", timeout=timeout)
    if kind == "mcp":
        return _mutate(["mcp", "remove", mid], kind=kind, mid=mid, action="remove",
                       stdin="y\n", timeout=timeout)
    return _unsupported(kind, mid, "remove", "kind must be plugin|skill|mcp")


def set_enabled(kind: str, mid: str, enabled: bool, *, timeout: float = WRITE_TIMEOUT) -> dict[str, Any]:
    action = "enable" if enabled else "disable"
    if kind == "plugin":
        args = (["plugins", "enable", mid, "--no-allow-tool-override"] if enabled
                else ["plugins", "disable", mid])
        return _mutate(args, kind=kind, mid=mid, action=action, timeout=timeout)
    if kind == "skill":
        return _unsupported(kind, mid, action,
                            "toggle a skill by installing or removing it (or `hermes skills config`)")
    if kind == "mcp":
        return _unsupported(kind, mid, action,
                            "toggle an MCP server with `hermes mcp add`/`remove` or `mcp configure`")
    return _unsupported(kind, mid, action, "kind must be plugin|skill|mcp")


# --------------------------------------------------------------------------- #
# Best-effort description for the approval message
# --------------------------------------------------------------------------- #
def describe(kind: str, mid: str, *, timeout: float = 12.0) -> dict[str, Any]:
    """Resolve ``source``/``trust``/``installed`` for one item, best effort.

    Used to enrich the approval message. Bounded and swallows every failure
    (returns ``{}``): the approval must render even with the network down, so a
    slow or failing lookup degrades to the static per-kind description the
    caller already holds — it must never block or crash the gate.
    """
    try:
        if kind == "skill":
            for r in skill_search(mid, limit=5, timeout=timeout):
                if r["id"] == mid or r["name"].lower() == mid.lower():
                    return {"source": r["source"], "trust": r["trust"], "installed": r["installed"]}
        elif kind == "plugin":
            for r in plugin_list(timeout=timeout):
                if r["id"] == mid:
                    return {"source": r["source"], "trust": r["trust"], "installed": True}
            for r in plugin_search(mid, timeout=timeout):
                if r["id"] == mid:
                    return {"source": r["source"], "trust": r["trust"], "installed": r["installed"]}
        elif kind == "mcp":
            for r in mcp_catalog(timeout=timeout):
                if r["name"] == mid:
                    return {"source": r["source"], "trust": r["trust"], "installed": r["installed"]}
    except Exception:  # a best-effort enrichment must never break the gate
        return {}
    return {}
