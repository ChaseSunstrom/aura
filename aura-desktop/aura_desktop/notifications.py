"""The PC's notification observer (T-0143): the phone's rules, on this machine.

The phone reads notifications under four rules kept in code (``JarvisNotificationListener``):
opt-in per app, never its own, fenced, and data never instructions. This is the same on the PC:

1. **Opt-in per app.** A ``notification`` trigger names the apps it reports (``"apps"``); with none
   it reads nothing at all. ``"*"`` is every notification this PC shows and is documented as such.
2. **Never our own.** The agent's and the desktop app's notifications (app name ``Jarvis``) are
   dropped, so a routine's answer or a consent prompt cannot trigger anything.
3. **Fenced.** :func:`sanitize` is the phone's ``NotificationFence``: control and bidi characters
   stripped, lengths capped, the payload stamped ``untrusted``. Both answer every case in
   ``tests/contracts/notification-fence.json``.
4. **An event, never an action.** This lives with the triggers, which reach the channel and nothing
   else; jarvis-core decides what, if anything, a notification means.

Sources: Linux reads the session bus (``dbus-monitor --binary`` on
``org.freedesktop.Notifications.Notify``, no new dependency); Windows reads ``UserNotificationListener`` when the ``winsdk`` package is
installed and the person has allowed it; macOS has no public way to read other apps'
notifications and says so.

Not covered: lengths are counted in code points here and UTF-16 units on the phone, which differ
only for characters outside the Basic Multilingual Plane (emoji); a notification shown while the
agent was not running is not seen.
"""

from __future__ import annotations

import asyncio
import collections
import contextlib
import logging
import platform
import shutil
import struct
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Iterable, Mapping

from .triggers import EmitFn, Trigger

_LOGGER = logging.getLogger(__name__)

MAX_TITLE_CHARS = 200
MAX_TEXT_CHARS = 1000
MAX_PACKAGE_CHARS = 255
TRUNCATION_MARK = "…"
#: The names the agent and the desktop app post under: every ``notify-send`` here passes
#: ``-a`` the worn theme's name (a test holds that; AURA, or JARVIS for that theme), and the
#: Tauri app's toasts carry its product name, identifier or binary name depending on the build.
OURS = ("aura", "jarvis", "jarvis-desktop", "jarvis-desktop-app", "ai.jarvis.desktop")
#: The most of one D-Bus string that is read; the fence keeps 1000 characters of it anyway.
MAX_RAW_CHARS = 65536
#: One trigger reports at most this many notifications a minute, so a chatty app (or ``"*"``)
#: cannot use up the event budget presence and the other triggers share.
MAX_PER_MIN = 12

_WHITESPACE = "\n\r\t \u2028\u2029"


def _dangerous(c: str) -> bool:
    code = ord(c)
    return (code < 0x20 or 0x7F <= code <= 0x9F  # C0, DEL and C1 controls
            or code == 0x061C                # Arabic letter mark (a bidi mark)
            or 0x200B <= code <= 0x200F      # zero width + LTR/RTL marks
            or 0x202A <= code <= 0x202E      # bidi embedding and overrides
            or 0x2060 <= code <= 0x2064      # word joiner, invisible operators
            or 0x2066 <= code <= 0x2069      # bidi isolates
            or code == 0xFEFF                # zero-width no-break space
            or 0xE0000 <= code <= 0xE007F)   # tag characters: invisible text


def clean(value: str | None, max_chars: int) -> str | None:
    """Strip, flatten and cap one field: whitespace runs become one space, every other control,
    bidi or zero-width character goes, and a field past ``max_chars`` is cut with a mark."""
    if not value:
        return None
    out: list[str] = []
    last_space = False
    cut = False
    for i, c in enumerate(value):
        space = c in _WHITESPACE
        if not space and _dangerous(c):
            continue
        if space:
            if last_space or not out:
                continue
            last_space = True
            out.append(" ")
        else:
            last_space = False
            out.append(c)
        if len(out) > max_chars:
            # What follows is dropped: a cut unless all of it is space or would be stripped anyway.
            cut = out[-1] != " " or any(ch not in _WHITESPACE and not _dangerous(ch) for ch in value[i + 1:])
            break
    text = "".join(out).strip()
    if not text:
        return None
    return text[:max_chars].rstrip() + TRUNCATION_MARK if cut or len(text) > max_chars else text


