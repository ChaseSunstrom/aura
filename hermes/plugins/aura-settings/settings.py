"""Reading and changing JARVIS's settings from the apps and the agent.

The gateway's ``config.set`` RPC only knows ``prompt``, ``personality`` and
``display.*`` (everything else answers ``4002 unknown config key``), so the
console, the phone and the agent change settings through this plugin instead: a
``/jsettings`` slash command for the apps and the approval-gated ``settings_*``
tools for the agent.

Two stores, one schema (``aura_schema``, V2 D17). A leaf with a Hermes key
goes to ``config.yaml`` through Hermes's own config writer (:func:`_hermes_write_key`,
never hand-edited YAML text). A leaf with an env name goes to the override file
``<HERMES_HOME>/.storage/environment.json`` (ADR-0044), which the entrypoint
exports over the container environment on the next start and the adapter reads
at its own load — so an env-backed setting changes after a restart, the way
jarvis-core's own env store does, and never by editing the host ``.env``, which
the container cannot reach. A leaf with both is written to both so the next
render agrees. A leaf the compose file fixes on the host (a port bind, an image
tag, a value renamed on its way into a service) is refused: :data:`HOST_OWNED_ENV`.

Every write is validated by the schema's ``coerce`` and audit-logged one JSON
line per write to ``<HERMES_HOME>/logs/settings.jsonl``. A write from the
command path (the console) that the approval matrix would ask about does not
write: it becomes a proposal the person applies or discards, so the console
cannot walk around the ``settings_set`` ASK row that gates the agent's tool.

The prose contract is ``docs/hermes-hud-protocol.md`` ("Settings — reading and
changing Hermes's own configuration"); the machine contract the console, the
phone and the mock read is ``tests/contracts/hermes-settings.json``, generated
from the schema. :data:`KEYS` (the ``keys`` table there, the runtime allow-list
the agent's tools describe) and :data:`LEAVES` (every leaf the command serves)
both come from that schema, so the plugin cannot serve a key the contract does
not carry or accept a value the schema would refuse. Everything Hermes-specific
is imported lazily so this module (and its tests) load without Hermes on the
path — the rule the sibling plugins follow.
"""
from __future__ import annotations

import functools
import itertools
import json
import re
import logging
import os
import threading
import uuid
from collections.abc import Iterator, Mapping
import socket
import ssl
from datetime import date, datetime, timezone
from urllib.parse import urlparse
from typing import Any, Callable, List, Optional

import httpx
import yaml

from aura_schema import (
    HOST_OWNED_ENV,
    SETTINGS,
    Kind,
    SETTINGS_BY_KEY,
    Setting,
    applies as _applies,
    coerce as _coerce,
    env_string as _env_string,
    load_overrides as _load_overrides,
    overrides_path as _overrides_path,
    settable_settings,
)
from aura_schema import SettingsError as _SchemaError

from _shared import _audit, _egress, _failure, _house, _levels, _matrix
from _shared._broadcast import broadcast
from _shared._approval_hook import split_session

_LOGGER = logging.getLogger(__name__)

#: The slash command the apps dispatch (``command.dispatch {name:"jsettings"}``).
COMMAND_NAME: str = "jsettings"
#: The three agent tools, in the order the contract lists them.
TOOLS: list[str] = ["settings_list", "settings_get", "settings_set"]
#: Toolset the tools live under; the ``settings_set`` write is approval-gated.
TOOLSET: str = "settings"
#: Audit log path, relative to ``HERMES_HOME`` (matches the contract).
AUDIT_REL: str = "logs/settings.jsonl"
#: How many changes ``history`` lists when not told, and the most it ever lists: the
#: answer rides one command reply to a phone, and a layout change's values are large.
HISTORY_DEFAULT: int = 20
HISTORY_MAX: int = 50
#: Where a proposed-but-unapplied write waits, relative to ``HERMES_HOME``. Beside
#: the override file and 0600 like it: a proposal for a secret carries the value.
PROPOSALS_REL: str = ".storage/settings-proposals.json"


def _key_table() -> dict[str, dict[str, Any]]:
    """The allow-list, from the schema's ``settable`` rows in contract order.

    Each entry carries what ``tests/contracts/hermes-settings.json`` does
    (type/min/max/values/shape/restart_needed/doc) plus ``default`` — the value
    Hermes falls back to when the key is absent from config.yaml, so ``get``/
    ``list`` can answer for an unset key.
    """
    table: dict[str, dict[str, Any]] = {}
    for s in settable_settings():
        spec: dict[str, Any] = {"type": s.contract_type, "restart_needed": s.restart_needed,
                                "default": s.default, "doc": s.description}
        if s.contract_type in ("int", "float"):
            spec["min"], spec["max"] = s.min, s.max
        if s.contract_type == "enum":
            spec["values"] = list(s.choices or ())
        if s.contract_type == "object":
            spec["shape"] = s.shape
        table[s.key] = spec
    return table


#: The allow-list (see :func:`_key_table`). Insertion order is the contract's order (used by ``list``).
KEYS: dict[str, dict[str, Any]] = _key_table()

#: Every leaf the command serves: the contract keys first, in their order, then
#: every other leaf with an env name or a Hermes key in the schema's own order.
#: The agent's ``settings_list`` tool keeps to :data:`KEYS` — 175 rows in a tool
#: result is the context cost the matrix's comment on ``settings_find`` warns of.
LEAVES: dict[str, Setting] = {
    **{k: SETTINGS_BY_KEY[k] for k in KEYS},
    **{s.key: s for s in SETTINGS if s.env or s.hermes_key},
}

_MISSING = object()


class SettingsError(ValueError):
    """A key that is not allow-listed or a value that fails the key's rule."""


# --------------------------------------------------------------------------- #
# Hermes-backed config IO (imported lazily; injectable for tests)
# --------------------------------------------------------------------------- #
def _hermes_load_resolved() -> dict:
    """The merged config Hermes actually reads (defaults + managed + env)."""
    from hermes_cli.config import load_config

    return load_config()


def _hermes_load_raw() -> dict:
    """The user's config.yaml exactly as written (no defaults), or ``{}``.

    Presence of a key here is what distinguishes ``source: "config"`` (the user
    set it) from ``source: "default"`` (it comes from Hermes's defaults).
    """
    from hermes_cli.config import get_config_path, read_user_config_raw

    path = get_config_path()
    return read_user_config_raw(path) if os.path.exists(path) else {}


