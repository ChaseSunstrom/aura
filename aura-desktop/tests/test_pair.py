"""Pairing the desktop: the link, the claim, the config write, the setup."""
from __future__ import annotations

import io
import json
import stat
from pathlib import Path
from urllib import error as urlerror

import pytest

from aura_desktop import pair as P


def test_the_consoles_link_is_read_and_anything_else_is_not_a_link():
    link = P.parse_link("jarvis://pair?v=1&u=http%3A%2F%2Fjarvis.example.ts.net%3A8080&c=4812%209917")
    assert link == P.PairingLink(url="http://jarvis.example.ts.net:8080", code="48129917")
    assert P.parse_link("jarvis.example.ts.net:8080") is None
    assert P.parse_link("http://jarvis.example.ts.net:8080") is None
    with pytest.raises(P.PairingError):
        P.parse_link("jarvis://enrol?v=1")
    with pytest.raises(P.PairingError):
        P.parse_link("jarvis://pair?v=2&u=x&c=y")
    with pytest.raises(P.PairingError):
        P.parse_link("jarvis://pair?v=1&u=x")


def test_the_claim_goes_to_the_houses_http_base_whatever_was_typed():
    assert P.house_http_url("jarvis.lan:8080") == "http://jarvis.lan:8080"
    assert P.house_http_url("ws://jarvis.lan:8080/api/websocket") == "http://jarvis.lan:8080"
    assert P.house_http_url("https://jarvis.example.ts.net") == "https://jarvis.example.ts.net"
    with pytest.raises(P.PairingError):
        P.house_http_url("ftp://x")


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _opener(status=200, body=None, seen=None):
    def open_(req):
        if seen is not None:
            seen.append((req.full_url, req.get_method(), json.loads(req.data.decode())))
        if status != 200:
            raise urlerror.HTTPError(req.full_url, status, "nope", {}, io.BytesIO(json.dumps({"detail": "That pairing code is not valid, or it has expired."}).encode()))
        return _Resp(json.dumps(body or {"token": "tok-secret", "token_id": "t1", "name": "desktop"}).encode())
    return open_


def test_a_pairing_claims_the_code_once_and_writes_only_server_and_token(tmp_path):
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"file_roots": ["/home/me/work"], "token": "old"}))
    seen = []
    out = P.pair("jarvis://pair?v=1&u=http%3A%2F%2Fjarvis.lan%3A8080&c=1234%205678", name="study pc",
                 config_path=cfg, opener=_opener(seen=seen))
    # The desktop says what it is, so the console mints it no adapter key: it never talks to
    # the adapter (tests/contracts/pairing-claim.json, T-0569).
    contract = json.loads((Path(__file__).resolve().parents[2] / "tests/contracts/pairing-claim.json").read_text())
    kind = contract["senders"]["desktop"]["kind"]
    assert kind in contract["no_adapter_key"]
    assert seen == [("http://jarvis.lan:8080/api/pair/claim", "POST", {"code": "12345678", "name": "study pc", "kind": kind})]
    written = json.loads(cfg.read_text())
    assert written["server_url"] == "ws://jarvis.lan:8080/api/websocket" and written["token"] == "tok-secret"
    assert written["file_roots"] == ["/home/me/work"], "the rest of the config is kept"
    assert stat.S_IMODE(cfg.stat().st_mode) == 0o600
    assert out["server_url"] == "ws://jarvis.lan:8080/api/websocket" and out["name"] == "desktop"
    # "tok-secret" appears nowhere in what is said.
    assert "tok-secret" not in json.dumps(out)


def test_a_refused_code_is_a_sentence_not_a_retry(tmp_path):
    seen = []
    with pytest.raises(P.PairingError, match="not valid"):
        P.pair("jarvis.lan:8080", code="0000 0000", config_path=tmp_path / "c.json", opener=_opener(status=400, seen=seen))
    assert len(seen) == 1, "one attempt: a code is one use, and the house counts failures"
    assert not (tmp_path / "c.json").exists()


def test_the_setup_asks_for_the_link_or_the_address_and_the_code(tmp_path):
    said: list[str] = []
    answers = iter(["jarvis.lan:8080", "1234 5678", ""])
    rc = P.interactive(config_path=tmp_path / "c.json", ask=lambda _q: next(answers), say=said.append, opener=_opener())
    assert rc == 0 and json.loads((tmp_path / "c.json").read_text())["token"] == "tok-secret"
    assert any("Paired as 'desktop'" in line for line in said)
    # A link needs no code question.
    answers = iter(["jarvis://pair?v=1&u=http%3A%2F%2Fjarvis.lan%3A8080&c=1234", "lab"])
    rc = P.interactive(config_path=tmp_path / "d.json", ask=lambda _q: next(answers), say=said.append, opener=_opener())
    assert rc == 0
    # Nothing typed: nothing done.
    assert P.interactive(config_path=tmp_path / "e.json", ask=lambda _q: "", say=said.append) == 2