def sanitize(package: str | None, title: str | None, text: str | None, *, sub_text: str | None = None,
             category: str | None = None, ongoing: bool = False, group_summary: bool = False,
             posted_at_ms: int = 0, ours: Iterable[str] = OURS) -> dict[str, Any] | None:
    """One notification as the flat, labelled map that is reported, or None when it is not."""
    # Fenced like the text: on a PC the app name is whatever the sender says it is.
    pkg = clean(package, MAX_PACKAGE_CHARS)
    if not pkg:
        return None
    if pkg.lower() in {o.lower() for o in ours}:
        return None
    if ongoing or group_summary:
        return None
    t, b, s = clean(title, MAX_TITLE_CHARS), clean(text, MAX_TEXT_CHARS), clean(sub_text, MAX_TITLE_CHARS)
    if t is None and b is None and s is None:
        return None
    out: dict[str, Any] = {"package": pkg}
    if t is not None:
        out["title"] = t
    if b is not None:
        out["text"] = b
    if s is not None:
        out["sub_text"] = s
    if category and category.strip():
        out["category"] = clean(category, 64)
    if posted_at_ms > 0:
        out["posted_at"] = posted_at_ms
    out["untrusted"] = True
    return out


def is_wanted(package: str | None, allowed: set[str] | Iterable[str]) -> bool:
    """Reported at all? Nothing unless the allow-list names the app; ``"*"`` is everything."""
    if not package or not package.strip():
        return False
    allowed = {a.lower() for a in allowed}
    return bool(allowed) and ("*" in allowed or package.lower() in allowed)


class Deduper:
    """A repeat inside the window is refused and restarts it, bounded by count and age."""

    def __init__(self, window_ms: int = 60_000, max_entries: int = 128) -> None:
        self.window_ms = window_ms
        self.max_entries = max_entries
        self._seen: dict[str, int] = {}

    def accept(self, key: str, now_ms: int) -> bool:
        previous = self._seen.pop(key, None)
        self._seen[key] = now_ms
        if previous is not None and now_ms - previous < self.window_ms:
            return False
        for k in [k for k, at in self._seen.items() if now_ms - at > self.window_ms]:
            del self._seen[k]
        while len(self._seen) > self.max_entries:
            del self._seen[next(iter(self._seen))]
        return True


def dedupe_key(payload: Mapping[str, Any]) -> str:
    return f"{payload.get('package', '')} {payload.get('title', '')} {payload.get('text', '')}"


# --- sources -----------------------------------------------------------------------------------

#: The longest message the D-Bus specification allows; a larger length means the stream is not D-Bus.
_DBUS_MAX = 1 << 27


def _align(n: int, to: int) -> int:
    return (n + to - 1) & ~(to - 1)


def _dbus_string(msg: bytes | bytearray, pos: int, order: str) -> tuple[str, int]:
    pos = _align(pos, 4)
    (n,) = struct.unpack_from(order + "I", msg, pos)
    end = pos + 4 + n
    if end >= len(msg) or msg[end] != 0:
        raise ValueError("unterminated string")
    # Capped before decoding: a body of 100 MB of characters the fence drops would otherwise
    # hold the event loop long enough to drop the agent's socket.
    return bytes(msg[pos + 4:min(end, pos + 4 + MAX_RAW_CHARS)]).decode("utf-8", "replace"), end + 1


def _notify_call(msg: bytes | bytearray, order: str, fields_len: int) -> dict[str, str] | None:
    """A ``Notify`` call's app name, summary and body, or None for any other message."""
    pos, end, fields = 16, 16 + fields_len, {}
    while pos < end:
        pos = _align(pos, 8)
        code, sig_len = msg[pos], msg[pos + 1]
        sig = bytes(msg[pos + 2:pos + 2 + sig_len])
        pos += 3 + sig_len
        if sig in (b"s", b"o"):
            fields[code], pos = _dbus_string(msg, pos, order)
        elif sig == b"g":
            n = msg[pos]
            fields[code], pos = bytes(msg[pos + 1:pos + 1 + n]).decode("ascii", "replace"), pos + 2 + n
        elif sig == b"u":
            pos = _align(pos, 4) + 4
        else:
            return None  # no header field of another type exists today; skip rather than guess
    # 2 interface, 3 member, 8 body signature: Notify(app_name s, replaces_id u, icon s, summary s, body s, ...)
    if fields.get(2) != "org.freedesktop.Notifications" or fields.get(3) != "Notify":
        return None
    if not str(fields.get(8, "")).startswith("susss"):
        return None
    pos = _align(end, 8)
    app, pos = _dbus_string(msg, pos, order)
    pos = _align(pos, 4) + 4
    _icon, pos = _dbus_string(msg, pos, order)
    title, pos = _dbus_string(msg, pos, order)
    body, _ = _dbus_string(msg, pos, order)
    return {"package": app, "title": title, "text": body}


