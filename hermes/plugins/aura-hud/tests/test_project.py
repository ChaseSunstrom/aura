"""hud_project: the model projects a visual out of the core.

The contract is ``tests/contracts/projections.json``; the plugin carries a copy
(``projections.json`` beside ``projections.py``) because the image copies the
plugin, not the repo. The console holds the same examples and malformed payloads
to the same first error in ``aura-web/src/lib/ds/gen/contract.test.ts``.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

PLUGIN_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = PLUGIN_DIR.parents[2]
CANONICAL = REPO_ROOT / "tests" / "contracts" / "projections.json"

sys.path.insert(0, str(PLUGIN_DIR.parent))
from _plugin_loader import load_plugin  # noqa: E402

plugin = load_plugin("aura-hud")
hud = plugin.hud
projections = plugin.projections
CONTRACT = json.loads(CANONICAL.read_text(encoding="utf-8"))


class Recorder:
    def __init__(self, delivered: bool = True):
        self.frames: list[tuple[str, str, dict]] = []
        self.delivered = delivered

    def __call__(self, state_key: str, event: str, payload: dict) -> bool:
        self.frames.append((state_key, event, payload))
        return self.delivered


@pytest.fixture
def rec(monkeypatch) -> Recorder:
    r = Recorder()
    monkeypatch.setattr(hud, "STORE", hud.HudStore(emitter=r))
    monkeypatch.setattr(hud, "_state_key", lambda sid: sid or "s")
    return r


def _project(args: dict, **kwargs) -> dict:
    return json.loads(projections.handle_hud_project(args, session_id="s", **kwargs))


def test_project_copy_is_the_canonical_contract():
    assert (PLUGIN_DIR / "projections.json").read_bytes() == CANONICAL.read_bytes()


@pytest.mark.parametrize("kind", sorted(CONTRACT["types"]))
def test_project_example_is_valid(kind):
    assert projections.validate(kind, CONTRACT["examples"][kind]) is None


@pytest.mark.parametrize("case", CONTRACT["malformed"], ids=lambda c: c["error"])
def test_project_malformed_is_refused_with_its_error(case):
    error = projections.validate(case["type"], case["data"])
    assert error is not None and case["error"] in error, error


def test_project_counts_utf16_units_like_the_console():
    at = "\U0001f600" * 40  # 80 UTF-16 units: exactly the title cap
    assert projections.validate("list", {"title": at, "items": []}) is None
    assert "longer than 80" in projections.validate("list", {"title": at + "x", "items": []})


def test_project_refuses_what_json_allows_and_the_console_cannot_parse():
    nan = float("nan")
    assert projections.validate("bar", {"bars": [{"label": "a", "value": nan}]}) == (
        "bars[0].value must be a number"
    )
    assert projections.validate("bar", {"bars": [{"label": "a", "value": True}]}) == (
        "bars[0].value must be a number"
    )
    assert projections.validate("comparison", {"options": ["a", "b"], "rows": [], "picked": 1.0}) is None


def test_project_emits_one_frame_fenced_as_agent_output(rec):
    out = _project({"type": "bar", "data": CONTRACT["examples"]["bar"]})
    assert out["ok"] is True and out["delivered"] is True
    [(key, event, payload)] = rec.frames
    assert (key, event) == ("s", "hud.projection")
    assert payload["origin"] == "agent"
    assert payload["type"] == "bar" and payload["data"] == CONTRACT["examples"]["bar"]
    assert re.fullmatch(CONTRACT["limits"]["id_pattern"], payload["id"])
    assert out["id"] == payload["id"]


def test_project_keeps_a_given_id_so_a_second_call_replaces(rec):
    _project({"id": "power", "type": "stat", "data": CONTRACT["examples"]["stat"]})
    _project({"id": "power", "type": "stat", "data": CONTRACT["examples"]["stat"]})
    assert [p["id"] for _, _, p in rec.frames] == ["power", "power"]


@pytest.mark.parametrize(
    "args, error",
    [
        ({"type": "bar", "data": {"bars": "lots"}}, "bars must be an array"),
        ({"type": "hologram", "data": {}}, "hologram is not a projection type"),
        ({"type": "bar"}, "data must be an object"),
        ({"id": "has space", "type": "bar", "data": {"bars": []}}, "id must be"),
        ({"id": 7, "type": "bar", "data": {"bars": []}}, "id must be"),
    ],
)
def test_project_refuses_and_emits_nothing(rec, args, error):
    out = _project(args)
    assert out["ok"] is False and error in out["error"]
    assert rec.frames == []


def test_project_refuses_an_oversized_payload(rec):
    items = [{"text": f"row {i} ".ljust(150, "x")} for i in range(1500)]
    out = _project({"type": "list", "data": {"items": items}})
    assert out["ok"] is False
    assert out["error"].endswith(f"data is larger than {CONTRACT['limits']['data_bytes']} bytes")
    assert rec.frames == []


def test_project_names_a_long_unknown_type_briefly():
    error = projections.validate("x" * 5000, {})
    assert error is not None and len(error) < 80


def test_project_reports_undelivered(monkeypatch):
    r = Recorder(delivered=False)
    monkeypatch.setattr(hud, "STORE", hud.HudStore(emitter=r))
    out = _project({"type": "code", "data": {"code": "ls"}})
    assert out["ok"] is True and out["delivered"] is False


def test_project_is_registered_with_a_schema_naming_every_type():
    ctx = plugin.FakeCtx()
    plugin.register(ctx)
    tool = ctx.tools["hud_project"]
    assert tool["toolset"] == hud.TOOLSET
    assert tool["raw_handler"] is projections.handle_hud_project
    params = tool["schema"]["parameters"]
    assert params["properties"]["type"]["enum"] == list(CONTRACT["types"])
    assert set(params["required"]) == {"type", "data"}
    manifest = (PLUGIN_DIR / "plugin.yaml").read_text(encoding="utf-8")
    assert "  - hud_project\n" in manifest


def test_project_reaches_each_of_the_owners_screens_and_no_guests(monkeypatch, tmp_path):
    # T-0785: a chart Jarvis drew during a car voice turn never reached the car page.
    r = Recorder()
    monkeypatch.setattr(hud, "STORE", hud.HudStore(
        emitter=r, kept_path=tmp_path / "k.json", live=lambda: ["s", "car", "guest"],
        owner=lambda sid: sid != "guest"))
    out = _project({"type": "bar", "data": CONTRACT["examples"]["bar"]})
    assert out["delivered"] is True
    assert [(k, e) for k, e, _ in r.frames] == [("s", "hud.projection"), ("car", "hud.projection")]
