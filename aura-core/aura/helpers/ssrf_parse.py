# @generated from aura-desktop/aura_desktop/actions/ssrf_parse.py by `make ssrf-sync`; edit that file, not this one.
# jscpd:ignore-start
"""Reading an address the way a resolver reads it — the shared half of the SSRF guard.

Both SSRF guards (``jarvis-desktop``'s ``actions/ssrf.py``, which fetches public
pages and blocks the LAN, and ``jarvis-core``'s ``helpers/ssrf.py``, which is a
house assistant and allows it) need the same thing before they can apply their
*different* policies: to see the address a URL names the way libc will, not the
way a person reading an approval prompt does. ``2130706433``, ``0177.0.0.1``,
``0x7f.0.0.1`` and ``[::ffff:127.0.0.1]`` are all loopback to ``inet_aton``, and
a guard that reads them as anything else is a guard with a hole in it.

That reading is fiddly and security-critical, so it exists once. What a policy
module adds is its own blocked-range tables and :func:`address_in` against
them; nothing here decides what is blocked.

## This file exists twice, and only this one is edited

``aura-desktop/aura_desktop/actions/ssrf_parse.py`` is the source;
``aura-core/aura/helpers/ssrf_parse.py`` is generated from it by
``make ssrf-sync`` (a ``@generated`` header and jscpd markers around the same
text). They are not shared through an import because the two deploy
separately: the desktop agent is a self-contained pip package on a person's
machine and the hub image is built from ``aura-core/`` alone, and a hub that
depends on the client package would be the wrong way round.
``aura-core/tests/test_ssrf.py`` fails when the generated copy is stale or
hand-edited (the v2 plan, D28). Edit here, run ``make ssrf-sync``, run that test.
"""

from __future__ import annotations

import ipaddress
from typing import Iterable

__all__ = [
    "METADATA_NAMES",
    "address_in",
    "is_blocked_host_name",
    "is_ip_literal",
    "looks_like_ipv6",
    "parse_ipv4",
]

#: Names that never legitimately appear in a URL a model chose.
METADATA_NAMES = frozenset(
    {
        "metadata",
        "metadata.google.internal",
        "metadata.goog",
        "instance-data",
        "instance-data.ec2.internal",
        "metadata.azure.com",
        "169.254.169.254.nip.io",
    }
)

Networks = Iterable["ipaddress.IPv4Network | ipaddress.IPv6Network"]


def is_blocked_host_name(host: str) -> bool:
    """True for a *name* that is this machine or a metadata service."""
    h = host.strip().rstrip(".").lower()
    if h == "localhost" or h.endswith(".localhost"):
        return True
    if h in ("ip6-localhost", "ip6-loopback"):
        return True
    return h in METADATA_NAMES


def _parse_ipv4_part(part: str) -> int | None:
    """One segment of an ``inet_aton``-style address: decimal, octal or hex."""
    if not part or len(part) > 20:
        return None
    try:
        if part[:2].lower() == "0x":
            body = part[2:]
            if not body:
                return None
            value = int(body, 16)
        elif len(part) > 1 and part[0] == "0":
            value = int(part[1:], 8)
        else:
            if not part.isdigit():
                return None
            value = int(part, 10)
    except ValueError:
        return None
    return value if value >= 0 else None


def parse_ipv4(host: str) -> int | None:
    """Parse dotted-quad plus the legacy spellings ``inet_aton`` honours.

    Returns the address as a 32-bit int, or None when it is not IPv4 at all.
    ``2130706433``, ``0177.0.0.1`` and ``0x7f.0.0.1`` all come back as
    ``127.0.0.1`` — libc-backed resolvers accept them, so the guard must too.
    """
    h = host.strip().rstrip(".")
    if not h:
        return None
    parts = h.split(".")
    if len(parts) > 4:
        return None
    values: list[int] = []
    for part in parts:
        value = _parse_ipv4_part(part)
        if value is None:
            return None
        values.append(value)
    n = len(values)
    if n == 1:
        return values[0] if values[0] <= 0xFFFFFFFF else None
    if n == 2:
        if values[0] <= 0xFF and values[1] <= 0xFFFFFF:
            return (values[0] << 24) | values[1]
        return None
    if n == 3:
        if values[0] <= 0xFF and values[1] <= 0xFF and values[2] <= 0xFFFF:
            return (values[0] << 24) | (values[1] << 16) | values[2]
        return None
    if all(v <= 0xFF for v in values):
        return (values[0] << 24) | (values[1] << 16) | (values[2] << 8) | values[3]
    return None


def looks_like_ipv6(host: str) -> bool:
    """True when the string is shaped like an IPv6 literal (parseable or not)."""
    h = host.strip().strip("[]")
    if ":" not in h:
        return False
    body = h.split("%", 1)[0]
    return bool(body) and all(
        ch == ":" or ch == "." or ch.isdigit() or ch.lower() in "abcdef" for ch in body
    )


def is_ip_literal(host: str) -> bool:
    """True when the string is written as an IPv4 or IPv6 literal, any spelling."""
    return parse_ipv4(host) is not None or looks_like_ipv6(host)


def _ipv4_compatible(addr: ipaddress.IPv6Address) -> ipaddress.IPv4Address | None:
    packed = addr.packed
    if packed[:12] == b"\x00" * 12 and packed[12:] != b"\x00\x00\x00\x00":
        return ipaddress.IPv4Address(packed[12:])
    return None


def address_in(address: str, blocked_v4: Networks, blocked_v6: Networks) -> bool:
    """True when ``address`` falls in ``blocked_v4``/``blocked_v6``, any spelling.

    The policy module passes its own tables. An IPv6 string that will not parse
    fails closed (True); a string that is not an address at all is not this
    function's business and returns False — the caller has already decided it is
    a literal, or is checking what a name resolved to.
    """
    v4 = parse_ipv4(address)
    if v4 is not None:
        addr4 = ipaddress.IPv4Address(v4)
        return any(addr4 in net for net in blocked_v4)

    if looks_like_ipv6(address):
        text = address.strip().strip("[]").split("%", 1)[0]
        try:
            addr6 = ipaddress.IPv6Address(text)
        except ValueError:
            return True  # unparseable v6 => fail closed
        if any(addr6 in net for net in blocked_v6):
            return True
        # IPv4-mapped (::ffff:a.b.c.d) and IPv4-compatible (::a.b.c.d) carry a
        # v4 address inside a v6 wrapper; unwrap and re-check.
        mapped = addr6.ipv4_mapped or _ipv4_compatible(addr6)
        if mapped is not None and any(mapped in net for net in blocked_v4):
            return True
        return False

    return False
# jscpd:ignore-end
