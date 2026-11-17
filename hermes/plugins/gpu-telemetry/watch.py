"""Threshold watcher over the telemetry :mod:`tools` already reads.

Runs as an **agentless** cron job (``hermes cron create --script gpu-watch.py --no-agent``):
the scheduler runs it, whatever it prints is delivered verbatim to the job's target, and an
empty stdout sends nothing. No model in the loop — a watchdog that cost an inference every
five minutes would be switched off within a week, and one that reads its temperature out of
a language model can hallucinate a fire.

Everything about the design is therefore about *not speaking*:

* **Edge-triggered.** The first crossing speaks. Ticks two through infinity do not.
* **Hysteresis.** A rule fires at ``alert_at`` and only clears under a lower ``clear_at``
  (80 °C fires, 78 °C clears). Without the gap a card sitting exactly on the limit alternates
  alert/recovered every tick forever — the classic way an alert channel becomes noise.
* **A re-notice ceiling.** While a rule stays over, at most one reminder per hour, so a real
  sustained problem is not forgotten either.
* **Quiet hours** suppress the *informational* lines (recovery, the hourly reminder). A first
  crossing always speaks: a card at 90 °C at 3am is exactly what the owner asked to be woken
  for, and a recovery slept through is not news in the morning.

The state that makes all of that possible is one small JSON file; lose it and the next tick
re-announces whatever is currently wrong, which is the safe direction to fail in.
"""
from __future__ import annotations

import dataclasses
import json
import os
import sys
import time
from datetime import datetime, time as dtime
from pathlib import Path
from typing import Any, Iterable


