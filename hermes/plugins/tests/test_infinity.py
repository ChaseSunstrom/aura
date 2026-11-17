"""A number that arrives as Infinity in another machine's JSON must not raise (T-1345).

Python's json reads ``Infinity`` and ``NaN`` as floats, and ``int(inf)`` raises
OverflowError (``int(nan)`` ValueError), which a ``except (TypeError, ValueError)`` lets
through. Found twice on 2026-11-14 (an OAuth expiry, Plex); each case here hands one
reader of a remote answer an Infinity and wants a normal answer or a clean refusal.
This pins the decision (what the tool returns), not how the number was parsed.
"""
from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _plugin_loader import load_plugin  # noqa: E402

INF = float("inf")
NAN = float("nan")


def _json_response(body) -> httpx.Response:
    # httpx's own `json=` refuses Infinity; a real server sends it as the bare word.
    return httpx.Response(200, content=json.dumps(body).encode(),
                          headers={"content-type": "application/json"})


# --------------------------------------------------------------------------- #
# Proxmox: the vmid of a guest the cluster lists
# --------------------------------------------------------------------------- #
@pytest.fixture
def proxmox(monkeypatch):
    tools = load_plugin("proxmox-control").tools
    monkeypatch.setenv("PROXMOX_URL", "https://pve.test:8006")
    monkeypatch.setenv("PROXMOX_TOKEN_ID", "jarvis@pve!agent")
    monkeypatch.setenv("PROXMOX_TOKEN_SECRET", "s3cret")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api2/json/cluster/resources":
            return _json_response({"data": [
                {"vmid": INF, "name": "odd", "type": "lxc", "node": "pve", "status": "running"},
                {"vmid": 100, "name": "ok", "type": "qemu", "node": "pve", "status": "running"}]})
        return httpx.Response(404, text="nope")

    monkeypatch.setattr(tools, "_client", lambda timeout=20.0: httpx.Client(
        base_url="https://pve.test:8006/api2/json", transport=httpx.MockTransport(handler)))
    return tools


@pytest.mark.parametrize("handle,args", [("handle_guests", {}), ("handle_guest_status", {"vmid": 100})])
@pytest.mark.parametrize("bad", [INF, NAN])
def test_infinity_in_a_proxmox_vmid_is_a_clean_answer(proxmox, monkeypatch, handle, args, bad):
    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response({"data": [{"vmid": bad, "node": "pve", "type": "lxc"}]})

    monkeypatch.setattr(proxmox, "_client", lambda timeout=20.0: httpx.Client(
        base_url="https://pve.test:8006/api2/json", transport=httpx.MockTransport(handler)))
    out = json.loads(getattr(proxmox, handle)(args))
    assert out["ok"] is False and "error" in out


# --------------------------------------------------------------------------- #
# Jellyfin: the tick counts of a title and of how far in the user is
# --------------------------------------------------------------------------- #
def test_infinity_in_jellyfin_ticks_is_no_position(monkeypatch):
    tools = load_plugin("aura-jellyfin").tools
    monkeypatch.setenv("JELLYFIN_URL", "https://media.test")
    monkeypatch.setenv("JELLYFIN_TOKEN", "jf-key")
    monkeypatch.setenv("JELLYFIN_USER_ID", "u1")
    items = {"Items": [{"Id": "it-1", "Name": "Dune", "Type": "Movie", "RunTimeTicks": INF,
                        "UserData": {"PlaybackPositionTicks": INF, "PlayedPercentage": 38.0}}]}

    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response(items)

    Orig = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kw: Orig(
        base_url=kw.get("base_url", ""), headers=kw.get("headers"),
        transport=httpx.MockTransport(handler)))
    assert tools._seconds(INF) is None and tools._seconds(NAN) is None
    out = json.loads(tools.handle_resume({}))
    assert out["ok"] is True and out["items"][0]["position_s"] is None


