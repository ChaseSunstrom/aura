"""The ``aura-extract`` web provider: ``web_extract`` → the stack's own service.

Hermes resolves ``web.extract_backend`` against its provider registry
(``agent/web_search_registry.py``). Every backend that ships with Hermes is a
cloud service — Firecrawl, Exa, Parallel, Keenable — and with none configured the
registry falls through to a *keyless* tier that calls Exa's and Parallel's public
MCP endpoints anonymously. There is no "local" option to select, so this is it:
a thin client for ``deploy/extract``, registered under a name the config can name.

Search is deliberately not implemented. SearXNG owns search
(``web.search_backend: searxng``), this owns extraction, and a provider that
claimed both would let one misconfiguration silently take over the other —
``supports_search()`` returning False is what makes the registry fall through to
SearXNG rather than to this.

The behaviour lives in module-level functions and the ABC subclass is a thin
adapter built lazily by :func:`provider_class`, the same shape
``comfyui-imagegen`` uses: ``agent.web_search_provider`` ships with Hermes, not
with this repo, so importing it at module scope would make the plugin
unimportable in the test suite and in any tool that reads the tree.

The service is trusted with the URL but not with the answer: it is on the stack
network with no published port, and Hermes has already run its own SSRF filter
over every URL before the dispatcher reaches a provider. What comes back is page
text an attacker may have written, and it is handed to Hermes' own
post-processing (fencing, char limits) unchanged.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

# `_shared/` is a sibling dir, reachable only via sys.path (the same shim the other plugins use).
_PLUGINS_ROOT = str(Path(__file__).resolve().parent.parent)
if _PLUGINS_ROOT not in sys.path:
    sys.path.append(_PLUGINS_ROOT)

from aura_schema import from_env  # noqa: E402

from _shared import _failure  # noqa: E402
from _shared._egress import ServiceUrlError, service_url  # noqa: E402

_LOGGER = logging.getLogger(__name__)

#: The name ``web.extract_backend`` matches. Same string as the plugin's, so an
#: operator reading ``plugins.enabled`` and ``web.extract_backend`` sees one word.
NAME = "aura-extract"
DISPLAY_NAME = "JARVIS local extraction (Trafilatura)"


def is_available() -> bool:
    """True when the service has an address and has not been switched off.

    No network call: the registry paints this on every ``hermes tools`` and on
    tool registration, so it must stay cheap. Settings are read per call rather
    than cached — the gateway multiplexes profiles and ``jsettings`` can change a
    value at runtime, so a cached URL would keep a session pointed at an endpoint
    the operator has already moved.
    """
    settings = from_env()
    return bool(settings.extract_enabled and settings.extract_url)


#: One fixed sentence per failure kind the service reports (``deploy/extract/service.py``
#: ``KINDS``). The kind is only ever a key into this table: a value outside it reads as "other".
_KIND_SENTENCES = {
    "pdf": "the page is a PDF, which the local extractor does not read: use the pdf skill",
    "timeout": "the page did not answer in time",
    "blocked": "the site refused the request",
    "too_large": "the page is too large to read as an article",
    "other": "the page could not be read by the local extraction service",
}


def error_result(url: str, message: str) -> dict[str, Any]:
    """The per-URL failure shape Hermes' dispatcher understands."""
    return {"url": url, "title": "", "content": "", "raw_content": "", "metadata": {}, "error": message}


