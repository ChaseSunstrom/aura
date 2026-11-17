"""Watches (aura-hud/watches.py): "tell me when n8n is back", said once, not every hour.

The hourly Homelab health job re-alerted one n8n 502 every hour for three weeks
(2026-09-19 .. 2026-10-08), and Jarvis offered to watch a URL with nothing to keep it with.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
import pytest

PLUGIN_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_DIR.parent))
from _plugin_loader import load_plugin  # noqa: E402

watches = load_plugin("aura-hud").watches
DAY = 24 * 3600


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("N8N_URL", "http://n8n.test")
    # The voice is probed only when Hermes speaks through the shim; these tests are about n8n.
    monkeypatch.setenv("TTS_PROVIDER", "piper")
    for name in ("GRAFANA_URL", "LLAMA_SWAP_URL", "HASS_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    return tmp_path


def call(handler, args):
    return json.loads(handler(args))


def client(pages: dict):
    """A probe whose answers come from [pages]: url -> (status, body)."""
    def answer(request: httpx.Request) -> httpx.Response:
        status, body = pages.get(str(request.url), (404, ""))
        return httpx.Response(status, text=body)
    return httpx.Client(transport=httpx.MockTransport(answer))


def test_a_watch_is_validated_and_stored():
    assert call(watches.handle_watch_add, {"kind": "up", "target": "ftp://x", "say": "x"})["ok"] is False
    assert call(watches.handle_watch_add, {"kind": "entity", "target": "garage", "say": "x", "state": "open"})["ok"] is False
    assert call(watches.handle_watch_add, {"kind": "entity", "target": "cover.garage", "say": "x"})["ok"] is False
    assert call(watches.handle_watch_add, {"kind": "up", "target": "http://a.test/", "say": ""})["ok"] is False
    added = call(watches.handle_watch_add, {"kind": "up", "target": "http://a.test/", "say": "a is back"})
    assert added["ok"] and added["watch"]["once"] is True
    listed = call(watches.handle_watch_list, {})["watches"]
    assert [w["id"] for w in listed] == [added["watch"]["id"]]
    assert call(watches.handle_watch_remove, {"id": added["watch"]["id"]})["ok"]
    assert call(watches.handle_watch_remove, {"id": "nope"})["ok"] is False


def test_n8n_down_for_weeks_is_said_once_a_day_and_its_return_once():
    pages = {"http://n8n.test/healthz": (502, "")}
    t = 1_000_000.0
    assert watches.check_all(now=t, client=client(pages)) == ["n8n is down (HTTP 502)."]
    # The hourly passes after that say nothing...
    for hour in range(1, 24):
        assert watches.check_all(now=t + hour * 3600, client=client(pages)) == []
    # ...and a day on, one reminder naming when it went.
    [again] = watches.check_all(now=t + DAY, client=client(pages))
    assert again.startswith("n8n is still down, since ")
    pages["http://n8n.test/healthz"] = (200, "ok")
    assert watches.check_all(now=t + DAY + 600, client=client(pages)) == ["n8n is back up."]
    assert watches.check_all(now=t + DAY + 1200, client=client(pages)) == []


def test_a_healthy_service_is_a_silent_baseline():
    pages = {"http://n8n.test/healthz": (200, "ok")}
    assert watches.check_all(now=1.0, client=client(pages)) == []


def test_up_once_trips_and_is_gone_change_needs_a_baseline_entity_matches():
    call(watches.handle_watch_add, {"kind": "up", "target": "http://a.test/", "say": "a is back"})
    call(watches.handle_watch_add, {"kind": "change", "target": "http://p.test/", "say": "the page changed", "once": False})
    call(watches.handle_watch_add, {"kind": "entity", "target": "cover.garage_door", "state": "Open", "say": "the garage opened", "once": False})
    pages = {"http://a.test/": (503, ""), "http://p.test/": (200, "v1"), "http://n8n.test/healthz": (200, "")}
    garage = {"cover.garage_door": "closed"}
    run = lambda t: watches.check_all(now=t, client=client(pages), entity_state=garage.get)  # noqa: E731
    assert run(1) == []  # a down, page baseline, garage closed
    pages["http://a.test/"] = (200, "")
    pages["http://p.test/"] = (200, "  v1  ")  # whitespace is not a change
    assert run(2) == ["a is back"]
    assert [w["kind"] for w in call(watches.handle_watch_list, {})["watches"]] == ["change", "entity"]
    pages["http://p.test/"] = (200, "v2")
    garage["cover.garage_door"] = "open"
    assert run(3) == ["the page changed", "the garage opened"]
    # A repeating watch re-arms only after its condition was false again.
    assert run(4) == []
    garage["cover.garage_door"] = "closed"
    assert run(5) == []
    garage["cover.garage_door"] = "open"
    assert run(6) == ["the garage opened"]


def test_a_watch_that_cannot_be_read_counts_its_failed_looks_and_recovers():
    call(watches.handle_watch_add, {"kind": "entity", "target": "cover.garage_door", "state": "open", "say": "x", "once": False})
    states: dict = {}
    for t in (1, 2, 3):
        watches.check_all(now=t, client=client({"http://n8n.test/healthz": (200, "")}), entity_state=states.get)
    assert [w["fails"] for w in watches.load()["watches"]] == [3]
    states["cover.garage_door"] = "closed"
    watches.check_all(now=4, client=client({"http://n8n.test/healthz": (200, "")}), entity_state=states.get)
    assert [w["fails"] for w in watches.load()["watches"]] == [0]


def test_a_numeric_entity_watch_and_an_unreadable_state():
    w = {"kind": "entity", "below": 18}
    assert watches._entity_matches(w, "17.5") and not watches._entity_matches(w, "18")
    assert not watches._entity_matches(w, "unavailable") and not watches._entity_matches(w, None)


def test_the_gate_wakes_the_model_only_with_something_to_say(capsys, monkeypatch):
    monkeypatch.setattr(watches, "check_all", lambda: [])
    watches.main()
    assert json.loads(capsys.readouterr().out.strip().splitlines()[-1]) == {"wakeAgent": False}
    monkeypatch.setattr(watches, "check_all", lambda: ["n8n is back up."])
    watches.main()
    assert capsys.readouterr().out.strip() == "- n8n is back up."


def test_a_public_watch_whose_host_turns_private_is_not_probed():
    """Approved as public, then resolving inside the house (DNS changed): never fetched."""
    asked: list[str] = []

    def answer(request: httpx.Request) -> httpx.Response:
        asked.append(str(request.url))
        return httpx.Response(200, text="")

    data = {"watches": [{"id": "w1", "kind": "up", "target": "http://10.0.0.7/", "say": "x", "once": True,
                         "private": False}], "homelab": {}}
    watches._save(data)
    assert watches.check_all(now=1.0, client=httpx.Client(transport=httpx.MockTransport(answer))) == []
    assert "http://10.0.0.7/" not in asked
    assert [w["id"] for w in watches.load()["watches"]] == ["w1"]


def test_the_llama_swap_probe_sends_the_inference_key_it_now_needs():
    # Since T-0284 llama-swap answers nothing without LLM_API_KEY: a probe without it would
    # report the model server down forever.
    from types import SimpleNamespace
    cfg = SimpleNamespace(n8n_url="", grafana_url="", house_token="", house_url="",
                          llama_swap_url="http://jarvis-models:8000/", model_api_key="k",
                          tts_provider="piper", tts_shim_url="")
    assert watches.homelab_targets(cfg) == [("llama-swap", "http://jarvis-models:8000/running",
                                             {"Authorization": "Bearer k"})]
    cfg.model_api_key = ""
    assert watches.homelab_targets(cfg)[0][2] == {}


def test_the_voice_on_the_cpu_is_said_down_and_said_back_on_cuda(monkeypatch):
    """T-0435: CosyVoice on the CPU (a host driver update) quietly hands every line to Piper.

    Before this, only the next deploy's smoke noticed; the 10-minute probe now says it.
    """
    monkeypatch.setenv("TTS_PROVIDER", "openai")
    monkeypatch.setenv("TTS_OPENAI_BASE_URL", "http://jarvis-cosyvoice:8890/v1")
    health = "http://jarvis-cosyvoice:8890/health"
    pages = {"http://n8n.test/healthz": (200, "ok"),
             health: (200, json.dumps({"status": "ok", "device": "cuda"}))}
    assert watches.check_all(now=1.0, client=client(pages)) == []
    pages[health] = (200, json.dumps({"status": "ok", "device": "cpu"}))
    [down] = watches.check_all(now=600.0, client=client(pages))
    assert down.startswith("The voice is down (") and "CPU" in down
    pages[health] = (200, json.dumps({"status": "ok", "device": "cuda"}))
    assert watches.check_all(now=1200.0, client=client(pages)) == ["The voice is back up."]
    # A restart reports the CPU until its warm-up has loaded the model: not an outage.
    pages[health] = (200, json.dumps({"status": "ok", "device": "cpu", "warming": True}))
    assert watches.check_all(now=1500.0, client=client(pages)) == []
    pages[health] = (200, json.dumps({"status": "no-voices", "device": "cuda", "voices": []}))
    [clipless] = watches.check_all(now=1800.0, client=client(pages))
    assert clipless.startswith("The voice is down (") and "clip" in clipless
    pages[health] = (200, "not json")
    assert watches.check_all(now=2400.0, client=client(pages)) == []  # still down: said once


def test_the_voice_is_not_probed_when_hermes_speaks_with_piper(monkeypatch):
    from types import SimpleNamespace
    cfg = SimpleNamespace(n8n_url="", grafana_url="", house_token="", house_url="", llama_swap_url="",
                          model_api_key="", tts_provider="openai",
                          tts_shim_url="http://jarvis-cosyvoice:8890/v1/")
    assert watches.homelab_targets(cfg) == [("The voice", "http://jarvis-cosyvoice:8890/health", {})]
    cfg.tts_provider = "piper"
    assert watches.homelab_targets(cfg) == []
