"""The deploy's own probe turns say they are probes (T-1201, tests/contracts/rig-session.json).

scripts/smoke.sh runs inside a container and golden-turn.mjs on the host: neither can read the
contract at run time, so each names the mark as `PROBE_SURFACE = "<value>"` and this test reads
the script text against the contract's `probe_surface`.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROBE = json.loads((ROOT / "tests" / "contracts" / "rig-session.json").read_text())["probe_surface"]
SMOKE = (ROOT / "scripts" / "smoke.sh").read_text()
GOLDEN = (ROOT / "scripts" / "verify" / "golden-turn.mjs").read_text()


def _mark(text: str) -> str:
    found = re.findall(r"""PROBE_SURFACE\s*=\s*["']([^"']+)["']""", text)
    assert len(found) == 1, found
    return found[0]


def test_probe_smoke_sh_names_the_contracts_mark():
    assert _mark(SMOKE) == PROBE


def test_probe_smoke_sh_marks_every_prompt_it_submits():
    calls = re.findall(r'call\("prompt\.submit", \{[^\n]*\}\)', SMOKE)
    assert len(calls) == SMOKE.count('"prompt.submit"') >= 3
    for c in calls:
        assert '"surface": {"name": PROBE_SURFACE}' in c, c


def test_probe_golden_turn_names_the_contracts_mark_and_rewrites_the_frame():
    assert _mark(GOLDEN) == PROBE
    # the console gains no test switch: the script rewrites its outgoing frame
    assert "prompt.submit" in GOLDEN and "routeWebSocket" in GOLDEN
    assert "params.surface = { name: PROBE_SURFACE }" in GOLDEN
