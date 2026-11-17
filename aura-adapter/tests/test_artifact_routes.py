"""The artifact endpoints: auth, range requests over HTTP, and the error codes.

The store's own behaviour is covered in ``test_artifacts.py``. What is here is
the half a caller sees: that the bearer key is enforced on every route (the media
brief lists "auth enforced" as a gate), that a seek produces a 206 with the right
``content-range``, and that an unsatisfiable range is a 416 rather than a whole
file — a player that gets 200 to a range request downloads everything.
"""

from __future__ import annotations

import asyncio
import os
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from aura_adapter import artifacts
from aura_adapter import app as app_mod
from aura_adapter.app import create_app, signed_path, sweep_forever
from aura_adapter.config import Settings

KEY = "app-key-0123456789abcdef"
AUTH = {"Authorization": f"Bearer {KEY}"}
CLIP = bytes(range(256)) * 8


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    # The routes take no root, so the store's module-level one is redirected.
    monkeypatch.setattr(artifacts, "ROOT", tmp_path)
    return TestClient(create_app(Settings(api_key=KEY)))


def publish(client: TestClient, data: bytes = CLIP, **headers: str):
    return client.post(
        "/api/artifacts",
        content=data,
        headers={
            **AUTH,
            "x-artifact-mime": "video/mp4",
            "x-artifact-title": "a clip",
            **headers,
        },
    )


class TestAuth:
    """"Auth enforced" is one of the brief's gates, so every verb gets a case."""

    def test_publish_without_a_key_is_refused(self, client: TestClient) -> None:
        assert client.post("/api/artifacts", content=b"x").status_code in (401, 403)

    def test_listing_without_a_key_is_refused(self, client: TestClient) -> None:
        assert client.get("/api/artifacts").status_code in (401, 403)

    def test_fetching_without_a_key_is_refused(self, client: TestClient) -> None:
        ident = publish(client).json()["id"]
        assert client.get(f"/api/artifacts/{ident}").status_code in (401, 403)

    def test_a_wrong_key_is_refused(self, client: TestClient) -> None:
        r = client.get("/api/artifacts", headers={"Authorization": "Bearer nope"})
        assert r.status_code in (401, 403)


def test_publishing_returns_the_content_hash_and_the_provenance(client: TestClient) -> None:
    r = publish(client, b"a chart", **{"x-artifact-session": "s1", "x-artifact-tool": "charts"})
    assert r.status_code == 200
    body = r.json()
    assert len(body["id"]) == 64
    assert body["provenance"]["session"] == "s1"
    assert body["provenance"]["tool"] == "charts"
    assert body["size"] == len(b"a chart")


def test_a_ttl_of_never_pins_the_artifact_open(client: TestClient) -> None:
    assert publish(client, b"forever", **{"x-artifact-ttl": "never"}).json()["expires"] is None


def test_a_bad_ttl_is_a_400_not_a_crash(client: TestClient) -> None:
    assert publish(client, b"x", **{"x-artifact-ttl": "soon"}).status_code == 400


class TestRangeOverHttp:
    def test_a_full_get_advertises_that_it_can_be_seeked(self, client: TestClient) -> None:
        # A player decides whether it can seek from this header. Without it on
        # the 200, it downloads the whole file before showing a frame.
        ident = publish(client).json()["id"]
        r = client.get(f"/api/artifacts/{ident}", headers=AUTH)
        assert r.status_code == 200
        assert r.headers["accept-ranges"] == "bytes"
        assert r.headers["content-type"] == "video/mp4"
        assert len(r.content) == len(CLIP)

    def test_a_seek_gets_a_206_with_the_right_bytes(self, client: TestClient) -> None:
        ident = publish(client).json()["id"]
        r = client.get(f"/api/artifacts/{ident}", headers={**AUTH, "Range": "bytes=10-19"})
        assert r.status_code == 206
        assert r.headers["content-range"] == f"bytes 10-19/{len(CLIP)}"
        assert r.content == bytes(range(10, 20))

    def test_an_open_ended_seek_returns_the_rest(self, client: TestClient) -> None:
        ident = publish(client).json()["id"]
        r = client.get(f"/api/artifacts/{ident}", headers={**AUTH, "Range": "bytes=2040-"})
        assert r.status_code == 206
        assert len(r.content) == len(CLIP) - 2040

    def test_an_unsatisfiable_range_is_416_not_the_whole_file(self, client: TestClient) -> None:
        ident = publish(client).json()["id"]
        r = client.get(f"/api/artifacts/{ident}", headers={**AUTH, "Range": "bytes=99999-"})
        assert r.status_code == 416
        assert r.headers["content-range"] == f"bytes */{len(CLIP)}"
        assert r.content == b""


