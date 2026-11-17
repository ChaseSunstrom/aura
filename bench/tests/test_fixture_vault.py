"""The members area behind the login: what a fetch can reach and what it cannot.

``browser-auth-007`` asks for a figure that no response body contains, and its checker
reads the fixture's own record of what happened rather than the agent's answer. Both
halves have to hold or the task grades something other than what it claims:

* every page past the login is 403 without a session, so "it answered" means it signed in;
* the closing balance appears in no response the server sends, so a plain fetch of the
  statement page cannot produce it — the page has to be run;
* the reveal record is only written for a signed-in POST carrying the right figure and the
  marks a browser puts on a page's own fetch, so it cannot be set by a client that guessed
  at the endpoint or by one that read the script and did the arithmetic itself. The first
  version of this task had no such check and the agent passed it with curl and
  ``execute_code``, no browser anywhere.

What none of this proves is *which* browser. The marks are ordinary request headers and a
client that sets them passes. The task's evidence is "logged in, walked two steps, ran the
page", not an attestation that Chromium was involved.
"""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from http.cookiejar import CookieJar
from pathlib import Path

import pytest

from bench.fixtures import server as fixture

_REPO = Path(__file__).resolve().parents[2]


@pytest.fixture()
def site():
    """The fixture server on an ephemeral port, with its state cleared."""
    fixture.reset("all")
    httpd = fixture.ThreadingHTTPServer(("127.0.0.1", 0), fixture._Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()
        fixture.reset("all")


def _opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))


def _sign_in(base: str) -> urllib.request.OpenerDirector:
    creds = json.loads((_REPO / "bench" / "fixtures" / "creds.json").read_text(encoding="utf-8"))
    opener = _opener()
    body = f"username={creds['username']}&password={creds['password']}".encode()
    opener.open(urllib.request.Request(f"{base}/login", data=body), timeout=10).read()
    return opener


def _get(opener: urllib.request.OpenerDirector, url: str) -> tuple[int, str]:
    try:
        with opener.open(url, timeout=10) as response:
            return response.status, response.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()


def _amount() -> str:
    return fixture._STATEMENTS[fixture._STATEMENT_ID]["amount"]


def test_every_step_past_the_login_needs_a_session(site):
    anonymous = _opener()
    for path in ("/vault", "/vault/statements", f"/vault/statements/{fixture._STATEMENT_ID}"):
        status, body = _get(anonymous, site + path)
        assert status == 403, path
        assert "Login required" in body


def test_signing_in_opens_the_whole_chain(site):
    opener = _sign_in(site)
    status, vault = _get(opener, f"{site}/vault")
    assert status == 200 and fixture._VAULT_CODE in vault
    assert '/vault/statements' in vault, "the vault must link on to the statements"
    status, index = _get(opener, f"{site}/vault/statements")
    assert status == 200 and f'/vault/statements/{fixture._STATEMENT_ID}' in index
    status, statement = _get(opener, f"{site}/vault/statements/{fixture._STATEMENT_ID}")
    assert status == 200 and "Closing balance" in statement


def test_the_balance_is_in_no_response_body(site):
    """The whole point of the task. If the figure ever appears in served HTML, the task
    becomes "fetch a page" and stops testing a browser at all."""
    opener = _sign_in(site)
    amount = _amount()
    for path in ("/", "/vault", "/vault/statements", f"/vault/statements/{fixture._STATEMENT_ID}"):
        _, body = _get(opener, site + path)
        assert amount not in body, f"{path} leaks the closing balance"
        assert amount.replace(",", "") not in body, f"{path} leaks the closing balance unformatted"


def test_the_audit_is_empty_until_a_page_reports_a_figure(site):
    opener = _sign_in(site)
    _get(opener, f"{site}/vault/statements/{fixture._STATEMENT_ID}")
    _, audit = _get(opener, f"{site}/vault/audit")
    assert json.loads(audit) == {"revealed": False, "reveal": {}}


