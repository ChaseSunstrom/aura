"""Netscan — who is home, from the devices answering on the home Wi-Fi/LAN.

    netscan:
      scan_interval: 120   # seconds between sweeps
      away_after: 600      # a named device unseen this long is not_home
      alert_new: true      # tell the owner when a device never seen before joins
      subnet: 192.168.1.0/24   # optional; default: the default route's on-link subnet

Every sweep sends one empty UDP datagram to every address in the subnet (the
kernel has to ARP-resolve each one to send it, which needs no raw socket and no
root), waits for the answers, and reads the kernel's neighbour table. What it
publishes:

* ``sensor.lan_devices``: how many devices answered, and each one's ``mac``,
  ``ip``, ``name`` (when named), ``private`` (a randomised, locally
  administered MAC: a phone or tablet hiding its hardware address) and
  ``first_seen`` (since this process started: "the one that just joined" is
  how a new phone gets named).
  After the census (``netscan.census``, run once at start-up and on demand; see
  ``census.py``) each also has ``kind`` (tv, speaker, printer, nas, phone, pc, camera,
  bridge, cast, iot, router, unknown), ``confidence``, ``evidence`` and ``suggest`` (the
  integration it could be set up with): the console offers to name the unnamed ones.
* ``device_tracker.<name>`` for every device named with ``netscan.name_device``
  (``mac`` + ``name``): ``home`` while it answers, ``not_home`` after
  ``away_after`` without an answer. A ``person:`` entry can list it, and an
  automation can trigger on it, like any other tracker.
* ``netscan_new_device`` (``mac``, ``ip``, ``private``) the first time a MAC the house has
  never seen answers — remembered in ``.storage``, so a restart does not re-announce, and the
  very first sweep learns the house silently — told to the owner through ``companion.notify``
  (a quiet notification under do not disturb) unless ``alert_new: false``.
* ``wake_on_lan.send_magic_packet`` (Home Assistant's name and fields: ``mac``,
  ``broadcast_address``, ``broadcast_port``): ``mac`` may be a named device's
  name; the default address is the swept subnet's broadcast.

What it does not see: a device on another subnet or VLAN, one that sleeps its
Wi-Fi and stops answering ARP (some phones in deep doze: ``away_after`` is the
margin), and a phone that changes its private MAC for this network (Android and
iOS keep one per network unless set to rotate it).
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import re
import socket
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ...const import STATE_HOME, STATE_NOT_HOME
from ...entity import Entity, EntityPlatform
from ...services import ServiceCall
from ...state import slugify
from ...store import Store
from . import census as census_mod

if TYPE_CHECKING:  # pragma: no cover
    from ...core import Jarvis

_LOGGER = logging.getLogger(__name__)

DOMAIN = "netscan"
#: A MAC this house has never seen answered a sweep (T-0545): ``mac``, ``ip``, ``private``.
EVENT_NEW_DEVICE = "netscan_new_device"
ARP_TABLE = Path("/proc/net/arp")
ROUTES = Path("/proc/net/route")
#: The kernel's ARP probing gives up on a silent address after about eight
#: seconds (5 s delay_first_probe_time + 3 probes); reading sooner would still
#: count a device that left as present.
SETTLE_S = 9.0
_MAC = re.compile(r"^[0-9a-f]{2}(:[0-9a-f]{2}){5}$")


def parse_arp(text: str) -> dict[str, str]:
    """``/proc/net/arp`` → {mac: ip} for complete entries (flags 0x2) only."""
    out: dict[str, str] = {}
    for line in text.splitlines()[1:]:
        parts = line.split()
        if len(parts) < 4:
            continue
        ip, flags, mac = parts[0], parts[2], parts[3].lower()
        if int(flags, 16) & 0x2 and _MAC.match(mac) and mac != "00:00:00:00:00:00":
            out[mac] = ip
    return out


def is_private_mac(mac: str) -> bool:
    """A locally administered (randomised) address: bit 1 of the first octet."""
    return bool(int(mac[:2], 16) & 0x02)


def normalise_mac(mac: str) -> str | None:
    m = str(mac).strip().lower().replace("-", ":")
    return m if _MAC.match(m) else None


def send_magic_packet(mac: str, address: str, port: int) -> None:
    """Six 0xff bytes then the MAC sixteen times, one UDP datagram.

    Fire and forget: nothing answers a magic packet, so a device that is
    unplugged, has WoL off in its BIOS/NIC, or sits on another subnet (a
    broadcast does not cross a router) stays asleep and this still succeeds.
    """
    payload = b"\xff" * 6 + bytes.fromhex(mac.replace(":", "")) * 16
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.sendto(payload, (address, port))


def tracker_state(last_seen: float | None, now: float, away_after: float) -> str:
    return STATE_HOME if last_seen is not None and now - last_seen <= away_after else STATE_NOT_HOME


def lan_from_routes(text: str) -> ipaddress.IPv4Network:
    """``/proc/net/route`` → the on-link subnet of the interface the default route uses.

    Not "the address a packet would leave from": with a tailnet exit node that is the
    tailscale address, and sweeping 100.x finds nobody.
    """
    rows = [line.split() for line in text.splitlines()[1:] if line.strip()]

    def ip(hex_le: str) -> str:
        return socket.inet_ntoa(int(hex_le, 16).to_bytes(4, "little"))

    default = next((r[0] for r in rows if len(r) > 7 and r[1] == "00000000" and r[7] == "00000000"), None)
    for r in rows:
        if len(r) > 7 and r[0] == default and r[1] != "00000000" and r[2] == "00000000":
            return ipaddress.IPv4Network(f"{ip(r[1])}/{ip(r[7])}", strict=False)
    raise ValueError("no default route with an on-link subnet")


def poke(network: ipaddress.IPv4Network) -> None:
    """One empty datagram to the discard port of every host: each send makes the kernel ARP for it."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.setblocking(False)
        for host in network.hosts():
            try:
                s.sendto(b"", (str(host), 9))
            except OSError:
                pass  # an unreachable or refused address answers nothing, which is the answer