def read_dbus_messages(buf: bytearray) -> list[dict[str, str]]:
    """Take every complete message off the front of ``buf`` (what ``dbus-monitor --binary``
    writes: marshalled messages back to back) and return the ``Notify`` calls among them.

    Binary, never dbus-monitor's text: the text form does not escape a string's quotes or
    newlines, so one app's notification body could print as a second call from another app.
    Raises ValueError when the bytes are not D-Bus, which nothing can resynchronise from.

    Not covered: the app name is what the calling program says it is -- any program running as
    you can call itself Signal. A program you run is already trusted as you; what this rules out
    is a notification's *text* posing as another app.
    """
    calls: list[dict[str, str]] = []
    while len(buf) >= 16:
        if buf[0] not in b"lB" or buf[3] != 1:
            raise ValueError("not a D-Bus message")
        order = "<" if buf[0] == ord("l") else ">"
        body_len, _serial, fields_len = struct.unpack_from(order + "III", buf, 4)
        size = _align(16 + fields_len, 8) + body_len
        if size > _DBUS_MAX:
            raise ValueError("message longer than D-Bus allows")
        if len(buf) < size:
            break
        msg = bytes(buf[:size])
        del buf[:size]
        try:
            call = _notify_call(msg, order, fields_len)
        except (ValueError, IndexError, struct.error):
            _LOGGER.debug("skipped a malformed message on the session bus")
            continue
        if call:
            calls.append(call)
    return calls


