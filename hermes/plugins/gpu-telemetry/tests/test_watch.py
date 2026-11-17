"""The watchdog's whole job is knowing when to stay quiet, so that is what is tested."""
from __future__ import annotations

import importlib
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from _plugin_loader import load_plugin  # noqa: E402

# Imported as a submodule rather than from the package's __init__: the watchdog is run by the
# cron script, never by the agent, so the plugin does not pay for importing it at load time.
load_plugin("gpu-telemetry")
watch = importlib.import_module("gpu_telemetry.watch")
LIMITS = watch.Limits()

#: A working afternoon: outside quiet hours, so nothing is suppressed for the time of day.
NOON = datetime(2026, 9, 3, 14, 30)
NIGHT = datetime(2026, 9, 3, 3, 15)


def gpus(*temps: float, vram_pct: float | None = None) -> list[dict]:
    out = []
    for i, t in enumerate(temps):
        gpu: dict = {"gpu": str(i), "temperature": t}
        if vram_pct is not None:
            gpu["vram_pct"] = vram_pct
        out.append(gpu)
    return out


def run(samples, state, *, now=1000.0, stamp=NOON, limits=LIMITS):
    return watch.evaluate(samples, state, now=now, stamp=stamp, limits=limits)


def test_quiet_while_everything_is_under_the_limit():
    lines, state = run(gpus(61.0, 58.0), {})
    assert lines == []
    assert state == {"firing": {}, "telemetry_down": False}


def test_first_crossing_speaks_once_and_then_stops():
    lines, state = run(gpus(84.2, 61.0), {})
    assert len(lines) == 1
    assert "GPU 0 temperature is 84.2 °C, above the 80.0 °C limit" in lines[0]

    # Same reading a minute later: already announced, so nothing more is said.
    again, state2 = run(gpus(84.4, 61.0), state, now=1060.0)
    assert again == []
    assert state2["firing"] == state["firing"]


def test_renotice_once_an_hour_while_it_stays_over():
    _lines, state = run(gpus(84.0), {})
    assert run(gpus(84.0), state, now=1000.0 + 3599)[0] == []
    lines, _state = run(gpus(84.0), state, now=1000.0 + 3600)
    assert len(lines) == 1 and "still" in lines[0]


def test_hysteresis_keeps_a_card_on_the_limit_from_flapping():
    """79 °C is under the 80 °C alert but over the 78 °C clear point: still firing, silent."""
    _lines, state = run(gpus(84.0), {})
    lines, state = run(gpus(79.0), state, now=1060.0)
    assert lines == []
    assert state["firing"] == {"temp:0": 1000.0}

    lines, state = run(gpus(77.5), state, now=1120.0)
    assert len(lines) == 1 and "back to 77.5 °C" in lines[0]
    assert state["firing"] == {}


def test_each_gpu_and_each_metric_is_its_own_rule():
    lines, state = run(gpus(84.0, 61.0, vram_pct=99.0), {})
    assert sorted(state["firing"]) == ["temp:0", "vram:0", "vram:1"]
    assert any("GPU 0 temperature" in ln for ln in lines)
    assert any("GPU 1 VRAM is 99%" in ln for ln in lines)
    assert not any("GPU 1 temperature" in ln for ln in lines)


def test_a_missing_metric_is_not_a_cold_card():
    """A sample with no temperature must yield no temperature verdict, not a passing one."""
    lines, state = run([{"gpu": "0"}], {})
    assert lines == [] and state["firing"] == {}


def test_telemetry_outage_speaks_once_and_recovery_speaks_once():
    lines, state = run(None, {})
    assert len(lines) == 1 and "no readings" in lines[0]
    assert state["telemetry_down"] is True

    assert run(None, state, now=1060.0)[0] == []

    lines, state = run(gpus(61.0), state, now=1120.0)
    assert len(lines) == 1 and "telemetry is back" in lines[0]
    assert state["telemetry_down"] is False


def test_no_samples_reads_differently_from_a_failed_read():
    lines, _state = run([], {})
    assert "exporter has written no samples" in lines[0]


def test_nothing_clears_while_the_watchdog_is_blind():
    """An outage must not be mistaken for a recovery — the rule stays armed."""
    _lines, state = run(gpus(84.0), {})
    _lines, state = run(None, state, now=1060.0)
    assert state["firing"] == {"temp:0": 1000.0}


def test_quiet_hours_hold_the_informational_lines_but_never_the_alert():
    lines, state = run(gpus(90.0), {}, stamp=NIGHT)
    assert len(lines) == 1 and "above the" in lines[0]

    # The recovery at 03:15 is not news anyone needs woken for.
    lines, state = run(gpus(60.0), state, now=1060.0, stamp=NIGHT)
    assert lines == [] and state["firing"] == {}


