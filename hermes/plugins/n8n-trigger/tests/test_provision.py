"""AURA sets n8n up itself (T-1523): the first-run owner, an API key, the key where the tools read it.

The fake below copies what n8n 2.46.6 did on a throwaway instance (T-1523's log): the session
cookie is Secure and HttpOnly, so a client on plain http has to send it back by hand; every /rest
call is bound to the ``browser-id`` header the login carried; a second owner setup is a 400.
"""
from __future__ import annotations

import json
import logging
import os
import stat
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from _plugin_loader import load_plugin  # noqa: E402,F401  (puts aura-schema on the path)

from _shared import _n8n_setup as setup  # noqa: E402

URL = "http://n8n:5678"  # the stack's own address (deploy/.env.example); _egress refuses a dotted http host


class FakeN8n:
    """Just enough of n8n's first-run and public API to provision against."""

    def __init__(self, *, owner: dict | None = None, keys: set[str] | None = None) -> None:
        self.owner = owner
        self.keys = set(keys or ())
        self.sessions: dict[str, str] = {}  # cookie -> browser-id
        self.setups = 0
        self.minted = 0
        self.labels: set[str] = set()
        self.up = True

    def _session_ok(self, request: httpx.Request) -> bool:
        cookie = request.headers.get("cookie", "")
        token = cookie.removeprefix("n8n-auth=") if cookie.startswith("n8n-auth=") else ""
        return bool(token) and self.sessions.get(token) == request.headers.get("browser-id")

    def _login(self, request: httpx.Request) -> httpx.Response:
        token = f"t{len(self.sessions)}"
        self.sessions[token] = request.headers.get("browser-id") or ""
        return httpx.Response(200, json={"data": {"email": self.owner["email"]}},
                              headers={"set-cookie": f"n8n-auth={token}; Path=/; HttpOnly; Secure; SameSite=Lax"})

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if not self.up:
            raise httpx.ConnectError("refused", request=request)
        path, method = request.url.path, request.method
        body = json.loads(request.content) if request.content else {}
        if path == "/rest/settings":
            return httpx.Response(200, json={"data": {"userManagement": {
                "showSetupOnFirstLoad": self.owner is None}}})
        if path == "/rest/owner/setup" and method == "POST":
            if self.owner is not None:
                return httpx.Response(400, json={"code": 400, "message": "Instance owner already setup"})
            self.setups += 1
            self.owner = {"email": body["email"], "password": body["password"]}
            return self._login(request)
        if path == "/rest/login" and method == "POST":
            if not self.owner or body.get("emailOrLdapLoginId") != self.owner["email"] \
                    or body.get("password") != self.owner["password"]:
                return httpx.Response(401, json={"message": "Wrong username or password"})
            return self._login(request)
        if path.startswith("/rest/"):
            if not self._session_ok(request):
                return httpx.Response(401, json={"status": "error", "message": "Unauthorized"})
            if path == "/rest/api-keys/scopes":
                return httpx.Response(200, json={"data": ["workflow:list", "workflow:create"]})
            if path == "/rest/api-keys" and method == "POST":
                # 2.46.6: UNIQUE(userId, label) on user_api_keys; a second "AURA" is a 500.
                if body["label"] in self.labels:
                    return httpx.Response(500, json={"code": 500, "message": "SQLITE_CONSTRAINT"})
                self.labels.add(body["label"])
                self.minted += 1
                key = f"minted-{self.minted}"
                self.keys.add(key)
                return httpx.Response(200, json={"data": {"id": "1", "label": body["label"],
                                                          "rawApiKey": key, "scopes": body["scopes"]}})
        if path == "/api/v1/workflows":
            ok = request.headers.get("x-n8n-api-key") in self.keys
            return httpx.Response(200, json={"data": []}) if ok else httpx.Response(401, json={"message": "Unauthorized"})
        return httpx.Response(404)


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("N8N_URL", URL)
    monkeypatch.delenv("N8N_API_KEY", raising=False)
    return tmp_path / "hermes"


def _run(home: Path, fake: FakeN8n) -> dict:
    return setup.ensure(home=home, transport=httpx.MockTransport(fake))


def _overrides(home: Path) -> dict:
    path = home / ".storage" / "environment.json"
    return json.loads(path.read_text()) if path.exists() else {}


