"""get_network_info on canned Linux, Windows and macOS command output.

Nothing here runs a real command: ``netinfo._run`` is replaced by a table of
argv-prefix -> stdout, so each platform's reader is pinned on the text its OS
actually prints.
"""

from __future__ import annotations

import json

import pytest

from aura_desktop.actions import netinfo
from aura_desktop.actions.builtins import TIER_TABLE
from aura_desktop.policy import ActionTier

TAILSCALE_UP = json.dumps(
    {
        "BackendState": "Running",
        "Self": {"DNSName": "desk.tail1234.ts.net.", "TailscaleIPs": ["100.64.0.7", "fd7a:115c::7"]},
    }
)

LINUX_ADDR = json.dumps(
    [
        {"ifname": "lo", "addr_info": [{"family": "inet", "local": "127.0.0.1", "prefixlen": 8}]},
        {
            "ifname": "wlp2s0",
            "operstate": "UP",
            "addr_info": [
                {"family": "inet", "local": "192.168.1.20", "prefixlen": 24},
                {"family": "inet6", "local": "fe80::1", "prefixlen": 64},
            ],
        },
        {"ifname": "docker0", "operstate": "DOWN", "addr_info": []},
    ]
)
LINUX_ROUTE = json.dumps([{"dst": "default", "gateway": "192.168.1.1", "dev": "wlp2s0"}])

WINDOWS_PS = json.dumps(
    {
        "a": [
            {"InterfaceAlias": "Wi-Fi", "IPAddress": "192.168.1.30", "PrefixLength": 24, "AddressFamily": 2},
            {"InterfaceAlias": "Wi-Fi", "IPAddress": "fe80::2%7", "PrefixLength": 64, "AddressFamily": 23},
            {"InterfaceAlias": "Loopback Pseudo-Interface 1", "IPAddress": "127.0.0.1",
             "PrefixLength": 8, "AddressFamily": 2},
        ],
        "r": [{"InterfaceAlias": "Wi-Fi", "NextHop": "192.168.1.1"}],
    }
)
WINDOWS_WLAN = """
There is 1 interface on the system:

    Name                   : Wi-Fi
    State                  : connected
    SSID                   : Home Net
    BSSID                  : aa:bb:cc:dd:ee:ff
    Signal                 : 90%
"""

MAC_IFCONFIG = """lo0: flags=8049<UP,LOOPBACK,RUNNING,MULTICAST> mtu 16384
\tinet 127.0.0.1 netmask 0xff000000
en0: flags=8863<UP,BROADCAST,SMART,RUNNING,SIMPLEX,MULTICAST> mtu 1500
\tether a4:83:e7:00:00:01
\tinet6 fe80::1c%en0 prefixlen 64 secured scopeid 0x4
\tinet 10.0.0.5 netmask 0xffffff00 broadcast 10.0.0.255
utun3: flags=8051<UP,POINTOPOINT,RUNNING,MULTICAST> mtu 1280
\tinet 100.64.0.9 --> 100.64.0.9 netmask 0xffffffff
"""
MAC_ROUTE = """   route to: default
destination: default
       mask: default
    gateway: 10.0.0.1
  interface: en0
"""


def _fake(monkeypatch, ctx, system, outputs, tools=("ip", "nmcli", "tailscale", "networksetup",
                                                   "netsh", "powershell", "ifconfig", "route")):
    """Pretend to be ``system``; ``outputs`` maps an argv prefix to stdout."""
    import platform

    monkeypatch.setattr(platform, "system", lambda: system)
    monkeypatch.setattr(ctx, "which", lambda *names: next((n for n in names if n in tools), None))

    def run(argv, timeout=8.0):
        for prefix, out in outputs.items():
            if tuple(argv[: len(prefix)]) == prefix:
                return 0, out, ""
        return 1, "", "not canned"

    monkeypatch.setattr(netinfo, "_run", run)