def _hermes_write_key(key: str, value: Any) -> None:
    """Write one dotted key into config.yaml through Hermes's own writer.

    Prefers the gateway's ``_write_config_key`` (the exact primitive
    ``config.set`` uses — a comment-preserving raw read → mutate → atomic save).
    Off the gateway it falls back to the CLI's own raw read + ``_set_nested`` +
    atomic write, so a write never hand-edits YAML text and never persists the
    managed overlay or ``${VAR}`` expansion into the user's file.
    """
    writer: Optional[Callable[[str, Any], None]] = None
    try:
        import tui_gateway.server as _srv

        writer = _srv._write_config_key
    except Exception:
        writer = None
    if writer is not None:
        writer(key, value)
        return

    from hermes_cli.config import (
        ensure_hermes_home,
        get_config_path,
        _set_nested,
        read_user_config_raw,
    )
    from utils import atomic_yaml_write

    path = get_config_path()
    cfg = read_user_config_raw(path) if os.path.exists(path) else {}
    _set_nested(cfg, key, value)
    ensure_hermes_home()
    atomic_yaml_write(path, cfg, sort_keys=False)


def _hermes_home() -> str:
    """The active ``HERMES_HOME`` (the directory config.yaml lives in)."""
    try:
        from hermes_cli.config import get_config_path

        return os.path.dirname(str(get_config_path()))
    except Exception:
        return os.environ.get("HERMES_HOME") or os.path.expanduser("~/.hermes")


def _nested(data: Any, dotted: str) -> Any:
    """``data`` walked by a dotted key, or :data:`_MISSING` if any hop is absent."""
    cur = data
    for seg in dotted.split("."):
        if isinstance(cur, dict) and seg in cur:
            cur = cur[seg]
        else:
            return _MISSING
    return cur


def _write_json_0600(path: str, data: Any, mode: int = 0o600) -> None:
    """Replace ``path`` atomically with ``data`` as JSON, readable by its owner only.

    The override file carries secrets in clear (the same ones the host ``.env``
    holds), so it is created 0600 before a byte is written, never chmod'ed down
    afterwards. The directory is 0711 like ``HERMES_HOME`` itself: cd-through,
    no listing, so a reader that is granted the file later needs no directory
    change. A crash between the tmp write and the rename leaves the old file.
    """
    os.makedirs(os.path.dirname(path), mode=0o711, exist_ok=True)
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1, sort_keys=True)
        fh.write("\n")
    os.chmod(tmp, mode)  # a leftover tmp from an older umask keeps O_CREAT's mode otherwise
    os.replace(tmp, path)


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #
def _validate_value(key: str, value: Any) -> Any:
    """The schema's ``coerce`` in strict mode, raising this plugin's :class:`SettingsError`.

    Strict: the value arrived as JSON, so it is already typed — a string
    ``"true"`` or ``"30"`` is refused rather than converted, so the value Hermes
    reads from config.yaml is a real YAML bool/int, never a truthy string. The
    returned value is normalised (a layout document comes back with its defaults
    filled in) and is what gets written.
    """
    try:
        return _coerce(SETTINGS_BY_KEY[key], value, strict=True)
    except _SchemaError as exc:
        raise SettingsError(str(exc)) from None


def writable(setting: Setting) -> bool:
    """Whether this plugin can land a value for the leaf anywhere the stack reads it."""
    return bool(setting.hermes_key) or setting.env not in HOST_OWNED_ENV


#: What a secret's value shows as on an approval card and in the change record.
MASK = "(secret)"


def _shown(setting: Setting, value: Any) -> Any:
    """A secret's value never leaves the store: a set one is :data:`MASK`, an unset one ``""``."""
    if setting.secret and value:
        return MASK
    return value


