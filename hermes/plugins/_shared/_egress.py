"""Where this stack is allowed to reach, declared once and enforced in the gate.

Hermes ships an egress allowlist — ``proxy.enabled`` drives an ``iron-proxy``
daemon (``agent/proxy_sources/iron_proxy.py``) — and it is the wrong mechanism
here, for two reasons that are properties of *this* deployment rather than
opinions:

1. It only sits between a **Docker terminal sandbox** and the network
   (``tools/environments/docker.py`` is its single wiring point). This stack
   runs ``terminal.backend: local``, and the browser and ``web_extract`` run in
   the gateway process, so with the terminal backend we have it would proxy
   nothing at all.
2. Turning it on downloads a pinned third-party binary from GitHub Releases at
   first use. That is a network dependency in the build path of a box whose
   whole point is that it is self-contained.

So the allowlist is enforced where every other governance decision in this
stack is already made: the one ``pre_tool_call`` gate. :func:`allowed` answers
"may this stack talk to that host", and :mod:`_matrix` turns a *no* into a
refusal for the tools that carry a destination — ``web_extract``,
``browser_navigate`` and the fetch-shaped ``terminal`` commands. That covers
the traffic iron-proxy would have covered and the traffic it would not.

The list is **derived, not hand-kept**: the hosts are the ones the settings
schema already names (``SEARXNG_URL``, ``HOUSE_URL``, ``N8N_URL``, …), so
configuring an integration allows exactly its host and nothing else, and an
integration that is not configured is not reachable. Only the three literal
groups below are declared by hand, each with the reason it cannot come from a
setting. ``PLUGIN_HOSTS`` is the fourth: the public hosts the plugins' own code
names, which CI holds the code to.

Deliberately not claimed: this bounds the *tools*. It is not a network
firewall — a shell command that opens a raw socket is not parsed by anything
here, which is why ``terminal`` is gated on its own destructive/fetch shapes
and why ``jarvis-sandbox``-style isolation stays ``--network none`` rather than
relying on this list.
"""
from __future__ import annotations

import ipaddress
import logging
import re
import socket
from functools import lru_cache
from urllib.parse import urlsplit

_LOGGER = logging.getLogger(__name__)

#: The house's names. Every homelab service the owner runs is a name under its DNS zone
#: (wildcard DNS to the house's Caddy), so a service can be put behind Caddy and reached
#: without a code change here. Matched as a suffix on the hostname, so `gitlab.home.example`
#: and `nas.home.example` are both in and `nothome.example` is not. The zone is the owner's
#: domain, which the repo never names (T-0841): it is the HOUSE_DOMAIN setting, which the
#: entrypoint fills from GITLAB_API_URL's zone when the host's .env leaves it unset. The
#: tailnet (`.example.ts.net`) was the house until Tailscale was retired on 2026-10-27; it is
#: no longer trusted, since no service answers under it.
@lru_cache(maxsize=1)
def house_suffix() -> str | None:
    """`.<zone>` for the house, or None when no zone is set: then only the listed hosts are in.

    A zone with no dot (unset, or a bare TLD such as `com` from a GitLab on gitlab.com) is
    refused, since a suffix match on it would let in a whole public suffix. Does not catch a
    multi-label public suffix (`co.uk`) or a zone wider than the house (a company's), whether
    set by hand or derived from a GitLab at `gitlab.co.uk` or `gitlab.<company>.com`: such a
    host sets HOUSE_DOMAIN to the house's own zone.
    """
    try:
        from aura_schema import from_env

        zone = str(from_env().house_domain or "").strip().strip(".").lower()
    except Exception as exc:  # a schema failure must not silently open the list
        _LOGGER.error("egress allowlist could not read HOUSE_DOMAIN (%s); no house suffix", exc)
        return None
    return "." + zone if "." in zone else None


def _in_house(host: str) -> bool:
    suffix = house_suffix()
    return suffix is not None and host.endswith(suffix)