def test_network_info_is_tier_one_like_the_phone():
    assert TIER_TABLE["get_network_info"] == ActionTier.AUTO
    assert netinfo.GetNetworkInfo().tier == ActionTier.AUTO


def test_network_info_on_linux(ctx, monkeypatch):
    _fake(monkeypatch, ctx, "Linux", {
        ("ip", "-j", "addr"): LINUX_ADDR,
        ("ip", "-j", "route"): LINUX_ROUTE,
        ("nmcli",): "no:Neighbour\nyes:Home\\:Net\n",
        ("tailscale",): TAILSCALE_UP,
    })
    data = netinfo.GetNetworkInfo().run(ctx, {}).data
    assert data["interfaces"] == [{
        "name": "wlp2s0",
        "addresses": [
            {"family": "ipv4", "address": "192.168.1.20", "prefix": 24},
            {"family": "ipv6", "address": "fe80::1", "prefix": 64},
        ],
    }]
    assert data["default_route"] == {"gateway": "192.168.1.1", "interface": "wlp2s0"}
    assert data["ssid"] == "Home:Net"
    assert data["transport"] == "wifi"
    assert data["tailnet"] == {
        "installed": True, "up": True, "addresses": ["100.64.0.7", "fd7a:115c::7"],
        "name": "desk.tail1234.ts.net",
    }


def test_network_info_on_windows(ctx, monkeypatch):
    _fake(monkeypatch, ctx, "Windows", {
        ("powershell",): WINDOWS_PS,
        ("netsh", "wlan"): WINDOWS_WLAN,
    }, tools=("powershell", "netsh"))
    data = netinfo.GetNetworkInfo().run(ctx, {}).data
    assert data["interfaces"] == [{
        "name": "Wi-Fi",
        "addresses": [
            {"family": "ipv4", "address": "192.168.1.30", "prefix": 24},
            {"family": "ipv6", "address": "fe80::2", "prefix": 64},
        ],
    }]
    assert data["default_route"] == {"gateway": "192.168.1.1", "interface": "Wi-Fi"}
    assert data["ssid"] == "Home Net"
    assert data["tailnet"] == {"installed": False, "up": False}


def test_network_info_on_macos(ctx, monkeypatch):
    _fake(monkeypatch, ctx, "Darwin", {
        ("ifconfig",): MAC_IFCONFIG,
        ("route",): MAC_ROUTE,
        ("networksetup", "-getairportnetwork", "en0"): "You are not associated with an AirPort network.\n",
        ("tailscale",): json.dumps({"BackendState": "Stopped", "Self": {"TailscaleIPs": []}}),
    })
    data = netinfo.GetNetworkInfo().run(ctx, {}).data
    assert data["interfaces"] == [
        {"name": "en0", "addresses": [
            {"family": "ipv6", "address": "fe80::1c", "prefix": 64},
            {"family": "ipv4", "address": "10.0.0.5", "prefix": 24},
        ]},
        {"name": "utun3", "addresses": [{"family": "ipv4", "address": "100.64.0.9", "prefix": 32}]},
    ]
    assert data["default_route"] == {"gateway": "10.0.0.1", "interface": "en0"}
    assert data["ssid"] is None
    assert data["transport"] == "other"
    assert data["tailnet"]["installed"] is True and data["tailnet"]["up"] is False


@pytest.mark.parametrize("system", ["Linux", "Windows", "Darwin"])
def test_network_info_with_nothing_installed_still_answers(ctx, monkeypatch, system):
    _fake(monkeypatch, ctx, system, {}, tools=())
    result = netinfo.GetNetworkInfo().run(ctx, {})
    assert result.ok
    assert result.data["interfaces"] == [] and result.data["default_route"] is None
    assert result.data["ssid"] is None and result.data["transport"] == "none"
    assert result.data["tailnet"] == {"installed": False, "up": False}


def test_macos_ssid_from_networksetup():
    assert netinfo._mac_ssid("Current Wi-Fi Network: Cafe 5G\n") == "Cafe 5G"