# --------------------------------------------------------------------------- #
# Open-Meteo: a temperature that is Infinity
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("field", ["temperature_2m", "apparent_temperature"])
def test_infinity_in_a_forecast_is_a_refusal(field):
    weather = load_plugin("aura-hud").weather
    hours = {"time": [f"2026-11-08T{h:02d}:00" for h in range(24)],
             "temperature_2m": [40.0] * 24, "precipitation_probability": [10] * 24,
             "weather_code": [3] * 24}
    body = {"current": {"time": "2026-11-08T16:15", "temperature_2m": 41.2, "apparent_temperature": 36.0,
                        "relative_humidity_2m": 80, "wind_speed_10m": 9.1, "precipitation": 0.0,
                        "weather_code": 3, field: INF},
            "hourly": hours,
            "daily": {"time": ["2026-11-08"], "temperature_2m_max": [45.0], "temperature_2m_min": [33.0],
                      "precipitation_probability_max": [40], "weather_code": [61],
                      "sunrise": ["2026-11-08T06:30"], "sunset": ["2026-11-08T16:35"]}}
    out = weather.forecast({"lat": 41.9, "lon": -87.6}, fetch=lambda url, params: body,
                           house=lambda: {"unit_system": "metric"})
    assert out["ok"] is False and "Open-Meteo" in out["error"]


# --------------------------------------------------------------------------- #
# The house router: a trip that takes Infinity seconds
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("bad", [INF, NAN, -INF])
def test_infinity_in_a_route_duration_is_no_route(bad):
    places = load_plugin("aura-hud").places
    out = json.loads(places.handle_route_eta(
        {"from_lat": 41.88, "from_lon": -87.63, "lat": 42.0, "lon": -87.7},
        lambda a, b: {"duration_s": bad, "distance_m": 1000}, now=lambda: 1_700_000_000.0))
    assert out["ok"] is False and out["error"].startswith("no route")


# --------------------------------------------------------------------------- #
# The desktop agent: a screen scale of Infinity must not poison the next click
# --------------------------------------------------------------------------- #
def test_infinity_in_a_desktop_scale_is_taken_at_one(monkeypatch, tmp_path):
    tools = load_plugin("aura-desktop").tools
    monkeypatch.setenv("HASS_URL", "http://core.test:8080")
    monkeypatch.setenv("HASS_TOKEN", "tok")
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    tools._scales.clear()
    jpeg = base64.b64encode(bytes.fromhex(
        "ffd8ffe000104a46494600010100000100010000ffdb004300080606070605080707070909080a0c140d0c0b0b0c1912130f14"
        "1d1a1f1e1d1a1c1c20242e2720222c231c1c2837292c30313434341f27393d38323c2e333432ffc0000b080002000201011100"
        "ffc40014000100000000000000000000000000000000ffc40014100100000000000000000000000000000000ffda0008010100"
        "003f00d2cf20ffd9")).decode()
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/config/companion/list":
            return _json_response([{"id": "study-pc", "name": "Study PC", "kind": "desktop", "online": True,
                                    "actions": [{"name": "screenshot"}, {"name": "click"}]}])
        body = json.loads(request.content)
        seen.append(body)
        result = ({"screen": [3200, 1800], "image_size": [1600, 900], "scale": INF, "image_jpeg_base64": jpeg}
                  if body["action"] == "screenshot" else {"done": True})
        return _json_response({"changed_states": [], "service_response": {
            "status": "ok", "device_id": body["device_id"], "action": body["action"], "tier": 2,
            "result": result}})

    monkeypatch.setattr(tools._house, "client", lambda timeout=20.0: httpx.Client(
        base_url="http://core.test:8080", headers={"Authorization": "Bearer tok"},
        transport=httpx.MockTransport(handler)))
    shot = json.loads(tools.handle_screen({}))
    assert shot["ok"] and shot["scale"] == 1.0
    click = json.loads(tools.handle_click({"x": 100, "y": 50}))
    assert click["ok"] and click["screen_point"] == [100, 50]
    tools._scales.clear()