def test_provision_a_fresh_n8n_gets_an_owner_and_a_key_the_tools_read_now(home, caplog):
    fake = FakeN8n()
    caplog.set_level(logging.DEBUG)

    out = _run(home, fake)

    assert out["state"] == "provisioned", out
    assert fake.setups == 1 and fake.minted == 1
    key = _overrides(home)["N8N_API_KEY"]
    assert key in fake.keys
    # The gateway's tools read from_env() per call: the key works in this process now, not
    # only after the next start exports the override file.
    assert os.environ["N8N_API_KEY"] == key
    login = home / ".storage" / "n8n-owner.json"
    assert stat.S_IMODE(login.stat().st_mode) == 0o600
    saved = json.loads(login.read_text())
    assert saved["email"] == fake.owner["email"] and saved["password"] == fake.owner["password"]
    # Neither secret reaches a log line or the answer a tool would show.
    shown = caplog.text + json.dumps(out)
    assert key not in shown and fake.owner["password"] not in shown


def test_provision_a_second_run_adopts_and_writes_nothing(home):
    fake = FakeN8n()
    _run(home, fake)
    env_file = home / ".storage" / "environment.json"
    before = env_file.stat().st_mtime_ns

    out = _run(home, fake)

    assert out["state"] == "adopted", out
    assert fake.setups == 1 and fake.minted == 1
    assert env_file.stat().st_mtime_ns == before


def test_provision_a_key_the_owner_set_and_n8n_accepts_is_never_replaced(home, monkeypatch):
    monkeypatch.setenv("N8N_API_KEY", "owners-key")
    fake = FakeN8n(owner={"email": "o@example.com", "password": "x"}, keys={"owners-key"})

    out = _run(home, fake)

    assert out["state"] == "adopted"
    assert fake.minted == 0
    assert "N8N_API_KEY" not in _overrides(home)
    assert os.environ["N8N_API_KEY"] == "owners-key"


def test_provision_renews_a_refused_key_with_the_login_it_made(home, monkeypatch):
    fake = FakeN8n()
    _run(home, fake)
    fake.keys.clear()  # n8n's key store was reset; the owner (ours) survived

    out = _run(home, fake)

    assert out["state"] == "renewed", out
    assert fake.setups == 1 and fake.minted == 2
    assert _overrides(home)["N8N_API_KEY"] == "minted-2" == os.environ["N8N_API_KEY"]


def test_provision_an_n8n_someone_else_owns_is_left_alone(home, monkeypatch):
    monkeypatch.setenv("N8N_API_KEY", "stale")
    fake = FakeN8n(owner={"email": "o@example.com", "password": "theirs"})

    out = _run(home, fake)

    assert out["state"] == "owned_elsewhere", out
    assert fake.minted == 0 and fake.setups == 0
    assert _overrides(home) == {}
    assert "key" in out["detail"]  # says what the owner can do: give AURA a key


def test_provision_keeps_the_other_overrides_in_the_file(home):
    store = home / ".storage"
    store.mkdir(parents=True)
    (store / "environment.json").write_text(json.dumps({"GOALS_MAX_TURNS": "9"}))

    _run(home, FakeN8n())

    data = _overrides(home)
    assert data["GOALS_MAX_TURNS"] == "9" and data["N8N_API_KEY"]
    # The adapter (the gateway's group) reads this file at start: the settings plugin's mode.
    assert stat.S_IMODE((store / "environment.json").stat().st_mode) == 0o640


def test_provision_is_off_without_a_url_and_quiet_when_n8n_is_down(home, monkeypatch):
    monkeypatch.setenv("N8N_URL", "")
    assert _run(home, FakeN8n())["state"] == "off"

    monkeypatch.setenv("N8N_URL", URL)
    down = FakeN8n()
    down.up = False
    out = _run(home, down)
    assert out["state"] == "unreachable"
    assert not (home / ".storage" / "environment.json").exists()


def test_provision_status_is_the_last_answer_without_a_secret(home):
    fake = FakeN8n()
    _run(home, fake)

    got = setup.status(home)

    assert got["state"] == "provisioned" and got["checked"]
    assert str(home / ".storage" / "n8n-owner.json") == got["login_file"]
    text = json.dumps(got)
    assert fake.owner["password"] not in text and "minted-1" not in text