def test_a_missing_artifact_is_404(client: TestClient) -> None:
    assert client.get("/api/artifacts/" + "0" * 64, headers=AUTH).status_code == 404


def test_pin_then_unpin_round_trips(client: TestClient) -> None:
    ident = publish(client).json()["id"]
    assert client.post(f"/api/artifacts/{ident}/pin", headers=AUTH).json()["pinned"] is True
    off = client.post(f"/api/artifacts/{ident}/pin?pinned=false", headers=AUTH).json()
    assert off["pinned"] is False and off["expires"] is not None


def test_delete_removes_it_from_the_listing(client: TestClient) -> None:
    ident = publish(client).json()["id"]
    assert len(client.get("/api/artifacts", headers=AUTH).json()["artifacts"]) == 1
    assert client.delete(f"/api/artifacts/{ident}", headers=AUTH).json()["ok"] is True
    assert client.get("/api/artifacts", headers=AUTH).json()["artifacts"] == []


def test_listing_filters_by_kind(client: TestClient) -> None:
    publish(client, b"pic", **{"x-artifact-mime": "image/png"})
    publish(client, CLIP)
    rows = client.get("/api/artifacts?kind=image/", headers=AUTH).json()["artifacts"]
    assert [a["mime"] for a in rows] == ["image/png"]


class TestSecurity:
    """Both found by the commit security review of b270118f."""

    def test_a_traversal_id_is_404_not_a_file(self, client: TestClient) -> None:
        r = client.get("/api/artifacts/..%2F..%2F..%2Fetc%2Fpasswd", headers=AUTH)
        assert r.status_code == 404
        assert b"root:" not in r.content

    def test_a_traversal_delete_is_404(self, client: TestClient) -> None:
        assert client.delete("/api/artifacts/..%2F..%2Fx", headers=AUTH).status_code == 404

    def test_a_published_web_page_comes_back_as_a_download(self, client: TestClient) -> None:
        ident = publish(
            client, b"<script>fetch('/api/artifacts')</script>", **{"x-artifact-mime": "text/html"}
        ).json()["id"]
        r = client.get(f"/api/artifacts/{ident}", headers=AUTH)
        assert r.headers["content-type"].startswith("application/octet-stream")
        assert r.headers["content-disposition"].startswith("attachment")

    def test_a_video_is_still_served_inline_so_it_can_play(self, client: TestClient) -> None:
        ident = publish(client).json()["id"]
        r = client.get(f"/api/artifacts/{ident}", headers=AUTH)
        assert r.headers["content-type"] == "video/mp4"
        assert r.headers["content-disposition"].startswith("inline")


