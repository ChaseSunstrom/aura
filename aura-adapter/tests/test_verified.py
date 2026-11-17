"""The last-verified board: what the deploy's smoke last proved, check by check (T-0580)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from aura_adapter import verified
from aura_adapter.app import create_app
from aura_adapter.config import Settings

KEY = "app-key-0123456789abcdef"
AUTH = {"Authorization": f"Bearer {KEY}"}
CONTRACT = json.loads((Path(__file__).resolve().parents[2] / "tests/contracts/verified.json").read_text())


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(verified, "FILE", tmp_path / "verified.state")
    with TestClient(create_app(Settings(api_key=KEY))) as c:
        yield c


def run(tag: str, at: float, passed: list[str], failed: str | None = None) -> dict:
    return {"tag": tag, "at": at, "passed": passed, "failed": failed}


def test_it_needs_the_key(client):
    assert client.get("/api/verified").status_code == 401
    assert client.put("/api/verified", json=run("t1", 100.0, ["health"])).status_code == 401


def test_an_empty_board_lists_every_check_as_never_passed(client):
    board = client.get("/api/verified", headers=AUTH).json()
    assert [c["id"] for c in board["checks"]] == [c["id"] for c in CONTRACT["checks"]]
    assert all(c["passed_at"] is None and c["current"] is False for c in board["checks"])
    assert board["run"] is None
    assert [(s["id"], s["ok"], s["passed_at"]) for s in board["surfaces"]] == [
        ("console", None, None), ("car", None, None), ("phone", None, None), ("desktop", None, None)]
    assert all("golden-turn" in s["why"] for s in board["surfaces"])


def test_a_run_stamps_what_passed_and_a_later_failed_run_keeps_the_old_pass(client):
    assert client.put("/api/verified", headers=AUTH, json=run("t1", 100.0, ["health", "chat"])).status_code == 200
    client.put("/api/verified", headers=AUTH, json=run("t2", 200.0, ["health"], "SSE chunks=0"))
    board = client.get("/api/verified", headers=AUTH).json()
    by = {c["id"]: c for c in board["checks"]}
    assert by["health"] == {**by["health"], "passed_at": 200.0, "tag": "t2", "current": True}
    # Passed on t1, not on the deploy that is running now.
    assert by["chat"] == {**by["chat"], "passed_at": 100.0, "tag": "t1", "current": False}
    assert board["run"] == {"tag": "t2", "at": 200.0, "ok": False, "failed": "SSE chunks=0", "failed_check": None}


def test_it_survives_a_restart(client, tmp_path):
    client.put("/api/verified", headers=AUTH, json=run("t1", 100.0, ["health"]))
    assert json.loads((tmp_path / "verified.state").read_text())["checks"]["health"]["tag"] == "t1"


@pytest.mark.parametrize("body", [
    {"tag": "t1", "at": 1.0, "passed": ["no-such-check"]},
    {"tag": "", "at": 1.0, "passed": []},
    {"tag": "x" * 200, "at": 1.0, "passed": []},
    {"tag": "t1", "at": "now", "passed": []},
    {"tag": "t1", "at": 1.0, "passed": "health"},
    {"tag": "t1", "at": 1.0, "passed": [], "failed": "x" * 5000},
    {"tag": "t1", "at": float("nan"), "passed": []},
    {"tag": "t1", "at": float("inf"), "passed": []},
    {"tag": "t1", "at": 10 ** 400, "passed": []},
    {"tag": "t1", "at": 1.0, "passed": [], "failed_check": "no-such-check"},
    {"at": 1.0},
    {"at": 1.0, "surfaces": {"toaster": {"ok": True, "why": ""}}},
    {"at": 1.0, "surfaces": {"car": {"ok": "yes", "why": ""}}},
    {"at": 1.0, "surfaces": {"car": {"ok": 0, "why": ""}}},
    {"at": 1.0, "surfaces": {"car": {"ok": 1, "why": ""}}},
    {"at": 1.0, "surfaces": {"car": {"ok": None, "why": "x" * 500}}},
    ["not", "an", "object"],
])
def test_a_bad_run_is_refused_and_changes_nothing(client, body):
    # Sent as text: the client's own encoder refuses NaN, and the house must too.
    raw = json.dumps(body)
    assert client.put("/api/verified", headers={**AUTH, "content-type": "application/json"}, content=raw).status_code == 422
    assert client.get("/api/verified", headers=AUTH).json()["run"] is None


def test_an_unreadable_file_is_an_empty_board_not_a_500(client, tmp_path):
    (tmp_path / "verified.state").write_text("{not json")
    assert client.get("/api/verified", headers=AUTH).json()["run"] is None


def test_a_surface_run_keeps_the_last_pass_beside_a_later_skip_or_failure(client):
    """The golden turn (scripts/verify/golden-turn.mjs): each surface passed, failed or was skipped with a reason."""
    put = lambda at, surfaces: client.put("/api/verified", headers=AUTH, json={"at": at, "surfaces": surfaces})  # noqa: E731
    assert put(100.0, {"console": {"ok": True, "why": ""}, "phone": {"ok": None, "why": "no phone attached (adb)"}}).status_code == 200
    put(200.0, {"console": {"ok": False, "why": "no reply in 120 s"}})
    by = {s["id"]: s for s in client.get("/api/verified", headers=AUTH).json()["surfaces"]}
    assert by["console"] == {**by["console"], "passed_at": 100.0, "ok": False, "at": 200.0, "why": "no reply in 120 s"}
    assert by["phone"] == {**by["phone"], "passed_at": None, "ok": None, "at": 100.0, "why": "no phone attached (adb)"}
    # A surface the run did not name is left as it was; the smoke's run is untouched.
    assert by["car"]["at"] is None and client.get("/api/verified", headers=AUTH).json()["run"] is None


def test_a_run_names_the_check_it_failed_at(client):
    client.put("/api/verified", headers=AUTH, json={**run("t1", 5.0, ["health"], "tool"), "failed_check": "tool"})
    assert client.get("/api/verified", headers=AUTH).json()["run"]["failed_check"] == "tool"


def test_the_status_carries_the_board(client, monkeypatch):
    client.put("/api/verified", headers=AUTH, json=run("t1", 100.0, ["health"]))
    from aura_adapter import app as app_mod

    async def fake_status(*_a, **_k):
        return {}

    monkeypatch.setattr(app_mod, "build_status", fake_status)
    assert client.get("/api/status", headers=AUTH).json()["verified"]["run"]["tag"] == "t1"


def test_the_contract_names_the_same_checks_in_the_same_order():
    assert [(c["id"], c["words"]) for c in CONTRACT["checks"]] == list(verified.WORDS.items())
    assert [(u["id"], u["words"]) for u in CONTRACT["surfaces"]] == list(verified.SURFACES.items())
    # The board's row name is cut at 17 characters.
    assert all(len(r["words"]) <= 17 for r in CONTRACT["checks"] + CONTRACT["surfaces"])
