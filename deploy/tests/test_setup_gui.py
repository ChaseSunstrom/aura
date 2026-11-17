"""deploy/setup/server.py: the page the installer opens, and the only thing that writes its answers.

What these pin, as decisions: a request without the one-time token is refused, and so is one
whose Host is not this server (a page on another site cannot read it through a rebound name);
every answer is checked here, not in the browser; nothing that could start a new line, a quote or
a command substitution reaches the answers file; the file is private; a console password leaves
only as the hash the console itself verifies; a model key is sent to the endpoint under test and
to nowhere else, and never comes back or into the log.

Not covered: the page (jarvis-web/e2e/install-setup.spec.ts drives it in a browser), a real
model endpoint (a local stub answers), and whether the installer honours the answers
(test_install.py).
"""
from __future__ import annotations

import base64
import importlib.util
import json
import os
import re
import signal
import socket
import stat
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
TOKEN = "t" * 20 + "0123456789abcdef" * 2          # made up; a real one is 64 hex from the installer


def _server_module():
    spec = importlib.util.spec_from_file_location("aura_setup_server", ROOT / "deploy" / "setup" / "server.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


srv = _server_module()

FACTS = {
    "version": 1, "gpu": False, "addresses": ["192.0.2.10"], "timezone": "Etc/UTC",
    "dir": "/home/user/aura", "os": "Linux", "registry": False,
}


def good(**over: Any) -> dict[str, Any]:
    answers: dict[str, Any] = {
        "server": {"bind": "192.0.2.10", "public_url": "", "timezone": "Europe/Berlin",
                   "password": "correct horse battery"},
        "models": {"mode": "endpoint", "url": "http://192.0.2.20:11434/v1", "model": "qwen3:8b", "key": ""},
        "voice": {"tts": "piper", "stt": "small"},
        "wake": {"enabled": True},
        "devices": ["phone", "desktop"],
        "theme": "aura",
    }
    for dotted, value in over.items():
        section, _, field = dotted.partition("__")
        if field:
            answers[section][field] = value
        else:
            answers[section] = value
    return answers


# ---- validation -----------------------------------------------------------------------------

def test_good_answers_become_one_flat_set_of_lines():
    env, errors = srv.validate(good(), FACTS)
    assert errors == {}
    assert env["SETUP_BIND"] == "192.0.2.10"
    assert env["SETUP_PUBLIC_URL"] == "http://192.0.2.10:8643"       # the default follows the address
    assert env["SETUP_TZ"] == "Europe/Berlin"
    assert env["SETUP_MODEL"] == "endpoint" and env["SETUP_LLM_MODEL"] == "qwen3:8b"
    assert env["SETUP_TTS"] == "piper" and env["SETUP_STT"] == "small"
    assert env["SETUP_WAKE"] == "on" and "SETUP_WAKE_WORD" not in env   # the word follows the theme
    assert env["SETUP_DEVICES"] == "phone,desktop" and env["SETUP_THEME"] == "aura"
    assert env["SETUP_CONSOLE_HASH"].startswith("scrypt$16384$8$1$")
    assert "correct horse" not in "\n".join(env.values())


@pytest.mark.parametrize("field,value", [
    ("server__bind", "0.0.0.0"),                      # every interface: never
    ("server__bind", "224.0.0.1"),
    ("server__bind", "192.0.2.10\nLLM_URL=http://evil.example"),
    ("server__bind", "not an address"),
    ("server__public_url", "javascript:alert(1)"),
    ("server__public_url", "http://192.0.2.10:8643/$(id)"),
    ("server__timezone", "Europe/Berlin\nX=1"),
    ("server__timezone", "../../etc/passwd"),
    ("server__password", "short"),
    ("models__mode", "cloud"),
    ("models__url", "file:///etc/passwd"),
    ("models__url", "http://192.0.2.20/v1 --extra"),
    ("models__model", "a model with `ticks`"),
    ("models__key", "sk-abc\nAURA_TOKEN=x"),
    ("models__key", 'sk-"quoted"'),
    ("models__key", "sk-$(id)"),
    ("voice__tts", "espeak"),
    ("voice__stt", "enormous"),
    ("wake__enabled", "yes; rm -rf /"),
    ("voice__tts", "none"),
    ("devices", ["phone", "toaster"]),
    ("theme", "matrix"),
])
def test_a_bad_answer_is_named_and_nothing_is_written(field, value, tmp_path):
    env, errors = srv.validate(good(**{field: value}), FACTS)
    assert env == {}
    assert list(errors) == [field.replace("__", ".")], errors


def test_what_needs_a_graphics_card_is_refused_without_one():
    for field, value in (("models__mode", "local"), ("voice__tts", "kokoro")):
        _env, errors = srv.validate(good(**{field: value}), FACTS)
        assert list(errors) == [field.replace("__", ".")]
        env, errors = srv.validate(good(**{field: value}), {**FACTS, "gpu": True})
        assert errors == {} and env


def test_no_value_can_start_a_line_a_quote_or_a_command():
    env, errors = srv.validate(good(models__key="sk-" + "Ab1_" * 12 + "~+/=.-"), FACTS)
    assert errors == {}
    for key, value in env.items():
        assert srv.SAFE_VALUE.fullmatch(value), key
        assert not set(value) & set("\n\r\"'`\\ #;&|<>(){}*?!"), key
    # The hash is the one value with a dollar in it, and it is the console's own format.
    assert [k for k, v in env.items() if "$" in v] == ["SETUP_CONSOLE_HASH"]


def test_the_password_hash_is_the_one_the_console_verifies():
    """tests/contracts/console-password.json binds this to jarvis-web's verifyPassword
    (consoleAuth.test.ts reads the same table)."""
    table = json.loads((ROOT / "tests" / "contracts" / "console-password.json").read_text(encoding="utf-8"))
    for row in table["rows"]:
        line = srv.hash_password(row["password"], base64.b64decode(row["salt"]))
        assert line == row["line"], row["password"]
    one, two = srv.hash_password("correct horse battery"), srv.hash_password("correct horse battery")
    assert one != two                                  # a fresh salt each time


def test_answers_are_written_private_and_whole(tmp_path):
    env, _ = srv.validate(good(), FACTS)
    dest = tmp_path / "state" / "answers.env"
    srv.write_env(dest, env)
    assert stat.S_IMODE(dest.stat().st_mode) == 0o600
    assert stat.S_IMODE(dest.parent.stat().st_mode) == 0o700
    lines = dest.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "SETUP_VERSION=1"
    assert sorted(line.split("=", 1)[0] for line in lines[1:]) == sorted(env)
    assert not list(dest.parent.glob("*.tmp*"))        # written beside and renamed, nothing left over


# ---- progress ---------------------------------------------------------------------------------

def test_progress_is_read_as_data_and_only_known_words_pass(tmp_path):
    path = tmp_path / "progress.json"
    assert srv.read_progress(path) == {"phase": "waiting", "state": "running", "done": [], "detail": "", "console": ""}
    path.write_text(json.dumps({"phase": "images", "state": "running", "done": ["preflight", "setup", "secrets"],
                                "detail": "building jarvis-web (3 of 6)", "console": ""}))
    assert srv.read_progress(path)["detail"] == "building jarvis-web (3 of 6)"
    path.write_text(json.dumps({"phase": "<script>", "state": "running", "done": ["x", "start"],
                                "detail": "<img src=x onerror=1>", "console": "javascript:1"}))
    assert srv.read_progress(path) == {"phase": "waiting", "state": "running", "done": ["start"], "detail": "", "console": ""}
    path.write_text("{ half a file")
    assert srv.read_progress(path)["phase"] == "waiting"   # caught mid-write: the next poll reads it whole
    path.write_text(json.dumps({"phase": "done", "state": "done", "done": list(srv.PHASES),
                                "detail": "", "console": "http://192.0.2.10:8199"}))
    assert srv.read_progress(path)["console"] == "http://192.0.2.10:8199"


# ---- the endpoint test ------------------------------------------------------------------------

class _Stub(BaseHTTPRequestHandler):
    seen: list[tuple[str, str]] = []

    def do_GET(self):  # noqa: N802 - the base class's name
        type(self).seen.append((self.path, self.headers.get("Authorization", "")))
        if self.path.startswith("/v1/models"):
            body = json.dumps({"data": [{"id": "qwen3:8b"}, {"id": "tiny"}]}).encode()
            self.send_response(200)
        elif self.path.startswith("/locked/models"):
            body, _ = b"{}", self.send_response(401)
        else:
            body, _ = b"<html>not an api</html>", self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_a):
        pass