# --------------------------------------------------------------------------- #
# Store
# --------------------------------------------------------------------------- #
class SettingsStore:
    """Reads and writes the settings and appends the audit log.

    The config IO callables, the environment and the home directory are
    injected so the tests drive the whole store against an in-memory config, a
    dict environment and a temp home without Hermes on the path (the pattern
    the sibling plugins use for their gateway / HTTP dependencies).
    """

    def __init__(
        self,
        *,
        load_resolved: Optional[Callable[[], dict]] = None,
        load_raw: Optional[Callable[[], dict]] = None,
        write_key: Optional[Callable[[str, Any], None]] = None,
        audit_path: Optional[str] = None,
        clock: Optional[Callable[[], datetime]] = None,
        environ: Optional[Mapping[str, str]] = None,
        home: Optional[str] = None,
        announce: Optional[Callable[[str], Any]] = None,
        announce_proposals: Optional[Callable[[dict], Any]] = None,
    ) -> None:
        self._load_resolved = load_resolved or _hermes_load_resolved
        self._load_raw = load_raw or _hermes_load_raw
        self._write_key = write_key or _hermes_write_key
        self._audit_path = audit_path
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._environ = environ
        self._home = home
        # Every open console hears which key changed, so a screen that shows settings re-reads
        # them on a change instead of on a timer (T-0746: the car re-read all of them every 4 s).
        # Only the HUD gateway reaches consoles: a write from the API server or a cron run is
        # seen by a console on its next reconnect.
        self._announce = announce or (lambda key: broadcast("settings.changed", {"key": key}))
        # The proposals file changed (made, applied, discarded): an open Approvals screen re-reads
        # its list (T-1322). The payload is the count and nothing else: the file holds a proposed
        # secret in clear, so no key, value or id goes out on the socket.
        self._announce_proposals = announce_proposals or (
            lambda payload: broadcast("settings.proposals", payload))
        self._audit_lock = threading.Lock()
        self._state_lock = threading.Lock()

    def _tell_proposals(self, waiting: int) -> None:
        try:
            self._announce_proposals({"waiting": waiting})
        except Exception:  # the file is already written: a wedged socket must not fail the change
            _LOGGER.exception("settings.proposals announce failed")

    # -- files ------------------------------------------------------------- #
    def _home_dir(self) -> str:
        return self._home or _hermes_home()

    def _overrides_file(self) -> str:
        return _overrides_path(self._home_dir())

    def _proposals_file(self) -> str:
        return os.path.join(self._home_dir(), PROPOSALS_REL)

    def _read_overrides(self) -> dict[str, str]:
        """What the next start will export (a broken file is logged and counts as none)."""
        try:
            return _load_overrides(self._overrides_file())
        except (OSError, ValueError):
            _LOGGER.exception("settings overrides unreadable: %s", self._overrides_file())
            return {}

    def _read_proposals(self) -> list[dict]:
        try:
            with open(self._proposals_file(), encoding="utf-8") as fh:
                data = json.load(fh)
        except FileNotFoundError:
            return []
        except (OSError, ValueError):
            _LOGGER.exception("settings proposals unreadable: %s", self._proposals_file())
            return []
        return [p for p in data if isinstance(p, dict)] if isinstance(data, list) else []

    # -- reads ------------------------------------------------------------- #
    def _snapshot(self) -> tuple[dict, dict, dict[str, str], Mapping[str, str]]:
        """One load of everything a row reads — ``list`` walks 175 leaves."""
        environ = self._environ if self._environ is not None else os.environ
        return self._load_resolved(), self._load_raw(), self._read_overrides(), environ

    def _current_from(self, setting: Setting, snap: tuple) -> tuple[Any, str]:
        """``(value, source)``: config if the user set the Hermes key, else the
        override file, else the environment, else the default.

        Config wins for a leaf with both a Hermes key and an env name because it
        is what the gateway reads now; the override and the env are what the
        next start will render from, and a ``set`` writes both so they agree.
        """
        resolved, raw, overrides, environ = snap
        rv: Any = _MISSING
        if setting.hermes_key:
            rv = _nested(resolved, setting.hermes_key)
            if _nested(raw, setting.hermes_key) is not _MISSING:
                return (rv if rv is not _MISSING else _nested(raw, setting.hermes_key)), "config"
        if setting.env:
            if setting.env in overrides:
                return self._typed(setting, overrides[setting.env]), "override"
            present = (environ.get(setting.env) or "").strip()
            if present:
                return self._typed(setting, present), "env"
        if rv is not _MISSING:
            return rv, "default"
        return setting.default, "default"

    @staticmethod
    def _typed(setting: Setting, text: str) -> Any:
        """An env string as its declared type; a value that does not fit is shown as it is."""
        try:
            return _coerce(setting, text)
        except _SchemaError:
            return text

    def _current(self, setting: Setting) -> tuple[Any, str]:
        return self._current_from(setting, self._snapshot())

    def _row(self, setting: Setting, value: Any, source: str) -> dict:
        return {
            "key": setting.key,
            "value": _shown(setting, value),
            "type": setting.contract_type,
            "restart_needed": setting.restart_needed,
            "doc": setting.description,
            "source": source,
            "settable": writable(setting),
            "applies": _applies(setting),
        }

    def get(self, key: str) -> dict:
        setting = LEAVES.get(key)
        if setting is None:
            return _unknown(key)
        value, source = self._current(setting)
        return self._row(setting, value, source)

    def list(self, *, contract_only: bool = False) -> List[dict]:  # `list` is this class's method
        """Every leaf (the command), or the contract's allow-list (the agent's tool)."""
        snap = self._snapshot()
        keys = KEYS if contract_only else LEAVES
        return [self._row(LEAVES[k], *self._current_from(LEAVES[k], snap)) for k in keys]

    # -- write ------------------------------------------------------------- #
    def set(self, key: str, value: Any, *, actor: str, session: str = "") -> dict:
        """Validate, then write or propose.

        Returns ``{key, value, previous, restart_needed, applies, source}`` for a
        write, ``{"status": "proposed", "proposal": {...}}`` when the approval
        matrix would ask about ``settings_set`` on this key and the caller is the
        command path, or ``{"error": …}``. The agent's tool (``actor="tool"``)
        always writes: its ``pre_tool_call`` hook has already asked.
        """
        setting = LEAVES.get(key)
        if setting is None:
            return _unknown(key)
        if not writable(setting):
            return {"error": f"{key} is fixed outside the container ({setting.env} in the host "
                             ".env / compose file); edit .env and recreate the stack"}
        try:
            value = _validate_value(key, value)
        except SettingsError as exc:
            return {"error": str(exc)}
        if actor == "command":
            decision = _matrix.decide("settings_set", {"key": key})
            if decision.verdict == _matrix.DENY:
                return {"error": f"refused: {decision.reason}"}
            if decision.verdict == _matrix.ASK:
                return {"status": "proposed", "proposal": self._propose(setting, value)}
        return self._write(setting, value, actor=actor, session=session)

    def _write(self, setting: Setting, value: Any, *, actor: str, session: str,
               proposal: Optional[str] = None) -> dict:
        before, _ = self._current(setting)
        if setting.hermes_key:
            self._write_key(setting.hermes_key, value)
        if setting.env and setting.env not in HOST_OWNED_ENV:
            with self._state_lock:
                data = self._read_overrides()
                data[setting.env] = _env_string(setting, value)
                # 0640, not 0600: the adapter (uid 10001, in the gateway's group) reads this
                # file at start for its own leaves; the proposals stay the gateway's alone.
                _write_json_0600(self._overrides_file(), data, mode=0o640)
        self._audit(actor=actor, session=session, key=setting.key,
                    before=_shown(setting, before), after=_shown(setting, value), proposal=proposal)
        self._announce(setting.key)
        return {
            "key": setting.key,
            "value": _shown(setting, value),
            "previous": _shown(setting, before),
            "restart_needed": setting.restart_needed,
            "applies": _applies(setting),
            "source": "config" if setting.hermes_key else "override",
        }

    # -- proposals --------------------------------------------------------- #
    def _propose(self, setting: Setting, value: Any) -> dict:
        before, source = self._current(setting)
        proposal = {
            "id": uuid.uuid4().hex[:12],
            "key": setting.key,
            "before": before,
            "after": value,
            "source": source,
            "applies": _applies(setting),
            "ts": self._clock().strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        with self._state_lock:
            items = self._read_proposals()
            items.append(proposal)
            _write_json_0600(self._proposals_file(), items)
        self._tell_proposals(len(items))
        return self._shown_proposal(proposal)

    @staticmethod
    def _shown_proposal(proposal: dict) -> dict:
        setting = LEAVES.get(str(proposal.get("key")))
        if setting is None:
            return dict(proposal)
        return {**proposal, "before": _shown(setting, proposal.get("before")),
                "after": _shown(setting, proposal.get("after"))}

    def proposals(self) -> List[dict]:
        return [self._shown_proposal(p) for p in self._read_proposals()]

    def _take_proposal(self, pid: str) -> Optional[dict]:
        """Remove and return the proposal ``pid``, or None."""
        with self._state_lock:
            items = self._read_proposals()
            found = next((p for p in items if p.get("id") == pid), None)
            if found is not None:
                rest = [p for p in items if p is not found]
                _write_json_0600(self._proposals_file(), rest)
        if found is not None:
            self._tell_proposals(len(rest))
        return found

    def apply(self, pid: str, *, actor: str, session: str = "") -> dict:
        """Write a proposal: the person's yes. Validated again, and still refused
        while the kill switch is engaged — the brake cannot be talked past."""
        proposal = next((p for p in self._read_proposals() if p.get("id") == pid), None)
        if proposal is None:
            return {"error": f"no proposal {pid!r}"}
        setting = LEAVES.get(str(proposal.get("key")))
        if setting is None or not writable(setting):
            self._take_proposal(pid)
            return {"error": f"proposal {pid!r} names a setting that can no longer be written"}
        try:
            value = _validate_value(setting.key, proposal.get("after"))
        except SettingsError as exc:
            return {"error": str(exc)}
        decision = _matrix.decide("settings_set", {"key": setting.key})
        if decision.verdict == _matrix.DENY:
            return {"error": f"refused: {decision.reason}"}
        self._take_proposal(pid)
        return {**self._write(setting, value, actor=actor, session=session, proposal=pid),
                "proposal": pid}

    def _changes(self) -> Iterator[dict]:
        """The audit log's change records, newest first; a torn or foreign line is skipped."""
        # ponytail: reads the whole log per call; seek from the end once it outgrows a few MB.
        try:
            with open(self._audit_file(), encoding="utf-8") as fh:
                lines = fh.read().splitlines()
        except (OSError, ValueError):  # ValueError: bytes that are not UTF-8
            return
        for line in reversed(lines):
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if isinstance(rec, dict) and "before" in rec and isinstance(rec.get("key"), str):
                yield rec

    def last_change(self, key: str = "") -> Optional[dict]:
        """The newest audit line (for ``key`` if given), or None."""
        return next((rec for rec in self._changes() if not key or rec["key"] == key), None)

    def history(self, limit: int = HISTORY_DEFAULT) -> List[dict]:
        """The newest ``limit`` changes (at most :data:`HISTORY_MAX`), newest first: ``ts``,
        ``actor``, ``key``, ``before``, ``after`` as the audit stored them (a secret is already
        its mask there), and ``undoable``: :meth:`undo` would act on this row.

        ``undoable`` is true only on the newest row of a key this store can write and that is
        not a secret, because undo is one level. Not claimed: that the undo will be accepted
        (a security key becomes a proposal, the kill switch refuses), nor that a change made
        outside this log (the house's settings, a hand edit) is listed.
        """
        rows: List[dict] = []
        seen: set = set()
        for rec in itertools.islice(self._changes(), max(1, min(int(limit), HISTORY_MAX))):
            key = rec["key"]
            setting = LEAVES.get(key)
            row = {name: rec.get(name) for name in ("ts", "actor", "key", "before", "after")}
            row["undoable"] = bool(key not in seen and setting is not None
                                   and not setting.secret and writable(setting))
            seen.add(key)
            rows.append(row)
        return rows

    def undo(self, key: str = "", *, actor: str, session: str = "") -> dict:
        """Put back the value the newest change (to ``key`` if given) replaced.

        One level only: the undo is itself an audited change, so a second undo
        takes the first back. Does not cover a secret (the audit keeps only its
        mask) or an edit made outside this plugin (it was never audited).
        """
        rec = self.last_change(key)
        if rec is None:
            return {"error": f"nothing to undo{' for ' + key if key else ''}"}
        setting = LEAVES.get(str(rec.get("key")))
        if setting is not None and setting.secret:
            return {"error": f"{setting.key} is a secret; its old value is not kept, set it again"}
        return self.set(str(rec.get("key")), rec["before"], actor=actor, session=session)

    def discard(self, pid: str) -> dict:
        if self._take_proposal(pid) is None:
            return {"error": f"no proposal {pid!r}"}
        return {"status": "discarded", "id": pid}

    # -- audit ------------------------------------------------------------- #
    def _audit_file(self) -> str:
        if self._audit_path:
            return self._audit_path
        return os.path.join(self._home_dir(), AUDIT_REL)

    def _audit(self, *, actor: str, session: str, key: str, before: Any, after: Any,
               proposal: Optional[str] = None) -> None:
        """Append one JSON line recording who changed what, from what, to what.

        A failure here must never fail the setting write itself (the value is
        already persisted), so it is logged and swallowed. Secrets arrive masked:
        the log is backed up with the rest of the volume.
        """
        record: dict[str, Any] = {
            "ts": self._clock().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "actor": actor,
            "session": session,
            "key": key,
            "before": before,
            "after": after,
        }
        if proposal:
            record["proposal"] = proposal
        line = json.dumps(record)
        path = self._audit_file()
        try:
            with self._audit_lock:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "a", encoding="utf-8") as fh:
                    fh.write(line + "\n")
        except OSError:  # a full disk / permissions must not break the write
            _LOGGER.exception("settings audit append failed: key=%s", key)


