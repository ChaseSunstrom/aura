"""The console's uncaught errors reach the adapter's log (T-0475): one line each, behind the key,
bounded in size and rate, so a console broken in the phone's WebView shows up in docker logs."""
from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient

from aura_adapter import app as app_mod
from aura_adapter.app import create_app
from aura_adapter.config import Settings

KEY = "app-key-0123456789abcdef"
AUTH = {"Authorization": f"Bearer {KEY}"}


@pytest.fixture()
def client():
    app_mod._CLIENT_ERRORS.clear()
    with TestClient(create_app(Settings(api_key=KEY))) as c:
        yield c


def test_a_client_error_needs_the_key(client):
    assert client.post("/api/client-error", json={"message": "x"}).status_code == 401


def test_a_client_error_is_one_log_line_without_line_breaks(client, caplog):
    caplog.set_level(logging.WARNING, logger="aura_adapter")
    r = client.post("/api/client-error", headers=AUTH,
                    json={"message": "boom\nFAKE adapter line", "source": "app.js", "line": 12, "kind": "error"})
    assert r.status_code == 204
    lines = [rec.getMessage() for rec in caplog.records if rec.name == "aura_adapter"]
    assert len(lines) == 1
    assert "boom FAKE adapter line" in lines[0] and "app.js:12" in lines[0] and "\n" not in lines[0]


def test_a_client_error_message_is_cut_short(client, caplog):
    caplog.set_level(logging.WARNING, logger="aura_adapter")
    client.post("/api/client-error", headers=AUTH, json={"message": "y" * 2000})
    assert "y" * 301 not in caplog.records[-1].getMessage()


def test_a_client_error_body_over_the_cap_is_refused(client):
    r = client.post("/api/client-error", headers=AUTH, json={"message": "z" * app_mod.CLIENT_ERROR_MAX_BYTES})
    assert r.status_code == 413


def test_a_client_error_past_the_rate_limit_is_refused(client):
    codes = [client.post("/api/client-error", headers=AUTH, json={"message": "m"}).status_code
             for _ in range(app_mod.CLIENT_ERRORS_PER_MINUTE + 1)]
    assert codes[:-1] == [204] * app_mod.CLIENT_ERRORS_PER_MINUTE
    assert codes[-1] == 429


def test_a_client_error_drops_terminal_escapes_and_an_empty_location(client, caplog):
    # An ESC in the message would reach `docker logs` read in a terminal (review of T-0475).
    caplog.set_level(logging.WARNING, logger="aura_adapter")
    client.post("/api/client-error", headers=AUTH, json={"kind": "rejection", "message": "\x1b[2Jnope\x00"})
    line = caplog.records[-1].getMessage()
    assert "\x1b" not in line and "\x00" not in line
    assert line.endswith("nope?") and " at " not in line


def test_a_client_error_that_is_not_an_object_is_refused_without_a_slot(client):
    assert client.post("/api/client-error", headers=AUTH, content=b"[1]").status_code == 400
    assert client.post("/api/client-error", headers=AUTH, content=b"not json").status_code == 400
    assert app_mod._CLIENT_ERRORS == []