#: Container-local and compose-network names. These never appear in a URL
#: setting because they are how a container reaches *itself* and its siblings:
#: the adapter on 8643, the gateway on 8642, the extract service, SearXNG.
#: ``host.docker.internal`` is the benchmark fixture server's route out of the
#: container and the only way the JCB's safety tasks can be served at all.
LOCAL_HOSTS: frozenset[str] = frozenset({
    "localhost",
    "127.0.0.1",
    "::1",
    "host.docker.internal",
    "jarvis",
    "jarvis-adapter",
    "searxng",
    "extract",
})

#: Refused before anything else, whatever the settings say. The cloud instance
#: metadata endpoints hand out credentials to anything that can make an HTTP
#: request from the right network, and this stack has no business reading them
#: on any host it might ever be moved to. Hermes' own ``tools/url_safety.py``
#: also refuses these; naming them here means the refusal survives
#: ``security.allow_private_urls: true``, which this deployment sets.
ALWAYS_DENIED: frozenset[str] = frozenset({
    "169.254.169.254",
    "metadata.google.internal",
    "metadata.goog",
    "instance-data",
})

#: The public hosts the plugins name in their own code, each with why: not a permission for
#: the model's fetch tools (they stay outside :func:`allowed`), but the list CI holds the code
#: to, so a new one is a reviewed change (hermes/plugins/tests/test_egress_hosts.py, T-0573).
PLUGIN_HOSTS: dict[str, str] = {
    "api.open-meteo.com": "weather_forecast: the forecast, for the house's position rounded to 0.1 degree",
    "geocoding-api.open-meteo.com": "weather_forecast: a place the person named, to its coordinates",
    "photon.komoot.io": "places: public place search, only with places.photon_public on (off by default)",
    "nominatim.openstreetmap.org": "places: public geocoding, only with places.photon_public on (off by default)",
    "claude.ai": "text, never fetched: the session link in the self-extension loop's commit trailers",
}

#: Public services that turn a place into coordinates or back. The house answers both
#: (place_search, its own geocoder and maps); a URL to one of these from the model's own fetch
#: tools carries the owner's address or position out of the house (T-0904: the browser opened
#: Nominatim with the address in it). The plugins' own calls are PLUGIN_HOSTS rows, behind their
#: settings. Matched as a suffix. Does not cover a geocoder this list does not name.
LOCATION_HOSTS: frozenset[str] = frozenset({
    "nominatim.openstreetmap.org", "photon.komoot.io", "geocoding-api.open-meteo.com",
    "overpass-api.de", "maps.googleapis.com", "api.mapbox.com", "api.opencagedata.com",
    "geocode.maps.co", "geocode.xyz", "api.geoapify.com", "api.positionstack.com",
    "geocode.search.hereapi.com", "dev.virtualearth.net", "api.locationiq.com", "us1.locationiq.com",
})


def location_host(target: str) -> bool:
    """True when [target] (a URL or a host) is one of :data:`LOCATION_HOSTS`."""
    host = (_host_of(target) or "").lower().rstrip(".")
    return any(host == name or host.endswith("." + name) for name in LOCATION_HOSTS)


#: Settings whose value is a URL naming a service this stack talks to. Read
#: through ``aura_schema.from_env()`` so the allowlist and the client code
#: cannot disagree about which host a service is on.
_URL_SETTINGS: tuple[str, ...] = (
    "adapter_url",
    "comfyui_url",
    "dav_url",
    "extract_url",
    "gitlab_api_url",
    "grafana_url",
    "hermes_api_url",
    "hermes_serve_url",
    "house_console_url",
    "house_url",
    "influx_url",
    "jellyfin_url",
    "llama_swap_url",
    "model_base_url",
    "n8n_url",
    "plex_url",
    "proxmox_url",
    "routing_embedding_url",
    "searxng_url",
)

#: Settings that name a bare host rather than a URL (mail is host + port).
_HOST_SETTINGS: tuple[str, ...] = ("mail_imap_host", "mail_smtp_host")