class NetScan:
    def __init__(self, network: ipaddress.IPv4Network, away_after: float, names: dict[str, str]) -> None:
        self.network = network
        self.away_after = away_after
        self.names = names  # mac -> name
        self.proxmox: dict[str, Any] = {}  # url/token_id/token_secret/verify_ssl; empty = no Proxmox names
        self.present: dict[str, str] = {}  # mac -> ip, from the last sweep
        self.last_seen: dict[str, float] = {}
        # In memory: "the one that just joined" since this process started. Arrivals that
        # matter across restarts are `known`, which is persisted.
        self.first_seen: dict[str, float] = {}
        #: Every MAC this house has ever seen; None until loaded, and the first sweep after
        #: that seeds it silently, so installing netscan does not announce the whole house.
        self.known: set[str] | None = None
        self.swept_at: float | None = None
        self.census: dict[str, dict[str, Any]] = {}  # mac -> census row, from the last census
        self.census_at: float | None = None

    async def run_census(self) -> list[dict[str, Any]]:
        try:
            gateway = census_mod.gateway_from_routes(await asyncio.to_thread(ROUTES.read_text))
        except OSError:
            gateway = None
        vm_names = await asyncio.to_thread(census_mod.proxmox_names, self.proxmox)
        rows = await census_mod.run_census(self.network, self.present, self.names, gateway, vm_names)
        self.census = {r["mac"]: r for r in rows}
        self.census_at = time.time()
        return rows

    async def sweep(self) -> list[str]:
        """Sweep the subnet; the MACs it saw for the first time ever, returned rather than kept
        on the scanner, so two overlapping sweeps (start-up's and a poll) cannot both report one."""
        await asyncio.to_thread(poke, self.network)
        await asyncio.sleep(SETTLE_S)
        table = await asyncio.to_thread(ARP_TABLE.read_text)
        now = time.time()
        self.present = {mac: ip for mac, ip in parse_arp(table).items()
                        if ipaddress.ip_address(ip) in self.network}
        for mac in self.present:
            self.last_seen[mac] = now
            self.first_seen.setdefault(mac, now)
        arrived: list[str] = []
        if self.known is None:
            self.known = set(self.present)
        else:
            arrived = sorted(m for m in self.present if m not in self.known)
            self.known.update(arrived)
        self.swept_at = now
        return arrived

    def _guess(self, mac: str) -> dict[str, Any]:
        c = self.census.get(mac)
        return {} if c is None else {"kind": c["kind"], "confidence": c["confidence"],
                                     "suggest": c["suggest"], "evidence": c["evidence"]}

    def devices(self) -> list[dict[str, Any]]:
        return [{"mac": mac, "ip": ip, "name": self.names.get(mac), "private": is_private_mac(mac),
                 "first_seen": self.first_seen.get(mac), **self._guess(mac)}
                for mac, ip in sorted(self.present.items(), key=lambda kv: ipaddress.ip_address(kv[1]))]


