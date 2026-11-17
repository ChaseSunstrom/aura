"""LAN census: what kind of thing each netscan device is, from what it announces.

Evidence per device, strongest first: mDNS service types (a Chromecast advertises
``_googlecast._tcp``), SSDP/UPnP (``ST``/``SERVER``/device description), open
well-known TCP ports, and the MAC's vendor (a small bundled OUI excerpt, not the
IEEE registry). ``classify`` is pure; the ``discover_*`` functions are the only
network code and send only to places this module names:

* SSDP: one ``M-SEARCH`` to the standard multicast group 239.255.255.250:1900, and
  a GET of each responder's own ``LOCATION`` when that host is the responder itself;
* mDNS: ``zeroconf`` browse of a fixed list of service types (multicast);
* ports: a TCP connect (nothing written) to ``PORTS`` on addresses in the swept subnet.

What it does not do: see a device that answers none of these (a bare ARP responder
stays ``unknown``, or ``phone`` at low confidence when its MAC is randomised), or tell
two products with one protocol apart (a Chromecast and a Cast-built-in TV are both
``cast`` unless SSDP or the ports say ``tv``).
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import re
import socket
import ssl
import urllib.parse
import urllib.request
from typing import Any, Optional

_LOGGER = logging.getLogger(__name__)

#: The only TCP ports probed: connect, never write.
PORTS = (22, 53, 80, 139, 443, 445, 515, 548, 631, 3389, 5000, 8008, 8009, 8096, 9100)
SSDP_GROUP = ("239.255.255.250", 1900)
MDNS_TYPES = ("_googlecast._tcp", "_airplay._tcp", "_raop._tcp", "_ipp._tcp", "_printer._tcp",
              "_hap._tcp", "_spotify-connect._tcp", "_smb._tcp", "_afpovertcp._tcp", "_ssh._tcp",
              "_hue._tcp", "_esphomelib._tcp", "_homekit._tcp", "_workstation._tcp",
              "_companion-link._tcp", "_androidtvremote2._tcp", "_sonos._tcp", "_apple-mobdev2._tcp")

#: OUI -> vendor. An excerpt of common home vendors, not the registry: a miss is "unknown", never wrong.
OUI = {
    "b8:27:eb": "Raspberry Pi", "dc:a6:32": "Raspberry Pi", "e4:5f:01": "Raspberry Pi",
    "00:17:88": "Philips Hue", "00:11:32": "Synology", "90:09:d0": "Synology",
    "24:0a:c4": "Espressif", "30:ae:a4": "Espressif", "a4:cf:12": "Espressif",
    "7c:df:a1": "Espressif", "3c:71:bf": "Espressif", "ec:fa:bc": "Espressif",
    "f4:f5:d8": "Google", "3c:5a:b4": "Google", "54:60:09": "Google", "44:07:0b": "Google",
    "18:b4:30": "Nest", "00:0c:29": "VMware", "bc:24:11": "Proxmox VM", "52:54:00": "QEMU",
    "00:0e:58": "Sonos", "5c:aa:fd": "Sonos", "78:28:ca": "Sonos", "b8:e9:37": "Sonos",
    "b0:a7:37": "Roku", "08:05:81": "Roku", "dc:3a:5e": "Roku",
    "44:65:0d": "Amazon", "f0:27:2d": "Amazon", "74:c2:46": "Amazon",
}
#: Vendors whose every product is one kind; a weak vote, below any protocol evidence.
VENDOR_KIND = {"Raspberry Pi": "pc", "Philips Hue": "bridge", "Synology": "nas", "Espressif": "iot",
               "Sonos": "speaker", "Roku": "tv", "Nest": "iot", "VMware": "pc", "Proxmox VM": "pc", "QEMU": "pc"}

#: (kind, test on the evidence): first match wins, so order is priority.
_RULES: list[tuple[str, Any]] = [
    ("printer", lambda e: bool(e["mdns"] & {"_ipp._tcp", "_printer._tcp"}) or bool(e["ports"] & {631, 9100, 515})),
    ("bridge", lambda e: "_hue._tcp" in e["mdns"] or "philips hue" in e["ssdp"]),
    ("nas", lambda e: "synology" in e["ssdp"] or e["vendor"] == "Synology"
     or ("_afpovertcp._tcp" in e["mdns"] and 5000 in e["ports"])),
    ("camera", lambda e: "camera" in e["ssdp"] or "ipcam" in e["ssdp"]),
    ("tv", lambda e: "_androidtvremote2._tcp" in e["mdns"] or bool(re.search(r"\b(tv|bravia|webos|tizen|roku)\b", e["ssdp"]))),
    ("speaker", lambda e: bool(e["mdns"] & {"_sonos._tcp", "_raop._tcp", "_spotify-connect._tcp"}) or "sonos" in e["ssdp"]),
    ("cast", lambda e: "_googlecast._tcp" in e["mdns"] or bool(e["ports"] & {8008, 8009})),
    ("phone", lambda e: bool(e["mdns"] & {"_companion-link._tcp", "_apple-mobdev2._tcp"}) and not (e["ports"] & {22, 445, 3389})),
    ("pc", lambda e: bool(e["ports"] & {22, 139, 445, 3389}) or bool(e["mdns"] & {"_workstation._tcp", "_smb._tcp", "_ssh._tcp"})),
    ("iot", lambda e: bool(e["mdns"] & {"_esphomelib._tcp", "_hap._tcp", "_homekit._tcp"}) or e["vendor"] in ("Espressif", "Nest")),
    ("router", lambda e: 53 in e["ports"] and bool(e["ports"] & {80, 443})),
]
#: The integration a found device could be set up with.
_SUGGEST = {"bridge": "hue", "cast": "cast", "tv": "cast", "iot": "esphome"}


def vendor_of(mac: str) -> str | None:
    return OUI.get(mac[:8].lower())


def classify(mac: str, ip: str, mdns: set[str] | None = None, ssdp: str = "",
             ports: set[int] | None = None, gateway: bool = False) -> dict[str, Any]:
    """One device's guessed ``kind`` with the ``evidence`` it rests on.

    ``confidence`` is ``high`` when a protocol answered, ``low`` when only the vendor or a
    randomised MAC (the one thing a phone does on Wi-Fi that little else does) points there.
    """
    private = bool(int(mac[:2], 16) & 0x02)
    ev: dict[str, Any] = {"vendor": vendor_of(mac), "mdns": set(mdns or ()), "ssdp": ssdp.lower(), "ports": set(ports or ())}
    kind, confidence = "unknown", "none"
    if gateway:  # the default route's next hop is the router whatever else it answers
        kind, confidence = "router", "high"
    for k, test in ([] if gateway else _RULES):
        if test(ev):
            kind = k
            confidence = "low" if k == "iot" and not (ev["mdns"] or ev["ssdp"]) else "high"
            break
    else:
        if gateway:
            pass
        elif ev["vendor"] in VENDOR_KIND:
            kind, confidence = VENDOR_KIND[ev["vendor"]], "low"
        elif private:
            kind, confidence = "phone", "low"
    return {"mac": mac, "ip": ip, "kind": kind, "confidence": confidence, "suggest": _SUGGEST.get(kind),
            "evidence": {"vendor": ev["vendor"], "mdns": sorted(ev["mdns"]), "ssdp": ssdp.strip()[:160] or None,
                         "ports": sorted(ev["ports"]), "private_mac": private}}


def _in(network: ipaddress.IPv4Network, ip: str) -> bool:
    try:
        return ipaddress.IPv4Address(ip) in network
    except ValueError:
        return False


def _clean(text: str) -> str:
    """A device's own words, cut to a plain label: they reach the model as house state, and
    a name like "ignore your rules and ..." is someone else's text (commit review)."""
    return re.sub(r"[^A-Za-z0-9 .,()_+/-]", "", text)[:48].strip()