def test_provision_keeper_runs_ensure_on_a_beat_and_is_off_without_a_url(home, monkeypatch):
    import threading

    calls = threading.Semaphore(0)
    monkeypatch.setattr(setup, "ensure", lambda **_kw: calls.release() or {"state": "adopted"})
    monkeypatch.setattr(setup, "_keeper", None)

    stop = setup.start(first=0.0, interval=0.01)
    try:
        assert stop is not None
        assert calls.acquire(timeout=5) and calls.acquire(timeout=5), "the keeper ran ensure() again"
        assert setup.start(first=0.0, interval=0.01) is None, "one keeper per process"
    finally:
        stop.set()
        setup._keeper.join(timeout=5)

    monkeypatch.setattr(setup, "_keeper", None)
    monkeypatch.setenv("N8N_URL", "")
    assert setup.start() is None


def test_provision_the_n8n_plugin_starts_the_keeper(monkeypatch):
    started = []
    monkeypatch.setattr(setup, "start", lambda **kw: started.append(kw))
    plugin = load_plugin("n8n-trigger")
    from _plugin_loader import FakeCtx

    plugin.register(FakeCtx())

    assert started == [{}]


def test_provision_a_url_the_address_check_refuses_sends_nothing_and_says_why(home, monkeypatch):
    monkeypatch.setenv("N8N_URL", "http://n8n.lan.example:5678")  # a dotted name over plain http
    fake = FakeN8n()

    out = _run(home, fake)

    assert out["state"] == "off"
    assert "N8N_URL" in out["detail"] and "not set" not in out["detail"]
    assert fake.setups == 0 and fake.owner is None


def test_provision_keeper_runs_again_soon_after_n8n_was_down(monkeypatch):
    """A fresh stack: the gateway is up before n8n, so the first check finds nothing. Waiting the
    full ten minutes for the second left the n8n tools off that long on every first start."""
    import threading

    answers = iter([{"state": "unreachable"}, {"state": "provisioned"}])
    calls = []

    def fake_ensure(**_kw):
        calls.append(1)
        return next(answers, {"state": "adopted"})

    monkeypatch.setattr(setup, "ensure", fake_ensure)
    stop = threading.Event()
    keeper = threading.Thread(target=setup._keep, args=(stop, 0.0, 600.0), kwargs={"retry": 0.05})
    keeper.start()
    try:
        for _ in range(100):
            if len(calls) >= 2:
                break
            stop.wait(0.01)
    finally:
        stop.set()
        keeper.join(2)
    assert len(calls) == 2


def test_provision_an_n8n_behind_a_proxy_that_fails_is_unreachable_not_someone_elses(home):
    """Production's N8N_URL went through a reverse proxy that answered 502. The settings check read
    that as "not a fresh n8n" and reported owned_elsewhere, telling the owner to make a key for an
    n8n that had never been set up. A settings answer that is not n8n's is "did not answer"."""
    fake = FakeN8n()

    def proxy(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502) if request.url.path == "/rest/settings" else fake(request)

    out = setup.ensure(home=home, transport=httpx.MockTransport(proxy))

    assert out["state"] == "unreachable", out
    assert "502" in out["detail"] and "N8N_URL" in out["detail"]
    assert fake.setups == 0


def test_provision_another_gateway_process_adopts_the_key_the_first_one_minted(home, monkeypatch):
    """Production runs several gateway processes, each with the keeper. The one that minted put
    the key in its own environment and the override file; the others read only their environment,
    took the key for refused, and minted again under the same label: n8n's UNIQUE(userId, label)
    answered 500 every minute and the status said failed (T-1523)."""
    fake = FakeN8n()
    assert _run(home, fake)["state"] == "provisioned"
    monkeypatch.delenv("N8N_API_KEY")  # a second process: started before the key existed

    out = _run(home, fake)

    assert out["state"] == "adopted", out
    assert fake.minted == 1
    assert os.environ["N8N_API_KEY"] == _overrides(home)["N8N_API_KEY"], "and the tools here have it now"


def test_provision_a_renewal_mints_under_a_label_n8n_has_not_seen(home):
    fake = FakeN8n()
    _run(home, fake)
    fake.keys.clear()  # the key was revoked in n8n; its label row stays

    out = _run(home, fake)

    assert out["state"] == "renewed", out
    assert fake.minted == 2 and len(fake.labels) == 2
