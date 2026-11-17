"""A stand-in for Telegram's Bot API, so a delivery can be proven without a bot token.

Hermes' Telegram platform is python-telegram-bot, whose ``base_url`` exists for people
running the self-hosted ``telegram-bot-api`` server: PTB appends ``<token>/<method>`` to it
and speaks ordinary Bot API over HTTP. That is the seam this uses. Point a gateway at

    gateway.platforms.telegram.extra.base_url: http://127.0.0.1:8901/bot

and every message the gateway sends — including a cron job's ``--deliver telegram`` — lands
here instead of on the internet, where a test can read it back. Nothing about the *Hermes*
side is stubbed: the platform, the allowlist, the scheduler and the delivery path are the
real ones, and the only difference from production is which host answers.

Two extra routes outside the Bot API namespace, for the harness:

* ``GET  /__sent``   — every ``sendMessage`` so far, newest last.
* ``POST /__reset``  — forget them.
* ``POST /__inject`` — queue an update (``{"chat_id": 42, "text": "hello"}``) for the next
  ``getUpdates``, so the inbound half can be driven too.

Stdlib only, like the other fakes in this repo: it has to run anywhere the tests do.

Run it directly::

    python3 -m bench.fixtures.telegram_fake --port 8901
"""
from __future__ import annotations

import argparse
import json
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse

#: The bot identity every ``getMe`` returns. The token is never checked — any token works,
#: which is the point: the proof must not need a real one.
BOT = {"id": 424242, "is_bot": True, "first_name": "JARVIS (fake)", "username": "jarvis_fake_bot",
       "can_join_groups": False, "can_read_all_group_messages": False,
       "supports_inline_queries": False}

#: How long a ``getUpdates`` long-poll waits before answering with an empty list. Well under
#: PTB's own read timeout, and short enough that a test never sits on it.
POLL_SECONDS = 1.0

#: The characters Telegram's MarkdownV2 requires a sender to escape. Straight from the Bot API
#: documentation for that parse mode, in its order.
MARKDOWN_V2_RESERVED = r"_*[]()~`>#+-=|{}.!"

_MARKDOWN_V2_ESCAPE = re.compile("\\\\([" + re.escape(MARKDOWN_V2_RESERVED) + "])")


def unescape_markdown_v2(text: str) -> str:
    r"""``84\.2`` → ``84.2``: the message as a person reads it, not as the wire carries it.

    Hermes sends cron deliveries with ``parse_mode=MarkdownV2``, which escapes every reserved
    character — so a test looking for a temperature, a job name with a hyphen, or any sentence
    ending in a full stop finds nothing in the raw text and concludes the delivery failed.
    Only a backslash before a *reserved* character is removed, so a literal backslash in the
    message survives.
    """
    return _MARKDOWN_V2_ESCAPE.sub(r"\1", text)


