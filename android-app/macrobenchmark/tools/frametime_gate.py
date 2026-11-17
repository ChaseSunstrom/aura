#!/usr/bin/env python3
"""Hold a macrobenchmark's frame timings to the GLASS_HUD budget (§8.3 rule 6).

Reads every ``*-benchmarkData.json`` the ``connectedBenchmarkAndroidTest`` run
left under the outputs directory given, finds ``frameDurationCpuMs`` and
``frameOverrunMs`` for ``HudFrameTimingBenchmark``, and fails when

* P95 ``frameDurationCpuMs`` is over the budget for the device class — 8.3 ms
  on a 120 Hz Pixel (the file names the device), 16.7 ms on anything else; or
* the maximum is over 50 ms (``motion.budget.frame``); or
* an emulator produced the numbers and ``--allow-emulator`` was not given —
  the emulator's GPU is the host's, and a green number from it says nothing
  about the phone; it is allowed only as a smoke check, labelled as such.

Not covered: the P50 (reported, informational) and the web/desktop surfaces
(``jarvis-web/e2e/hud-frametime.spec.ts``). This runs where the benchmark ran,
which today is nowhere in CI (no device runner) — see quality-hud.yml.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BUDGET_P95_MS = {"pixel120": 8.3, "default": 16.7}
BUDGET_MAX_MS = 50.0
PIXEL_120HZ = ("Pixel 6", "Pixel 7", "Pixel 8", "Pixel 9", "Pixel 10")


def device_class(context: dict) -> str:
    model = str(context.get("build", {}).get("model", ""))
    return "pixel120" if any(model.startswith(p) for p in PIXEL_120HZ) else "default"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("outputs", help="the connected_android_test_additional_output directory")
    ap.add_argument("--allow-emulator", action="store_true", help="accept numbers from an emulator (smoke only)")
    args = ap.parse_args()
    files = sorted(Path(args.outputs).rglob("*-benchmarkData.json"))
    if not files:
        print(f"FAIL: no *-benchmarkData.json under {args.outputs} — the benchmark did not run")
        return 1
    failed = False
    for path in files:
        doc = json.loads(path.read_text())
        context = doc.get("context", {})
        klass = device_class(context)
        emulator = bool(context.get("build", {}).get("fingerprint", "").count("generic")) or "sdk_gphone" in str(context.get("build", {}).get("model", ""))
        label = f"{context.get('build', {}).get('model', '?')} ({klass}{', emulator' if emulator else ''})"
        for bench in doc.get("benchmarks", []):
            if "HudFrameTimingBenchmark" not in bench.get("className", ""):
                continue
            metrics = bench.get("metrics", {})
            dur = metrics.get("frameDurationCpuMs", {})
            over = metrics.get("frameOverrunMs", {})
            p50, p95, mx = dur.get("P50"), dur.get("P95"), dur.get("maximum")
            budget = BUDGET_P95_MS[klass]
            ok = p95 is not None and p95 <= budget and mx is not None and mx <= BUDGET_MAX_MS
            if emulator and not args.allow_emulator:
                ok = False
            print(
                f"{'ok  ' if ok else 'FAIL'} {bench.get('name')} on {label}: frameDurationCpuMs P50 {p50} P95 {p95} max {mx} ms"
                f" (budget P95 ≤ {budget}, max ≤ {BUDGET_MAX_MS}); frameOverrunMs P95 {over.get('P95')}"
                + ("; emulator numbers refused without --allow-emulator" if emulator and not args.allow_emulator else "")
            )
            failed |= not ok
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
