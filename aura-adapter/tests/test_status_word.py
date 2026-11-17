"""The adapter's one status word (T-0583): every row of the shared table, and the report carries it."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from aura_adapter.status import status_word

TABLE = json.loads((Path(__file__).resolve().parents[2] / "tests" / "contracts" / "status-words.json").read_text())


@pytest.mark.parametrize("case", TABLE["cases"], ids=lambda c: c["name"])
def test_status_word_matches_the_table(case):
    got = status_word(api=case["api"], backend=case["backend"], swap=case["swap"],
                      model_state=case["model_state"], kill=case["kill"])
    assert got == {"word": case["word"], "reason": case["reason"]}
    label = got["word"] + (f": {got['reason']}" if got["reason"] else "")
    assert label == case["label"]


def test_status_word_table_keeps_to_its_own_vocabulary():
    reasons = {c["reason"] for c in TABLE["cases"]} - {""}
    assert reasons == set(TABLE["precedence"])
    assert {c["word"] for c in TABLE["cases"]} == set(TABLE["words"])
    assert all(len(r) <= TABLE["reason_max"] for r in reasons | {TABLE["no_answer"]["reason"]})


@pytest.mark.asyncio
async def test_status_word_rides_the_real_report_when_the_model_is_starting(monkeypatch):
    """The field is asserted on build_status's own answer, not on a hand-written fixture."""
    from types import SimpleNamespace

    from aura_adapter import status as mod

    async def fake_get(client, url, headers=None, **kw):
        if url.endswith("/running"):
            return True, {"running": [{"model": "house", "state": "starting"}]}
        return True, {"version": "1"}

    monkeypatch.setattr(mod, "_get_json", fake_get)
    monkeypatch.setattr(mod, "read_kill_switch", lambda: {"engaged": False})
    monkeypatch.setattr(mod, "read_persona", lambda: {})
    settings = SimpleNamespace(llama_swap_url="http://swap", llm_api_key="", hermes_api_url="http://api",
                               hermes_serve_url="http://serve", hermes_serve_token="t", model="house")
    report = await mod.build_status(None, settings)
    assert report["status"] == {"word": "degraded", "reason": "model warming"}
