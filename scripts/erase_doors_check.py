"""After a deploy, the two erase doors are there and guarded, and nothing was erased to find out (T-1412).

The house's ``POST /api/config/forget_window`` and the adapter's ``POST /api/artifacts/forget_window``
are what the Hermes ``forget_recent`` tool calls after the owner approved. Their unit tests run on
real files, never on the deployed stack, so an image that lags the tree (no route), a door that
lost its key check, or a window check that went missing would ship unseen. This asks each door
twice with a window of ``minutes: 0``, which a working door refuses with a 400 and which erases
nothing even where the check is gone (the cutoff is "now"): once with no key, which must be refused
with a 401 or 403 (a 404 is a door that is not deployed), and once with the key, which must be a 400.

It never sends a whole number from 1 to 1440, nor any other value: ``post`` refuses to send a
body that is not exactly ``{"minutes": 0}``, so a later edit cannot turn the check into an erase.

    erase_doors_check.py [ADAPTER_BASE [ADAPTER_KEY]]

Env: AURA_BASE and AURA_API_KEY when the arguments are not given (the smoke passes them, the
adapter as the smoke's python sees it); HASS_URL and HASS_TOKEN for the house, which the Hermes
container already holds, so the smoke runs this in it. Without HASS_URL the house is not asked and
the line says so; without HASS_TOKEN only the no-key refusal is checked on the house. SMOKE_TIMEOUT
bounds each request (at most 15 seconds).

Does not cover: that a valid window erases (the doors' own tests do), a paired device's key being
refused (the 403 branch), or the Hermes approval in front of the tool. Stdlib only, so any python3
runs it. Exits 1 with the first failure on the last line.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

ADAPTER_PATH = "/api/artifacts/forget_window"
HOUSE_PATH = "/api/config/forget_window"
#: The only window this check sends. 0 is out of range (the doors take 1 to 1440), and where the
#: range check is missing a window of 0 reaches back no further than now.
BODY = {"minutes": 0}
REFUSED_UNKEYED = (401, 403)


class DoorError(Exception):
    """A door answered what a deployed, guarded door never does."""


def post(url: str, key: str | None, body: dict, timeout: float) -> int:
    """POST ``body`` as JSON and return the status. Refuses anything but ``BODY``."""
    if body != BODY or type(body["minutes"]) is not int or body["minutes"] != 0:
        raise ValueError(f"erase_doors_check sends only {BODY!r}, not {body!r}")
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status
    except urllib.error.HTTPError as err:
        return err.code
    except (urllib.error.URLError, OSError) as err:
        raise DoorError(f"{url} did not answer: {getattr(err, 'reason', err)}") from err


def check_door(name: str, url: str, key: str | None, timeout: float) -> str:
    """Ask one door with no key, then (when there is a key) with it; returns "name 401/400"."""
    unkeyed = post(url, None, BODY, timeout)
    if unkeyed == 404:
        raise DoorError(f"{name}: {url} is not there (404): the deployed image has no such door")
    if unkeyed not in REFUSED_UNKEYED:
        raise DoorError(f"{name}: no key was answered {unkeyed}, not 401 or 403: the door is not guarded")
    if not key:
        return f"{name} {unkeyed} unkeyed (no key to ask with)"
    keyed = post(url, key, BODY, timeout)
    if keyed != 400:
        raise DoorError(f"{name}: a window of 0 minutes was answered {keyed}, not 400: the window check is gone")
    return f"{name} {unkeyed}/{keyed}"


def main(argv: list[str], env: dict[str, str] | None = None) -> int:
    env = dict(os.environ if env is None else env)
    base = (argv[0] if argv else env.get("AURA_BASE", "")).rstrip("/")
    key = (argv[1] if len(argv) > 1 else env.get("AURA_API_KEY", "")) or None
    house = env.get("HASS_URL", "").rstrip("/")
    try:
        timeout = min(15.0, float(env.get("SMOKE_TIMEOUT") or 15))
    except ValueError:
        timeout = 15.0
    if not base or not key:
        print("no adapter base or key to ask with (AURA_BASE, AURA_API_KEY)")
        return 1
    done = []
    try:
        done.append(check_door("adapter", base + ADAPTER_PATH, key, timeout))
        if house:
            done.append(check_door("house", house + HOUSE_PATH, env.get("HASS_TOKEN") or None, timeout))
        else:
            done.append("house not asked (HASS_URL unset)")
    except DoorError as err:
        print(err)
        return 1
    print("; ".join(done))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
