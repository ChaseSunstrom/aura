"""Which persona is primary, as `GET /api/status` reports it, against the shared contract.

The adapter is its own image and re-implements the read rather than importing the
plugin; `tests/contracts/persona.json` is what stops the two drifting.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from aura_adapter.status import read_persona

CONTRACT = json.loads(
    (Path(__file__).resolve().parents[2] / "tests" / "contracts" / "persona.json").read_text(encoding="utf-8")
)


def _write(tmp_path: Path, body: str | None) -> str:
    target = tmp_path / "persona.json"
    if body is not None:
        target.write_text(body, encoding="utf-8")
    return str(target)


@pytest.mark.parametrize("case", CONTRACT["cases"], ids=[c["name"] for c in CONTRACT["cases"]])
def test_the_adapter_answers_the_contract(case, tmp_path):
    assert read_persona(_write(tmp_path, case["file"]))["active"] == case["active"]


def test_the_record_is_carried_whole(tmp_path):
    body = '{"schema": "jarvis.persona.v1", "active": "jarvis", "since": "2026-09-09T21:03:00Z", "previous": "aura"}'
    assert read_persona(_write(tmp_path, body)) == {
        "active": "jarvis", "theme": "jarvis", "since": "2026-09-09T21:03:00Z", "previous": "aura"}


def test_every_persona_wears_the_theme_the_contract_names():
    from aura_adapter.status import PERSONAS, THEMES, DEFAULT_PERSONA
    assert list(PERSONAS) == CONTRACT["personas"] and DEFAULT_PERSONA == CONTRACT["default"]
    assert THEMES == {k: v for k, v in CONTRACT["themes"].items() if not k.startswith("$")}


def test_an_applied_drafted_theme_is_worn_and_served(tmp_path):
    from aura_adapter.status import read_theme
    state = tmp_path / "state"
    state.mkdir()
    path = _write(state, '{"active": "aura", "theme": "sunrise"}')
    assert read_persona(path)["theme"] == "sunrise"
    assert read_theme("sunrise", path) is None, "nothing applied under that id yet"
    (tmp_path / "themes").mkdir()
    (tmp_path / "themes" / "sunrise.json").write_text('{"id": "sunrise"}', encoding="utf-8")
    assert read_theme("sunrise", path) == {"id": "sunrise"}
    assert read_theme("../state/persona", path) is None, "an id is a name, never a path"
    assert read_persona(_write(state, '{"active": "jarvis", "theme": "../x"}'))["theme"] == "jarvis"
