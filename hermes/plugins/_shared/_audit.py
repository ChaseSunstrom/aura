"""One structured log of everything JARVIS did to the world.

Before this there were three: ``logs/integrations.jsonl`` (what was installed),
``logs/settings.jsonl`` (what was reconfigured) and ``logs/activity.jsonl`` (a
shell hook, appended by ``agent-hooks/activity-log.sh`` for a matcher-listed
subset of tools). Between them they covered a slice of what a capable agent
does, in three formats, and answering "what did it do at 14:02 and did anyone
approve it" meant reading all three and joining them by eye.

This is the one log. Its shape is fixed by the question it has to answer, which
is always the same five-part question: **who** asked, **in which session**,
**what** was called with **which arguments**, and **what happened** — plus the
governance answer, the verdict the approval matrix returned and, when a human
was asked, what they said. Every field is present on every record, so the log
can be filtered without special-casing record kinds.

Three properties it has to keep:

* **Append-only, one JSON object per line.** A reader can ``tail -f`` it, a
  test can parse it line by line, and a crash mid-write costs the last line
  rather than the file. The matrix denies the shell shapes that would truncate
  it (``terminal:sealed``), which is what makes "append-only" a property rather
  than a naming convention.
* **Arguments are redacted before they are written.** The log is a file on a
  volume that gets backed up; a token written into it is a token in the backups
  for ever. Redaction goes through Hermes' own engine
  (``agent.redact.redact_sensitive_text``) so the log and the model's own tool
  output are masked by one set of patterns, with a local fallback for the tests
  and any context where Hermes is not importable.
* **A logging failure never fails the action.** The write is already done by
  the time the outcome is recorded; refusing to proceed because the log is full
  would turn a disk problem into an outage. Failures are logged to the ordinary
  logger and swallowed — which is why the rotation below is size-based and
  eager rather than something a cron job has to remember.

Deliberately not claimed: this is not tamper-proof. A process that can write
the volume can rewrite history. What it is, is complete and structured enough
that a rewrite is visible as a gap.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_LOGGER = logging.getLogger(__name__)

#: Relative to ``HERMES_HOME``, beside the logs it replaces.
AUDIT_REL: str = "logs/audit.jsonl"

#: Stamped on every record. A reader that meets a record without it is reading
#: one of the three old logs and should say so rather than mis-parse it.
SCHEMA: str = "jarvis.audit.v1"

#: Rotate at 32 MiB, keeping five generations. Sized so a busy month fits
#: without rotating and a runaway loop cannot fill the volume: the failure this
#: prevents is the backup job failing at 03:00 because a tool call retried
#: fifty thousand times overnight.
MAX_BYTES: int = 32 * 1024 * 1024
KEEP: int = 5

#: The record kinds. Each is a thing that changed the world or decided whether
#: something would: a tool call, the approval decision in front of it, a
#: settings write, an outbound message, a deploy, and the kill switch itself.
KIND_TOOL_CALL: str = "tool_call"
KIND_APPROVAL: str = "approval"
KIND_SETTINGS: str = "settings_write"
KIND_OUTBOUND: str = "outbound"
KIND_DEPLOY: str = "deploy"
KIND_KILL_SWITCH: str = "kill_switch"

KINDS: frozenset[str] = frozenset({
    KIND_TOOL_CALL, KIND_APPROVAL, KIND_SETTINGS,
    KIND_OUTBOUND, KIND_DEPLOY, KIND_KILL_SWITCH,
})

#: Every record carries every key, so a query never has to ask whether a field
#: exists for this kind. The defaults are what "not applicable" looks like.
_TEMPLATE: dict[str, Any] = {
    "schema": SCHEMA,
    "ts": "",
    "kind": "",
    "actor": "",
    "session": "",
    "task": "",
    "tool": "",
    "args": {},
    "verdict": "",
    "reason": "",
    "rule_key": "",
    "decision": "",
    "outcome": "",
    "detail": "",
    "duration_ms": 0,
}

_LOCK = threading.Lock()

#: Argument keys whose value is a credential by name, whatever it looks like.
#: The regex engine catches shapes; this catches the opaque token that has no
#: shape at all.
_SECRET_KEYS: frozenset[str] = frozenset({
    "password", "passwd", "token", "secret", "api_key", "apikey", "key",
    "authorization", "auth", "credential", "credentials", "private_key",
    "access_token", "refresh_token", "session",
})

MASK: str = "[redacted]"


def _hermes_home() -> Path:
    """Resolved per call — the tests point ``HERMES_HOME`` at a tmp dir."""
    return Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes")))


def audit_path() -> Path:
    return _hermes_home() / AUDIT_REL


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def redact(value: Any, *, depth: int = 0) -> Any:
    """Mask credentials in an argument tree, by key name and by value shape.

    By key first, because a token that matches no vendor prefix is still a
    token when it arrives under ``"api_key"``. By shape second, through
    Hermes' engine when it is importable — the same patterns that mask the
    model's own tool output, so a secret cannot be masked in one place and
    written in clear in the other.

    ``depth`` bounds the walk: an argument tree is user-supplied and a cyclic
    or pathologically deep one must cost a truncated log line, not a stack
    overflow inside a hook that Hermes fails closed on.
    """
    if depth > 6:
        return "[truncated]"
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if str(key).strip().lower() in _SECRET_KEYS:
                out[str(key)] = MASK
            else:
                out[str(key)] = redact(item, depth=depth + 1)
        return out
    if isinstance(value, (list, tuple)):
        return [redact(item, depth=depth + 1) for item in value[:64]]
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return redact_text(str(value))


def redact_text(text: str) -> str:
    """Hermes' redaction engine, with a local fallback.

    ``force=True`` on purpose: ``security.redact_secrets: false`` is an opt-out
    for a developer reading tool output on their own screen, and it must not
    also decide what is written to a file that gets backed up.
    """
    try:
        from agent.redact import redact_sensitive_text

        return redact_sensitive_text(text, force=True)
    except Exception:  # noqa: BLE001 — Hermes absent (tests, tooling) or changed
        return _fallback_redact(text)


#: The credential shapes **this** deployment issues that Hermes v0.21.0 does not
#: recognise. Both were found by running its own redactor against them:
#: ``redact_sensitive_text("tskey-auth-k…", force=True)`` returns the key
#: unchanged, and so does a ``PVEAPIToken=`` string. Hermes' vendor-prefix list
#: (``agent/redact.py``, ``_PREFIX_PATTERNS``) knows the cloud LLM providers and
#: the forges; it has no reason to know about a tailnet or a Proxmox cluster,
#: which are precisely the two credentials that would open this house.
#:
#: They are registered with Hermes' own engine at plugin load
#: (:func:`register_with_hermes`) rather than only being applied here, so they
#: mask everywhere the built-in patterns do — terminal output, ``read_file``
#: results, log lines, approval prompts — and not merely in this log.
#:
#: Hermes validates each one (``register_redaction_patterns``): at least two
#: literal leading characters, no top-level alternation, no nested unbounded
#: quantifier. Keep new entries inside those rules or they are silently skipped.
DEPLOYMENT_SECRET_PATTERNS: tuple[str, ...] = (
    r"tskey-[A-Za-z0-9-]{16,}",                  # Tailscale auth / OAuth key
    # The `=` is inside the class on purpose: the real shape is
    # `PVEAPIToken=user@realm!tokenid=<uuid>`, and stopping at the second `=
    # would leave the secret half of it in the clear.
    r"PVEAPIToken=[A-Za-z0-9@!_.:=-]{16,}",      # Proxmox API token, id and secret
)

#: Used only when Hermes is not importable — the plugin suite, the docs
#: generator, a developer's shell. Deliberately small: a second full redaction
#: engine would be a second thing to keep in step, and ``test_guard.py`` pins
#: that the real engine is preferred whenever it is present.
_FALLBACK_PATTERNS: tuple[str, ...] = DEPLOYMENT_SECRET_PATTERNS + (
    r"glpat-[A-Za-z0-9_-]{16,}",          # GitLab personal access token
    r"glrt-[A-Za-z0-9_-]{16,}",           # GitLab runner token
    r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}",  # a JWT
)


#: ``key: value`` pairs: the key stays and the value goes, as Hermes' own engine
#: does it. Masking the whole pair took the field's name with it -- the activity
#: trail read "type into [redacted]" under this fallback and "type into Password:
#: [redacted]" under Hermes, so what a line said depended on which engine
#: happened to be importable (pipeline 367).
_FALLBACK_KEYED = r"(?i)(\b(?:bearer|token|api[_-]?key|password)\s*[:=]?\s*)\S{8,}"


def _fallback_redact(text: str) -> str:
    import re

    for pattern in _FALLBACK_PATTERNS:
        text = re.sub(pattern, MASK, text)
    return re.sub(_FALLBACK_KEYED, lambda m: m.group(1) + MASK, text)


def register_with_hermes() -> int:
    """Teach Hermes' redactor this deployment's own credential shapes.

    Returns the number of patterns Hermes accepted, or 0 when Hermes is not
    importable. Never raises: a redaction pattern that fails to register must
    degrade the masking, not stop the gateway from starting.
    """
    try:
        from agent.redact import register_redaction_patterns

        return int(register_redaction_patterns(
            DEPLOYMENT_SECRET_PATTERNS, source="plugin:jarvis-guard"))
    except Exception:  # noqa: BLE001 — Hermes absent or the API moved
        _LOGGER.info("Hermes redaction registry unavailable; "
                     "deployment secret patterns apply to the audit log only")
        return 0


def _private(tool: str, args: Any, raw: Any) -> Any:
    """A text's words are not kept: the recipient stays, the body becomes a hash and a length.

    The recipient is put back unmasked: Hermes' redactor masks a phone number's middle, and a masked
    one could never match the "has this person been written to before" lookup (_first_time).
    """
    if tool != "texts_reply" or not isinstance(args, dict) or not isinstance(raw, dict):
        return args
    out = dict(args)
    for key in ("conversation", "to"):
        if key in raw:
            out[key] = raw[key]
    if isinstance(raw.get("text"), str):
        body = raw["text"]
        out["text"] = f"[private sha={hashlib.sha256(body.encode('utf-8')).hexdigest()[:12]} len={len(body)}]"
    return out


def append(
    kind: str,
    *,
    actor: str = "",
    session: str = "",
    task: str = "",
    tool: str = "",
    args: Any = None,
    verdict: str = "",
    reason: str = "",
    rule_key: str = "",
    decision: str = "",
    outcome: str = "",
    detail: str = "",
    duration_ms: int = 0,
) -> dict[str, Any]:
    """Append one record and return it (the tests assert on the return value).

    Returns the record even when the write failed, so a caller that wants to
    surface "I did this" in the HUD does not have to re-read the file.
    """
    if kind not in KINDS:
        _LOGGER.error("audit: unknown record kind %r — recording it anyway", kind)
    record = dict(_TEMPLATE)
    record.update({
        "ts": _now(),
        "kind": kind,
        "actor": actor or "jarvis",
        "session": session,
        "task": task,
        "tool": tool,
        "args": _private(tool, redact(args if args is not None else {}), args),
        "verdict": verdict,
        "reason": redact_text(reason) if reason else "",
        "rule_key": rule_key,
        "decision": decision,
        "outcome": outcome,
        "detail": redact_text(detail) if detail else "",
        "duration_ms": int(duration_ms or 0),
    })
    line = json.dumps(record, ensure_ascii=False, default=str)
    path = audit_path()
    try:
        with _LOCK:
            path.parent.mkdir(parents=True, exist_ok=True)
            _rotate(path)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
    except OSError:
        # A full disk must cost the record, not the action it describes.
        _LOGGER.exception("audit append failed: kind=%s tool=%s", kind, tool)
    return record


def _rotate(path: Path) -> None:
    """Size-based rotation, checked on the write rather than on a timer.

    A timer would need something to run it; a gateway that is asleep at
    midnight would never rotate. Checked here, the log is bounded by the thing
    that grows it.
    """
    try:
        if path.stat().st_size < MAX_BYTES:
            return
    except FileNotFoundError:
        return
    for index in range(KEEP - 1, 0, -1):
        older = path.with_suffix(path.suffix + f".{index}")
        newer = path.with_suffix(path.suffix + f".{index + 1}")
        if older.exists():
            os.replace(older, newer)
    os.replace(path, path.with_suffix(path.suffix + ".1"))


def readable() -> bool:
    """Is the live log whole? A missing file is (nothing happened yet); an unreadable one, or one
    with a line that is not a JSON object, is not. `read` skips such lines; a caller that needs
    to know nothing was skipped (who has been written to) asks this first."""
    try:
        lines = audit_path().read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return True
    except (OSError, ValueError):
        return False
    for line in lines:
        if line.strip():
            try:
                if not isinstance(json.loads(line), dict):
                    return False
            except ValueError:
                return False
    return True


def read(limit: int = 100, *, kind: str = "", tool: str = "",
         session: str = "", since: str = "", until: str = "") -> list[dict[str, Any]]:
    """The most recent records, newest first, for the HUD and the tests.

    Filters are exact matches and are applied before the limit, so asking for
    ten approvals returns ten approvals rather than the approvals among the
    last ten records. ``since``/``until`` are ``ts`` strings in the log's own
    UTC format, a half-open range compared as text. Unparseable lines are
    skipped rather than raised on: a torn last line must not make the whole
    log unreadable. Only the live file is read, not the rotated ``.1``…: a
    range older than the last rotation comes back short.
    """
    path = audit_path()
    try:
        raw = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out: list[dict[str, Any]] = []
    for line in reversed(raw):
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if not isinstance(record, dict):
            continue
        if kind and record.get("kind") != kind:
            continue
        if tool and record.get("tool") != tool:
            continue
        if session and record.get("session") != session:
            continue
        if since or until:
            ts = record.get("ts")
            if (not isinstance(ts, str) or not ts  # a line with no time is in no range
                    or (since and ts < since) or (until and ts >= until)):
                continue
        out.append(record)
        if len(out) >= max(1, limit):
            break
    return out
