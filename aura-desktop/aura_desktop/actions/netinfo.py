"""``get_network_info`` — what this machine is connected to.

The desktop twin of the phone's ``get_network_info`` (Tier 1 AUTO there and
here): addresses, the default route, the Wi-Fi name when the OS says it, and
whether the tailnet is up. Read-only, fixed argv, stdlib plus each OS's own
tools (``ip``/``nmcli`` on Linux, PowerShell/``netsh`` on Windows,
``ifconfig``/``route``/``networksetup`` on macOS, ``tailscale`` anywhere), so no
psutil is needed.

Not covered: whether the internet is actually reachable (the phone's
``internet`` key comes from Android validating the network; nothing here sends a
packet), and an SSID the OS hides — macOS 14.4+ ``networksetup`` and Windows 11
24H2 ``netsh`` without location access both answer without one, which is
reported as ``null``, never guessed.
"""

from __future__ import annotations

import ipaddress
import json
import re
from typing import Any

from ..policy import ActionTier
from .base import Action, ActionContext, ActionResult
from .system import _run

__all__ = ["GetNetworkInfo"]

# One PowerShell call for both addresses and the default route, as JSON, so
# Windows needs no text scraping of ipconfig (whose labels are localised).
_WINDOWS_PS = (
    "@{a=@(Get-NetIPAddress | Select-Object InterfaceAlias,IPAddress,PrefixLength,AddressFamily);"
    " r=@(Get-NetRoute -DestinationPrefix 0.0.0.0/0 -ErrorAction SilentlyContinue"
    " | Sort-Object RouteMetric | Select-Object InterfaceAlias,NextHop)}"
    " | ConvertTo-Json -Depth 3 -Compress"
)


def _addr(family: str, address: str, prefix: Any) -> dict[str, Any] | None:
    """One address, or None for loopback and junk — nobody asks for 127.0.0.1."""
    address = address.split("%", 1)[0]  # fe80::1%en0 -> fe80::1
    try:
        if ipaddress.ip_address(address).is_loopback:
            return None
    except ValueError:
        return None
    try:
        prefix = int(prefix)
    except (TypeError, ValueError):
        prefix = None
    return {"family": family, "address": address, "prefix": prefix}


def _group(rows: list[tuple[str, dict[str, Any] | None]]) -> list[dict[str, Any]]:
    """``(interface, address)`` pairs -> interfaces in first-seen order, empty ones dropped."""
    out: dict[str, list[dict[str, Any]]] = {}
    for name, addr in rows:
        if addr is not None:
            out.setdefault(name, []).append(addr)
    return [{"name": name, "addresses": addrs} for name, addrs in out.items()]


def _json(text: str) -> Any:
    try:
        return json.loads(text)
    except ValueError:
        return None


# --- Linux -------------------------------------------------------------------


def _linux_interfaces(text: str) -> list[dict[str, Any]]:
    rows = []
    for link in _json(text) or []:
        for info in link.get("addr_info") or []:
            family = {"inet": "ipv4", "inet6": "ipv6"}.get(info.get("family"))
            if family:
                rows.append((link.get("ifname", "?"), _addr(family, info.get("local", ""), info.get("prefixlen"))))
    return _group(rows)


def _linux_route(text: str) -> dict[str, Any] | None:
    routes = _json(text) or []
    if not routes:
        return None
    return {"gateway": routes[0].get("gateway"), "interface": routes[0].get("dev")}


def _nmcli_ssid(text: str) -> str | None:
    # `nmcli -t` escapes ':' in values as '\:'; the active row starts "yes:".
    for line in text.splitlines():
        active, _, ssid = line.partition(":")
        if active == "yes" and ssid:
            return ssid.replace("\\:", ":")
    return None


# --- Windows -----------------------------------------------------------------


def _windows(text: str) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    data = _json(text)
    if not isinstance(data, dict):
        return [], None
    rows = []
    for row in data.get("a") or []:
        # ConvertTo-Json writes the enum as a number on 5.1 and may write the
        # name on 7: accept both.
        family = {2: "ipv4", "IPv4": "ipv4", 23: "ipv6", "IPv6": "ipv6"}.get(row.get("AddressFamily"))
        if family:
            rows.append((row.get("InterfaceAlias", "?"), _addr(family, row.get("IPAddress", ""), row.get("PrefixLength"))))
    routes = data.get("r") or []
    route = {"gateway": routes[0].get("NextHop"), "interface": routes[0].get("InterfaceAlias")} if routes else None
    return _group(rows), route


