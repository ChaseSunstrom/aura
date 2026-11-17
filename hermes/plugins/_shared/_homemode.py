"""Hold Hermes' home folder at the mode the stack sets for it (T-1497).

The compose file gives the gateway ``HERMES_HOME_MODE=0711``: the adapter is another uid on a
read-only mount of the same volume and must pass through the home to reach
``state/kill-switch.json``, the settings overrides and the persona. Hermes applies that mode
when a process starts (``ensure_hermes_home``) and then undoes it: saving its credential store
(``hermes_cli/auth.py``) tightens the store's parent with ``secure_parent_dir``, which sets
0700 whatever the setting says, and the store's parent is the home. On a clean install that
write came twelve seconds after the gateway started. The adapter's reader fails towards
*engaged* when it cannot reach the switch, so the console drew a stopped assistant on a stack
where nothing was stopped, and went on drawing it until the next restart.

So the mode is held, not just set: a thread in the gateway looks at the folder once a second
and puts the set mode back when it differs. It is started by ``jarvis-guard``, the one plugin
that is always loaded.

Not covered: the second between a tightening and the next look (a status request that lands in
it still reads the switch as engaged, once); a home this process does not own (said once in
the log, never raised); a process that never loads the plugins. With no setting, or one that is
not an octal mode, nothing is held and Hermes' own default stands. This does not change what
the adapter does with a switch it cannot read: that still reads as engaged.
"""
from __future__ import annotations

import logging
import os
import stat
import threading
from pathlib import Path

_LOGGER = logging.getLogger(__name__)

#: Seconds between looks: the longest a tightened home stays tightened. One ``stat`` a second.
INTERVAL: float = 1.0
THREAD_NAME: str = "aura-home-mode"

_lock = threading.Lock()
_keeper: threading.Thread | None = None
_stop = threading.Event()
_said: bool = False


def _home() -> Path:
    # Call time, not import time: the tests point HERMES_HOME somewhere new for each test.
    return Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes")))


def wanted() -> int | None:
    """The mode the stack set for the home, or None when it set none this module will hold.

    Read as Hermes reads it (stripped, octal). Zero and anything past the permission bits are
    not held: a home nobody can enter, its owner included, is not a mode to keep putting back.
    """
    raw = os.environ.get("HERMES_HOME_MODE", "").strip()
    if not raw:
        return None
    try:
        mode = int(raw, 8)
    except ValueError:
        return None
    return mode if 0 < mode <= 0o7777 else None


def hold() -> bool:
    """Put the home back at its set mode if it has another. True when it changed it; never raises."""
    global _said
    mode = wanted()
    if mode is None:
        return False
    home = _home()
    try:
        if stat.S_IMODE(os.stat(home).st_mode) == mode:
            return False
        os.chmod(home, mode)
    except OSError as exc:
        if not _said:
            # Once: this runs every second, and a home that is not ours stays not ours.
            _said = True
            _LOGGER.warning("the home folder %s is not at its set mode %04o and could not be set back "
                            "(%s): the adapter may read the kill switch as engaged", home, mode,
                            exc.__class__.__name__)
        return False
    _LOGGER.info("the home folder %s was set back to its set mode %04o", home, mode)
    return True


def _run(interval: float, stop: threading.Event) -> None:
    hold()
    while not stop.wait(interval):
        hold()


def start(interval: float | None = None) -> bool:
    """Start the keeper, once per process. True when this call started it; never raises.

    False with no mode to hold, with a keeper already running, and when the thread cannot be
    started: the plugin that calls this is the governor, and it must load either way.
    """
    global _keeper, _stop
    if wanted() is None:
        return False
    with _lock:
        if _keeper is not None and _keeper.is_alive():
            return False
        stop = threading.Event()
        try:
            keeper = threading.Thread(target=_run, args=(INTERVAL if interval is None else interval, stop),
                                      name=THREAD_NAME, daemon=True)
            keeper.start()
        except Exception as exc:  # noqa: BLE001 - a helper thread must never stop the guard loading
            _LOGGER.warning("the home-mode keeper could not start (%s)", exc.__class__.__name__)
            return False
        _keeper, _stop = keeper, stop
        # The one line that says the keeper is on: afterwards it writes only when it acts.
        _LOGGER.info("holding the home folder %s at its set mode %04o", _home(), wanted() or 0)
        return True


def stop() -> None:
    """Stop the keeper and wait for it. For tests; the gateway never stops it."""
    global _keeper, _said
    with _lock:
        keeper, _keeper = _keeper, None
        _stop.set()
        _said = False
    if keeper is not None and keeper.is_alive():
        keeper.join(timeout=5)


__all__ = ["INTERVAL", "THREAD_NAME", "hold", "start", "stop", "wanted"]