def _unknown(key: str) -> dict:
    return {"error": f"unknown setting {key!r}; jsettings list names the {len(LEAVES)} it serves"}


#: The single process-wide store the tools and the command share.
STORE = SettingsStore()


# --------------------------------------------------------------------------- #
# Slash command  /jsettings @<sid> get <key> | set <key> <json> | list | ...
# --------------------------------------------------------------------------- #
USAGE = ("usage: /jsettings @<sid> get <key> | set <key> <json> | list | "
         "apply <id> | discard <id> | proposals | history [n] | undo [key]")


def handle_command(raw_args: str) -> str:
    # `@<sid> <rest>` — the apps prefix the arg with the session id session.create
    # returned, because plugin command handlers are never handed the session (same
    # convention as the /hud command). The sid is only used to stamp the audit line.
    sid, rest = split_session(raw_args)
    if sid is None:
        return USAGE + " (prefix the argument with @<session_id>)"
    head, _, tail = rest.partition(" ")
    head = head.lower()
    tail = tail.strip()

    if head in ("", "list"):
        return json.dumps(STORE.list())

    if head == "get":
        if not tail:
            return json.dumps({"error": "get needs a key"})
        return json.dumps(STORE.get(tail.split()[0]))

    if head == "set":
        key, _, blob = tail.partition(" ")
        key = key.strip()
        blob = blob.strip()
        if not key or not blob:
            return json.dumps({"error": "set needs a key and a JSON value"})
        try:
            value = json.loads(blob)
        except json.JSONDecodeError as exc:
            return json.dumps({"error": f"value is not valid JSON: {exc}"})
        return json.dumps(STORE.set(key, value, actor="command", session=sid))

    if head == "proposals":
        return json.dumps(STORE.proposals())

    if head in ("apply", "discard"):
        if not tail:
            return json.dumps({"error": f"{head} needs a proposal id"})
        pid = tail.split()[0]
        if head == "apply":
            return json.dumps(STORE.apply(pid, actor="command", session=sid))
        return json.dumps(STORE.discard(pid))

    if head == "history":
        # A count that is not a positive number reads as "the default", not as an error: the
        # list is a view, and a console that sent a bad count should still show one.
        count = tail.split()[0] if tail else ""
        return json.dumps(STORE.history(int(count) if count.isdigit() and int(count) else HISTORY_DEFAULT))

    if head == "undo":
        # As the person, not as the model: a security key comes back as a proposal that
        # needs their yes, exactly as `set` does (T-0700).
        return json.dumps(STORE.undo(tail.split()[0] if tail else "", actor="command", session=sid))

    return json.dumps({"error": USAGE})