def _load_sibling_tools() -> Any:
    """``tools.py`` from beside this file, loaded by path.

    By path and under a unique name, never ``import tools``: Hermes has its own top-level
    ``tools`` package in the same interpreter, so a plain import resolves to that one
    whenever sys.path happens to favour the Hermes root — and the watchdog would then be
    querying a completely different module for a GPU temperature.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "gpu_telemetry_tools", Path(__file__).resolve().with_name("tools.py"))
    if spec is None or spec.loader is None:  # pragma: no cover — the file is beside us
        raise ImportError("gpu-telemetry/tools.py is missing next to watch.py")
    module = importlib.util.module_from_spec(spec)
    # Registered before execution for the same reason the cron stub registers this module:
    # anything resolving annotations through sys.modules[__module__] needs it there.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


try:  # imported as part of the plugin package (tests, Hermes' loader)
    from . import tools
except ImportError:  # loaded by file path from the cron stub, outside any package
    tools = _load_sibling_tools()

# The lines this prints are delivered to a chat platform verbatim, so they are
# rendered by the shared templates: the reactor's glyph for the level first,
# then the sentence. `_shared` is importable here for the same reason `tools`
# can import `_shared._grafana` — the plugins root is on the path both in
# Hermes and in the cron stub.
from _shared import _messages  # noqa: E402


@dataclasses.dataclass(frozen=True)
class Rule:
    """One threshold on one GPU, resolved for this tick."""

    key: str
    label: str
    value: float
    alert_at: float
    clear_at: float
    unit: str


@dataclasses.dataclass(frozen=True)
class Limits:
    """Where the lines are drawn. ``clear`` sits below ``alert`` on purpose (hysteresis)."""

    temp_alert_c: float = 80.0
    temp_clear_c: float = 78.0
    vram_alert_pct: float = 95.0
    vram_clear_pct: float = 90.0
    renotice_s: float = 3600.0
    #: Local-clock quiet window; equal values mean "no quiet hours".
    quiet_from_h: int = 22
    quiet_to_h: int = 7


def limits_from_settings() -> Limits:
    """Read the limits off the one settings schema (D17). The only place a threshold is
    decided; the defaults on :class:`Limits` exist for the tests and for a bare import."""
    cfg = tools.from_env()
    return Limits(
        temp_alert_c=float(cfg.gpu_temp_alert_c),
        temp_clear_c=float(cfg.gpu_temp_clear_c),
        vram_alert_pct=float(cfg.gpu_vram_alert_pct),
        vram_clear_pct=float(cfg.gpu_vram_clear_pct),
        renotice_s=float(cfg.gpu_alert_renotice_s),
        quiet_from_h=int(cfg.proactive_quiet_hours_from),
        quiet_to_h=int(cfg.proactive_quiet_hours_to),
    )


def in_quiet_hours(when: datetime, limits: Limits) -> bool:
    """True inside the local quiet window, which normally wraps midnight (22:00 → 07:00)."""
    if limits.quiet_from_h == limits.quiet_to_h:
        return False
    start, end = dtime(hour=limits.quiet_from_h % 24), dtime(hour=limits.quiet_to_h % 24)
    now = when.time()
    if start < end:
        return start <= now < end
    return now >= start or now < end


def rules_for(gpus: Iterable[dict[str, Any]], limits: Limits) -> list[Rule]:
    """The thresholds that can be judged from this tick's samples.

    A metric a GPU did not report yields no rule at all, rather than a zero: a missing
    ``temperature`` must never read as a cold, healthy card.
    """
    out: list[Rule] = []
    for gpu in gpus:
        name = str(gpu.get("gpu", "?"))
        if gpu.get("temperature") is not None:
            out.append(Rule(f"temp:{name}", f"GPU {name} temperature", float(gpu["temperature"]),
                            limits.temp_alert_c, limits.temp_clear_c, "°C"))
        if gpu.get("vram_pct") is not None:
            out.append(Rule(f"vram:{name}", f"GPU {name} VRAM", float(gpu["vram_pct"]),
                            limits.vram_alert_pct, limits.vram_clear_pct, "%"))
    return out


def _fmt(value: float, unit: str) -> str:
    return f"{value:.0f}{unit}" if unit == "%" else f"{value:.1f} {unit}"


def evaluate(
    gpus: list[dict[str, Any]] | None,
    state: dict[str, Any],
    *,
    now: float,
    stamp: datetime,
    limits: Limits,
) -> tuple[list[str], dict[str, Any]]:
    """Judge one tick. Returns (lines to deliver, the state to persist).

    ``gpus`` is None when the telemetry read itself failed, ``[]`` when it succeeded but the
    exporter has written nothing recently — a different fault with a different sentence, and
    both are worth exactly one line until they recover.
    """
    firing: dict[str, float] = dict(state.get("firing") or {})
    was_down = bool(state.get("telemetry_down"))
    quiet = in_quiet_hours(stamp, limits)
    at = stamp.strftime("%H:%M")
    lines: list[str] = []

    if gpus is None or not gpus:
        why = "the telemetry read failed" if gpus is None else "the exporter has written no samples"
        if not was_down:
            lines.append(_messages.blind(why, at, "Check Grafana and the exporter."))
        # Thresholds cannot be judged without numbers, so nothing clears while blind: the
        # rules that were firing stay firing and re-announce only if they are still over
        # when the readings return.
        return lines, {"firing": firing, "telemetry_down": True}

    if was_down:
        if not quiet:
            lines.append(_messages.recovered(f"GPU watchdog: telemetry is back ({at})."))

    for rule in rules_for(gpus, limits):
        over = rule.value > rule.alert_at
        last = firing.get(rule.key)
        if over and last is None:
            lines.append(_messages.alert(
                f"{rule.label} is {_fmt(rule.value, rule.unit)}, above the "
                f"{_fmt(rule.alert_at, rule.unit)} limit ({at})."))
            firing[rule.key] = now
        elif over and now - float(last or 0.0) >= limits.renotice_s:
            if not quiet:
                lines.append(_messages.alert(
                    f"{rule.label} is still {_fmt(rule.value, rule.unit)}, over the "
                    f"{_fmt(rule.alert_at, rule.unit)} limit ({at})."))
            # The clock advances either way: a quiet-hours reminder is dropped, not deferred
            # into a burst at 07:00.
            firing[rule.key] = now
        elif not over and last is not None and rule.value <= rule.clear_at:
            if not quiet:
                lines.append(_messages.recovered(
                    f"{rule.label} is back to {_fmt(rule.value, rule.unit)}, under "
                    f"{_fmt(rule.clear_at, rule.unit)} ({at})."))
            firing.pop(rule.key, None)
        # Between clear_at and alert_at while firing: the hysteresis band. Say nothing.

    return lines, {"firing": firing, "telemetry_down": False}


def state_path() -> Path:
    home = os.environ.get("HERMES_HOME") or os.path.expanduser("~/.hermes")
    return Path(home) / "state" / "gpu-watch.json"


def read_state(path: Path) -> dict[str, Any]:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def write_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state), encoding="utf-8")
    tmp.replace(path)


def sample() -> list[dict[str, Any]] | None:
    """This tick's GPUs, or None if the telemetry could not be read at all."""
    try:
        return tools.shape_status(tools.query(tools.flux_latest(10)))["gpus"]
    except Exception:  # noqa: BLE001 — any failure here is "no readings", never a traceback
        return None    # delivered to a phone at 3am.


def source_set() -> bool:
    """Whether this install points at any GPU telemetry: InfluxDB directly, or Grafana's proxy.

    Says nothing about whether the source answers; a source that is set and down is an outage,
    and the watch reports that once.
    """
    cfg = tools.from_env()
    return bool(str(cfg.influx_url or "").strip() or str(cfg.grafana_url or "").strip())


def main() -> int:
    """Print what is worth saying; print nothing when all is well.

    Nothing at all, and no state written, on an install with no telemetry source: every read
    there fails, and the first tick used to tell the owner to "check Grafana and the exporter"
    about a Grafana they never set up (T-1502). The seeder no longer ships the job there; this
    covers an install seeded before that, and a source removed between two starts.
    """
    if not source_set():
        return 0
    path = state_path()
    limits = limits_from_settings()
    lines, state = evaluate(sample(), read_state(path), now=time.time(),
                            stamp=datetime.now(), limits=limits)
    write_state(path, state)
    for line in lines:
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