def _netsh_ssid(text: str) -> str | None:
    for line in text.splitlines():
        key, sep, value = line.partition(":")
        if sep and key.strip() == "SSID" and value.strip():
            return value.strip()
    return None


# --- macOS -------------------------------------------------------------------


def _mac_interfaces(text: str) -> list[dict[str, Any]]:
    rows = []
    name = "?"
    for line in text.splitlines():
        if line and not line[0].isspace():
            name = line.split(":", 1)[0]
            continue
        parts = line.split()
        if len(parts) < 2 or parts[0] not in ("inet", "inet6"):
            continue
        if parts[0] == "inet":
            mask = parts[parts.index("netmask") + 1] if "netmask" in parts else None
            prefix = bin(int(mask, 16)).count("1") if mask and mask.startswith("0x") else None
            rows.append((name, _addr("ipv4", parts[1], prefix)))
        else:
            plen = parts[parts.index("prefixlen") + 1] if "prefixlen" in parts else None
            rows.append((name, _addr("ipv6", parts[1], plen)))
    return _group(rows)


def _mac_route(text: str) -> dict[str, Any] | None:
    fields = dict(
        (key.strip(), value.strip())
        for key, sep, value in (line.partition(":") for line in text.splitlines())
        if sep
    )
    if "gateway" not in fields and "interface" not in fields:
        return None
    return {"gateway": fields.get("gateway"), "interface": fields.get("interface")}


def _mac_ssid(text: str) -> str | None:
    match = re.search(r"Current Wi-Fi Network:\s*(.+)", text)
    return match.group(1).strip() if match else None


# --- tailnet -----------------------------------------------------------------


def _tailnet(ctx: ActionContext) -> dict[str, Any]:
    if ctx.which("tailscale") is None:
        return {"installed": False, "up": False}
    _code, out, _err = _run(["tailscale", "status", "--json"])
    data = _json(out)
    if not isinstance(data, dict):
        return {"installed": True, "up": False}
    up = data.get("BackendState") == "Running"
    me = data.get("Self") or {}
    result: dict[str, Any] = {"installed": True, "up": up}
    if up:
        result["addresses"] = list(me.get("TailscaleIPs") or [])
        result["name"] = (me.get("DNSName") or "").rstrip(".") or None
    return result


class GetNetworkInfo(Action):
    id = "get_network_info"
    tier = ActionTier.AUTO  # read-only, as on the phone
    description = (
        "This machine's network: interfaces and addresses, the default route, "
        "the Wi-Fi name when the OS says it, and whether the tailnet is up."
    )
    params_schema: dict[str, str] = {}
    capability = "system"
    timeout_s = 30.0  # three or four shell-outs at 8s each, worst case

    def run(self, ctx: ActionContext, params: dict[str, Any]) -> ActionResult:
        system = ctx.system
        ssid: str | None = None
        if system == "Windows":
            _c, out, _e = _run(["powershell", "-NoProfile", "-NonInteractive", "-Command", _WINDOWS_PS], timeout=15.0)
            interfaces, route = _windows(out)
            ssid = _netsh_ssid(_run(["netsh", "wlan", "show", "interfaces"])[1])
        elif system == "Darwin":
            interfaces = _mac_interfaces(_run(["ifconfig"])[1])
            route = _mac_route(_run(["route", "-n", "get", "default"])[1])
            if route and route.get("interface"):
                ssid = _mac_ssid(_run(["networksetup", "-getairportnetwork", route["interface"]])[1])
        else:
            interfaces = _linux_interfaces(_run(["ip", "-j", "addr", "show"])[1])
            route = _linux_route(_run(["ip", "-j", "route", "show", "default"])[1])
            ssid = _nmcli_ssid(_run(["nmcli", "-t", "-f", "active,ssid", "dev", "wifi"])[1])
        # The phone's word for the link. "other" is a default route the OS will
        # not name (cable, or Wi-Fi with a hidden SSID): said, not guessed.
        transport = "wifi" if ssid else ("other" if route else "none")
        return ActionResult.success({
            "transport": transport,
            "interfaces": interfaces,
            "default_route": route,
            "ssid": ssid,
            "tailnet": _tailnet(ctx),
        })