# --------------------------------------------------------------------------- #
# Tool handlers  (registry calls handler(args, session_id=…, task_id=…, …))
# --------------------------------------------------------------------------- #
#: Words that carry no intent and appear in almost every description, so a
#: phrase containing them would otherwise match the whole catalogue. Found by
#: the obvious way: "what is the weather in oslo" returned all eight settings
#: because "the" is three letters and is in most docs. Kept short and English
#: on purpose — this is a scoring nudge, not a language model, and a word not
#: in this list simply scores on its merits.
_STOPWORDS = frozenset({
    "the", "and", "for", "you", "your", "are", "was", "how", "what", "when",
    "why", "who", "can", "does", "did", "with", "from", "that", "this", "there",
    "then", "than", "get", "set", "make", "have", "has", "its", "his", "her",
    "them", "they", "into", "out", "off", "not", "all", "any", "one", "two",
    "before", "after", "much", "many", "some", "please",
    # "stop announcing backups" matched hard_stop_enabled on "stop" alone.
    "stop", "use", "using", "turn",
})


def _house_rows() -> list[dict]:
    """The house's editable settings (jarvis-core's overlay allowlist), or none when it cannot be asked.

    Not cached: a find is rare, and a stale value is the failure a settings answer must not have.
    """
    if not _house.configured():
        return []
    try:
        with _house.client(timeout=8.0) as c:
            return list(c.get("/api/config/settings/list").raise_for_status().json().get("settings") or [])
    except Exception:  # noqa: BLE001 - the house being down leaves Hermes' own keys findable
        _LOGGER.warning("house settings list unavailable", exc_info=True)
        return []


#: The house's refusal word -> what the model is told. The words are tests/contracts/setting-refusal.json;
#: the sentences are ours, so nothing the house's validator wrote reaches the model.
REFUSAL_HINTS = {
    "range": "it is outside the allowed range or length; read the setting's limits and try a value inside them",
    "type": "it is the wrong kind of value (a number, on/off or plain text was expected)",
    "choice": "it is not one of the allowed choices; read the setting's choices and use one of them",
    "unknown": "the house has no editable setting with that key",
}


def _refusal_reason(response: Any) -> str:
    """The refusal word from the house's error body, or "" unless it is one we know."""
    try:
        detail = response.json().get("detail")
        reason = detail.get("reason") if isinstance(detail, dict) else None
    except Exception:  # noqa: BLE001 - any body that is not the house's shape has no word
        return ""
    return reason if isinstance(reason, str) and reason in REFUSAL_HINTS else ""


def _house_set(key: str, value: Any) -> dict:
    """Write one house key through jarvis-core's overlay API; it validates, applies and audit-logs."""
    try:
        with _house.client(timeout=15.0) as c:
            r = c.post("/api/config/settings/set", json={"key": key, "value": value})
        if r.status_code >= 400:
            # The house's error text can quote the value that was refused and whatever else its
            # validator saw, so none of it is relayed: the status and a fixed hint, like every
            # other service answer (T-1374).
            answer = _failure.status_answer(r.status_code, r.headers.get("retry-after"))
            reason = _refusal_reason(r)
            if reason:
                # The one thing taken from the body: a word, and only one on our own list (T-1388).
                return {"error": "HTTP %d: the value was refused: %s" % (r.status_code, REFUSAL_HINTS[reason]),
                        "reason": reason}
            if r.status_code in (400, 422):
                answer["error"] = ("HTTP %d: the value was refused: check the setting's allowed values"
                                   % r.status_code)
            return {"error": answer["error"]}
        body = _house.body_of(r)
        return body if isinstance(body, dict) else {"result": body}
    except Exception as exc:  # noqa: BLE001
        failed = _failure.of(exc)
        return {"error": failed["error"] if failed
                else f"the house could not be reached: {type(exc).__name__}"}


def _is_security(key: str, hrow: dict | None = None) -> bool:
    """Ask first unless the matrix lists the key plain AND, for a house key, core says so too.

    Core's row carries its own `security` mark; a row without one (an older core) counts as
    security, so the two lists can disagree only toward asking.
    """
    if _matrix.is_security_setting(key):
        return True
    return hrow is not None and hrow.get("security") is not False