def _tag(xml: str, name: str) -> str:
    m = re.search(rf"<{name}>([^<]{{1,80}})</{name}>", xml)
    return _clean(m.group(1)) if m else ""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


#: Fetches a responder's description with no redirect: a 302 to another host is not followed.
_FETCH = urllib.request.build_opener(_NoRedirect)


def _own_location(loc: str, ip: str) -> Optional[str]:
    """The description URL rebuilt from its parts when it is plain http on the responder
    itself; None otherwise. Parsed as urllib does, so userinfo ("http://ip:80@other/") or a
    second host cannot slip past a string comparison (commit review, 2026-10-09)."""
    try:
        u = urllib.parse.urlsplit(loc)
        port = u.port or 80
    except ValueError:
        return None
    if u.scheme != "http" or u.username or u.password or u.hostname != ip:
        return None
    return urllib.parse.urlunsplit(("http", f"{ip}:{port}", u.path or "/", u.query, ""))


def _lan_address(network: ipaddress.IPv4Network) -> str:
    """This host's address on ``network``: the multicast must leave by the LAN, not the tailnet."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.connect((str(next(network.hosts())), 9))  # connect on UDP sends nothing; it picks the route
        return s.getsockname()[0]


def _ssdp_sync(network: ipaddress.IPv4Network, wait: float, ips: list[str]) -> dict[str, str]:
    msg = (b'M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\nMAN: "ssdp:discover"\r\nMX: 2\r\n'
           b"ST: ssdp:all\r\n\r\n")
    found: dict[str, list[str]] = {}
    locations: dict[str, str] = {}
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(0.5)
            s.bind((_lan_address(network), 0))
            s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
            s.sendto(msg, SSDP_GROUP)
            # Also straight to each known host: a bridge that snoops IGMP (an LXC on Proxmox) drops
            # the multicast both ways, and UPnP devices answer a unicast M-SEARCH just the same.
            for ip in ips:
                if _in(network, ip):
                    try:
                        s.sendto(msg.replace(b"HOST: 239.255.255.250:1900", f"HOST: {ip}:1900".encode()), (ip, 1900))
                    except OSError:
                        pass
            for _ in range(int(wait / 0.5)):
                try:
                    data, (ip, _port) = s.recvfrom(4096)
                except socket.timeout:
                    continue
                if not _in(network, ip):
                    continue
                head = data.decode("latin-1", "replace")
                keep = ("ST", "SERVER", "NT")
                lines = [_clean(ln.strip()) for ln in head.split("\r\n")[1:] if ln.split(":", 1)[0].strip().upper() in keep]
                have = found.setdefault(ip, [])
                have.extend(x for x in lines if x not in have)
                m = re.search(r"(?im)^location:\s*(\S+)", head)
                if m:
                    locations.setdefault(ip, m.group(1))
    except OSError as err:
        _LOGGER.debug("census: ssdp failed: %s", err)
    out = {ip: " | ".join(v) for ip, v in found.items()}
    for ip, loc in locations.items():
        # Only the responder's own description: a LOCATION naming another host is not followed.
        url = _own_location(loc, ip)
        if url is None:
            continue
        try:
            with _FETCH.open(url, timeout=2) as r:
                xml = r.read(65536).decode("utf-8", "replace")
            out[ip] += " | " + " ".join(filter(None, (_tag(xml, "friendlyName"), _tag(xml, "manufacturer"),
                                                      _tag(xml, "modelName"), _tag(xml, "deviceType"))))
        except (OSError, ValueError):
            pass
    return out


async def discover_ssdp(network: ipaddress.IPv4Network, ips: list[str], wait: float = 4.0) -> dict[str, str]:
    """ip -> the SSDP text it answered with (ST/SERVER/NT lines and its description's names)."""
    return await asyncio.to_thread(_ssdp_sync, network, wait, ips)


def _mdns_unicast_sync(network: ipaddress.IPv4Network, ips: list[str], wait: float) -> dict[str, set[str]]:
    """Ask each host directly (a "legacy unicast" query, from a non-5353 port, so it answers us, not the group)."""
    from zeroconf import DNSIncoming, DNSOutgoing, DNSQuestion
    from zeroconf.const import _CLASS_IN, _FLAGS_QR_QUERY, _TYPE_PTR
    q = DNSOutgoing(_FLAGS_QR_QUERY, multicast=False)
    q.add_question(DNSQuestion("_services._dns-sd._udp.local.", _TYPE_PTR, _CLASS_IN))
    out: dict[str, set[str]] = {}
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.settimeout(0.5)
        for ip in ips:
            if _in(network, ip):
                try:
                    s.sendto(q.packets()[0], (ip, 5353))
                except OSError:
                    pass
        for _ in range(int(wait / 0.5)):
            try:
                data, (ip, _port) = s.recvfrom(9000)
            except socket.timeout:
                continue
            if not _in(network, ip):
                continue
            try:
                for rec in DNSIncoming(data).answers():
                    name = getattr(rec, "alias", "")  # PTR rdata: the service type the host offers
                    if name.endswith("._tcp.local.") or name.endswith("._udp.local."):
                        out.setdefault(ip, set()).add(name.removesuffix(".local."))
            except Exception:  # a malformed reply from a LAN host is not worth the census
                continue
    return out


async def discover_mdns(network: ipaddress.IPv4Network, ips: list[str], wait: float = 5.0) -> dict[str, set[str]]:
    """ip -> mDNS service types: the unicast ask per host, then a multicast browse of ``MDNS_TYPES``.

    ``zeroconf`` is imported here: it is slow to import and optional.
    """
    try:
        out = await asyncio.to_thread(_mdns_unicast_sync, network, ips, 3.0)
    except ImportError:
        return {}
    for ip, types in (await _mdns_browse(network, wait)).items():
        out.setdefault(ip, set()).update(types)
    return out


async def _mdns_browse(network: ipaddress.IPv4Network, wait: float) -> dict[str, set[str]]:
    try:
        from zeroconf import ServiceStateChange
        from zeroconf.asyncio import AsyncServiceBrowser, AsyncZeroconf
    except ImportError:
        return {}
    out: dict[str, set[str]] = {}
    tasks: set[asyncio.Future] = set()
    azc = AsyncZeroconf()
    try:
        async def resolve(zc_type: str, name: str) -> None:
            info = await azc.async_get_service_info(zc_type, name, 2000)
            for ip in (info.parsed_addresses() if info else ()):
                if _in(network, ip):
                    out.setdefault(ip, set()).add(zc_type.removesuffix(".local."))

        def on_change(zeroconf, service_type, name, state_change) -> None:
            if state_change is ServiceStateChange.Added:
                t = asyncio.ensure_future(resolve(service_type, name))
                tasks.add(t)
                t.add_done_callback(tasks.discard)

        browser = AsyncServiceBrowser(azc.zeroconf, [f"{t}.local." for t in MDNS_TYPES], handlers=[on_change])
        await asyncio.sleep(wait)
        await browser.async_cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
    finally:
        await azc.async_close()
    return out


async def _open(ip: str, port: int) -> bool:
    try:
        _r, w = await asyncio.wait_for(asyncio.open_connection(ip, port), 0.6)
    except (OSError, asyncio.TimeoutError):
        return False
    w.close()
    return True


async def discover_ports(network: ipaddress.IPv4Network, ips: list[str]) -> dict[str, set[int]]:
    """ip -> open PORTS. Only addresses inside ``network``; a connect, nothing written."""
    sem = asyncio.Semaphore(64)
    out: dict[str, set[int]] = {}

    async def one(ip: str, port: int) -> None:
        async with sem:
            if await _open(ip, port):
                out.setdefault(ip, set()).add(port)

    await asyncio.gather(*(one(ip, p) for ip in ips if _in(network, ip) for p in PORTS))
    return out


def gateway_from_routes(text: str) -> str | None:
    """``/proc/net/route`` → the default route's next hop (the router), or None."""
    for r in (line.split() for line in text.splitlines()[1:]):
        if len(r) > 7 and r[1] == "00000000" and r[7] == "00000000" and r[2] != "00000000":
            return socket.inet_ntoa(int(r[2], 16).to_bytes(4, "little"))
    return None


def _ptr(ip: str) -> str | None:
    """The reverse-DNS (or DHCP-registered, when the LAN's resolver serves leases) name of ``ip``."""
    try:
        return _clean(socket.gethostbyaddr(ip)[0]) or None
    except (OSError, ValueError):
        return None


#: Only https carries the token; a test points this at a plain-http fake server.
_PVE_SCHEME = "https://"


def verify_off(cfg: dict[str, Any]) -> bool:
    """TLS checking is skipped only when ``verify_ssl`` is explicitly 0/false/no/off."""
    return str(cfg.get("verify_ssl") or "").strip().lower() in ("0", "false", "no", "off")


def proxmox_names(cfg: dict[str, Any], limit: int = 200) -> dict[str, str]:
    """mac -> guest name from Proxmox: GET /cluster/resources, then each guest's config for its MACs.

    Read-only (GET only). ``cfg`` is ``url``, ``token_id``, ``token_secret``, ``verify_ssl``,
    ``ca_file``; any missing, a non-https URL (the token would cross the LAN in clear) or an
    unreachable server gives ``{}``, so a name falls back to the PTR. TLS is verified unless
    ``verify_ssl`` is explicitly 0/false/no/off; a self-signed PVE is trusted by pointing
    ``ca_file`` at its CA. The token should be a privilege-separated PVEAuditor token: this
    code only reads, but the server enforces that, not us. It is sent as a header and never
    logged; errors are logged by class only.
    """
    url, tid, secret = (str(cfg.get(k) or "").strip() for k in ("url", "token_id", "token_secret"))
    if not (url and tid and secret):
        return {}
    if not url.lower().startswith(_PVE_SCHEME):
        _LOGGER.warning("census: proxmox url is not https; not sending the token")
        return {}
    try:
        ctx = ssl.create_default_context(cafile=str(cfg.get("ca_file") or "").strip() or None)
    except (OSError, ssl.SSLError) as err:
        _LOGGER.warning("census: proxmox ca_file unusable (%s)", type(err).__name__)
        return {}
    if verify_off(cfg):
        ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
    # No redirect: a 302 must not carry the token to another host.
    opener = urllib.request.build_opener(_NoRedirect, urllib.request.HTTPSHandler(context=ctx))
    headers = {"Authorization": f"PVEAPIToken={tid}={secret}"}

    def get(path: str) -> Any:
        req = urllib.request.Request(f"{url.rstrip('/')}/api2/json{path}", headers=headers, method="GET")
        with opener.open(req, timeout=5) as r:
            return json.loads(r.read(1 << 20)).get("data")

    out: dict[str, str] = {}
    try:
        for g in (get("/cluster/resources?type=vm") or [])[:limit]:
            kind = g.get("type")
            node, vmid, name = g.get("node"), g.get("vmid"), _clean(str(g.get("name") or ""))
            if kind not in ("qemu", "lxc") or not name or not re.fullmatch(r"[\w.-]+", str(node)) or not isinstance(vmid, int):
                continue
            conf = get(f"/nodes/{node}/{kind}/{vmid}/config") or {}
            for key, val in conf.items():
                if re.fullmatch(r"net\d+", key):
                    m = re.search(r"(?i)(?:^|[=,])((?:[0-9a-f]{2}:){5}[0-9a-f]{2})(?:,|$)", str(val))
                    if m:
                        out[m.group(1).lower()] = name
    except (OSError, ValueError, AttributeError, TypeError) as err:
        _LOGGER.warning("census: proxmox names unavailable (%s)", type(err).__name__)
    return out


async def run_census(network: ipaddress.IPv4Network, present: dict[str, str],
                     names: dict[str, str], gateway: str | None = None,
                     vm_names: dict[str, str] | None = None) -> list[dict[str, Any]]:
    """Classify every device in ``present`` (mac -> ip) with fresh SSDP, mDNS and port evidence.

    ``gateway`` is the default route's next hop; ``vm_names`` mac -> Proxmox guest name. A name
    the owner gave wins; else the router's and a VM's own (Proxmox, else PTR) name.
    """
    ips = list(present.values())
    got = await asyncio.gather(discover_ssdp(network, ips), discover_mdns(network, ips),
                               discover_ports(network, ips), return_exceptions=True)
    # One failed source (no multicast route, zeroconf missing) must not blank the census.
    ssdp: Any; mdns: Any; ports: Any
    ssdp, mdns, ports = ({} if isinstance(x, BaseException) else x for x in got)
    rows = []
    for mac, ip in sorted(present.items(), key=lambda kv: ipaddress.ip_address(kv[1])):
        row = classify(mac, ip, mdns.get(ip), ssdp.get(ip, ""), ports.get(ip), gateway=ip == gateway)
        row["name"] = names.get(mac) or (vm_names or {}).get(mac)
        if not row["name"] and (ip == gateway or row["evidence"]["vendor"] in ("Proxmox VM", "QEMU")):
            row["name"] = await asyncio.to_thread(_ptr, ip)
        rows.append(row)
    return rows