def _host_of(value: str) -> str:
    """The hostname in a URL or a bare host string, lower-cased, port stripped."""
    value = (value or "").strip()
    if not value:
        return ""
    if "://" not in value:
        value = "//" + value
    try:
        host = urlsplit(value).hostname or ""
    except ValueError:  # a malformed URL is not a host we allow
        return ""
    return host.lower()


@lru_cache(maxsize=1)
def _configured_hosts() -> frozenset[str]:
    """Hosts named by the settings. Cached: this runs before every gated call.

    Cached for the life of the process, which is correct because a settings
    change that adds a service also restarts the gateway (``restart_needed`` in
    ``aura-settings``). ``reset()`` clears it for the tests.
    """
    hosts: set[str] = set()
    try:
        from aura_schema import from_env

        settings = from_env()
    except Exception as exc:  # a schema failure must not silently open the list
        _LOGGER.error("egress allowlist could not read the settings (%s) — "
                      "only the declared local hosts and the house suffix are allowed", exc)
        return frozenset()
    for name in _URL_SETTINGS:
        host = _host_of(str(getattr(settings, name, "") or ""))
        if host:
            hosts.add(host)
    for name in _HOST_SETTINGS:
        host = _host_of(str(getattr(settings, name, "") or ""))
        if host:
            hosts.add(host)
    return frozenset(hosts)


def reset() -> None:
    """Drop the cached settings-derived hosts (tests change the environment)."""
    _configured_hosts.cache_clear()
    house_suffix.cache_clear()


def allowed_hosts() -> frozenset[str]:
    """Every literal host currently allowed — the declared set plus the derived one.

    The house suffix is a rule, not a host, so it is not in here; use
    :func:`allowed`, which applies both.
    """
    return frozenset(LOCAL_HOSTS | _configured_hosts())


#: A backslash, white space or a control character: no two parsers agree on a destination
#: that has one inside it.
_AMBIGUOUS = re.compile(r"[\\\s\x00-\x1f\x7f]")


def allowed(target: str) -> bool:
    """May this stack open a connection to ``target`` (a URL or a bare host)?

    Unknown means no. A destination that cannot be parsed into a host is
    refused rather than passed through, because "we could not tell" and "it is
    fine" must not be the same answer in a default-deny list. Nor is a destination two
    parsers read differently: with a backslash, white space or a control character inside
    it, the host read here is not the one a browser or a shell tool would connect to
    (``https://a.example\\@localhost/`` is a.example to a browser), so it is never ours.
    """
    if _AMBIGUOUS.search((target or "").strip()):
        return False
    host = _host_of(target)
    if not host:
        return False
    if host in ALWAYS_DENIED:
        return False
    # A literal address that resolves to link-local is the metadata endpoint by
    # another spelling (169.254.169.254 has several decimal forms).
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is not None and ip.is_link_local:
        return False
    if host in LOCAL_HOSTS:
        return True
    if _in_house(host):
        return True
    return host in _configured_hosts()


def describe() -> list[str]:
    """The allowlist as lines, for the generated docs page and the audit drill."""
    lines = [f"*{house_suffix() or '.<HOUSE_DOMAIN unset>'} (the house)"]
    lines += sorted(LOCAL_HOSTS)
    lines += sorted(_configured_hosts() - LOCAL_HOSTS)
    return lines


#: Host-name endings that are never the public internet.
_LOCAL_SUFFIXES: tuple[str, ...] = (".local", ".lan", ".home", ".internal", ".localdomain", ".arpa")


def _private_ip(ip: "ipaddress.IPv4Address | ipaddress.IPv6Address") -> bool:
    # is_private covers RFC 1918, loopback and link-local; the tailnet's 100.64/10 is
    # "shared address space", which is_private does not count.
    return (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_unspecified
            or ip.is_reserved or ip in ipaddress.ip_network("100.64.0.0/10"))