def find(phrase: str, limit: int = 8) -> list[dict]:
    """The allow-listed settings a phrase might mean, best first.

    The gap this closes: `settings_get` and `settings_set` both take a key, so
    a person had to already know that "make the study warmer at night" means
    `climate.night_setpoint`. `settings_list` returns everything, which works
    until the catalogue is long enough that the whole thing is a poor use of a
    context window.

    Scored, not fuzzy-matched, and deliberately dumb: a word in the key is
    worth more than a word in the description, because a key is what the person
    half-remembers. No library, no embedding, no second catalogue — this reads
    the same `KEYS` table `settings_set` validates against, so a key that
    cannot be written can never be suggested.

    A word this returns nothing for is a real answer. The caller should say it
    could not find the setting rather than guess at a neighbouring one, which
    is why the empty list is not padded with the best of a bad set.
    """
    words = [w for w in re.split(r"[^a-z0-9]+", phrase.lower())
             if len(w) > 2 and w not in _STOPWORDS]
    if not words:
        return []
    # Scored against the static table, not against `STORE.list()`. Searching the
    # live list meant loading and resolving every setting's current value to
    # match text that is in `KEYS` already — work the search does not need, and
    # a hard dependency on the config being readable. A lookup that cannot
    # answer "which setting means this" because a config file is missing is a
    # lookup that fails when it is most needed.
    def score_of(key: str, doc: str) -> int:
        key, doc = key.lower(), doc.lower()
        return sum((3 if w in key else 0) + (1 if w in doc else 0) for w in words)

    # Hermes' own keys, then the house's (the live list, so a key the house gains is findable
    # at once). One entry each: (score, key, house row or None).
    scored: list[tuple[int, str, dict | None]] = []
    for key, spec in KEYS.items():
        if score := score_of(key, str(spec.get("doc") or "")):
            scored.append((score, key, None))
    for house_row in _house_rows():
        key = str(house_row.get("key") or "")
        if key and key not in KEYS and (
                score := score_of(key, f"{house_row.get('label') or ''} {house_row.get('note') or ''}")):
            scored.append((score, key, house_row))
    # Descending by score, then the contract's own order, which `KEYS` carries —
    # `sort` is stable, so ties keep it.
    scored.sort(key=lambda t: -t[0])

    rows: list[dict] = []
    for _, key, hrow in scored[:limit]:
        if hrow is not None:
            rows.append({"key": key, "scope": "house", "type": hrow.get("type"), "doc": hrow.get("label"),
                         "note": hrow.get("note"), "value": hrow.get("value"),
                         "live": hrow.get("apply") == "live",
                         "restart_needed": hrow.get("apply") != "live",
                         "security": _is_security(key, hrow)})
            continue
        spec = KEYS[key]
        row = {"key": key, "scope": "hermes", "type": spec["type"], "restart_needed": spec["restart_needed"],
               "live": not spec["restart_needed"], "security": _is_security(key),
               "doc": spec["doc"]}
        # The current value is the useful half of the answer but not the
        # findable half: if the config cannot be read, say which key it is and
        # leave the value out rather than returning nothing at all.
        try:
            row["value"] = STORE.get(key).get("value")
        except Exception:  # noqa: BLE001 - any config failure, and none is fatal here
            row["value"] = None
            row["value_unavailable"] = True
        rows.append(row)
    return rows


def handle_settings_find(args: dict, **kwargs: Any) -> str:
    del kwargs
    phrase = str((args or {}).get("phrase") or "").strip()
    if not phrase:
        return json.dumps({"error": "settings_find needs a phrase"})
    rows = find(phrase)
    if not rows:
        return json.dumps({"matches": [], "note": f"no allow-listed setting matches {phrase!r}"})
    return json.dumps({"matches": rows})


def handle_settings_list(args: dict, **kwargs: Any) -> str:
    del args, kwargs
    return json.dumps(STORE.list(contract_only=True))


def handle_settings_get(args: dict, **kwargs: Any) -> str:
    del kwargs
    key = str((args or {}).get("key") or "").strip()
    if not key:
        return json.dumps({"error": "settings_get needs a key"})
    return json.dumps(STORE.get(key))


def handle_settings_set(args: dict, **kwargs: Any) -> str:
    args = args or {}
    key = str(args.get("key") or "").strip()
    if not key:
        return json.dumps({"error": "settings_set needs a key"})
    if "value" not in args:
        return json.dumps({"error": "settings_set needs a value"})
    session = str(kwargs.get("session_id") or "")
    if key not in LEAVES:
        # Not one of Hermes' keys: the house's allowlist decides (it refuses what it does not know).
        return json.dumps(_house_set(key, args.get("value")) if key in {r.get("key") for r in _house_rows()}
                          else _unknown(key))
    return json.dumps(STORE.set(key, args.get("value"), actor="tool", session=session))


def handle_capability_set(args: dict, **kwargs: Any) -> str:
    """Set a capability's permission level (v2 §3). The matrix always asks before this runs
    (the ``capabilities:set`` row); the write lands in the audit log beside every settings write."""
    args = args or {}
    capability = str(args.get("capability") or "").strip().lower()
    level = str(args.get("level") or "").strip().lower()
    try:
        previous = _levels.set_level(capability, level, actor="tool")
    except ValueError as exc:
        return json.dumps({"error": str(exc)})
    _audit.append(_audit.KIND_SETTINGS, actor="tool", session=str(kwargs.get("session_id") or ""),
                  tool="capability_set", args={"capability": capability, "level": level, "previous": previous},
                  outcome="ok", detail=f"{capability}: {previous or 'matrix default'} -> {level}")
    return json.dumps({"capability": capability, "level": level, "previous": previous})


def handle_settings_undo(args: dict, **kwargs: Any) -> str:
    key = str((args or {}).get("key") or "").strip()
    return json.dumps(STORE.undo(key, actor="tool", session=str(kwargs.get("session_id") or "")))


# --------------------------------------------------------------------------- #
# Approval gate for settings_set
# --------------------------------------------------------------------------- #
def approval_for(tool_name: str, args: dict) -> Optional[dict]:
    """The ``pre_tool_call`` verdict: approve ``settings_set`` (naming the key
    and both values), pass every other tool through untouched.

    Reads the current value so the person is shown exactly what will change,
    keyed by ``settings:<key>`` so a remembered approval is per key. A secret's
    values are masked: the prompt is read on a phone. ``settings_undo`` is asked
    the same way, naming the value the newest change replaced.
    """
    change = change_for(tool_name, args)
    # A plain setting just happens (the matrix says free; the write is logged): no card.
    if change is None or not change["security"]:
        return None
    return {
        "action": "approve",
        "message": (
            f"{tool_name} will change Hermes setting {change['key']!r} "
            f"from {change['before']!r} to {change['after']!r}"
        ),
        "rule_key": f"settings:{change['key']}",
    }


