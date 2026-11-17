#!/usr/bin/env python3
"""Diff the installed Hermes's surface against the decisions in the v3 plan §8.

The brief's Part 4 asks that nothing Hermes offers is "left at a default nobody
chose". A table in a document cannot enforce that: a Hermes upgrade adds a
toolset and the document is silently out of date. So the table is data
(``tests/contracts/hermes-coverage.json``) and this reads the *installed*
version — `hermes tools list` and the config's own platform table — and fails on

  * a toolset, plugin toolset, MCP server or platform the contract does not
    mention, which is the default nobody chose; and
  * a decision the contract records that the installed version contradicts,
    which is a claim that has stopped being true.

A ``PENDING:`` reason is a decision made and not yet carried out. It is reported
and does not fail, because recording the intention honestly is better than
either claiming the toolset is on or pretending no decision was made — but it is
printed every run so it cannot be forgotten.

Usage: python3 scripts/verify/hermes-coverage.py [--json]
Exit 0 when the installed surface matches the table, 1 otherwise, 2 when Hermes
is not installed (a machine without it cannot answer the question).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONTRACT = ROOT / "tests" / "contracts" / "hermes-coverage.json"

#: `  ✓ enabled  web  🔍 Web Search & Scraping` / `  ✗ disabled  video  …`
_TOOL_LINE = re.compile(r"^\s*(✓ enabled|✗ disabled)\s+(\S+)\s")


#: How to reach the Hermes the table is *about*.
#:
#: The table records what JARVIS can reach, and JARVIS runs in the `jarvis`
#: container — not in this checkout's shell. Reading the dev box's own install
#: answered a different question and answered it wrongly: it reported
#: `hud_layout` missing and the table carried that as a PENDING, while the
#: deployed container had the plugin enabled the whole time.
#:
#: The container first, then the local install, so this still works on a machine
#: with no stack running (CI, where it exits 2). `AURA_COVERAGE_LOCAL=1` forces
#: the local one when someone genuinely wants to ask about this box.
_CONTAINER = os.environ.get("AURA_COVERAGE_CONTAINER", "jarvis")


def _container_runs_hermes() -> bool:
    if os.environ.get("AURA_COVERAGE_LOCAL"):
        return False
    try:
        done = subprocess.run(["docker", "exec", _CONTAINER, "hermes", "--version"],
                              capture_output=True, text=True, timeout=60, check=False)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    return done.returncode == 0


_HERMES_IN_CONTAINER = _container_runs_hermes()


def _hermes(*args: str) -> str:
    if _HERMES_IN_CONTAINER:
        return subprocess.run(["docker", "exec", _CONTAINER, "hermes", *args],
                              capture_output=True, text=True, timeout=120).stdout
    return subprocess.run(["hermes", *args], capture_output=True, text=True, timeout=120).stdout


def installed_toolsets() -> tuple[dict[str, bool], dict[str, bool], list[str]]:
    """(built-in, plugin, mcp server names) as `hermes tools list` reports them."""
    builtin: dict[str, bool] = {}
    plugin: dict[str, bool] = {}
    mcp: list[str] = []
    section = ""
    for line in _hermes("tools", "list").splitlines():
        low = line.strip().lower()
        if low.startswith("built-in toolsets"):
            section = "builtin"
            continue
        if low.startswith("plugin toolsets"):
            section = "plugin"
            continue
        if low.startswith("mcp servers"):
            section = "mcp"
            continue
        if section == "mcp":
            name = line.strip().split()[0] if line.strip() else ""
            if name and not name.startswith("("):
                mcp.append(name)
            continue
        hit = _TOOL_LINE.match(line)
        if not hit:
            continue
        (builtin if section == "builtin" else plugin)[hit.group(2)] = hit.group(1).startswith("✓")
    return builtin, plugin, mcp


#: `  Telegram      ✓ configured (home: …)` / `  Discord       ✗ not configured`
_PLATFORM_LINE = re.compile(r"^\s{2}(\w[\w ]*?)\s+(✓ configured|✗ not configured)")


def configured_platforms() -> set[str]:
    """The messaging platforms `hermes status` reports as configured.

    `platform_toolsets` is not this list and reading it as though it were is how
    the table came to look wrong: that key names the platforms with a *custom
    toolset override*, so a platform enabled with the default toolset is simply
    absent from it. On the deployed container it names `cli` and `api_server`
    only, and the checker duly reported Telegram and Discord as claims that had
    stopped being true — while `hermes status` showed both configured, one of
    them twice (built-in and plugin).

    A platform is counted when *any* line for it says configured, because the
    built-in and the plugin are two ways to reach the same inbox.
    """
    out: set[str] = set()
    inside = False
    for line in _hermes("status").splitlines():
        if "Messaging Platforms" in line:
            inside = True
            continue
        if inside and line.strip() and not line.startswith("  "):
            break
        hit = _PLATFORM_LINE.match(line)
        if inside and hit and hit.group(2).startswith("✓"):
            out.add(hit.group(1).strip().lower().replace(" ", "_"))
    return out


def installed_platforms() -> list[str]:
    """Every platform this deployment actually offers.

    The union of two things, because neither alone is the answer: the
    `platform_toolsets` keys (which is where `cli` and `api_server` live, and
    they are not messaging platforms at all) and the messaging platforms
    `hermes status` reports as configured.
    """
    # The same Hermes `_hermes` talks to, or the platform list describes a
    # different machine than the toolset list does.
    if _HERMES_IN_CONTAINER:
        text = subprocess.run(["docker", "exec", _CONTAINER, "sh", "-c",
                               "cat \"${HERMES_HOME:-/opt/data}\"/config.yaml"],
                              capture_output=True, text=True, timeout=60, check=False).stdout
    else:
        text = (Path.home() / ".hermes" / "config.yaml").read_text(encoding="utf-8")
    out: list[str] = []
    inside = False
    for line in text.splitlines():
        if line.startswith("platform_toolsets:"):
            inside = True
            continue
        if inside:
            if line and not line.startswith(" "):
                break
            hit = re.match(r"^  (\w+):\s*$", line)
            if hit:
                out.append(hit.group(1))
    for name in sorted(configured_platforms()):
        if name not in out:
            out.append(name)
    return out


def check() -> tuple[list[str], list[str]]:
    """(failures, pending)."""
    table = json.loads(CONTRACT.read_text(encoding="utf-8"))
    builtin, plugin, mcp = installed_toolsets()
    platforms = installed_platforms()
    bad: list[str] = []
    pending: list[str] = []

    def compare(kind: str, decided: dict, actual: dict[str, bool] | list[str]) -> None:
        names = actual.keys() if isinstance(actual, dict) else actual
        for name in names:
            row = decided.get(name)
            if row is None:
                bad.append(f"{kind} {name!r} is not in the coverage table — a default nobody chose")
                continue
            if row["why"].startswith("PENDING:"):
                pending.append(f"{kind} {name}: {row['why']}")
                continue
            if isinstance(actual, dict) and (row["state"] == "on") != actual[name]:
                bad.append(
                    f"{kind} {name!r}: the table says {row['state']} and the installed version "
                    f"says {'on' if actual[name] else 'off'}"
                )
        for name in decided:
            if name in names:
                continue
            row = decided[name]
            if row["why"].startswith("PENDING:"):
                pending.append(f"{kind} {name}: {row['why']}")
            elif row["state"] == "on":
                # A row the table says is ON and the installed version does not
                # offer is a claim that is not true. An OFF row that is absent is
                # simply absent, which is what off means.
                bad.append(f"{kind} {name!r}: the table says on and the installed version does not offer it")

    compare("built-in toolset", table["builtin_toolsets"], builtin)
    compare("plugin toolset", table["plugin_toolsets"], plugin)
    compare("MCP server", table["mcp_servers"], mcp)
    compare("gateway platform", table["gateway_platforms"], platforms)
    return bad, pending


def main(argv: list[str]) -> int:
    if not _HERMES_IN_CONTAINER and shutil.which("hermes") is None:
        print("hermes-coverage: hermes is not installed; nothing to check", file=sys.stderr)
        return 2
    bad, pending = check()
    if "--json" in argv:
        print(json.dumps({"failures": bad, "pending": pending}, indent=2))
    else:
        for line in pending:
            print(f"hermes-coverage: PENDING  {line}")
        for line in bad:
            print(f"hermes-coverage: FAIL     {line}")
        if not bad:
            print(f"hermes-coverage: ok — every toolset, server and platform is decided "
                  f"({len(pending)} pending)")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
