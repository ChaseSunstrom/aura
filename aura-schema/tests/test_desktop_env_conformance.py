"""The desktop trees and the schema agree on what every ``AURA_*`` name means.

The desktop agent and the desktop app are the one part of JARVIS that does NOT
import :mod:`aura_schema` — see V2_PLAN §10 D31 for why, in short: the agent
ships with one hard dependency by design and pydantic is 2.5 MB with a compiled
wheel, and most of what it reads is per-machine anyway. That decision is only
safe if the boundary is written down and checked, which is what this file does.

The rule, and the failure each half prevents:

* Every ``AURA_*`` name these trees read is either **shared** with the schema or
  **device-local**, and the lists below say which. A name that is in neither fails,
  so a new setting cannot appear in the desktop without somebody deciding where it
  belongs.
* A **device-local** name must NOT be declared in the schema. That is the collision
  the split exists to stop: ``AURA_PUSH_TO_TALK`` was a bool in the schema and an
  accelerator in the shell, so writing ``false`` to switch push-to-talk off
  registered a global hotkey named "false" (fixed by ``desktop.push_to_talk_key``).
* A **shared** name must still be declared in the schema, so renaming a setting
  there cannot silently strand the desktop on the old variable.
* Neither list may carry a name the trees no longer read, so they cannot rot into
  a record of what used to be true.

The scan looks for the ``AURA_*`` literal anywhere in the source rather than for
a particular call shape. Both config modules take an injected ``env`` mapping and
read it as ``env.get("AURA_X")``, ``env.AURA_X`` or ``pick(key, "AURA_X")``,
so a regex written around ``os.environ`` — the one the plugin conformance test
uses — matches none of them and would pass these trees while seeing nothing.
"""
from __future__ import annotations

import re
from pathlib import Path

from aura_schema import settings as S

ROOT = Path(__file__).resolve().parents[2]
AGENT = ROOT / "aura-desktop" / "aura_desktop"
#: The desktop app (Tauri, ADR-0051): its Rust side and its own pages.
SHELL = ROOT / "jarvis-desktop-app" / "src-tauri" / "src"
SHELL_PAGES = ROOT / "jarvis-desktop-app" / "ui"

#: Names the schema declares AND the desktop reads. Each one is a deliberate
#: overlap, not an accident, and each is here because the same value means the
#: same thing on both sides.
SHARED_WITH_SCHEMA: dict[str, str] = {
    "AURA_TOKEN": "house.console_token — a jarvis-core token a client presents; the agent "
                    "presents one to the same websocket the console does.",
    "AURA_AUTO_UPDATE": "system.auto_update — the shell's updater switch, and the schema's "
                          "default was corrected to True to match what both clients ship.",
    "AURA_PUSH_TO_TALK_KEY": "desktop.push_to_talk_key — the shell's accelerator, declared in "
                               "the schema so the settings reference shows it.",
    "AURA_PALETTE_KEY": "desktop.palette_key — the shell's second accelerator (the island with "
                          "its typing line), declared in the schema so the settings reference "
                          "shows it.",
    "AURA_DICTATION_KEY": "desktop.dictation_key — the shell's hold-to-dictate accelerator "
                            "(T-1258), off until set, declared in the schema so the settings "
                            "reference shows it.",
    "AURA_ADAPTER_URL": "adapter.url — the adapter the glass console (`aura_desktop console`) "
                          "connects to; the same address the web console is configured with.",
    "AURA_ADAPTER_KEY": "adapter.key — the bearer key that console presents to the adapter; "
                          "the same value as the web console's, read from the environment "
                          "or --key-file, never from a server-sent config.",
}

#: Names only the desktop knows. Every one is a property of the machine the agent
#: runs on or of the person's own install, so there is nothing for a
#: server-generated config to say about it — and for the capability switches there
#: is a stronger reason: ``aura_desktop.config``'s contract is that nothing in it
#: can be set by the server, so a ``device_command`` cannot add a file root, widen
#: the shell denylist or turn on ``shell=True``.
DEVICE_LOCAL: dict[str, str] = {
    # identity and storage — different on every machine
    "AURA_DEVICE_ID": "this installation's id, minted per machine",
    "AURA_DEVICE_NAME": "what this machine calls itself",
    "AURA_STATE_DIR": "where this install keeps policy, audit and identity",
    "AURA_FILE_ROOTS": "the directories on THIS machine file actions are confined to",
    # connection — the agent is a client of a server, not a service of one
    "AURA_SERVER": "which server this device connects to",
    "AURA_PINNED_HOST": "host pinning, so a rewritten config cannot move the agent",
    "AURA_TOKEN_FILE": "a path to read the token from instead of the environment",
    "AURA_CONSOLE_URL": "which console the shell window loads",
    "AURA_SHELL_ANNOUNCE": "set by the desktop app when it starts this agent, which then prints "
                             "the socket's port and token file on stdout (ADR-0051); nothing "
                             "else sets it",
    # capability switches — the security model, local by contract
    "AURA_SHELL_ENABLED": "whether run_command exists on this machine at all",
    "AURA_SHELL_USE_SHELL": "whether commands go through a shell; the server must never set it",
    "AURA_INPUT_ENABLED": "synthetic keyboard/mouse, off unless this machine's owner says so",
    "AURA_HEADLESS_DENY": "what to do about a consent prompt with no display to draw it on",
    # this install's own updates and diagnostics
    "AURA_AUTO_UPDATE_CHECK": "the Python agent's updater switch (its own PyPI index, not the "
                                "shell's GitLab feed, so a separate switch)",
    # not read at all — written
    "AURA_AGENT": "SET, not read: marks a spawned child as ours in `ps` output",
}