def change_for(tool_name: str, args: dict, session: str = "") -> Optional[dict]:
    """What ``settings_set`` or ``settings_undo`` would change: ``key``, ``before`` (the value
    now), ``after`` (the value asked for; an undo's is the one the newest change replaced),
    ``known`` (Hermes or the house has the key), ``security`` and ``secret``. None for another tool.

    This is what both approval texts are written from: this plugin's own and, through
    ``_approval_hook.CHANGE_DESCRIBERS``, the matrix gate's card (T-1317). A secret's two values
    are both :data:`MASK`, set or not: "not set" would say something about a secret too. Not
    claimed: that the write will be accepted (the handler validates), nor that ``before`` is
    still the value when the owner answers. An unknown key's ``before`` is None, not a lookup.
    """
    if tool_name not in ("settings_set", "settings_undo"):
        return None
    args = args or {}
    key = str(args.get("key") or "").strip()
    after = args.get("value")
    if tool_name == "settings_undo":
        rec = STORE.last_change(key) or {}
        key, after = str(rec.get("key") or key), rec.get("before")
    setting = LEAVES.get(key)
    hrow = None
    if setting is None:
        hrow = next((r for r in _house_rows() if r.get("key") == key), None)
    secret = bool(setting is not None and setting.secret)
    try:
        before = (STORE.get(key).get("value") if setting is not None
                  else hrow.get("value") if hrow else None)
    except Exception:  # a read failure must not block the approval prompt
        before = None
    if secret:
        before = after = MASK
    return {"key": key, "before": before, "after": after, "secret": secret,
            "known": setting is not None or hrow is not None, "security": _is_security(key, hrow)}


# --------------------------------------------------------------------------- #
# What is not set up
# --------------------------------------------------------------------------- #
def _requires_env(plugin_yaml: str) -> tuple[str, list[str]]:
    """A plugin.yaml's ``description`` and ``requires_env`` list (the bench's probe reads it the same way)."""
    what, names, inside = "", [], False
    for line in plugin_yaml.splitlines():
        if line.startswith("description:"):
            what = line.split(":", 1)[1].strip().strip("'\"")
        if line.startswith("requires_env:"):
            inside = True
        elif inside and line.lstrip().startswith("- "):
            name = line.split("- ", 1)[1].split("#", 1)[0].strip().strip("'\"")
            if name:
                names.append(name)
        elif inside and line.strip() and not line.startswith((" ", "\t")):
            inside = False
    return what, names


def setup_gaps(plugins_dir: str | None = None, environ: Mapping[str, str] | None = None,
               expiry: bool = False) -> dict[str, Any]:
    """Every installed capability that cannot run, and the exact setting each is missing.

    The owner kept not knowing why mail, the calendar, Jellyfin or Proxmox "did nothing":
    each plugin is simply hidden while its ``requires_env`` is unset, and nothing named
    what to set. Names and where to set them only -- never a value, set or not.
    """
    env = os.environ if environ is None else environ
    root = plugins_dir or os.path.join(_hermes_home(), "plugins")
    by_env = {s.env: s for s in SETTINGS if s.env}
    gaps, ready = [], []
    for name in sorted(os.listdir(root)) if os.path.isdir(root) else []:
        try:
            with open(os.path.join(root, name, "plugin.yaml"), encoding="utf-8") as fh:
                what, needed = _requires_env(fh.read())
        except OSError:
            continue
        if not needed:
            continue
        missing = [n for n in needed if not env.get(n)]
        if not missing:
            ready.append(name)
            continue
        rows = []
        for var in missing:
            s = by_env.get(var)
            row: dict[str, Any] = {"env": var}
            if s is not None:
                row.update(setting=s.key, about=s.description, secret=s.secret)
            row["where"] = ("settings_set " + s.key if s is not None and writable(s) and not s.secret
                            else "the host's environment file (/opt/jarvis-host/.env), then a redeploy; "
                                 "a secret is never set through chat")
            rows.append(row)
        # A plugin's yaml names the variable that gates it; the account and password beside it
        # live in the schema under the same prefix. Listed, so the model states them rather
        # than guessing names (it did: "likely DAV_USERNAME/DAV_PASSWORD", live 2026-10-06).
        prefixes = {var.split("_", 1)[0] + "_" for var in missing}
        also = [{"env": s.env, "setting": s.key, "about": s.description, "secret": s.secret,
                 "set": bool(env.get(s.env))}
                for s in SETTINGS if s.env and s.env.startswith(tuple(prefixes)) and s.env not in missing]
        gaps.append({"plugin": name, "does": what, "missing": rows, "also": also})
    out = {"ok": True, "gaps": gaps, "ready": ready,
           "note": "Each gap hides that plugin's tools until every variable listed is set."}
    if expiry:
        out["expiring"] = expiring(env, root)
    return out


#: Warned this many days ahead (T-0572). The check runs weekly, so a window of a week would
#: first see a credential with 8 days left as fine and next have it a day from lapsing; 21
#: gives at least two weeks' notice. Caddy renews a certificate with 30 days left, so one
#: under 21 is a renewal that has been failing for over a week.
EXPIRY_WARN_DAYS = 21
#: The image's copy of deploy/config.template.yaml: which MCP server reads which variable.
CONFIG_TEMPLATE = "/opt/jarvis/seed/config.template.yaml"


def _token_expiry(base: str, token: str) -> date | None:
    """GitLab's own answer for the token it is shown; None when it never expires. The address
    must pass ``_egress.service_url`` first (the token goes nowhere else), and a redirect is an
    error: httpx does not follow one and ``raise_for_status`` refuses a 3xx."""
    url = _egress.service_url(base, "GITLAB_API_URL")
    resp = httpx.get(f"{url}/personal_access_tokens/self", headers={"PRIVATE-TOKEN": token},
                     timeout=5, follow_redirects=False)
    resp.raise_for_status()
    expires = resp.json().get("expires_at")
    return date.fromisoformat(expires) if expires else None