class LanDevicesSensor(Entity):
    """``sensor.lan_devices``; its poll is the sweep that every tracker reads."""

    def __init__(self, scan: NetScan, on_swept) -> None:
        self._scan = scan
        self._on_swept = on_swept
        self._attr_name = "LAN devices"
        self._attr_unique_id = "netscan_lan_devices"
        self._attr_icon = "mdi:lan"
        self._attr_should_poll = True
        self._attr_state: Any = None
        self._attr_extra_attributes: dict[str, Any] = {}

    async def async_update(self) -> None:
        try:
            arrived = await self._scan.sweep()
        except OSError as err:
            _LOGGER.warning("netscan: sweep failed: %s", err)
            return
        self._attr_state = len(self._scan.present)
        self.set_devices_attr()
        await self._on_swept(arrived)

    def set_devices_attr(self) -> None:
        self._attr_extra_attributes = {"devices": self._scan.devices(), "subnet": str(self._scan.network),
                                       "census_at": self._scan.census_at}
        if self.jarvis is not None:
            self.async_write_state()


class DeviceTracker(Entity):
    def __init__(self, scan: NetScan, mac: str, name: str) -> None:
        self._scan = scan
        self.mac = mac
        self._attr_name = name
        self._attr_unique_id = f"netscan_{mac.replace(':', '')}"
        self._attr_icon = "mdi:cellphone-wireless"
        self._attr_state: Any = None
        self._attr_extra_attributes: dict[str, Any] = {"mac": mac, "source_type": "router"}

    def refresh(self) -> None:
        seen = self._scan.last_seen.get(self.mac)
        # unknown until the first sweep: a not_home at start-up would fire every "left home" automation.
        self._attr_state = (None if self._scan.swept_at is None
                            else tracker_state(seen, time.time(), self._scan.away_after))
        self._attr_extra_attributes = {"mac": self.mac, "source_type": "router", "ip": self._scan.present.get(self.mac),
                                       "last_seen": seen}


