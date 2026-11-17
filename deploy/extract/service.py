"""jarvis-extract — the stack's local article-extraction service (V2 §4, D19).

One job: turn a URL or a blob of HTML into the readable text of the page, with
its title and a little metadata, and nothing else. It exists so Hermes'
``web_extract`` has a backend that runs on this network. Without one the
provider registry falls through its keyless tier to Exa's and Parallel's public
MCP endpoints — cloud services, over the exit node, carrying whatever URL the
agent was reading. "Fully local" has to include the part that reads the page.

Why Trafilatura rather than the alternatives the repo already measured:

* Crawl4AI — 4.23 GB image, 411 MB resident, and it refuses loopback targets
  (``docs/explanation/TOOLING_DECISIONS.md``). A homelab assistant reads its own
  tailnet constantly; a backend that will not fetch a private address is the
  wrong shape for this stack.
* Docling — resolves to 101 packages including the CUDA stack, on hosts with no
  GPU to give it.
* Self-hosted Firecrawl — the option the v2 plan §4 named first. It is an
  API server, a worker, a Redis and a Playwright service; it wants a real
  browser to render every page and an API key to satisfy its own validation.
  Four containers and a browser to read an article is not a small service.
* Trafilatura — one pip install (lxml plus pure Python), no browser, no model,
  no GPU, and the best main-content scores in the published extractor
  comparisons. It also keeps a table's rows, which the retired jarvis-browser
  extractor was specifically kept for.

What this service does NOT do, so nobody discovers it the hard way: it does not
run JavaScript (a page that renders its content client-side comes back nearly
empty — that is what the browser toolset is for), it does not do OCR, it does
not follow links or crawl, and it does not read PDFs or office documents: the
``pdf``, ``docx`` and ``xlsx`` skills bundled in the JARVIS image own those, and
handing one of those files here now returns an error that says so by name
(:func:`sniff_binary`) rather than an empty page. It is not an authorization
boundary either: it fetches whatever URL it is handed, so it must stay on the
stack network with no published port. The SSRF decision belongs to the caller.

Wire protocol (deliberately tiny, so the Hermes provider is thin):

    GET  /health   -> {"status": "ok", "extractor": "trafilatura", ...}
    POST /extract  -> {"results": [ {url, title, content, raw_content,
                                     metadata, error?}, ... ]}

    request body:   {"urls": ["https://…", …]}          # fetch and extract
                    {"url": "https://…"}                 # one URL, same thing
                    {"html": "<html>…", "url": "https://…"}   # extract only

The result list is positional: one entry per requested URL, in order, so a
caller can zip it against its own list. A per-URL failure is an ``error`` string
on that entry, never a non-200 for the whole batch — one dead link must not cost
the caller the other four pages. It also carries ``kind``, one of :data:`KINDS`,
the only part of a failure a caller may relay: ``error`` can quote the page.

Run it: ``python3 -m deploy.extract.service`` or ``python3 service.py``
(``EXTRACT_PORT``, ``EXTRACT_BIND``, ``EXTRACT_FETCH_TIMEOUT``,
``EXTRACT_MAX_BYTES``, ``EXTRACT_MAX_URLS`` tune it; the container sets none of
them and takes the defaults).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

_LOGGER = logging.getLogger("jarvis-extract")

#: Trafilatura's own default (250 chars) drops a short page's structure and falls
#: back to a cruder text pass — the fixture's router-comparison table came back as
#: a column of loose words instead of markdown rows, and a release-notes list lost
#: every item. The pages a homelab assistant reads are often that short, so the
#: minimums go to zero and the extractor keeps its structured output.
_SIZE_KEYS = (
    "MIN_EXTRACTED_SIZE",
    "MIN_OUTPUT_SIZE",
    "MIN_EXTRACTED_COMM_SIZE",
    "MIN_OUTPUT_COMM_SIZE",
)

#: Cap on a fetched body. A page is text; anything past this is a download, and
#: reading it into memory is how a 1 GB "article" takes the container's limit.
MAX_BYTES = int(os.environ.get("EXTRACT_MAX_BYTES", 8 * 1024 * 1024))
#: Per-URL fetch timeout. Short on purpose: the caller has its own deadline and a
#: hung upstream must not hold the connection until Hermes gives up on us.
FETCH_TIMEOUT = float(os.environ.get("EXTRACT_FETCH_TIMEOUT", 20.0))
#: Most URLs one request may ask for. An agent that pastes a hundred links gets a
#: refusal naming the cap rather than a request that runs for ten minutes.
MAX_URLS = int(os.environ.get("EXTRACT_MAX_URLS", 20))
#: Identifies the fetcher in someone else's access log. Not a stealth tool.
USER_AGENT = os.environ.get("EXTRACT_USER_AGENT", "jarvis-extract/1.0 (+local)")

_ALLOWED_SCHEMES = frozenset({"http", "https"})


#: The closed set a failed page's ``kind`` comes from. Chosen here, from where the failure
#: happened, never from page or server text, so a caller may relay it; the provider keeps its own
#: copy of the words and reads anything else as "other".
KINDS = ("pdf", "timeout", "blocked", "too_large", "other")
#: Status codes that mean "the site said no to this fetcher" rather than "no such page".
_BLOCKED_STATUS = frozenset({401, 403, 429, 451})


class ExtractError(ValueError):
    """A request this service refuses, with the reason in the message and a ``kind`` (see :data:`KINDS`)."""

    def __init__(self, message: str, kind: str = "other") -> None:
        super().__init__(message)
        self.kind = kind


def _failed(url: str, message: str, kind: str) -> dict[str, Any]:
    return {"url": url, "title": "", "content": "", "raw_content": "", "metadata": {},
            "error": message, "kind": kind}


def _trafilatura() -> Any:
    """Import Trafilatura, or raise :class:`ExtractError` naming the fix.

    Imported here rather than at module scope so the HTTP layer stays testable
    (and importable) in a checkout that has not installed the extractor — the
    container always has it.
    """
    try:
        import trafilatura  # noqa: PLC0415 — deliberate lazy import, see docstring
    except ImportError as exc:  # pragma: no cover — the image always has it
        raise ExtractError(
            "trafilatura is not installed in this image; "
            "rebuild deploy/extract/Dockerfile"
        ) from exc
    return trafilatura


def _config() -> Any:
    """Trafilatura settings with the minimum-size floors removed (see :data:`_SIZE_KEYS`)."""
    from copy import deepcopy

    from trafilatura.settings import DEFAULT_CONFIG  # noqa: PLC0415 — with the lazy import

    cfg = deepcopy(DEFAULT_CONFIG)
    for key in _SIZE_KEYS:
        cfg["DEFAULT"][key] = "0"
    return cfg


def fetch(url: str) -> str:
    """Return the body of *url* as text, or raise :class:`ExtractError`.

    Plain ``urllib`` rather than a client library: this is one GET with a
    timeout and a size cap, and the container's dependency list is the thing
    being kept small. Only http/https — a ``file://`` URL here would read the
    container's filesystem into the model's context.
    """
    scheme = url.split(":", 1)[0].lower() if ":" in url else ""
    if scheme not in _ALLOWED_SCHEMES:
        raise ExtractError(f"refusing scheme {scheme or '(none)'!r}: only http and https")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=FETCH_TIMEOUT) as response:  # noqa: S310 — scheme checked above
            raw = response.read(MAX_BYTES + 1)
            charset = response.headers.get_content_charset() or "utf-8"
    except urllib.error.HTTPError as exc:
        raise ExtractError(f"HTTP {exc.code} from {url}",
                           "blocked" if exc.code in _BLOCKED_STATUS else "other") from exc
    except (urllib.error.URLError, OSError, ValueError) as exc:
        # A connect timeout arrives wrapped in URLError, a read timeout bare; both are TimeoutError.
        timed_out = isinstance(getattr(exc, "reason", exc), TimeoutError)
        raise ExtractError(f"fetch failed: {exc}", "timeout" if timed_out else "other") from exc
    if len(raw) > MAX_BYTES:
        raise ExtractError(f"body exceeds {MAX_BYTES} bytes; refusing to read a download as an article",
                           "too_large")
    try:
        return raw.decode(charset, errors="replace")
    except LookupError:
        # A header naming a charset Python does not know (a typo, a vendor name) is not an
        # ExtractError, so it failed the whole batch with a 500 (T-1398). Read as no charset.
        return raw.decode("utf-8", errors="replace")


#: Leading bytes that mean "this was never an HTML page", and the name to say instead.
#: Trafilatura returns ``None`` for every one of them, which the caller can only report as an
#: empty page — and an empty page sends an agent to the browser toolset, which cannot read
#: these either. Naming the format is what lets it reach for the right reader. Keyed on the
#: decoded prefix because :func:`fetch` decodes with ``errors="replace"``; each signature here
#: is ASCII or C0 control bytes, which survive that unchanged in every charset in use.
_BINARY_SIGNATURES: tuple[tuple[str, str], ...] = (
    ("%PDF-", "a PDF"),
    ("PK\x03\x04", "a zip container (.docx, .xlsx, .pptx or an archive)"),
    ("\xd0\xcf\x11\xe0", "a legacy Office document (.doc, .xls)"),
    ("\x89PNG", "a PNG image"),
    ("GIF8", "a GIF image"),
    ("\xff\xd8\xff", "a JPEG image"),
    ("%!PS", "a PostScript document"),
)


def sniff_binary(document: str) -> str:
    """The human name of the non-HTML format *document* starts with, or ``""``.

    Deliberately a prefix test on the first few bytes rather than the response's
    ``Content-Type``: a static file server that labels everything
    ``application/octet-stream`` is common, and the bytes cannot lie about themselves.
    """
    head = document[:8]
    for signature, name in _BINARY_SIGNATURES:
        if head.startswith(signature):
            return name
    return ""


def extract_html(html: str, url: str = "") -> dict[str, Any]:
    """Extract one document. Returns the per-URL result shape described above.

    ``raw_content`` is the HTML as received, capped: Hermes' own post-processing
    reads it when the caller asked for raw, and a caller that did not ask still
    pays for it in the JSON, so it is bounded rather than unbounded.
    """
    binary = sniff_binary(html)
    if binary:
        # Returning early rather than letting Trafilatura decline: the raw_content of a PDF
        # is megabytes of binary rendered as replacement characters, and handing that to a
        # model is worse than handing it nothing.
        return _failed(url, f"this is {binary}, not an HTML page — extraction reads HTML only. "
                            "Fetch the file and read it with the matching document skill.",
                       "pdf" if binary == "a PDF" else "other")
    trafilatura = _trafilatura()
    cfg = _config()
    content = trafilatura.extract(
        html,
        url=url or None,
        output_format="markdown",
        include_tables=True,
        include_formatting=True,
        include_links=True,
        favor_recall=True,
        config=cfg,
    )
    meta: dict[str, Any] = {}
    title = ""
    try:
        doc = trafilatura.extract_metadata(html, default_url=url or None)
    except Exception as exc:  # noqa: BLE001 — metadata is a nicety, content is the job
        _LOGGER.debug("metadata extraction failed for %s: %s", url or "(inline html)", exc)
        doc = None
    if doc is not None:
        as_dict = doc.as_dict() if hasattr(doc, "as_dict") else {}
        title = str(as_dict.get("title") or "")
        meta = {
            key: value
            for key, value in as_dict.items()
            if key in ("author", "date", "description", "sitename", "hostname", "categories", "tags")
            and value
        }
    return {
        "url": url,
        "title": title,
        "content": content or "",
        "raw_content": html[:MAX_BYTES],
        "metadata": meta,
    }


def extract_url(url: str) -> dict[str, Any]:
    """Fetch and extract one URL. A failure becomes an ``error`` on the result."""
    try:
        return extract_html(fetch(url), url)
    except ExtractError as exc:
        return _failed(url, str(exc), exc.kind)


def handle_extract(payload: dict[str, Any]) -> dict[str, Any]:
    """The ``POST /extract`` body → the response body. Raises :class:`ExtractError`.

    Split out from the HTTP handler so the contract can be tested without a
    socket, and so the shape lives in one place.
    """
    if not isinstance(payload, dict):
        raise ExtractError("body must be a JSON object")
    html = payload.get("html")
    if html is not None:
        if not isinstance(html, str):
            raise ExtractError("'html' must be a string")
        url = payload.get("url") or ""
        if not isinstance(url, str):
            raise ExtractError("'url' must be a string")
        return {"results": [extract_html(html, url)]}

    urls = payload.get("urls")
    if urls is None and isinstance(payload.get("url"), str):
        urls = [payload["url"]]
    if not isinstance(urls, list) or not urls:
        raise ExtractError("give 'urls' (a non-empty list), 'url', or 'html'")
    if not all(isinstance(u, str) and u for u in urls):
        raise ExtractError("'urls' must be a list of non-empty strings")
    if len(urls) > MAX_URLS:
        raise ExtractError(f"{len(urls)} URLs requested; the cap is {MAX_URLS}")
    return {"results": [extract_url(u) for u in urls]}


def health() -> dict[str, Any]:
    """The healthcheck body. Reports the extractor so a deploy cannot quietly
    ship an image whose extractor failed to install."""
    try:
        version = str(getattr(_trafilatura(), "__version__", "unknown"))
    except ExtractError as exc:
        return {"status": "degraded", "extractor": "trafilatura", "error": str(exc)}
    return {"status": "ok", "extractor": "trafilatura", "version": version}


class Handler(BaseHTTPRequestHandler):
    """The three routes. Errors are JSON so a caller never has to parse HTML."""

    server_version = "jarvis-extract/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        # Through the logger, so the container's lines look like its neighbours'
        # (json-file driver, one line per request) instead of BaseHTTPRequestHandler's
        # bare stderr writes. A URL can carry a token in a query string, so the
        # request line is logged, never the body.
        _LOGGER.info("%s %s", self.address_string(), fmt % args)

    def _send(self, status: int, obj: Any) -> None:
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_HEAD(self) -> None:  # noqa: N802 — BaseHTTPRequestHandler's naming
        self.do_GET()

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        if path in ("/health", "/healthz", "/"):
            return self._send(200, health())
        return self._send(404, {"error": f"no route {path}"})

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        if path != "/extract":
            return self._send(404, {"error": f"no route {path}"})
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BYTES:
            return self._send(413, {"error": f"body exceeds {MAX_BYTES} bytes"})
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except ValueError as exc:
            return self._send(400, {"error": f"body is not JSON: {exc}"})
        try:
            return self._send(200, handle_extract(payload))
        except ExtractError as exc:
            return self._send(400, {"error": str(exc)})
        except Exception as exc:  # noqa: BLE001 — a crash must not kill the server
            _LOGGER.exception("extract failed")
            return self._send(500, {"error": f"{type(exc).__name__}: {exc}"})


class _Server(ThreadingHTTPServer):
    #: Threads are daemons so a SIGTERM from the container runtime is not held
    #: open by an in-flight fetch that is waiting out its full timeout.
    daemon_threads = True
    #: Restart-after-crash must not lose the port to TIME_WAIT.
    allow_reuse_address = True


def serve(bind: str, port: int) -> None:
    """Serve until interrupted. Logs the address so the container's first line says where it is."""
    with _Server((bind, port), Handler) as httpd:
        _LOGGER.info("jarvis-extract listening on %s:%s (%s)", bind, port, health())
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            _LOGGER.info("shutting down")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Local article extraction for the JARVIS stack.")
    parser.add_argument("--bind", default=os.environ.get("EXTRACT_BIND", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("EXTRACT_PORT", 8896)))
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=os.environ.get("EXTRACT_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    serve(args.bind, args.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