class _State:
    """What the fake remembers, guarded because PTB polls from another thread."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.sent: list[dict[str, Any]] = []
        self.pending: list[dict[str, Any]] = []
        #: Every Bot API method asked for, in order. A gateway's first ``getMe`` is how a
        #: test knows the platform has connected, rather than sleeping and hoping.
        self.methods: list[str] = []
        self.update_id = 1000
        self.message_id = 500

    def record(self, params: dict[str, Any]) -> dict[str, Any]:
        text, at = str(params.get("text", "")), time.time()
        with self.lock:
            self.message_id += 1
            message_id = self.message_id
            self.sent.append({"chat_id": str(params.get("chat_id", "")), "text": text,
                              "message_id": message_id, "at": at})
        return {"message_id": message_id, "date": int(at),
                "chat": {"id": params.get("chat_id"), "type": "private"},
                "from": BOT, "text": text}

    def inject(self, chat_id: Any, text: str) -> int:
        with self.lock:
            self.update_id += 1
            self.pending.append({
                "update_id": self.update_id,
                "message": {"message_id": self.update_id, "date": int(time.time()),
                            "chat": {"id": chat_id, "type": "private"},
                            "from": {"id": chat_id, "is_bot": False, "first_name": "Test"},
                            "text": text},
            })
            return self.update_id

    def drain(self) -> list[dict[str, Any]]:
        with self.lock:
            out, self.pending = self.pending, []
        return out


class _Handler(BaseHTTPRequestHandler):
    state: _State

    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: Any) -> None:  # keep the test output readable
        del fmt, args

    # ---------------------------------------------------------------- plumbing
    def _body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        if not raw:
            return {}
        ctype = (self.headers.get("Content-Type") or "").lower()
        if "json" in ctype:
            try:
                loaded = json.loads(raw)
            except ValueError:
                return {}
            return loaded if isinstance(loaded, dict) else {}
        from urllib.parse import parse_qs

        return {k: v[0] for k, v in parse_qs(raw.decode("utf-8", "replace")).items()}

    def _json(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _method(self) -> str:
        """The Bot API method out of ``/bot<token>/<method>``."""
        parts = [p for p in urlparse(self.path).path.split("/") if p]
        return parts[-1] if parts else ""

    # ---------------------------------------------------------------- routes
    def do_GET(self) -> None:  # noqa: N802 — BaseHTTPRequestHandler's spelling
        path = urlparse(self.path).path
        if path == "/__sent":
            with self.state.lock:
                self._json(list(self.state.sent))
            return
        if path == "/__methods":
            with self.state.lock:
                self._json(list(self.state.methods))
            return
        self._dispatch(self._method(), {})

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/__reset":
            with self.state.lock:
                self.state.sent.clear()
                self.state.pending.clear()
                self.state.methods.clear()
            self._json({"ok": True})
            return
        if path == "/__inject":
            body = self._body()
            self._json({"ok": True, "update_id": self.state.inject(body.get("chat_id", 1), str(body.get("text", "")))})
            return
        self._dispatch(self._method(), self._body())

    def _dispatch(self, method: str, params: dict[str, Any]) -> None:
        with self.state.lock:
            self.state.methods.append(method)
        if method == "getMe":
            self._json({"ok": True, "result": BOT})
        elif method == "getUpdates":
            updates = self.state.drain()
            if not updates:
                # Answer the long-poll the way Telegram would rather than immediately: an
                # instant empty answer makes PTB spin at whatever rate the loop allows.
                time.sleep(POLL_SECONDS)
                updates = self.state.drain()
            self._json({"ok": True, "result": updates})
        elif method == "sendMessage":
            self._json({"ok": True, "result": self.state.record(params)})
        else:
            # deleteWebhook, setMyCommands, getMyCommands, sendChatAction … PTB and the
            # gateway call a handful of these at startup and none of them matter here.
            self._json({"ok": True, "result": True})


class FakeTelegram:
    """A running fake. Use as a context manager; ``base_url`` is what a gateway is pointed at."""

    def __init__(self, port: int = 0, host: str = "127.0.0.1") -> None:
        self.state = _State()
        handler = type("Handler", (_Handler,), {"state": self.state})
        self._server = ThreadingHTTPServer((host, port), handler)
        self._server.daemon_threads = True
        # server_address is typed str|bytes; the f-string in base_url needs the str.
        self.host = str(self._server.server_address[0])
        self.port = int(self._server.server_address[1])
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        """What goes in ``platforms.telegram.extra.base_url`` (PTB appends token + method)."""
        return f"http://{self.host}:{self.port}/bot"

    @property
    def sent(self) -> list[dict[str, Any]]:
        with self.state.lock:
            return list(self.state.sent)

    @property
    def methods(self) -> list[str]:
        with self.state.lock:
            return list(self.state.methods)

    def wait_for_method(self, method: str, timeout: float = 60.0) -> bool:
        """True once ``method`` has been called — how a test knows a gateway connected."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if method in self.methods:
                return True
            time.sleep(0.25)
        return False

    def wait_for_message(self, containing: str = "", timeout: float = 60.0) -> dict[str, Any] | None:
        """The first message whose text contains ``containing``, or None within ``timeout``.

        Matched against the message as sent *and* against its MarkdownV2-unescaped form, so
        a caller may look for the sentence a person would read. Raw-only matching is a trap
        with teeth: Hermes sends cron deliveries as MarkdownV2, so the perfectly ordinary
        needle ``"84.2"`` is never present — the wire carries ``84\\.2`` — and a proof that
        the alert arrived failed while the alert was arriving correctly every minute.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for entry in self.sent:
                if containing in entry["text"] or containing in unescape_markdown_v2(entry["text"]):
                    return entry
            time.sleep(0.25)
        return None

    def start(self) -> FakeTelegram:
        self._thread.start()
        return self

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()

    def __enter__(self) -> FakeTelegram:
        return self.start()

    def __exit__(self, *exc: object) -> None:
        self.stop()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8901)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()
    fake = FakeTelegram(port=args.port, host=args.host).start()
    print(f"fake telegram bot api on {fake.base_url}", flush=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        fake.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