async def async_setup(jarvis: "Jarvis", config: Any) -> bool:
    config = config if isinstance(config, dict) else {}
    store = Store(jarvis.config_dir, DOMAIN)
    saved = await store.load() or {}
    names: dict[str, str] = {m: n for m, n in (saved.get("names") or {}).items() if normalise_mac(m)}
    try:
        network = ipaddress.IPv4Network(config["subnet"], strict=False) if config.get("subnet") else lan_from_routes(ROUTES.read_text())
    except (OSError, ValueError) as err:
        _LOGGER.warning("netscan: no subnet to sweep (%s); not starting", err)
        return False
    scan = NetScan(network, float(config.get("away_after", 600)), names)
    scan.proxmox = config["proxmox"] if isinstance(config.get("proxmox"), dict) else {}
    if scan.proxmox.get("url") and census_mod.verify_off(scan.proxmox):
        _LOGGER.warning("netscan: PROXMOX_VERIFY_SSL is off; the Proxmox API token travels over unverified TLS")
    if isinstance(saved.get("known"), list):
        scan.known = {m for m in saved["known"] if normalise_mac(m)}
    alert_new = bool(config.get("alert_new", True))
    #: The seeded set is written once, so a restart before any arrival still knows the house.
    unsaved = {"known": scan.known is None}

    def stored() -> dict[str, Any]:
        return {"names": names, "known": sorted(scan.known or ())}
    # Two platforms because the domain is the entity id's prefix: sensor.lan_devices, device_tracker.<name>.
    sensors = EntityPlatform(jarvis, "sensor", DOMAIN, scan_interval=max(float(config.get("scan_interval", 120)), 30.0))
    platform = EntityPlatform(jarvis, "device_tracker", DOMAIN)
    trackers: dict[str, DeviceTracker] = {}

    async def on_swept(arrived: list[str]) -> None:
        for t in trackers.values():
            t.refresh()
            t.async_write_state()
        if arrived or unsaved["known"]:
            await store.save(stored())
            unsaved["known"] = False
        for mac in arrived:
            ip = scan.present.get(mac, "")
            await jarvis.bus.async_fire(EVENT_NEW_DEVICE, {"mac": mac, "ip": ip, "private": is_private_mac(mac)})
            if alert_new and jarvis.services.has_service("companion", "notify"):
                # No MAC in the words: this can be spoken, and a MAC read aloud is noise (home-presence).
                hidden = ", probably a phone or tablet" if is_private_mac(mac) else ""
                try:
                    await jarvis.services.async_call("companion", "notify", {
                        "message": f"A device I have not seen before joined the network at {ip}{hidden}.",
                        "importance": "normal", "kind": "notify",
                    }, blocking=True, return_response=True)
                except Exception:  # noqa: BLE001 - the event and the sensor already carry it
                    _LOGGER.exception("netscan: could not tell anyone about %s", mac)

    async def add_tracker(mac: str, name: str) -> DeviceTracker:
        old = trackers.pop(mac, None)
        if old is not None:
            await platform.async_remove_entity(old.entity_id)
        t = DeviceTracker(scan, mac, name)
        t.refresh()
        trackers[mac] = t
        await platform.async_add_entities([t])
        return t

    async def handle_name(call: ServiceCall) -> dict[str, Any]:
        mac, name = normalise_mac(call.get("mac") or ""), str(call.get("name") or "").strip()
        if not mac or not name:
            return {"ok": False, "error": "mac (aa:bb:cc:dd:ee:ff) and name are required"}
        names[mac] = name
        await store.save(stored())
        t = await add_tracker(mac, name)
        return {"ok": True, "entity_id": t.entity_id, "state": t.state}

    async def handle_forget(call: ServiceCall) -> dict[str, Any]:
        mac = normalise_mac(call.get("mac") or "") or next(
            (m for m, n in names.items() if slugify(n) == slugify(str(call.get("name") or ""))), None)
        if mac is None or mac not in names:
            return {"ok": False, "error": "no named device with that mac or name"}
        del names[mac]
        await store.save(stored())
        old = trackers.pop(mac, None)
        if old is not None:
            await platform.async_remove_entity(old.entity_id)
        return {"ok": True}

    async def handle_census(call: ServiceCall) -> dict[str, Any]:
        if not scan.present:
            await on_swept(await scan.sweep())  # a stranger first seen here is still said
        rows = await scan.run_census()
        sensor.set_devices_attr()
        return {"ok": True, "devices": rows, "unnamed": [r["mac"] for r in rows if not r["name"]]}

    async def handle_wake(call: ServiceCall) -> dict[str, Any]:
        # HA's wake_on_lan.send_magic_packet, so Hermes' ha_call_service reaches it unchanged;
        # ``mac`` may also be a device's netscan name ("wake the gaming PC").
        given = str(call.get("mac") or "")
        mac = normalise_mac(given) or next((m for m, n in names.items() if slugify(n) == slugify(given)), None)
        if mac is None:
            return {"ok": False, "error": "mac (aa:bb:cc:dd:ee:ff) or a name from netscan.name_device is required"}
        # The LAN's own broadcast, not 255.255.255.255: on a multi-homed host (tailnet, docker
        # bridges) the limited broadcast leaves by whichever interface the kernel picks.
        address = str(call.get("broadcast_address") or network.broadcast_address)
        port = int(call.get("broadcast_port") or 9)
        # Only the LAN and the two WoL ports: a free address and port would make this a
        # UDP sender to any host the house can reach (commit review, 2026-10-09).
        try:
            on_lan = ipaddress.IPv4Address(address) in network
        except ValueError:
            on_lan = False
        if not on_lan or port not in (7, 9):
            return {"ok": False, "error": f"broadcast_address must be on {network} and broadcast_port 7 or 9"}
        try:
            send_magic_packet(mac, address, port)
        except OSError as err:
            return {"ok": False, "error": f"send failed: {err}"}
        return {"ok": True, "mac": mac, "broadcast_address": address, "broadcast_port": port}

    jarvis.services.register("wake_on_lan", "send_magic_packet", handle_wake,
                             description="Wake a PC or server on the LAN (Wake-on-LAN).",
                             fields={"mac": {"description": "Its MAC, or the name given with netscan.name_device."},
                                     "broadcast_address": {"description": "Default: the LAN's broadcast address."},
                                     "broadcast_port": {"description": "Default: 9."}},
                             supports_response=True)
    jarvis.services.register(DOMAIN, "census", handle_census,
                             description="Classify every LAN device (TV, speaker, printer, NAS, phone, PC...) "
                                         "from mDNS, SSDP, open ports and MAC vendor; offer the unnamed ones to name.",
                             supports_response=True)
    jarvis.services.register(DOMAIN, "name_device", handle_name,
                             description="Name a device seen on the LAN; it becomes device_tracker.<name>.",
                             fields={"mac": {"description": "Its MAC, from sensor.lan_devices."},
                                     "name": {"description": "What to call it, e.g. Isa's phone."}},
                             supports_response=True)
    jarvis.services.register(DOMAIN, "forget_device", handle_forget,
                             description="Stop tracking a named LAN device.",
                             fields={"mac": {"description": "Its MAC."}, "name": {"description": "Or its name."}},
                             supports_response=True)

    jarvis.data[DOMAIN] = scan
    jarvis.data[f"{DOMAIN}_platform"] = platform
    for mac, name in names.items():
        await add_tracker(mac, name)
    sensor = LanDevicesSensor(scan, on_swept)
    await sensors.async_add_entities([sensor])

    async def first() -> None:
        # The poll loop sleeps first; the first answer should not wait a whole interval.
        await sensor.async_update_state()
        if config.get("census", True):
            await handle_census(ServiceCall(DOMAIN, "census", {}))

    jarvis.async_create_task(first())
    return True