class TestSignedUrl:
    """A `<video src>` cannot send a bearer, so the JSON carries a URL that proves itself.

    A bad, stale or absent signature is refused exactly as a missing bearer is: the
    route falls through to the same check, so there is one 401 and not two.
    """

    def test_publish_hands_back_a_signed_url_that_a_media_element_can_fetch(self, client: TestClient) -> None:
        body = publish(client).json()
        assert body["signed_url"].startswith("http://testserver/api/artifacts/" + body["id"] + "?exp=")
        r = client.get(body["signed_url"])  # no Authorization header
        assert r.status_code == 200
        assert r.content == CLIP

    def test_the_listing_and_a_pin_carry_it_too(self, client: TestClient) -> None:
        ident = publish(client).json()["id"]
        assert "sig=" in client.get("/api/artifacts", headers=AUTH).json()["artifacts"][0]["signed_url"]
        assert "sig=" in client.post(f"/api/artifacts/{ident}/pin", headers=AUTH).json()["signed_url"]

    def test_a_seek_over_a_signed_url_is_still_a_206(self, client: TestClient) -> None:
        url = publish(client).json()["signed_url"]
        r = client.get(url, headers={"Range": "bytes=10-19"})
        assert r.status_code == 206
        assert r.content == bytes(range(10, 20))

    def test_an_expired_signature_is_401(self, client: TestClient) -> None:
        ident = publish(client).json()["id"]
        stale = signed_path(KEY, ident, int(time.time()) - 1)
        assert client.get(stale).status_code == 401

    def test_a_signature_for_another_id_is_401(self, client: TestClient) -> None:
        ident = publish(client).json()["id"]
        other = publish(client, b"other bytes").json()["id"]
        borrowed = signed_path(KEY, other, int(time.time()) + 60).replace(other, ident)
        assert client.get(borrowed).status_code == 401

    def test_a_signature_made_with_another_key_is_401(self, client: TestClient) -> None:
        ident = publish(client).json()["id"]
        forged = signed_path("not-the-key-0123456789abcdef", ident, int(time.time()) + 60)
        assert client.get(forged).status_code == 401

    def test_no_signature_and_no_bearer_is_401(self, client: TestClient) -> None:
        ident = publish(client).json()["id"]
        assert client.get(f"/api/artifacts/{ident}?exp=notanumber&sig=").status_code == 401
        assert client.get(f"/api/artifacts/{ident}").status_code == 401


@pytest.mark.skipif(os.geteuid() == 0, reason="root writes through any mode")
def test_an_unwritable_store_is_a_507_with_a_reason_not_a_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Production's first store was under a read-only mount and every publish was an
    unhandled EROFS. The answer has to say what is wrong with the store."""
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o500)
    monkeypatch.setattr(artifacts, "ROOT", locked / "artifacts")
    try:
        r = publish(TestClient(create_app(Settings(api_key=KEY))))
    finally:
        locked.chmod(0o700)
    assert r.status_code == 507
    assert "not writable" in r.json()["detail"]


class TestSweeper:
    """`sweep()` had no caller: expired artifacts were hidden, never deleted."""

    def test_startup_schedules_the_sweep_and_shutdown_cancels_it(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(artifacts, "ROOT", tmp_path)
        app = create_app(Settings(api_key=KEY))
        with TestClient(app):
            task = app.state.sweeper
            assert isinstance(task, asyncio.Task) and not task.done()
        assert task.cancelled() or task.done()

    def test_a_failed_sweep_does_not_end_the_loop(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[str] = []

        def sweep_then_fail(root=None, now=None):  # noqa: ARG001
            calls.append("sweep")
            if len(calls) == 1:
                raise OSError("a sidecar vanished mid-walk")
            return []

        real_sleep = asyncio.sleep
        mine: list[asyncio.AbstractEventLoop] = []

        def another_loop_sleeps() -> None:
            # What a server thread left running by an earlier test does: asyncio.sleep is the
            # shared module's, so the patch below sees every loop in the process, not just ours.
            asyncio.run(asyncio.sleep(0))

        async def sleep_twice_then_stop(seconds: float) -> None:
            if asyncio.get_running_loop() is not mine[0]:
                return await real_sleep(seconds)  # not the loop under test: not in its list
            calls.append(f"sleep {seconds}")
            if len(calls) == 2:
                t = threading.Thread(target=another_loop_sleeps)
                t.start()
                t.join()
            if calls.count("sweep") == 3:
                raise asyncio.CancelledError

        async def run() -> None:
            mine.append(asyncio.get_running_loop())
            await sweep_forever(interval=7)

        monkeypatch.setattr(artifacts, "sweep", sweep_then_fail)
        monkeypatch.setattr(app_mod.asyncio, "sleep", sleep_twice_then_stop)
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(run())
        assert calls == ["sweep", "sleep 7", "sweep", "sleep 7", "sweep", "sleep 7"]
