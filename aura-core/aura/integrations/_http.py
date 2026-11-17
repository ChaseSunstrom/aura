"""Shared ``httpx.AsyncClient`` construction for the HTTP integrations.

Several integrations keep their client in ``jarvis.data[DOMAIN]`` the same way:
honour a client or transport a test injected, otherwise build one and remember
who owns it so shutdown only closes what this integration opened. Two shapes:

* :func:`cached_client` — one client *per TLS-verification setting* (``hue``,
  ``rest``): a single ``verify_ssl: false`` block must not switch certificate
  checking off for every other endpoint.
* :func:`single_client` — one client for the integration (``web``,
  ``orchestrator``), redirects off because the endpoints are on this host and a
  redirect is a reason to stop, not a hop to follow.

The per-integration ``create_client`` wrappers keep their own signatures,
defaults and docstrings; this holds only the caching dance they shared.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:  # pragma: no cover
    from ..core import Jarvis


def cached_client(
    jarvis: "Jarvis",
    domain: str,
    *,
    verify_ssl: bool,
    timeout: float,
    follow_redirects: bool = False,
) -> httpx.AsyncClient:
    """A client kept per ``verify_ssl`` setting, honouring test injection."""
    store = jarvis.data.setdefault(domain, {})
    injected = store.get("client")
    if injected is not None:
        store.setdefault("owns_client", False)
        return injected

    clients: dict[bool, httpx.AsyncClient] = store.setdefault("clients", {})
    key = bool(verify_ssl)
    client = clients.get(key)
    if client is not None:
        return client

    transport = store.get("transport")
    client = httpx.AsyncClient(
        transport=transport,
        timeout=httpx.Timeout(timeout),
        verify=key if transport is None else True,
        follow_redirects=follow_redirects,
    )
    clients[key] = client
    store["owns_client"] = True
    return client


async def close_cached_clients(jarvis: "Jarvis", domain: str) -> None:
    """Close every client :func:`cached_client` opened for ``domain``."""
    store = jarvis.data.get(domain) or {}
    for client in list((store.get("clients") or {}).values()):
        await client.aclose()
    store.get("clients", {}).clear()


def single_client(
    jarvis: "Jarvis",
    domain: str,
    *,
    timeout: Any,
    follow_redirects: bool = False,
) -> httpx.AsyncClient:
    """The one client for ``domain``, honouring test injection.

    ``timeout`` is passed straight to ``httpx.AsyncClient`` (the callers hand in
    a ready ``httpx.Timeout``), so this does not second-guess their config.
    """
    store = jarvis.data.setdefault(domain, {})
    injected = store.get("client")
    if injected is not None:
        store.setdefault("owns_client", False)
        return injected
    client = httpx.AsyncClient(
        transport=store.get("transport"),
        timeout=timeout,
        follow_redirects=follow_redirects,
    )
    store["client"] = client
    store["owns_client"] = True
    return client