#: Platform variables, the desktop counterpart of the plugins' HERMES_PLUMBING.
#: Where the OS says to put config and state, and whether there is a graphical
#: session to draw a consent dialog into. Not JARVIS settings in any sense.
PLATFORM = {
    "DISPLAY", "WAYLAND_DISPLAY", "XDG_SESSION_ID",
    "XDG_STATE_HOME", "XDG_CONFIG_HOME", "APPDATA", "LOCALAPPDATA",
}

_AURA_NAME = re.compile(r"\bAURA_[A-Z0-9_]+\b")
_ANY_ENV_READ = re.compile(
    r"""(?:os\.environ\.get|os\.getenv|env\.get)\(\s*["']([A-Z_][A-Z0-9_]*)["']"""
    r"""|(?:os\.environ|env)\[\s*["']([A-Z_][A-Z0-9_]*)["']\s*\]"""
    r"""|\benv\.([A-Z_][A-Z0-9_]*)"""
)


def _sources() -> list[Path]:
    out: list[Path] = []
    for root, suffix in ((AGENT, "*.py"), (SHELL, "*.rs"), (SHELL_PAGES, "*.ts")):
        out += [p for p in root.rglob(suffix) if "__pycache__" not in p.parts]
    return out


def _names_read() -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for path in _sources():
        for name in _AURA_NAME.findall(path.read_text(encoding="utf-8")):
            found.setdefault(name, set()).add(path.name)
    return found


def test_every_jarvis_name_is_shared_or_device_local():
    """A new setting in the desktop has to be filed on one side of the boundary."""
    unfiled = {n: sorted(f) for n, f in _names_read().items()
               if n not in SHARED_WITH_SCHEMA and n not in DEVICE_LOCAL}
    assert not unfiled, (
        "desktop reads AURA_* names that are neither declared shared with the schema nor "
        f"listed as device-local: {unfiled}"
    )


def test_a_device_local_name_is_not_also_a_schema_setting():
    """The push-to-talk defect, as a test.

    Two independent declarations of one name drift, and the drift is silent: the
    schema's reference tells a person to write a bool and the desktop reads a
    string. If a device-local name ever gains a schema declaration, one of the two
    is wrong and somebody has to say which.
    """
    declared = {s.env for s in S.SETTINGS if s.env}
    collisions = sorted(set(DEVICE_LOCAL) & declared)
    assert not collisions, (
        "these are declared in the schema AND listed as device-local — decide which meaning "
        f"wins and move the name: {collisions}"
    )


def test_every_shared_name_is_still_declared_in_the_schema():
    """A rename in the schema must not strand the desktop on the old variable."""
    declared = {s.env for s in S.SETTINGS if s.env}
    missing = sorted(set(SHARED_WITH_SCHEMA) - declared)
    assert not missing, f"listed as shared with the schema but the schema does not declare: {missing}"


def test_neither_list_carries_a_name_the_desktop_stopped_reading():
    """So the boundary stays a description of the code rather than of its history."""
    read = set(_names_read())
    stale_shared = sorted(set(SHARED_WITH_SCHEMA) - read)
    stale_local = sorted(set(DEVICE_LOCAL) - read)
    assert not stale_shared, f"listed as shared but no longer in the desktop source: {stale_shared}"
    assert not stale_local, f"listed as device-local but no longer in the desktop source: {stale_local}"


def test_only_platform_variables_are_read_outside_the_jarvis_namespace():
    """Everything else the desktop reads from the environment is the OS's, and the
    set is small enough to name. A new one is a decision, not a detail."""
    stray: dict[str, list[str]] = {}
    for path in _sources():
        for match in _ANY_ENV_READ.findall(path.read_text(encoding="utf-8")):
            name = next((x for x in match if x), "")
            if not name or name.startswith("AURA_") or name in PLATFORM:
                continue
            stray.setdefault(name, []).append(path.name)
    assert not stray, f"desktop reads environment variables that are neither JARVIS' nor the platform's: {stray}"


def test_the_boundary_says_why_for_every_name():
    """A list of names with no reasons is not a boundary, it is an allowlist."""
    empty = sorted(n for n, why in {**SHARED_WITH_SCHEMA, **DEVICE_LOCAL}.items() if len(why.strip()) < 20)
    assert not empty, f"these entries have no real reason written against them: {empty}"