def normalize(url: str, entry: Any) -> dict[str, Any]:
    """Force one service result into the shape Hermes documents.

    The URL is the one Hermes asked for, never the one the service reports: a
    redirect must not change which URL a result is filed under, because the
    caller zips results against its own list.
    """
    if not isinstance(entry, dict):
        return error_result(url, "extraction service returned a non-object result")
    out: dict[str, Any] = {
        "url": url,
        "title": str(entry.get("title") or ""),
        "content": str(entry.get("content") or ""),
        "raw_content": str(entry.get("raw_content") or ""),
        "metadata": entry.get("metadata") if isinstance(entry.get("metadata"), dict) else {},
    }
    error = entry.get("error")
    if error:
        # The service's own per-URL text is page-derived (it can quote what an attacker wrote), so
        # none of it is kept, clipped or not. Its ``kind`` is a word from a closed set it picks
        # itself; ours is the sentence. isinstance first: an unhashable value must not raise.
        kind = entry.get("kind")
        out["error"] = _KIND_SENTENCES[kind if isinstance(kind, str) and kind in _KIND_SENTENCES else "other"]
    elif not out["content"]:
        # An empty extraction is a real outcome (a page that renders itself in
        # JavaScript). Saying so beats handing the model a blank string it will
        # summarise as "the page was empty".
        out["error"] = "no readable text found (the page may render its content in JavaScript)"
    return out


def extract_urls(urls: list[str]) -> list[dict[str, Any]]:
    """Extract every URL in one request. Returns Hermes' per-URL result shape.

    A transport failure becomes one error entry per URL rather than an exception:
    the dispatcher shows the agent a per-URL ``error`` and lets it try something
    else, where a raised exception would lose the whole call.
    """
    import httpx

    settings = from_env()
    if not str(settings.extract_url or "").strip():
        return [error_result(url, "EXTRACT_URL is not set") for url in urls]
    try:
        base = service_url(settings.extract_url, "EXTRACT_URL")
    except ServiceUrlError as exc:  # our own fixed text; it never echoes a password
        return [error_result(url, str(exc)) for url in urls]
    try:
        response = httpx.post(
            f"{base}/extract",
            json={"urls": list(urls)},
            timeout=float(settings.extract_timeout),
        )
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:  # noqa: BLE001 — every transport failure reads the same to the agent
        # The model reads a status and a fixed hint, never the exception text (it quotes the
        # request address); the log keeps the class only for the same reason.
        _LOGGER.warning("aura-extract call failed: %s", type(exc).__name__)
        failed = _failure.of(exc)
        detail = failed["error"] if failed else f"local extraction service failed ({type(exc).__name__})"
        return [error_result(url, detail) for url in urls]

    results = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(results, list) or len(results) != len(urls):
        # Positional by contract; a length mismatch would silently pair the wrong
        # text with the wrong URL, which is worse than an error.
        got = len(results) if isinstance(results, list) else "no list"
        return [error_result(url, f"extraction service returned {got} results for {len(urls)} URLs")
                for url in urls]
    return [normalize(url, entry) for url, entry in zip(urls, results)]


def setup_schema() -> dict[str, Any]:
    """The row ``hermes tools`` draws for this backend."""
    return {
        "name": DISPLAY_NAME,
        "badge": "local",
        "tag": "Runs in the JARVIS stack. No API key, no cloud, no browser.",
        "env_vars": [{"key": "EXTRACT_URL", "prompt": "Extraction service URL", "url": ""}],
    }


def provider_class() -> type:
    """Build the ``WebSearchProvider`` subclass lazily (the ABC lives in Hermes)."""
    from agent.web_search_provider import WebSearchProvider

    class JarvisExtractProvider(WebSearchProvider):
        """``web_extract`` served by the stack's local extraction container."""

        @property
        def name(self) -> str:
            return NAME

        @property
        def display_name(self) -> str:
            return DISPLAY_NAME

        def is_available(self) -> bool:
            return is_available()

        def supports_search(self) -> bool:
            return False

        def supports_extract(self) -> bool:
            return True

        def extract(self, urls: list[str], **kwargs: Any) -> list[dict[str, Any]]:
            # kwargs (format, include_raw, max_chars) is ignored on purpose: the
            # service always returns markdown plus the raw HTML, and Hermes' own
            # post-processing picks what the caller asked for.
            return extract_urls(list(urls))

        def get_setup_schema(self) -> dict[str, Any]:
            return setup_schema()

    return JarvisExtractProvider
