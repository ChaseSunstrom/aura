"""netscan: who is home from the LAN's ARP answers (aura/integrations/netscan)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aura.const import STATE_HOME, STATE_NOT_HOME  # noqa: E402
from aura.core import Jarvis  # noqa: E402
from aura.integrations import netscan  # noqa: E402

ARP = """IP address       HW type     Flags       HW address            Mask     Device
192.168.1.1      0x1         0x2         9c:05:d6:11:22:33     *        eth0
192.168.1.141    0x1         0x2         A8:E2:91:AA:BB:CC     *        eth0
192.168.1.137    0x1         0x0         00:00:00:00:00:00     *        eth0
192.168.1.150    0x1         0x0         3a:11:22:33:44:55     *        eth0
172.17.0.2       0x1         0x2         c6:a5:2c:00:00:01     *        docker0
"""


def test_parse_arp_keeps_complete_entries_only():
    got = netscan.parse_arp(ARP)
    assert got["a8:e2:91:aa:bb:cc"] == "192.168.1.141"  # lower-cased
    assert "3a:11:22:33:44:55" not in got  # flags 0x0: asked, never answered
    assert "00:00:00:00:00:00" not in got


def test_private_mac_is_the_locally_administered_bit():
    assert netscan.is_private_mac("3a:11:22:33:44:55")
    assert not netscan.is_private_mac("a8:e2:91:aa:bb:cc")


def test_tracker_is_home_until_away_after_passes():
    assert netscan.tracker_state(100.0, 600.0, 600.0) == STATE_HOME
    assert netscan.tracker_state(100.0, 701.0, 600.0) == STATE_NOT_HOME
    assert netscan.tracker_state(None, 0.0, 600.0) == STATE_NOT_HOME


ROUTES = """Iface\tDestination\tGateway \tFlags\tRefCnt\tUse\tMetric\tMask\t\tMTU\tWindow\tIRTT
tailscale0\t0000FE64\t00000000\t0001\t0\t0\t0\t0000FFFF\t0\t0\t0
eth0\t00000000\t0101A8C0\t0003\t0\t0\t0\t00000000\t0\t0\t0
docker0\t000011AC\t00000000\t0001\t0\t0\t0\t0000FFFF\t0\t0\t0
eth0\t0001A8C0\t00000000\t0001\t0\t0\t0\t00FFFFFF\t0\t0\t0
"""


def test_the_lan_is_the_default_routes_subnet_not_the_tailnet():
    assert str(netscan.lan_from_routes(ROUTES)) == "192.168.1.0/24"


@pytest.fixture
async def jarvis(tmp_path, monkeypatch):
    table = tmp_path / "arp"
    table.write_text(ARP)
    monkeypatch.setattr(netscan, "ARP_TABLE", table)
    monkeypatch.setattr(netscan, "SETTLE_S", 0.0)
    monkeypatch.setattr(netscan, "poke", lambda network: None)
    instance = Jarvis(tmp_path)
    instance.config = {}
    await instance.async_start()
    yield instance
    await instance.async_stop()


async def _sweep(jarvis):
    sensor = next(e for e in jarvis.data["entity_objects"].values() if isinstance(e, netscan.LanDevicesSensor))
    await sensor.async_update_state()


async def test_a_named_device_is_home_and_survives_a_restart(jarvis, tmp_path):
    assert await netscan.async_setup(jarvis, {"subnet": "192.168.1.0/24"})
    await _sweep(jarvis)
    lan = jarvis.states.get("sensor.lan_devices")
    assert lan.state == "2"  # the docker bridge is outside the subnet
    assert {d["mac"] for d in lan.attributes["devices"]} == {"9c:05:d6:11:22:33", "a8:e2:91:aa:bb:cc"}

    result = await jarvis.services.async_call("netscan", "name_device", {"mac": "A8-E2-91-AA-BB-CC", "name": "Isa phone"},
                                              blocking=True, return_response=True)
    assert result["ok"] and result["state"] == STATE_HOME, result
    assert jarvis.states.get("device_tracker.isa_phone").state == STATE_HOME

    # Named devices are kept in .storage; a fresh instance starts them unknown, never not_home.
    again = Jarvis(tmp_path)
    again.config = {}
    await again.async_start()
    try:
        assert await netscan.async_setup(again, {"subnet": "192.168.1.0/24"})
        assert again.states.get("device_tracker.isa_phone").state == "unknown"
    finally:
        await again.async_stop()


async def test_a_bad_name_request_says_what_it_needs(jarvis):
    assert await netscan.async_setup(jarvis, {"subnet": "192.168.1.0/24"})
    result = await jarvis.services.async_call("netscan", "name_device", {"mac": "nope", "name": "x"},
                                              blocking=True, return_response=True)
    assert result["ok"] is False and "mac" in result["error"]


async def test_wake_on_lan_sends_the_magic_packet_to_a_named_device(jarvis, monkeypatch):
    import socket

    sent = []
    monkeypatch.setattr(netscan, "send_magic_packet", lambda mac, address, port: sent.append((mac, address, port)))
    assert await netscan.async_setup(jarvis, {"subnet": "192.168.1.0/24"})
    await jarvis.services.async_call("netscan", "name_device", {"mac": "a8:e2:91:aa:bb:cc", "name": "Gaming PC"},
                                     blocking=True)
    # HA's field names; the device is named instead of given by MAC.
    result = await jarvis.services.async_call(
        "wake_on_lan", "send_magic_packet", {"mac": "gaming pc", "broadcast_port": 7},
        blocking=True, return_response=True)
    assert result["ok"] and sent == [("a8:e2:91:aa:bb:cc", "192.168.1.255", 7)], result
    # The datagram itself, read off a loopback socket.
    monkeypatch.undo()
    listener = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    listener.bind(("127.0.0.1", 0))
    listener.settimeout(2)
    try:
        netscan.send_magic_packet("a8:e2:91:aa:bb:cc", "127.0.0.1", listener.getsockname()[1])
        payload = listener.recv(200)
    finally:
        listener.close()
    assert payload == b"\xff" * 6 + bytes.fromhex("a8e291aabbcc") * 16 and len(payload) == 102


async def test_wake_on_lan_sends_nowhere_but_the_lan_wol_ports(jarvis, monkeypatch):
    # Free address and port would make the house a UDP sender to anything it can reach.
    sent = []
    monkeypatch.setattr(netscan, "send_magic_packet", lambda mac, address, port: sent.append((mac, address, port)))
    assert await netscan.async_setup(jarvis, {"subnet": "192.168.1.0/24"})
    for bad in ({"broadcast_address": "8.8.8.8"}, {"broadcast_port": 53}, {"broadcast_address": "x"}):
        result = await jarvis.services.async_call("wake_on_lan", "send_magic_packet",
                                                  {"mac": "a8:e2:91:aa:bb:cc", **bad},
                                                  blocking=True, return_response=True)
        assert result["ok"] is False, bad
    assert sent == []


async def test_wake_on_lan_defaults_to_the_lan_broadcast_and_refuses_an_unknown_device(jarvis, monkeypatch):
    sent = []
    monkeypatch.setattr(netscan, "send_magic_packet", lambda mac, address, port: sent.append((mac, address, port)))
    assert await netscan.async_setup(jarvis, {"subnet": "192.168.1.0/24"})
    result = await jarvis.services.async_call("wake_on_lan", "send_magic_packet", {"mac": "9C-05-D6-11-22-33"},
                                              blocking=True, return_response=True)
    assert result["ok"] and sent == [("9c:05:d6:11:22:33", "192.168.1.255", 9)]
    result = await jarvis.services.async_call("wake_on_lan", "send_magic_packet", {"mac": "the toaster"},
                                              blocking=True, return_response=True)
    assert result["ok"] is False and "mac" in result["error"]


# --- census: classifier on recorded fixtures ---------------------------------------------------
from aura.integrations.netscan import census  # noqa: E402

FIXTURES = [  # (mac, mdns, ssdp, ports, kind, suggest)
    ("f4:f5:d8:00:00:01", {"_googlecast._tcp"}, "", {8008, 8009}, "cast", "cast"),
    ("00:17:88:aa:bb:cc", {"_hue._tcp", "_hap._tcp"}, "", {80, 443}, "bridge", "hue"),
    ("a0:11:22:33:44:55", {"_ipp._tcp", "_printer._tcp"}, "", {631, 9100}, "printer", None),
    ("00:11:32:11:22:33", {"_smb._tcp"}, "ST: upnp:rootdevice | SERVER: Synology DSM UPnP/1.0", {5000, 445}, "nas", None),
    ("a0:11:22:33:44:56", set(), "SERVER: Linux UPnP/1.0 Samsung TV DLNADOC", {8001}, "tv", "cast"),
    ("00:0e:58:01:02:03", {"_sonos._tcp", "_spotify-connect._tcp"}, "", {1400}, "speaker", None),
    ("a0:11:22:33:44:57", {"_workstation._tcp", "_ssh._tcp"}, "", {22}, "pc", None),
    ("24:0a:c4:01:02:03", {"_esphomelib._tcp"}, "", set(), "iot", "esphome"),
    ("a0:11:22:33:44:58", set(), "", {53, 80, 443}, "router", None),
    ("3a:11:22:33:44:55", set(), "", set(), "phone", None),  # randomised MAC, nothing else
    ("a0:11:22:33:44:59", set(), "", set(), "unknown", None),
]


@pytest.mark.parametrize("mac,mdns,ssdp,ports,kind,suggest", FIXTURES)
def test_census_classifies_recorded_fixtures(mac, mdns, ssdp, ports, kind, suggest):
    got = census.classify(mac, "192.168.1.9", mdns, ssdp, ports)
    assert (got["kind"], got["suggest"]) == (kind, suggest)
    assert got["evidence"]["mdns"] == sorted(mdns) and got["evidence"]["ports"] == sorted(ports)


def test_census_confidence_is_low_without_a_protocol_answer():
    assert census.classify("3a:11:22:33:44:55", "x")["confidence"] == "low"
    assert census.classify("00:11:32:11:22:33", "x")["evidence"]["vendor"] == "Synology"
    assert census.classify("f4:f5:d8:00:00:01", "x", {"_googlecast._tcp"})["confidence"] == "high"


async def test_the_census_service_lists_kinds_and_offers_the_unnamed(jarvis, monkeypatch):
    arp = ("IP address HW type Flags HW address Mask Device\n"
           "192.168.1.5 0x1 0x2 f4:f5:d8:00:00:01 * eth0\n192.168.1.6 0x1 0x2 3a:11:22:33:44:55 * eth0\n")
    monkeypatch.setattr(netscan, "SETTLE_S", 0.0)
    monkeypatch.setattr(netscan, "poke", lambda network: None)
    monkeypatch.setattr(netscan, "ARP_TABLE", type("P", (), {"read_text": lambda self: arp})())

    async def mdns(net, ips):
        return {"192.168.1.5": {"_googlecast._tcp"}}

    async def none(*a, **k):
        return {}

    monkeypatch.setattr(census, "discover_mdns", mdns)
    monkeypatch.setattr(census, "discover_ssdp", none)
    monkeypatch.setattr(census, "discover_ports", none)
    assert await netscan.async_setup(jarvis, {"subnet": "192.168.1.0/24", "census": False})
    await _sweep(jarvis)
    await jarvis.services.async_call("netscan", "name_device", {"mac": "3a:11:22:33:44:55", "name": "Isa's phone"},
                                     blocking=True, return_response=True)
    res = await jarvis.services.async_call("netscan", "census", {}, blocking=True, return_response=True)
    by = {d["mac"]: d for d in res["devices"]}
    assert by["f4:f5:d8:00:00:01"]["kind"] == "cast" and by["f4:f5:d8:00:00:01"]["name"] is None
    assert by["3a:11:22:33:44:55"]["name"] == "Isa's phone"
    assert res["unnamed"] == ["f4:f5:d8:00:00:01"]
    attr = jarvis.states.get("sensor.lan_devices").attributes
    assert {d["mac"]: d["kind"] for d in attr["devices"]}["f4:f5:d8:00:00:01"] == "cast"


async def test_census_ports_stay_on_the_subnet():
    import ipaddress
    net = ipaddress.IPv4Network("192.168.1.0/24")
    # An address off the subnet is never connected to, whatever the ARP table claims.
    assert await census.discover_ports(net, ["8.8.8.8", "100.64.0.1"]) == {}


def test_a_census_follows_only_the_responders_own_plain_description_and_cleans_its_words():
    # Commit review, 2026-10-09: a LOCATION with userinfo or another host must not be fetched,
    # and a device's advertised name reaches the model as a plain label only.
    from aura.integrations.netscan import census
    ip = "192.168.1.5"
    assert census._own_location(f"http://{ip}:80@evil.example/desc.xml", ip) is None
    assert census._own_location(f"http://user@{ip}/desc.xml", ip) is None
    assert census._own_location("http://192.168.1.6/desc.xml", ip) is None
    assert census._own_location(f"https://{ip}/desc.xml", ip) is None
    assert census._own_location(f"http://{ip}:49152/desc.xml", ip) == f"http://{ip}:49152/desc.xml"
    assert census._clean("Living Room TV <ignore> {rules}; do X!") == "Living Room TV ignore rules do X"


async def test_a_device_never_seen_before_is_said_once_and_remembered_across_restarts(jarvis, tmp_path):
    """T-0545: the first sweep learns the house as it is, quietly; a MAC never seen after that
    fires netscan_new_device and is told to the owner, once, and a restart does not re-announce it."""
    heard, told = [], []
    jarvis.bus.listen(netscan.EVENT_NEW_DEVICE, lambda event: heard.append(event.data))

    async def notify(call):
        told.append(dict(call.data))
        return {"ok": True}

    jarvis.services.register("companion", "notify", notify, supports_response=True)
    assert await netscan.async_setup(jarvis, {"subnet": "192.168.1.0/24", "census": False})
    await _sweep(jarvis)
    await jarvis.async_block_till_done()
    assert heard == [] and told == []

    (tmp_path / "arp").write_text(ARP + "192.168.1.77     0x1         0x2         de:ad:be:ef:00:01     *        eth0\n")
    await _sweep(jarvis)
    await _sweep(jarvis)
    await jarvis.async_block_till_done()
    assert [h["mac"] for h in heard] == ["de:ad:be:ef:00:01"] and heard[0]["ip"] == "192.168.1.77"
    assert len(told) == 1 and "192.168.1.77" in told[0]["message"] and told[0]["kind"] == "notify"
    assert "de:ad" not in told[0]["message"]  # it may be spoken: no MAC read aloud

    again = Jarvis(tmp_path)
    again.config = {}
    await again.async_start()
    try:
        later = []
        again.bus.listen(netscan.EVENT_NEW_DEVICE, lambda event: later.append(event.data))
        assert await netscan.async_setup(again, {"subnet": "192.168.1.0/24", "census": False})
        await _sweep(again)
        await again.async_block_till_done()
        assert later == []
    finally:
        await again.async_stop()


# --- T-0600: the router and each Proxmox VM get a name -----------------------------------------
import http.server  # noqa: E402
import json  # noqa: E402
import threading  # noqa: E402

ROUTE = ("Iface\tDestination\tGateway\tFlags\tRefCnt\tUse\tMetric\tMask\tMTU\tWindow\tIRTT\n"
         "eth0\t00000000\t011E140A\t0003\t0\t0\t0\t00000000\t0\t0\t0\n"
         "eth0\t001E140A\t00000000\t0001\t0\t0\t0\t00FFFFFF\t0\t0\t0\n")


def test_the_default_gateway_comes_from_the_route_table():
    assert census.gateway_from_routes(ROUTE) == "10.20.30.1"
    assert census.gateway_from_routes("Iface\tDestination\n") is None


class _Pve(http.server.BaseHTTPRequestHandler):
    seen: list = []

    def do_GET(self):
        _Pve.seen.append((self.command, self.path, self.headers.get("Authorization")))
        body = {
            "/api2/json/cluster/resources?type=vm": [
                {"vmid": 100, "node": "pve", "name": "hermes <vm>", "type": "qemu"},
                {"vmid": 101, "node": "pve", "name": "dns", "type": "lxc"}],
            "/api2/json/nodes/pve/qemu/100/config": {"net0": "virtio=BC:24:11:AA:BB:CC,bridge=vmbr0"},
            "/api2/json/nodes/pve/lxc/101/config": {"net0": "name=eth0,bridge=vmbr0,hwaddr=BC:24:11:00:00:02,ip=dhcp"},
        }.get(self.path)
        self.send_response(200 if body is not None else 404)
        self.end_headers()
        self.wfile.write(json.dumps({"data": body}).encode())

    def log_message(self, *a):
        pass


@pytest.fixture
def pve():
    _Pve.seen = []
    srv = http.server.HTTPServer(("127.0.0.1", 0), _Pve)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def _ctx_for(monkeypatch, **cfg):
    """The SSL context proxmox_names builds for an https URL nobody answers."""
    import ssl
    made = []
    real = ssl.create_default_context
    def fake(**kw):
        made.append((kw, real(**{**kw, "cafile": None})))  # record the ask; a missing file would raise
        return made[-1][1]

    monkeypatch.setattr(census.ssl, "create_default_context", fake)
    census.proxmox_names({"url": "https://127.0.0.1:1", "token_id": "a", "token_secret": "b", **cfg})
    return made[0]


def test_proxmox_tls_is_verified_by_default_and_takes_a_ca_file(monkeypatch):
    import ssl
    kw, ctx = _ctx_for(monkeypatch)
    assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname and kw["cafile"] is None
    for v in ("", "true", "1", "garbage"):  # anything but an explicit off stays verified
        assert _ctx_for(monkeypatch, verify_ssl=v)[1].verify_mode == ssl.CERT_REQUIRED
    assert _ctx_for(monkeypatch, ca_file="/nope.pem")[0]["cafile"] == "/nope.pem"


def test_proxmox_tls_is_skipped_only_when_explicitly_off_and_setup_warns_once(monkeypatch, caplog):
    import ssl
    for v in ("0", "false", "No", "OFF"):
        assert _ctx_for(monkeypatch, verify_ssl=v)[1].verify_mode == ssl.CERT_NONE
    assert census.proxmox_names({"url": "https://127.0.0.1:1", "token_id": "a", "token_secret": "b",
                                 "ca_file": "/nope.pem"}) == {}  # a bad CA file degrades, not raises


async def test_setup_warns_once_when_proxmox_tls_is_off(jarvis, caplog):
    caplog.set_level("WARNING")
    cfg = {"subnet": "192.168.1.0/24", "census": False,
           "proxmox": {"url": "https://pve:8006", "token_id": "a", "token_secret": "b", "verify_ssl": "false"}}
    assert await netscan.async_setup(jarvis, cfg)
    assert caplog.text.count("PROXMOX_VERIFY_SSL is off") == 1 and "token" in caplog.text


def test_a_non_https_proxmox_url_gets_no_token(pve, monkeypatch):
    sent = []
    monkeypatch.setattr(census.urllib.request.OpenerDirector, "open", lambda *a, **k: sent.append(a))
    assert census.proxmox_names({"url": pve, "token_id": "u@pam!t", "token_secret": "s3cret"}) == {}
    assert sent == [] and _Pve.seen == []


def test_proxmox_names_come_from_read_only_gets_keyed_by_mac(pve, monkeypatch):
    monkeypatch.setattr(census, "_PVE_SCHEME", "http://")  # the fake server is plain http
    got = census.proxmox_names({"url": pve, "token_id": "u@pam!t", "token_secret": "s3cret"})
    assert got == {"bc:24:11:aa:bb:cc": "hermes vm", "bc:24:11:00:00:02": "dns"}
    assert {m for m, _p, _a in _Pve.seen} == {"GET"}
    assert {a for _m, _p, a in _Pve.seen} == {"PVEAPIToken=u@pam!t=s3cret"}


def test_proxmox_names_are_empty_when_unset_or_unreachable(caplog):
    assert census.proxmox_names({}) == {}
    assert census.proxmox_names({"url": "http://127.0.0.1:1", "token_id": "a", "token_secret": "topsecret"}) == {}
    assert "topsecret" not in caplog.text


async def test_a_census_names_the_router_and_the_vms(monkeypatch):
    import ipaddress
    net = ipaddress.IPv4Network("10.20.30.0/24")
    present = {"9c:05:d6:11:22:33": "10.20.30.1", "bc:24:11:aa:bb:cc": "10.20.30.20",
               "52:54:00:11:22:33": "10.20.30.21", "f4:f5:d8:00:00:01": "10.20.30.30"}

    async def none(*a, **k):
        return {}

    monkeypatch.setattr(census, "discover_mdns", none)
    monkeypatch.setattr(census, "discover_ssdp", none)
    monkeypatch.setattr(census, "discover_ports", none)
    monkeypatch.setattr(census, "_ptr", lambda ip: {"10.20.30.1": "gw.lan", "10.20.30.21": "docker1.lan"}.get(ip))
    rows = await census.run_census(net, present, {}, gateway="10.20.30.1",
                                   vm_names={"bc:24:11:aa:bb:cc": "hermes"})
    by = {r["mac"]: r for r in rows}
    assert by["9c:05:d6:11:22:33"]["kind"] == "router" and by["9c:05:d6:11:22:33"]["name"] == "gw.lan"
    assert by["bc:24:11:aa:bb:cc"]["name"] == "hermes"          # Proxmox
    assert by["52:54:00:11:22:33"]["name"] == "docker1.lan"      # QEMU MAC, no Proxmox answer: PTR
    assert by["f4:f5:d8:00:00:01"]["name"] is None                # not a VM: no guessing a name
    owner = await census.run_census(net, present, {"9c:05:d6:11:22:33": "Mine"}, gateway="10.20.30.1")
    assert {r["mac"]: r for r in owner}["9c:05:d6:11:22:33"]["name"] == "Mine"
