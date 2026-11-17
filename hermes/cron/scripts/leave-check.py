#!/usr/bin/env python3
"""The gate of the "Leave-by nudge" job: the model wakes only when the phone is reachable.

The job asks the phone for its calendar, and an asleep phone made every half-hourly run wake
the model, wait 45 s and answer [SILENT] (64 runs in 72 h, 2026-11-13). This asks the adapter
(a loopback read behind the API key a cron script has; it has no house token) whether the
house has a phone on its device channel or heard in the last ten minutes. Not reachable: the
runner's ``{"wakeAgent": false}`` skips the model.

Fails open: an adapter that does not answer, refuses, answers nonsense, or a missing key or
schema wakes the model as before, with the reason on the line the model reads. A gate that
fails closed would silence every nudge the day the key rotates.

Does not cover a reachable phone that then does not answer (the model's own turn says so).
"""
import json
import sys
import urllib.error
import urllib.request

_TIMEOUT_S = 10


def _ask() -> bool:
    """True when a phone is reachable, False when the house says it is not; raises on anything else."""
    from aura_schema import from_env

    cfg = from_env()
    key = str(cfg.adapter_key or cfg.apps_api_key or "")
    if not key:
        raise RuntimeError("no adapter key in the environment")
    base = str(cfg.adapter_url or "http://127.0.0.1:8643").rstrip("/")
    req = urllib.request.Request(f"{base}/api/phone/reachable", headers={"Authorization": f"Bearer {key}"})
    with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as resp:  # noqa: S310 - the adapter on loopback
        body = json.loads(resp.read(4096))
    if not isinstance(body, dict) or not isinstance(body.get("reachable"), bool):
        raise ValueError("the adapter answered without a yes or no")
    return body["reachable"]


def main() -> int:
    try:
        reachable = _ask()
    except Exception as exc:  # noqa: BLE001 - one sentence for the model, never a traceback
        # Not an HTTPError's body or URL: the reason is the class and, for an HTTP refusal, its code.
        why = f"HTTP {exc.code}" if isinstance(exc, urllib.error.HTTPError) else f"{type(exc).__name__}: {exc}"
        print(f"leave-check: {why}", file=sys.stderr)
        print(f"- The phone check could not run ({why}); try the phone anyway.")
        return 0
    if reachable:
        print("- The phone is on the house's device channel.")
    else:
        print("- The phone is not on the house's device channel and was not heard in the last ten minutes: skipped.")
        print(json.dumps({"wakeAgent": False}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