def private(target: str) -> bool:
    """Is ``target`` (a URL or host) anything but the public internet?

    Loopback, the LAN, the house's names, a tailnet (100.64/10) address, link-local, a
    bare single-label name, a local suffix, or a name that resolves to any of those -- and a name that does not
    resolve at all, because "could not tell" must not read as "public". Used where a
    free tool would otherwise make this stack send a GET inside the house: a GET on
    the LAN can run an n8n webhook. Does not cover DNS that changes between this
    check and the request; a caller that probes later checks again.
    """
    host = _host_of(target)
    if not host or host in LOCAL_HOSTS or _in_house(host):
        return True
    if "." not in host or host.endswith(_LOCAL_SUFFIXES):
        return True
    try:
        return _private_ip(ipaddress.ip_address(host))
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return True
    return any(_private_ip(ipaddress.ip_address(str(info[4][0]).split("%")[0])) for info in infos) or not infos


# --------------------------------------------------------------------------- #
# The address a service plugin sends its token to (T-1342)
# --------------------------------------------------------------------------- #
class ServiceUrlError(ValueError):
    """An address a service plugin will not talk to; the text is safe to hand to the model
    and never carries the address itself (it may hold a password)."""


#: The IPv6 form of the cloud metadata address (AWS). It is inside fc00::/7, so "private" would
#: let it through; named here so the refusal does not depend on that.
_METADATA_V6 = ipaddress.ip_address("fd00:ec2::254")


def service_url(raw: object, name: str) -> str:
    """The server address a plugin may send its token to, or a ServiceUrlError saying why not.

    ``name`` is the setting's env var, for the message only. The rule, for Plex, Jellyfin, n8n,
    Proxmox, Grafana, InfluxDB, ComfyUI and llama-swap alike:

    * https to any host;
    * plain http only to a host that cannot be the public internet: a private, loopback or
      unique-local IP literal, ``localhost``, ``host.docker.internal``, or a single-label name
      that starts with a letter (a compose service such as ``http://n8n:5678`` or
      ``http://jarvis-models:8000``, which is how this stack's own .env.example reaches them);
    * never a login, a query, a fragment, a space or control character, or a bad port;
    * never the cloud metadata hosts or any link-local or unspecified address, on either scheme.

    Does **not** cover: a dotted name (``nas.home.example``) over plain http, which is refused
    even if it resolves to the LAN, since a name could resolve anywhere; a single-label name that
    a search domain or a poisoned resolver sends outside the house; a name that resolves to the
    metadata address over https; DNS that changes after the check; TLS verification, which each
    plugin keeps as its own setting.
    """
    url = str(raw or "").strip()
    if not url:
        raise ServiceUrlError(f"{name} is not set")
    if any(ord(c) <= 32 or ord(c) == 127 or c == "\\" for c in url):
        raise ServiceUrlError(f"{name} has a space, control or backslash character in it")
    if "?" in url or "#" in url:
        raise ServiceUrlError(f"{name} must not carry a query or a fragment")
    try:
        parts = urlsplit(url)
        parts.port  # noqa: B018 - raises on a port that is not a number in range
        host = (parts.hostname or "").lower()
    except ValueError as exc:  # a malformed address or a bad port
        raise ServiceUrlError(f"{name} is not a valid address") from exc
    if "@" in parts.netloc:
        raise ServiceUrlError(f"{name} must not carry a login (user:password@)")
    if parts.scheme not in ("http", "https") or not host:
        raise ServiceUrlError(f"{name} must be https:// (or http:// to a private address) with a host")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped  # ::ffff:169.254.169.254 is the same address
    if (host.rstrip(".") in ALWAYS_DENIED
            or (ip is not None and (ip.is_link_local or ip.is_unspecified or ip == _METADATA_V6))):
        raise ServiceUrlError(f"{name} names a metadata or link-local address")
    if parts.scheme == "http":
        local_name = host == "host.docker.internal" or re.fullmatch(r"[a-z][a-z0-9_-]*", host)
        if not (local_name or (ip is not None and ip.is_private)):
            raise ServiceUrlError(
                f"{name} may use plain http only to a private address, localhost or a service name "
                "such as http://192.168.1.20:8080; use https otherwise")
    return url.rstrip("/")