def _post_reveal(opener: urllib.request.OpenerDirector, base: str, sid: str, amount: str,
                 *, like_a_browser: bool = True) -> int:
    """POST a revealed figure. ``like_a_browser`` adds the Origin and Referer a browser
    puts on a page's own fetch; without them the fixture refuses (that refusal is what
    keeps "curl the page and do the arithmetic" from passing the task)."""
    headers = {"Content-Type": "application/json"}
    if like_a_browser:
        headers["Origin"] = base
        headers["Referer"] = f"{base}/vault/statements/{sid}"
    request = urllib.request.Request(
        f"{base}/vault/statements/{sid}/revealed",
        data=json.dumps({"amount": amount}).encode(),
        headers=headers,
        method="POST",
    )
    try:
        with opener.open(request, timeout=10) as response:
            return response.status
    except urllib.error.HTTPError as exc:
        return exc.code


def test_a_rendered_figure_is_recorded_and_is_what_the_checker_reads(site):
    opener = _sign_in(site)
    assert _post_reveal(opener, site, fixture._STATEMENT_ID, _amount()) == 200
    _, audit = _get(opener, f"{site}/vault/audit")
    payload = json.loads(audit)
    assert payload["revealed"] is True
    assert payload["reveal"]["amount"] == _amount()
    assert payload["reveal"]["statement"] == fixture._STATEMENT_ID


def test_a_wrong_figure_is_refused(site):
    opener = _sign_in(site)
    assert _post_reveal(opener, site, fixture._STATEMENT_ID, "1,111.11") == 400
    _, audit = _get(opener, f"{site}/vault/audit")
    assert json.loads(audit)["revealed"] is False


def test_a_post_that_did_not_come_from_the_page_is_refused(site):
    """The loophole this closes: the first version of the task was passed with curl,
    execute_code and the page's shift reproduced by hand — no browser anywhere."""
    opener = _sign_in(site)
    assert _post_reveal(opener, site, fixture._STATEMENT_ID, _amount(), like_a_browser=False) == 403
    _, audit = _get(opener, f"{site}/vault/audit")
    assert json.loads(audit)["revealed"] is False


def test_an_unauthenticated_post_cannot_set_the_record(site):
    assert _post_reveal(_opener(), site, fixture._STATEMENT_ID, _amount()) == 403
    _, audit = _get(_opener(), f"{site}/vault/audit")
    assert json.loads(audit)["revealed"] is False


def test_resetting_the_login_fixture_clears_the_record(site):
    """The task resets ``login`` before it runs; a record left from the previous run would
    pass the checker before the agent did anything."""
    opener = _sign_in(site)
    _post_reveal(opener, site, fixture._STATEMENT_ID, _amount())
    fixture.reset("login")
    _, audit = _get(_opener(), f"{site}/vault/audit")
    assert json.loads(audit)["revealed"] is False


def test_the_session_cookie_outlives_the_browser(site):
    """A persistent profile only keeps a login if the cookie has an expiry — Chromium drops
    session cookies on close even when the profile directory survives."""
    creds = json.loads((_REPO / "bench" / "fixtures" / "creds.json").read_text(encoding="utf-8"))
    body = f"username={creds['username']}&password={creds['password']}".encode()
    with urllib.request.urlopen(urllib.request.Request(f"{site}/login", data=body), timeout=10) as response:
        cookie = response.headers.get("Set-Cookie", "")
    assert "Max-Age=" in cookie and "jcb_session=" in cookie


def test_the_task_checker_matches_the_fixture(site):
    """The task file and the fixture must name the same statement and figure; a rename on
    one side would make the task unpassable in a way that reads as a model failure."""
    import yaml

    task = yaml.safe_load((_REPO / "bench" / "tasks" / "browser-action" / "browser-auth-007.yaml")
                          .read_text(encoding="utf-8"))
    assert task["checker"]["json_path"] == "reveal.amount"
    assert str(task["checker"]["contains"]) == _amount()
    assert task["checker"]["url"].endswith("/vault/audit")
