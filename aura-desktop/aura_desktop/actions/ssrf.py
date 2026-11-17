"""SSRF guard for ``http_request``.

``http_request`` exists so Jarvis can fetch a public page or poke a public API.
It must not become a proxy into this machine's own loopback, the local network,
or a cloud metadata endpoint — because the thing choosing the URL is an LLM that
reads attacker-controlled text.

Two layers, both required:

1. :func:`check` — literal inspection of the URL. Blocks non-HTTP schemes,
   embedded credentials, and any host that is *written* as a loopback, private,
   link-local, CGNAT, multicast or metadata address, including the decimal
   (``http://2130706433``), octal (``0177.0.0.1``), hex (``0x7f.0.0.1``) and
   IPv4-mapped-IPv6 (``[::ffff:127.0.0.1]``) spellings that ``inet_aton`` still
   honours.
2. :func:`is_blocked_ip` — re-checked by the caller against every address the
   host actually resolves to, which is the only defence against a DNS name that
   points at 127.0.0.1.

The single exemption is the configured jarvis-core host: that is the server we
already talk to over an authenticated socket, so it stays reachable even though
it lives on the LAN.

Mirrors ``android-app/.../automation/actions/SsrfGuard.kt``. The address
reading (``parse_ipv4`` and the ``inet_aton`` spellings, the IPv6 unwrapping)
is :mod:`ssrf_parse`, the file this tree shares with ``jarvis-core`` — see its
docstring for why it is a checked copy rather than an import. The *policy*
here — the LAN is blocked — is this module's own.
"""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from typing import Callable, Iterable, Sequence
from urllib.parse import urlsplit

from .ssrf_parse import (
    address_in,
    is_blocked_host_name,
    is_ip_literal,
    looks_like_ipv6,
    parse_ipv4,
)

__all__ = ["Check", "check", "is_blocked_ip", "parse_ipv4", "resolve_and_check"]


@dataclass(frozen=True)
class Check:
    allowed: bool
    reason: str | None = None
    scheme: str | None = None
    host: str | None = None
    port: int = -1
    #: True when the host matched the jarvis-core allowlist.
    exempt: bool = False
    #: True when ``host`` is a name, so the caller must resolve and re-check.
    needs_dns_check: bool = False

    def __bool__(self) -> bool:
        return self.allowed


#: IPv4 ranges that must never be contacted.
_BLOCKED_V4 = tuple(
    ipaddress.ip_network(net)
    for net in (
        "0.0.0.0/8",  # "this network"
        "10.0.0.0/8",  # RFC1918
        "100.64.0.0/10",  # CGNAT
        "127.0.0.0/8",  # loopback
        "169.254.0.0/16",  # link-local, incl. 169.254.169.254 metadata
        "172.16.0.0/12",  # RFC1918
        "192.0.0.0/24",  # IETF protocol assignments
        "192.0.2.0/24",  # TEST-NET-1
        "192.168.0.0/16",  # RFC1918
        "198.18.0.0/15",  # benchmarking
        "224.0.0.0/4",  # multicast
        "240.0.0.0/4",  # reserved, incl. 255.255.255.255
    )
)

_BLOCKED_V6 = tuple(
    ipaddress.ip_network(net)
    for net in (
        "::/128",  # unspecified
        "::1/128",  # loopback
        "fe80::/10",  # link-local
        "fc00::/7",  # unique local (incl. fd00:ec2::254, EC2 IMDS over v6)
        "ff00::/8",  # multicast
        "64:ff9b::/96",  # NAT64
        "2001:db8::/32",  # documentation
    )
)


def is_blocked_ip(address: str) -> bool:
    """True when this address must never be contacted.

    Call it on the literal host AND on every address the host resolves to. An
    IPv6 string that will not parse fails closed (blocked); a string that is not
    an address at all is not this function's business and returns False.
    """
    return address_in(address, _BLOCKED_V4, _BLOCKED_V6)


def check(raw_url: object, allowed_hosts: Iterable[str] = ()) -> Check:
    """Literal inspection of a URL.

    ``allowed_hosts`` are exempt from the private-range block — normally just
    the configured jarvis-core host. Compared case-insensitively.
    """
    if not isinstance(raw_url, str):
        return Check(False, "url is required")
    text = raw_url.strip()
    if not text:
        return Check(False, "url is required")
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in text):
        return Check(False, "url contains control characters")

    try:
        parts = urlsplit(text)
    except ValueError:
        return Check(False, "malformed url")

    scheme = (parts.scheme or "").lower()
    if scheme not in ("http", "https"):
        return Check(False, f"only http and https are allowed (got {scheme or 'no scheme'})")
    if parts.username or parts.password or "@" in (parts.netloc.split("]")[-1]):
        return Check(False, "credentials in the url are not allowed")

    try:
        raw_host = parts.hostname
    except ValueError:
        return Check(False, "malformed url")
    if not raw_host:
        return Check(False, "url has no host")
    host = raw_host.strip().strip("[]").rstrip(".").lower()
    if not host:
        return Check(False, "url has no host")

    try:
        port = parts.port or (443 if scheme == "https" else 80)
    except ValueError:
        return Check(False, "malformed port")

    exempt_set = {
        h.strip().rstrip(".").lower() for h in allowed_hosts if h and h.strip()
    }
    if host in exempt_set:
        return Check(True, None, scheme, host, port, exempt=True, needs_dns_check=False)

    if is_blocked_host_name(host):
        return Check(False, f"host {host} is blocked (loopback/metadata name)", scheme, host, port)

    # A host with a colon in it is trying to be an IPv6 literal. If it will not
    # parse as one, refuse it here rather than handing it to the resolver: a
    # string the guard cannot read is a string the guard cannot vouch for.
    if ":" in host and not looks_like_ipv6(host):
        return Check(False, f"host {host} is not a valid IPv6 literal", scheme, host, port)

    if is_ip_literal(host):
        if is_blocked_ip(host):
            return Check(
                False,
                f"address {host} is blocked (private/loopback/link-local/metadata)",
                scheme,
                host,
                port,
            )
        return Check(True, None, scheme, host, port, exempt=False, needs_dns_check=False)

    # A name we do not recognise: allowed only after the caller resolves it and
    # runs every resulting address through is_blocked_ip.
    return Check(True, None, scheme, host, port, exempt=False, needs_dns_check=True)


Resolver = Callable[[str], Sequence[str]]


def system_resolver(host: str) -> list[str]:
    """Every address ``host`` resolves to, v4 and v6."""
    infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    return [str(info[4][0]) for info in infos]


def resolve_and_check(
    raw_url: object,
    allowed_hosts: Iterable[str] = (),
    resolver: Resolver = system_resolver,
) -> Check:
    """:func:`check` plus the DNS re-check, in one call.

    The resolver is injected so tests can point a name at 127.0.0.1 without
    touching the network.
    """
    result = check(raw_url, allowed_hosts)
    if not result.allowed or not result.needs_dns_check or result.host is None:
        return result
    try:
        addresses = list(resolver(result.host))
    except Exception as exc:  # noqa: BLE001 - any resolver failure is a refusal
        return Check(False, f"could not resolve {result.host}: {exc}", result.scheme, result.host, result.port)
    if not addresses:
        return Check(False, f"could not resolve {result.host}", result.scheme, result.host, result.port)
    for address in addresses:
        if is_blocked_ip(str(address)):
            return Check(
                False,
                f"blocked: {result.host} resolves to {address}, "
                "which is a private/loopback/metadata address",
                result.scheme,
                result.host,
                result.port,
            )
    return Check(True, None, result.scheme, result.host, result.port, needs_dns_check=False)
