#!/usr/bin/env python3
"""Try every Hermes-internal import the plugins make, inside a candidate Hermes image.

The v2026.9.24 bump built, loaded every plugin and passed /health, then failed the deploy
smoke: ``_pipe_stdin`` had moved from ``tools.environments.base`` to ``base_output``, and
aura-bwrap imports it lazily, so nothing touched it until a goal ran. A plugin reaching
into Hermes' private modules is the one dependency no pin or lockfile covers. This reads
each plugin file's imports with ``ast`` (lazy ones inside functions included), keeps those
from Hermes' own top-level packages, and runs each in the image. Two imports of the same
names from different modules are alternatives (a try/except fallback): the names fail only
when every alternative does.

Run before bumping HERMES_TAG:
    python3 scripts/verify/hermes-imports.py nousresearch/hermes-agent:<new tag>

Not covered: an import that succeeds while the symbol's behaviour changed; the full smoke
after a deploy still decides that.
"""
from __future__ import annotations

import ast
import json
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLUGINS = ROOT / "hermes" / "plugins"
#: Hermes' own top-level packages (/opt/hermes in the image); anything else is stdlib, a
#: third-party wheel or ours.
HERMES_TOP = {"agent", "cron", "gateway", "hermes_cli", "hermes_state", "model_tools",
              "run_agent", "tools", "toolsets", "tui_gateway", "utils"}


def statements() -> dict[str, list[str]]:
    """Alternatives keyed by what they bind: names -> the import statements providing them."""
    found: dict[str, set[str]] = defaultdict(set)
    for path in PLUGINS.rglob("*.py"):
        if "tests" in path.parts or "__pycache__" in path.parts:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and node.module and node.level == 0 \
                    and node.module.split(".")[0] in HERMES_TOP:
                names = ", ".join(a.name for a in node.names)
                found[names].add(f"from {node.module} import {names}")
            elif isinstance(node, ast.Import):
                for a in node.names:
                    if a.name.split(".")[0] in HERMES_TOP:
                        found[a.name].add(f"import {a.name}")
    return {k: sorted(v) for k, v in sorted(found.items())}


PROBE = """
import json, sys
out = {}
for line in json.load(sys.stdin):
    try:
        exec(line, {})
        out[line] = ""
    except Exception as e:
        out[line] = f"{type(e).__name__}: {e}"
# tui_gateway.server points sys.stdout at stderr when imported (its stdout is the RPC channel).
print(json.dumps(out), file=sys.__stdout__)
"""


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__.split("Run before")[1].split("Not covered")[0].strip())
        return 2
    groups = statements()
    lines = sorted({s for alts in groups.values() for s in alts})
    run = subprocess.run(
        ["docker", "run", "--rm", "-i", "-w", "/opt/hermes", "--entrypoint",
         "/opt/hermes/.venv/bin/python", sys.argv[1], "-c", PROBE],
        input=json.dumps(lines), capture_output=True, text=True, timeout=300)
    if run.returncode:
        print(run.stderr.strip()[-2000:])
        return 2
    result = json.loads(run.stdout.strip().splitlines()[-1])
    broken = {k: alts for k, alts in groups.items() if all(result.get(a) for a in alts)}
    for names, alts in broken.items():
        for a in alts:
            print(f"BROKEN  {a}\n        {result[a]}")
    print(f"hermes-imports: {len(groups) - len(broken)}/{len(groups)} import groups resolve in {sys.argv[1]}")
    return 1 if broken else 0


if __name__ == "__main__":
    sys.exit(main())