class LinuxSource:
    """The session bus, through ``dbus-monitor --binary`` (it ships with D-Bus; nothing to install)."""

    name = "dbus"
    #: The first wait before dbus-monitor is started again; it doubles up to five minutes.
    restart_s = 5.0

    def available(self) -> tuple[bool, str]:
        if shutil.which("dbus-monitor"):
            return True, ""
        return False, "dbus-monitor is not installed (package dbus / dbus-bin)"

    async def stream(self, stop: asyncio.Event) -> AsyncIterator[dict[str, str]]:
        """Restarted with backoff whenever dbus-monitor ends (no session bus yet, the bus
        restarted, a dbus-monitor too old for ``--binary``), each time logged with why."""
        backoff = self.restart_s
        while not stop.is_set():
            started = asyncio.get_running_loop().time()
            async for call in self._once(stop):
                yield call
            if stop.is_set():
                return
            if asyncio.get_running_loop().time() - started > 60:
                backoff = self.restart_s
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(stop.wait(), backoff)
            backoff = min(backoff * 2, 300.0)

    async def _once(self, stop: asyncio.Event) -> AsyncIterator[dict[str, str]]:
        proc = await asyncio.create_subprocess_exec(
            "dbus-monitor", "--session", "--binary",
            "interface='org.freedesktop.Notifications',member='Notify'",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        assert proc.stdout is not None and proc.stderr is not None
        buf = bytearray()
        try:
            while not stop.is_set():
                try:
                    chunk = await asyncio.wait_for(proc.stdout.read(65536), timeout=1.0)
                except asyncio.TimeoutError:
                    continue
                if not chunk:
                    break
                buf += chunk
                try:
                    calls = read_dbus_messages(buf)
                except ValueError as err:
                    _LOGGER.warning("notifications: dbus-monitor wrote something that is not D-Bus (%s); restarting it", err)
                    break
                for call in calls:
                    yield call
        finally:
            if proc.returncode is None:
                proc.terminate()
            if not stop.is_set():
                code = await proc.wait()
                why = (await proc.stderr.read(4096)).decode("utf-8", "replace").strip()
                _LOGGER.warning("notifications: dbus-monitor ended (exit %s%s); starting it again",
                                code, f": {why[-300:]}" if why else "")


class WindowsSource:
    """``UserNotificationListener``, polled; it needs the ``winsdk`` package and the person's
    permission (Windows asks once). ponytail: a 5 s poll that diffs notification ids; the
    NotificationChanged event needs a packaged app, which the agent is not."""

    name = "winrt"

    def available(self) -> tuple[bool, str]:
        try:
            import winsdk.windows.ui.notifications.management  # noqa: F401
        except ImportError:
            return False, "reading Windows notifications needs the winsdk package (pip install winsdk)"
        return True, ""

    async def stream(self, stop: asyncio.Event) -> AsyncIterator[dict[str, str]]:  # pragma: no cover - Windows only
        from winsdk.windows.ui.notifications import KnownNotificationBindings, NotificationKinds
        from winsdk.windows.ui.notifications.management import (
            UserNotificationListener, UserNotificationListenerAccessStatus)

        listener = UserNotificationListener.current
        if await listener.request_access_async() != UserNotificationListenerAccessStatus.ALLOWED:
            _LOGGER.info("Windows did not allow reading notifications; the notification trigger is inert")
            return
        seen: set[int] = set()
        first = True
        while not stop.is_set():
            for item in await listener.get_notifications_async(NotificationKinds.TOAST):
                if item.id in seen:
                    continue
                seen.add(item.id)
                if first:
                    continue  # what was already on screen when the agent started is not news
                binding = item.notification.visual.get_binding(KnownNotificationBindings.toast_generic)
                texts = [t.text for t in binding.get_text_elements()] if binding else []
                yield {"package": item.app_info.display_info.display_name if item.app_info else "",
                       "title": texts[0] if texts else "", "text": "\n".join(texts[1:])}
            first = False
            try:
                await asyncio.wait_for(stop.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                pass


class UnavailableSource:
    name = "none"

    def __init__(self, why: str) -> None:
        self.why = why

    def available(self) -> tuple[bool, str]:
        return False, self.why

    async def stream(self, stop: asyncio.Event) -> AsyncIterator[dict[str, str]]:
        # Nothing to read: an empty async generator.
        nothing: tuple[dict[str, str], ...] = ()
        for item in nothing:
            yield item


def source_for(system: str | None = None) -> Any:
    system = system or platform.system()
    if system == "Linux":
        return LinuxSource()
    if system == "Windows":
        return WindowsSource()
    if system == "Darwin":
        return UnavailableSource("macOS has no public way to read other apps' notifications")
    return UnavailableSource(f"no notification source on {system}")


# --- the trigger --------------------------------------------------------------------------------


@dataclass
class NotificationTrigger(Trigger):
    """Fires ``notification`` with a fenced, untrusted payload for each new notification from an
    opted-in app. Nothing named, nothing read: the source is not even started."""

    apps: tuple[str, ...] = ()
    source: Any = None
    deduper: Deduper = field(default_factory=Deduper)

    def __post_init__(self) -> None:
        if self.source is None:
            self.source = source_for()

    async def run(self, emit: EmitFn, stop: asyncio.Event) -> None:
        if not self.apps:
            _LOGGER.info("notification trigger %s names no apps, so it reads nothing", self.id)
            return
        ok, why = self.source.available()
        if not ok:
            _LOGGER.info("notification trigger %s is inert: %s", self.id, why)
            return
        loop = asyncio.get_running_loop()
        sent: collections.deque[float] = collections.deque()
        async for raw in self.source.stream(stop):
            payload = sanitize(raw.get("package"), raw.get("title"), raw.get("text"))
            if payload is None or not is_wanted(payload["package"], set(self.apps)):
                continue
            now = loop.time()
            if not self.deduper.accept(dedupe_key(payload), int(now * 1000)):
                continue
            while sent and now - sent[0] > 60:
                sent.popleft()
            if len(sent) >= MAX_PER_MIN:
                _LOGGER.info("notification trigger %s: more than %d a minute; %s's dropped",
                             self.id, MAX_PER_MIN, payload["package"])
                continue
            sent.append(now)
            await emit("notification", {"trigger": self.id, **payload})

    def describe(self) -> dict[str, Any]:
        out = super().describe()
        ok, why = self.source.available()
        out.update({"apps": list(self.apps), "source": self.source.name if ok else f"unavailable: {why}"})
        return out
