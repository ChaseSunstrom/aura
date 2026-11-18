"""Close a persistent-profile Chromium over CDP before Hermes stops it, so it writes its cookies (T-1545).

Hermes stops the headless browser at the end of every turn (``cleanup_task_resources`` ->
``cleanup_browser``), and its stop is SIGTERM for the browser, then a kill for what survives.
Chromium writes cookies to the profile about every 30 s and on a clean shutdown, so a sign-in
made in the last half-minute of a turn was gone on the next turn: a browser goal signed in
again every turn. ``Browser.close`` is Chromium's own clean shutdown; measured in the jarvis
image, the cookie is kept with it and lost without it.

The wrap sits on ``tools.browser_launcher._terminate``, which Hermes calls only once it has
decided to stop that browser (the last holder of a shared profile), so it never closes a
browser someone else still uses. It touches only a browser this process launched (``owned``)
on a persistent profile (not ``ephemeral``) in its own PID namespace (not ``foreign``).

Does not cover: tabs, which never survive a turn end (the headless-browser skill reopens the
last URL); a browser the owner started, which Hermes never stops; or a Chromium that ignores
``Browser.close`` (it is stopped as before after ``CLOSE_WAIT_S``).
"""
from __future__ import annotations

import functools
import json
import logging
from typing import Any

_LOGGER = logging.getLogger(__name__)

#: How long the stop waits for Chromium's clean shutdown before Hermes's own SIGTERM.
CLOSE_WAIT_S = 3.0
_MARK = "_aura_flushes_cookies"


def _connect(url: str, **kwargs: Any):
    from websockets.sync.client import connect
    return connect(url, **kwargs)


def _close_cleanly(cdp_url: str, proc: Any) -> None:
    """Ask Chromium to shut down cleanly and wait for it, at most ``CLOSE_WAIT_S`` in all."""
    try:
        with _connect(cdp_url, open_timeout=CLOSE_WAIT_S, close_timeout=1, max_size=None) as socket:
            socket.send(json.dumps({"id": 1, "method": "Browser.close"}))
            try:
                socket.recv(timeout=CLOSE_WAIT_S)
            except Exception:  # noqa: BLE001 -- the socket closing is how Chromium says it is going
                pass
    except Exception as exc:  # noqa: BLE001 -- a failed close must never stop the stop
        _LOGGER.debug("browser flush: Browser.close failed (%s); stopping it as before", exc)
        return
    if proc is not None:
        try:
            proc.wait(timeout=CLOSE_WAIT_S)
        except Exception:  # noqa: BLE001 -- still running: Hermes's SIGTERM follows
            pass


def _persistent_owned(launcher: Any, pid: Any) -> str:
    """The CDP WebSocket of the owned persistent-profile browser ``pid`` is, else ""."""
    for browser in list(getattr(launcher, "_browsers", {}).values()):
        if getattr(browser, "pid", None) != pid or not pid:
            continue
        if browser.owned and not browser.ephemeral and not getattr(browser, "foreign", False) \
                and str(browser.cdp_url).startswith(("ws://", "wss://")):
            return str(browser.cdp_url)
    return ""


def install() -> bool:
    """Wrap Hermes's browser stop once. False when this Hermes has no such launcher."""
    try:
        # A static import, not importlib: deploy/hermes-imports.lock.json tracks it across bumps.
        import tools.browser_launcher as found
    except ImportError:
        _LOGGER.info("browser flush: tools.browser_launcher is absent; cookies keep Hermes's own timing")
        return False
    launcher: Any = found
    original = getattr(launcher, "_terminate", None)
    if original is None:
        return False
    if getattr(original, _MARK, False):
        return True

    @functools.wraps(original)
    def _terminate(pid, start_time, proc):
        cdp_url = _persistent_owned(launcher, pid)
        if cdp_url:
            _close_cleanly(cdp_url, proc)
            if proc is not None and proc.poll() is not None:
                # Exited and reaped: signalling its pid now would only log "PID was recycled"
                # at every turn end.
                return None
        return original(pid, start_time, proc)

    setattr(_terminate, _MARK, True)
    launcher._terminate = _terminate
    return True
