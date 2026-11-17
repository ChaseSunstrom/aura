"""The local extraction service's contract: routes, shapes, refusals, real extraction.

Two layers on purpose. The wire layer (routing, JSON errors, the caps) is tested
against a real socket with no Trafilatura involved, so a checkout without the
extractor still proves the contract Hermes' provider is written against. The
extraction layer runs the real Trafilatura on the pages the JCB fixture serves —
that is the part that would silently degrade (a table flattened into loose words,
a list dropped) without anyone noticing until a benchmark number moved.
"""
from __future__ import annotations

import importlib.util
import json
import threading
import urllib.error
import urllib.request
from http.server import HTTPServer
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
SERVICE_PATH = ROOT / "deploy" / "extract" / "service.py"


def _load_service() -> Any:
    """Import ``deploy/extract/service.py`` by path — it is a container entry
    point, not an installed package, and giving it an ``__init__.py`` would make
    it look like one."""
    spec = importlib.util.spec_from_file_location("jarvis_extract_service", SERVICE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


service = _load_service()

#: The extractor is a container dependency (deploy/extract/requirements.txt), not a
#: repo one, so a bare checkout may not have it. The wire tests below run either way;
#: only the ones that assert on extracted text need it, and they say so rather than
#: passing vacuously.
_HAS_EXTRACTOR = importlib.util.find_spec("trafilatura") is not None
needs_extractor = pytest.mark.skipif(
    not _HAS_EXTRACTOR,
    reason="pip install -r deploy/extract/requirements.txt to run the extraction tests",
)


@pytest.fixture()
def server():
    """The service on an ephemeral port, torn down after the test."""
    httpd = HTTPServer(("127.0.0.1", 0), service.Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()


def _post(base: str, path: str, body: Any) -> tuple[int, dict]:
    request = urllib.request.Request(
        f"{base}{path}",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def _get(base: str, path: str) -> tuple[int, dict]:
    try:
        with urllib.request.urlopen(f"{base}{path}", timeout=10) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


# --------------------------------------------------------------------------- wire


@needs_extractor
def test_health_names_the_extractor(server):
    """The healthcheck reports which extractor is installed, so an image whose
    pip install silently failed is unhealthy rather than quietly useless."""
    status, body = _get(server, "/health")
    assert status == 200
    assert body["extractor"] == "trafilatura"
    assert body["status"] == "ok"
    assert body["version"] != "unknown"


def test_unknown_route_is_json_not_html(server):
    status, body = _get(server, "/nope")
    assert status == 404 and "no route" in body["error"]


@needs_extractor
def test_extract_from_html_needs_no_network(server):
    status, body = _post(server, "/extract", {"html": "<html><body><p>Hello there.</p></body></html>"})
    assert status == 200
    assert "Hello there." in body["results"][0]["content"]


def test_a_body_without_urls_or_html_says_what_to_send(server):
    status, body = _post(server, "/extract", {})
    assert status == 400
    assert "urls" in body["error"] and "html" in body["error"]


def test_a_non_http_scheme_is_refused_rather_than_read(server):
    """``file:///etc/passwd`` must not become an 'article'. The refusal is a
    per-URL error, so a batch with one bad entry still returns the rest."""
    status, body = _post(server, "/extract", {"urls": ["file:///etc/passwd", "not-a-url"]})
    assert status == 200
    assert all("only http and https" in r["error"] for r in body["results"])


def test_too_many_urls_names_the_cap(server):
    status, body = _post(server, "/extract", {"urls": [f"http://x/{i}" for i in range(service.MAX_URLS + 1)]})
    assert status == 400 and str(service.MAX_URLS) in body["error"]


def test_results_are_positional(server):
    """One result per requested URL, in order — the provider zips them against
    its own list, so a dropped failure would misalign every later page."""
    urls = ["file:///a", "file:///b", "file:///c"]
    _, body = _post(server, "/extract", {"urls": urls})
    assert [r["url"] for r in body["results"]] == urls


def test_a_bad_json_body_is_a_400_not_a_traceback(server):
    request = urllib.request.Request(
        f"{server}/extract", data=b"{not json", headers={"Content-Type": "application/json"}, method="POST"
    )
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        urllib.request.urlopen(request, timeout=10)
    assert excinfo.value.code == 400


# ------------------------------------------------------------------- extraction


def _fixture_page(name: str) -> str:
    """One of the JCB fixture's pages, straight from the fixture module — the
    benchmark and this test must not drift onto different HTML."""
    import sys

    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from bench.fixtures import server as fixture

    return getattr(fixture, name)


@needs_extractor
def test_a_table_keeps_its_rows():
    """The regression that would be invisible: Trafilatura's default minimum
    extracted size drops a short page's structure and falls back to loose text,
    so the router comparison came back as a column of words with no way to tell
    which price belonged to which model."""
    out = service.extract_html(_fixture_page("_PRODUCTS"), "http://fixture.invalid/products")
    assert "| Kestrel Pro | £169 | 3 floors | 6 |" in out["content"]
    assert "|---|" in out["content"]


@needs_extractor
def test_a_list_survives():
    out = service.extract_html(_fixture_page("_RELEASE_NOTES"), "http://fixture.invalid/release-notes")
    for version in ("v3.4.1", "v3.4.0", "v3.3.2"):
        assert version in out["content"]


@needs_extractor
def test_boilerplate_is_dropped_but_the_answer_is_not():
    out = service.extract_html(_fixture_page("_FACTS"), "http://fixture.invalid/facts")
    assert "31.5" in out["content"] and "12 mm" in out["content"]


@needs_extractor
def test_the_title_comes_back_separately():
    """The heading, not the ``<title>`` tag: Trafilatura prefers the page's own
    h1, which is the more useful of the two when a site's title is boilerplate."""
    out = service.extract_html(_fixture_page("_FACTS"), "http://fixture.invalid/facts")
    assert out["title"] == "Greenhouse sensor log — this week"


@needs_extractor
def test_a_page_that_needs_javascript_comes_back_empty_not_wrong():
    """Documented limitation, pinned: no JS engine here. A client-rendered page
    yields nothing rather than the loading skeleton dressed up as the article —
    the browser toolset is the answer for those."""
    html = '<html><head><title>App</title></head><body><div id="root"></div><script>render()</script></body></html>'
    out = service.extract_html(html, "http://fixture.invalid/app")
    assert out["content"].strip() == ""


# --- Documents that were never HTML ------------------------------------------------------
#
# Trafilatura returns None for every binary format, and the provider turned that into "no
# readable text found (the page may render its content in JavaScript)". For a PDF that
# sentence is actively wrong in the expensive direction: it sends the agent to the browser
# toolset, which cannot read a PDF either, instead of to the pdf skill that can. The bench
# task pdf-read-004 is the one that pays for it.


def test_a_pdf_is_named_as_a_pdf_rather_than_called_an_empty_page():
    pdf = (ROOT / "bench" / "fixtures" / "documents" / "table.pdf").read_bytes()
    out = service.extract_html(pdf.decode("utf-8", errors="replace"), "http://fixture.invalid/t.pdf")
    assert "PDF" in out["error"], out
    assert out["content"] == ""
    # Megabytes of binary rendered as U+FFFD is worse for a model than nothing at all.
    assert out["raw_content"] == ""


def test_the_other_signatures_are_named_too():
    """A .docx arrives as a zip, and a static server that labels everything
    octet-stream is why this sniffs bytes rather than the Content-Type header."""
    cases = {"PK\x03\x04and the rest": "zip", "\x89PNG\r\n\x1a\n": "PNG", "%!PS-Adobe-3.0": "PostScript"}
    for document, expected in cases.items():
        assert expected in service.sniff_binary(document), document[:8]


# --- A failed page names why in one fixed word (T-1389) -------------------------------------
#
# ``kind`` is chosen by this service from where the failure happened, never from page text, so
# the plugin can relay it. Anything this code cannot place is "other".


class _Body:
    """A urlopen() result that yields *data* (or raises) and can be used in ``with``."""

    def __init__(self, data: bytes = b"", raises: Exception | None = None):
        self._data, self._raises = data, raises
        self.headers = type("H", (), {"get_content_charset": staticmethod(lambda: None)})()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self, n=-1):
        if self._raises:
            raise self._raises
        return self._data[:n]


def _fetch_with(monkeypatch, outcome) -> dict:
    """extract_url() with urlopen replaced: *outcome* is an exception to raise or a body."""
    def fake(request, timeout=None):
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(service.urllib.request, "urlopen", fake)
    return service.extract_url("http://page.example.test/x")


def _http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("http://page.example.test/x", code, "hostile text", {}, None)  # type: ignore[arg-type]


def test_a_pdf_result_has_kind_pdf():
    out = service.extract_html("%PDF-1.7 binary", "http://page.example.test/t.pdf")
    assert out["kind"] == "pdf"


def test_a_slow_page_has_kind_timeout(monkeypatch):
    assert _fetch_with(monkeypatch, TimeoutError("timed out"))["kind"] == "timeout"
    # urllib wraps a connect timeout in URLError; the reason is what says so.
    assert _fetch_with(monkeypatch, urllib.error.URLError(TimeoutError("timed out")))["kind"] == "timeout"
    assert _fetch_with(monkeypatch, _Body(raises=TimeoutError("read timed out")))["kind"] == "timeout"


@pytest.mark.parametrize("code", [401, 403, 429])
def test_a_refusing_server_has_kind_blocked(monkeypatch, code):
    assert _fetch_with(monkeypatch, _http_error(code))["kind"] == "blocked"


def test_an_oversize_body_has_kind_too_large(monkeypatch):
    monkeypatch.setattr(service, "MAX_BYTES", 16)
    assert _fetch_with(monkeypatch, _Body(b"x" * 64))["kind"] == "too_large"


def test_an_unknown_declared_charset_is_read_as_utf8_not_a_crash(monkeypatch):
    """T-1398: `bytes.decode` raises LookupError for a codec name it does not know."""
    body = _Body("café".encode())
    body.headers = type("H", (), {"get_content_charset": staticmethod(lambda: "x-made-up-charset")})()
    monkeypatch.setattr(service.urllib.request, "urlopen", lambda request, timeout=None: body)
    assert service.fetch("http://page.example.test/x") == "café"


def test_anything_else_has_kind_other(monkeypatch):
    assert _fetch_with(monkeypatch, _http_error(404))["kind"] == "other"
    assert _fetch_with(monkeypatch, urllib.error.URLError("connection refused"))["kind"] == "other"
    assert service.extract_url("file:///etc/passwd")["kind"] == "other"
    assert service.extract_html("\x89PNG\r\n\x1a\n", "http://page.example.test/i")["kind"] == "other"


def test_a_good_page_has_no_kind(monkeypatch):
    out = _fetch_with(monkeypatch, _Body(b"<html><body><p>Hello there.</p></body></html>"))
    assert "error" not in out and "kind" not in out


def test_html_is_not_mistaken_for_a_document():
    """The guard runs on every extraction, so a false positive would break the service."""
    for page in ("<html><body><p>ordinary</p></body></html>", "<!DOCTYPE html>\n<p>x</p>",
                 "  \n<html>", "%PD", ""):
        assert service.sniff_binary(page) == "", page[:16]
