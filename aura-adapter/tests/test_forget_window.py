"""The adapter erases the artifacts made in the last N minutes (T-1399).

Not covered here: the Hermes approval that gates the tool which calls this route, and a blob
with no readable sidecar (nothing says when it was made).
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from aura_adapter import artifacts, device_keys
from aura_adapter.app import create_app
from aura_adapter.config import Settings

KEY = "app-key-0123456789abcdef"
AUTH = {"Authorization": f"Bearer {KEY}"}
NOW = 1_800_000_000.0


def put(root: Path, data: bytes, age_min: float, **kw) -> artifacts.Artifact:
    """An artifact stored `age_min` minutes before the made-up clock."""
    art = artifacts.publish(io.BytesIO(data), mime="image/png", title="t", root=root, **kw)
    art.created = NOW - age_min * 60
    artifacts._save(root, art)
    return art


def present(root: Path, art: artifacts.Artifact) -> bool:
    return artifacts._blob(root, art.id).exists() or artifacts._meta(root, art.id).exists()


def test_forget_window_deletes_what_was_made_inside_and_leaves_older(tmp_path: Path) -> None:
    fresh, edge, old = put(tmp_path, b"fresh", 2), put(tmp_path, b"edge", 9.9), put(tmp_path, b"old", 11)
    pinned = put(tmp_path, b"pinned", 1, ttl=None)
    assert artifacts.delete_window(10, tmp_path, now=NOW) == 3
    assert not present(tmp_path, fresh) and not present(tmp_path, edge) and not present(tmp_path, pinned)
    assert present(tmp_path, old)


def test_forget_window_on_an_empty_or_missing_store_is_zero(tmp_path: Path) -> None:
    assert artifacts.delete_window(10, tmp_path / "nothing", now=NOW) == 0
    assert artifacts.delete_window(10, tmp_path, now=NOW) == 0


def test_forget_window_a_failed_delete_is_an_error_after_the_rest_went(tmp_path: Path, monkeypatch) -> None:
    a, b = put(tmp_path, b"a", 1), put(tmp_path, b"b", 2)
    real = artifacts.delete

    def flaky(ident, root=None):
        if ident == a.id:
            raise PermissionError("read-only")
        real(ident, root)

    monkeypatch.setattr(artifacts, "delete", flaky)
    with pytest.raises(artifacts.PartialErase) as err:
        artifacts.delete_window(10, tmp_path, now=NOW)
    assert err.value.erased == 1 and err.value.failed == 1
    assert not present(tmp_path, b) and present(tmp_path, a)


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(artifacts, "ROOT", tmp_path / "store")
    (tmp_path / "keys").mkdir()
    monkeypatch.setattr(device_keys, "ROOT", tmp_path / "keys")
    return TestClient(create_app(Settings(api_key=KEY, hermes_api_url="http://127.0.0.1:9",
                                          hermes_serve_url="http://127.0.0.1:9")))


def publish(client: TestClient, data: bytes) -> str:
    r = client.post("/api/artifacts", content=data, headers={**AUTH, "x-artifact-mime": "image/png"})
    assert r.status_code == 200
    return r.json()["id"]


def forget(client: TestClient, body, headers=AUTH):
    return client.post("/api/artifacts/forget_window", content=json.dumps(body), headers=headers)


def test_forget_window_route_erases_the_window_and_counts_it(client: TestClient) -> None:
    ident = publish(client, b"just made")
    r = forget(client, {"minutes": 5})
    assert r.status_code == 200 and r.json() == {"minutes": 5, "artifacts": 1}
    assert client.get(f"/api/artifacts/{ident}", headers=AUTH).status_code == 404
    assert forget(client, {"minutes": 5}).json()["artifacts"] == 0


def test_forget_window_route_leaves_an_older_artifact(client: TestClient) -> None:
    ident = publish(client, b"made last week")
    art = artifacts.get(ident)
    art.created -= 3600 * 30
    artifacts._save(artifacts.ROOT, art)
    assert forget(client, {"minutes": 1440}).json()["artifacts"] == 0
    assert client.get(f"/api/artifacts/{ident}", headers=AUTH).status_code == 200


@pytest.mark.parametrize("minutes", [0, 1441, -1, "10", None, True, 1.5])
def test_forget_window_route_takes_minutes_one_to_1440_only(client: TestClient, minutes) -> None:
    ident = publish(client, b"keep me")
    assert forget(client, {"minutes": minutes}).status_code == 400
    assert client.get(f"/api/artifacts/{ident}", headers=AUTH).status_code == 200


def test_forget_window_route_refuses_a_body_that_is_no_object(client: TestClient) -> None:
    assert forget(client, [10]).status_code == 400
    assert client.post("/api/artifacts/forget_window", content=b"not json", headers=AUTH).status_code == 400


def test_forget_window_is_refused_without_the_key_and_to_a_paired_device(client: TestClient) -> None:
    """The decision, not stored state: a paired phone's own key is no master, so it cannot wipe
    the day's artifacts; the artifact stays readable after every refusal."""
    ident = publish(client, b"mine")
    paired = client.post("/api/pair", json={"name": "phone", "kind": "phone"}, headers=AUTH).json()["key"]
    for headers in ({}, {"Authorization": "Bearer nope"}, {"Authorization": f"Bearer {paired}"}):
        assert forget(client, {"minutes": 5}, headers).status_code == 401
    assert client.get(f"/api/artifacts/{ident}", headers=AUTH).status_code == 200


def test_forget_window_route_a_partly_failed_erase_is_a_503_with_a_fixed_text(client: TestClient, monkeypatch) -> None:
    publish(client, b"x")

    def boom(minutes, root=None, now=None):
        raise artifacts.PartialErase(erased=1, failed=2)

    monkeypatch.setattr(artifacts, "delete_window", boom)
    r = forget(client, {"minutes": 5})
    assert r.status_code == 503 and r.json()["detail"] == "some artifacts could not be erased"
    assert str(artifacts.ROOT) not in r.text