def _cert_expiry(host: str, port: int) -> date:
    """The certificate a verified handshake is shown. One that no longer verifies (expired,
    wrong name) raises, which is itself the warning."""
    ctx = ssl.create_default_context()
    with socket.create_connection((host, port), timeout=5) as raw, \
            ctx.wrap_socket(raw, server_hostname=host) as tls:
        not_after = str((tls.getpeercert() or {})["notAfter"])
    return datetime.fromtimestamp(ssl.cert_time_to_seconds(not_after), timezone.utc).date()


def _resolves(host: str) -> bool:
    """Whether the name still leads anywhere. Not covered: a host that resolves and is down."""
    try:
        socket.getaddrinfo(host, None)
    except OSError:
        return False
    return True


def _readers(plugins_dir: str, template: str) -> dict[str, set[str]]:
    """Variable -> who reads it: each plugin whose plugin.yaml requires it, and each MCP
    server whose env in the config template names it."""
    who: dict[str, set[str]] = {}
    for name in sorted(os.listdir(plugins_dir)) if os.path.isdir(plugins_dir) else []:
        try:
            with open(os.path.join(plugins_dir, name, "plugin.yaml"), encoding="utf-8") as fh:
                _, needed = _requires_env(fh.read())
        except OSError:
            continue
        for var in needed:
            who.setdefault(var, set()).add(f"plugin {name}")
    try:
        with open(template, encoding="utf-8") as fh:
            servers = (yaml.safe_load(fh) or {}).get("mcp_servers") or {}
    except (OSError, yaml.YAMLError):
        servers = {}
    for name, spec in servers.items():
        for value in ((spec or {}).get("env") or {}).values() if isinstance(spec, dict) else ():
            for var in re.findall(r"\$\{(\w+)\}", str(value)):
                who.setdefault(var, set()).add(f"mcp server {name}")
    return who


def expiring(env: Mapping[str, str], plugins_dir: str, *, template: str = CONFIG_TEMPLATE,
             today: date | None = None,
             read_token: Callable[[str, str], date | None] = _token_expiry,
             read_cert: Callable[[str, int], date | None] = _cert_expiry,
             resolves: Callable[[str], bool] = _resolves) -> dict[str, Any]:
    """The dated credentials Jarvis depends on and when each lapses, with who reads it.

    The GitLab token (GitLab says when, so only while GITLAB_TOKEN and GITLAB_API_URL are
    set) and the TLS certificate of every https host a setting points at, each read once.
    A row within EXPIRY_WARN_DAYS, or one that could not be read, is also a warning line.
    Not covered: secrets with no readable expiry (HASS_TOKEN, LLM_API_KEY, ...) and hosts
    reached over plain http. Never a value: the token goes to GitLab and nowhere else.

    A URL setting whose host no longer resolves is a warning too, http or https (T-0720:
    COMFYUI_URL kept a name from a network that was gone, and image generation just failed).
    Its certificate is then not asked for: one line per dead name, not two.
    """
    today = today or datetime.now(timezone.utc).date()
    who = _readers(plugins_dir, template)
    checks: list[tuple[dict[str, Any], Callable[[], date | None]]] = []
    token, base = env.get("GITLAB_TOKEN"), env.get("GITLAB_API_URL")
    if token and base:
        checks.append(({"what": "GitLab token", "env": "GITLAB_TOKEN",
                        "read_by": sorted(who.get("GITLAB_TOKEN", ()))},
                       lambda: read_token(base, token)))
    hosts: dict[tuple[str, int], tuple[set[str], set[str]]] = {}
    gone: list[dict[str, Any]] = []
    for s in SETTINGS:
        if not s.env or s.kind != Kind.URL:  # a list of origins is not one host
            continue
        url = urlparse(env.get(s.env) or "")
        if url.hostname and not resolves(url.hostname):
            gone.append({"what": f"host of {s.key}", "env": s.env, "settings": [s.key],
                         "read_by": sorted(who.get(s.env, ())), "warn": True, "unresolved": True})
            continue
        if url.scheme == "https" and url.hostname:
            readers, keys = hosts.setdefault((url.hostname, url.port or 443), (set(), set()))
            readers.update(who.get(s.env, set()))
            keys.add(s.key)
    for (host, port), (readers, keys) in sorted(hosts.items()):
        label = host if port == 443 else f"{host}:{port}"
        checks.append(({"what": f"TLS certificate of {label}", "host": host, "port": port,
                        "read_by": sorted(readers), "settings": sorted(keys)},
                       functools.partial(read_cert, host, port)))
    rows, warnings = [], []
    for row, read in checks:
        # A host only Jarvis's core reads (its public URL) is named by its setting instead.
        named = ", ".join(row["read_by"]) or "the setting " + ", ".join(row.get("settings") or [row.get("env", "")])
        try:
            when = read()
        except Exception as exc:  # noqa: BLE001 -- any failure is the line, never the whole answer
            # Never str(exc): it quotes the request address and, for a TLS error, the server's own
            # certificate text. Our refusal's message, the status and hint, or the class name.
            failed = _failure.of(exc)
            row.update(warn=True, unreadable=str(exc)[:200] if isinstance(exc, _egress.ServiceUrlError)
                       else failed["error"] if failed else type(exc).__name__)
            warnings.append(f"{row['what']}: its expiry could not be read ({row['unreadable']}); read by {named}")
            rows.append(row)
            continue
        days = (when - today).days if when else None
        row.update(expires=when.isoformat() if when else None, days_left=days,
                   warn=days is not None and days <= EXPIRY_WARN_DAYS)
        if row["warn"]:
            warnings.append(f"{row['what']} expires {row['expires']}, in {days} days; read by {named}")
        rows.append(row)
    for row in gone:
        named = ", ".join(row["read_by"]) or "the setting " + row["settings"][0]
        warnings.append(f"{row['settings'][0]} ({row['env']}) names a host that no longer resolves; "
                        f"read by {named}. Set it in the host's environment file, then redeploy")
        rows.append(row)
    return {"rows": rows, "warnings": warnings}


def handle_setup_gaps(args: dict, **kwargs: Any) -> str:
    del kwargs
    return json.dumps(setup_gaps(expiry=bool((args or {}).get("expiry"))))