@pytest.fixture
def stub():
    _Stub.seen = []
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Stub)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def test_the_endpoint_test_lists_models_and_says_why_it_failed(stub):
    key = "sk-" + "made" + "up"
    ok = srv.probe_endpoint(stub + "/v1", key, "qwen3:8b")
    assert ok == {"ok": True, "models": ["qwen3:8b", "tiny"], "has_model": True, "error": ""}
    assert _Stub.seen[-1] == ("/v1/models", "Bearer " + key)
    assert srv.probe_endpoint(stub + "/v1", "", "other")["has_model"] is False
    assert _Stub.seen[-1][1] == ""                               # no key, no header
    refused = srv.probe_endpoint(stub + "/locked", key, "x")
    assert refused["ok"] is False and "401" in refused["error"] and key not in json.dumps(refused)
    assert srv.probe_endpoint(stub + "/html", key, "x")["ok"] is False
    assert srv.probe_endpoint("file:///etc/passwd", key, "x") == {
        "ok": False, "models": [], "has_model": False, "error": "the address must start with http:// or https://"}
    dead = srv.probe_endpoint("http://127.0.0.1:9/v1", key, "x")
    assert dead["ok"] is False and dead["error"] and key not in dead["error"]


# ---- the server -------------------------------------------------------------------------------