def test_quiet_hours_reminder_is_dropped_not_deferred():
    """Suppressed hourly reminders must not queue up and burst when quiet hours end."""
    _lines, state = run(gpus(90.0), {}, stamp=NIGHT)
    lines, state = run(gpus(90.0), state, now=1000.0 + 3600, stamp=NIGHT)
    assert lines == []
    assert state["firing"] == {"temp:0": 1000.0 + 3600}


def test_quiet_window_wraps_midnight():
    limits = watch.Limits(quiet_from_h=22, quiet_to_h=7)
    assert watch.in_quiet_hours(datetime(2026, 9, 3, 23, 0), limits) is True
    assert watch.in_quiet_hours(datetime(2026, 9, 3, 2, 0), limits) is True
    assert watch.in_quiet_hours(datetime(2026, 9, 3, 7, 0), limits) is False
    assert watch.in_quiet_hours(datetime(2026, 9, 3, 12, 0), limits) is False
    # Equal bounds mean "no quiet hours" rather than "quiet all day".
    assert watch.in_quiet_hours(datetime(2026, 9, 3, 3, 0), watch.Limits(quiet_from_h=0, quiet_to_h=0)) is False


def test_state_round_trips_and_a_corrupt_file_is_not_fatal(tmp_path):
    path = tmp_path / "state" / "gpu-watch.json"
    watch.write_state(path, {"firing": {"temp:0": 12.0}, "telemetry_down": False})
    assert watch.read_state(path)["firing"] == {"temp:0": 12.0}

    path.write_text("{not json", encoding="utf-8")
    assert watch.read_state(path) == {}
    assert watch.read_state(tmp_path / "missing.json") == {}


def test_limits_come_from_the_settings_schema(monkeypatch):
    """Every threshold is a settings row (D17), not a literal in the watchdog."""
    assert watch.limits_from_settings() == LIMITS  # the schema's own defaults

    monkeypatch.setenv("GPU_TEMP_ALERT_C", "70")
    monkeypatch.setenv("GPU_TEMP_CLEAR_C", "65")
    monkeypatch.setenv("PROACTIVE_QUIET_HOURS_FROM", "23")
    limits = watch.limits_from_settings()
    assert (limits.temp_alert_c, limits.temp_clear_c) == (70.0, 65.0)
    assert limits.quiet_from_h == 23
    assert limits.vram_alert_pct == 95.0, "an unset row keeps the schema default"


def test_sample_never_raises(monkeypatch):
    """A watchdog that tracebacks delivers a traceback to a phone at 3am."""
    def boom(_flux):
        raise RuntimeError("grafana said no")

    monkeypatch.setattr(watch.tools, "query", boom)
    assert watch.sample() is None


# --- an install with no telemetry at all (T-1502) ----------------------------------------------

def test_with_no_telemetry_source_the_watch_says_nothing_and_records_nothing(tmp_path, monkeypatch, capsys):
    """A new install has neither source set. Its every read would fail, and the first tick told
    the owner to "check Grafana and the exporter" about a Grafana they never set up."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.delenv("GRAFANA_URL", raising=False)
    monkeypatch.delenv("INFLUX_URL", raising=False)

    def read():
        raise AssertionError("the watch tried to read telemetry no setting points at")

    monkeypatch.setattr(watch, "sample", read)
    assert watch.main() == 0
    assert capsys.readouterr().out == ""
    # Nothing recorded as down either: a source set later must not start with "telemetry is back".
    assert not watch.state_path().exists()


def test_with_a_source_set_a_failed_read_still_speaks_once(tmp_path, monkeypatch, capsys):
    """The silence above is for a source nobody set, never for one that is set and down."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.delenv("INFLUX_URL", raising=False)
    monkeypatch.setenv("GRAFANA_URL", "http://grafana.test:3000")
    monkeypatch.setattr(watch, "sample", lambda: None)
    assert watch.main() == 0
    assert len(capsys.readouterr().out.splitlines()) == 1
    assert watch.read_state(watch.state_path())["telemetry_down"] is True
    assert watch.main() == 0
    assert capsys.readouterr().out == ""


def test_either_setting_counts_as_a_source(monkeypatch):
    monkeypatch.delenv("GRAFANA_URL", raising=False)
    monkeypatch.delenv("INFLUX_URL", raising=False)
    assert watch.source_set() is False
    monkeypatch.setenv("INFLUX_URL", "http://influx.test:8086")
    assert watch.source_set() is True
    monkeypatch.delenv("INFLUX_URL")
    monkeypatch.setenv("GRAFANA_URL", "   ")
    assert watch.source_set() is False, "spaces are not an address"
