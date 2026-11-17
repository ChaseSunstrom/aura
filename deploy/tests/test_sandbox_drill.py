"""The sandbox drill takes its destinations from the deployed configuration (T-1027).

The drill's fixed targets went stale (a LAN router address, a tailnet host from before
Tailscale went, a public resolver the container cannot reach), so its positive control
reached nothing in two classes and every production run ended INCONCLUSIVE. The target
choice is now a pure function of the container's environment; these tests pin it with
made-up addresses (RFC 5737 / example.test), never the house's own.

Not covered: that the chosen hosts are up on a given day. That is the drill itself, and
it says which class its control could not reach.
"""
from __future__ import annotations

import importlib.util
import re
from pathlib import Path

DRILL = Path(__file__).resolve().parents[2] / "scripts" / "verify" / "sandbox-drill.py"
_spec = importlib.util.spec_from_file_location("sandbox_drill", DRILL)
drill = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(drill)

ENV = {
    "TAILSCALE_IP": "192.0.2.10",
    "LLM_URL": "http://models.example.test:8000/v1",
    "SEARXNG_URL": "http://search.example.test",
}
NAMES = {"models.example.test": "192.0.2.20", "search.example.test": "192.0.2.30"}


def _choose(env=ENV, gateway="172.17.0.1"):
    return drill.choose_targets(env, gateway, NAMES.get)


def test_drill_control_targets_come_from_the_model_and_search_urls() -> None:
    targets, classes = _choose()
    by_name = {n: (h, p) for n, h, p in targets}
    assert by_name["model host"] == ("192.0.2.20", 8000)
    assert by_name["search host"] == ("192.0.2.30", 80)  # http with no port
    assert {"model host", "search host"} <= set(classes["internet/service"])


def test_drill_lan_target_is_the_configured_bind_address() -> None:
    targets, classes = _choose()
    by_name = {n: (h, p) for n, h, p in targets}
    assert by_name["LAN bind address :8643"] == ("192.0.2.10", 8643)
    assert "LAN bind address :8643" in classes["lan/host"]
    assert by_name["docker host :8080"] == ("172.17.0.1", 8080)


def test_drill_without_a_bind_address_has_no_lan_target() -> None:
    targets, _ = _choose({k: v for k, v in ENV.items() if k != "TAILSCALE_IP"})
    assert not any(n.startswith("LAN bind") for n, _, _ in targets)


def test_drill_skips_a_service_url_it_cannot_resolve_or_parse() -> None:
    env = {**ENV, "LLM_URL": "http://gone.example.test:8000", "SEARXNG_URL": "http://[bad:99999"}
    targets, classes = _choose(env)
    names = {n for n, _, _ in targets}
    assert "model host" not in names and "search host" not in names
    # What is left of the class is the public control only, so the verdict can still
    # say the class was missing rather than pass on nothing.
    assert classes["internet/service"] == ("public internet 1.1.1.1:53",)


def test_drill_literal_ip_service_host_needs_no_lookup() -> None:
    targets, _ = drill.choose_targets({"LLM_URL": "http://192.0.2.99:9000"}, "172.17.0.1", lambda h: None)
    assert ("model host", "192.0.2.99", 9000) in targets


def test_drill_names_no_tailnet_or_router_literal() -> None:
    text = DRILL.read_text()
    assert not re.search(r"\b100\.(6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.\d+\.\d+\b", text)  # CGNAT / tailnet
    assert "192.168." not in text
    assert "tailnet host" not in text and "LAN router" not in text