@pytest.fixture
def running(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    (state / "facts.json").write_text(json.dumps(FACTS))
    httpd = srv.make_server("127.0.0.1", 0, token=TOKEN, state=state, src=ROOT)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{port}", state
    httpd.shutdown()


def _call(base: str, path: str, *, token: str | None = TOKEN, body: Any = None, host: str | None = None,
          method: str | None = None) -> tuple[int, dict[str, str], bytes]:
    req = urllib.request.Request(base + path, data=None if body is None else json.dumps(body).encode(),
                                 method=method or ("POST" if body is not None else "GET"))
    if token is not None:
        req.add_header("Authorization", "Bearer " + token)
    if body is not None:
        req.add_header("Content-Type", "application/json")
    if host is not None:
        req.add_header("Host", host)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as err:
        return err.code, dict(err.headers), err.read()


def test_the_page_is_served_and_the_api_needs_the_token(running):
    base, _state = running
    status, headers, page = _call(base, "/?t=" + TOKEN, token=None)
    assert status == 200 and b"<title>" in page
    assert TOKEN.encode() not in page                          # the page never carries the token back
    assert "default-src 'self'" in headers["Content-Security-Policy"]
    assert headers["Cache-Control"] == "no-store" and headers["Referrer-Policy"] == "no-referrer"
    for path in ("/api/state", "/api/progress"):
        assert _call(base, path, token=None)[0] == 401
        assert _call(base, path, token="wrong" * 8)[0] == 401
        assert _call(base, path)[0] == 200
    assert _call(base, "/api/answers", token=None, body=good())[0] == 401
    assert _call(base, "/api/state?t=" + TOKEN, token=None)[0] == 401     # the header, never the address


def test_a_request_under_another_name_is_refused(running):
    base, _state = running
    assert _call(base, "/api/state", host="evil.example")[0] == 403
    assert _call(base, "/", token=None, host="evil.example:8198")[0] == 403
    port = base.rsplit(":", 1)[1]
    assert _call(base, "/api/state", host="localhost:" + port)[0] == 200


def test_state_says_what_the_machine_has_and_what_may_be_chosen(running):
    base, _state = running
    state = json.loads(_call(base, "/api/state")[2])
    assert state["facts"]["addresses"] == ["192.0.2.10"] and state["facts"]["gpu"] is False
    assert state["choices"]["themes"] == ["aura", "jarvis"]
    assert state["choices"]["tts"] == ["kokoro", "piper"] and "small" in state["choices"]["stt"]
    assert state["answered"] is False


def test_answers_are_checked_written_once_and_never_read_back(running):
    base, state = running
    status, _h, body = _call(base, "/api/answers", body=good(server__bind="0.0.0.0"))
    assert status == 422 and list(json.loads(body)["errors"]) == ["server.bind"]
    assert not (state / "answers.env").exists()

    key = "sk-" + "Ab1" * 10
    status, _h, body = _call(base, "/api/answers", body=good(models__key=key))
    assert status == 200 and json.loads(body) == {"ok": True}
    written = (state / "answers.env").read_text(encoding="utf-8")
    assert f"SETUP_LLM_KEY={key}" in written and "correct horse" not in written
    assert stat.S_IMODE((state / "answers.env").stat().st_mode) == 0o600

    after = _call(base, "/api/state")[2].decode()
    assert json.loads(after)["answered"] is True
    assert key not in after and "scrypt$" not in after          # neither comes back
    # The installer has started on these answers: a second set would change them under it.
    assert _call(base, "/api/answers", body=good(theme="jarvis"))[0] == 409
    assert "SETUP_THEME=aura" in (state / "answers.env").read_text(encoding="utf-8")


def test_oversized_and_malformed_bodies_are_refused(running):
    base, state = running
    assert _call(base, "/api/answers", body={"server": "x" * 100_000})[0] == 413
    req = urllib.request.Request(base + "/api/answers", data=b"{ not json", method="POST",
                                 headers={"Authorization": "Bearer " + TOKEN})
    with pytest.raises(urllib.error.HTTPError) as err:
        urllib.request.urlopen(req, timeout=10)
    assert err.value.code == 400
    assert _call(base, "/api/answers", body=["a", "list"])[0] == 422
    assert not (state / "answers.env").exists()


def test_only_named_files_are_served(running):
    base, _state = running
    for path in ("/setup.css", "/setup.js", "/tokens.css", "/themes.css", "/fonts.css",
                 "/fonts/jetbrains-mono-400.woff2"):
        assert _call(base, path, token=None)[0] == 200, path
    for path in ("/server.py", "/../server.py", "/fonts/../../../../README.md", "/fonts/%2e%2e/base.css",
                 "/.env", "/api/nothing", "/fonts/OFL-Sora.txt"):
        assert _call(base, path)[0] == 404, path
    assert _call(base, "/api/state", method="DELETE")[0] in (405, 501)


def test_the_log_never_holds_the_token_or_a_key(running, capfd):
    base, _state = running
    key = "sk-" + "Zz9" * 10
    _call(base, "/?t=" + TOKEN, token=None)
    _call(base, "/api/test-model", body={"url": "http://127.0.0.1:9/v1", "key": key, "model": "m"})
    _call(base, "/api/answers", body=good(models__key=key))
    out = "".join(capfd.readouterr())
    assert TOKEN not in out and key not in out and "correct horse" not in out


def test_many_wrong_tokens_close_the_api(running):
    base, _state = running
    for _ in range(srv.MAX_BAD_TOKENS):
        assert _call(base, "/api/state", token="x" * 40)[0] == 401
    assert _call(base, "/api/state")[0] == 429               # even the right one: start the install again


def test_requests_that_offer_no_token_never_close_the_api(running):
    """A page open in another tab can send this address requests, never an Authorization
    header. Counted as wrong tokens, thirty of them locked a person out of their own setup."""
    base, _state = running
    for _ in range(srv.MAX_BAD_TOKENS + 5):
        assert _call(base, "/api/state", token=None)[0] == 401
    assert _call(base, "/api/state")[0] == 200


def test_a_line_that_is_no_request_is_refused_and_the_server_goes_on(running, capfd):
    base, _state = running
    with socket.create_connection(("127.0.0.1", int(base.rsplit(":", 1)[1])), timeout=5) as s:
        s.sendall(b"\x16\x03\x01 this is not http\r\n\r\n")
        reply = s.recv(4096)
    assert b" 400 " in reply.split(b"\r\n", 1)[0], reply[:80]
    assert "Traceback" not in "".join(capfd.readouterr())
    assert _call(base, "/api/state")[0] == 200


def _listening(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(1)
        return s.connect_ex(("127.0.0.1", port)) == 0


def test_the_server_ends_when_the_installer_that_started_it_is_gone(tmp_path):
    """A killed installer runs no cleanup: the page it started must not keep the port, or the
    next run finds it taken. Not covered: the page served from a container, which the next
    run removes by name."""
    state = tmp_path / "state"
    state.mkdir()
    (state / "facts.json").write_text(json.dumps(FACTS))
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = ROOT / "deploy" / "setup" / "server.py"
    # The shell is the installer: it starts the page, says its pid, and holds on until told to go.
    shell = subprocess.Popen(
        ["bash", "-c", f'"{sys.executable}" "{server}" --state "{state}" --port {port} >/dev/null 2>&1 & echo $!; read -r _'],
        env={**os.environ, "AURA_SETUP_TOKEN": TOKEN}, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
    )
    assert shell.stdout is not None and shell.stdin is not None
    pid = int(shell.stdout.readline())
    try:
        deadline = time.monotonic() + 15
        while not _listening(port):
            assert time.monotonic() < deadline, "the page never started"
            time.sleep(0.1)
        shell.stdin.close()                           # the installer is gone, with no cleanup
        shell.wait(timeout=10)
        deadline = time.monotonic() + 15
        while _listening(port):
            assert time.monotonic() < deadline, "the page outlived the shell that started it"
            time.sleep(0.2)
    finally:
        shell.kill()
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def test_a_weak_token_is_refused_at_start(tmp_path):
    with pytest.raises(ValueError):
        srv.make_server("127.0.0.1", 0, token="short", state=tmp_path, src=ROOT)
    with pytest.raises(ValueError):
        srv.make_server("0.0.0.0", 0, token=TOKEN, state=tmp_path, src=ROOT)


def test_a_time_zone_that_does_not_exist_is_refused_where_it_can_be_looked_up(tmp_path, monkeypatch):
    """A made-up zone has the right shape; the container would keep UTC and say nothing."""
    answers = good(server__timezone="Nowhere/Atlantis")
    if srv.zone_exists("Europe/Berlin") and not srv.zone_exists("Nowhere/Atlantis"):
        _clean, errors = srv.validate(answers, FACTS)
        assert "no such time zone" in errors["server.timezone"]
        assert not srv.zone_exists("Europe")             # a folder of zones is not a zone
    # A machine with no zone database cannot look a name up, and must not refuse every answer.

    def absent(_name):
        raise srv.zoneinfo.ZoneInfoNotFoundError("no database")

    monkeypatch.setattr(srv.zoneinfo, "ZoneInfo", absent)
    _clean, errors = srv.validate(answers, FACTS)
    assert "server.timezone" not in errors


def test_the_page_is_under_the_token_lint_and_types_no_colour_outside_it():
    """The lint reads the page's stylesheet; its script and markup are not the lint's kind of
    file, so a colour typed there is refused here. Not covered: a size or a duration typed in
    the script. A colour function fed from a token (the canvas fades a token's hex) is not a
    typed colour: only one that starts with a number is."""
    lint = (ROOT / "scripts" / "verify" / "token_lint.py").read_text(encoding="utf-8")
    roots = re.search(r"^WEB_ROOTS = \((.*)\)$", lint, re.M)
    assert roots and '"deploy/setup"' in roots.group(1)
    for name in ("setup.js", "index.html"):
        text = (ROOT / "deploy" / "setup" / name).read_text(encoding="utf-8")
        typed = re.findall(r"#[0-9a-fA-F]{6}\b|#[0-9a-fA-F]{3}\b(?![\w-])|\b(?:rgb|hsl)a?\(\s*[0-9]", text)
        assert typed == [], (name, typed[:5])
