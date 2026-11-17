#!/usr/bin/env python3
"""The setup page's server: six questions in a browser while the installer waits.

scripts/install.sh starts this for the length of one install, on loopback unless told another
address, behind a one-time token it made. The page (index.html beside this file) asks where
AURA is reached, which model answers, how it speaks and listens, the wake word, the devices and
the look. This file checks every answer, writes them as one private file of ``SETUP_*=value``
lines the installer reads (never sources), and relays the installer's progress back to the page.

Standard library only, Python 3.8 or newer: it runs on the machine's own python3 or in a small
python container, before anything of AURA is built.

What it guarantees: no answer is written unchecked; a value cannot hold a newline, a quote, a
space or a shell metacharacter (the console's password hash, in the console's own format, is the
one value with a ``$``); the console password is never written, only its scrypt hash; a model key
goes to the endpoint under test and into the answers file, and is never sent back or logged; the
API answers only to the token and only under this server's own name.

What it does not cover: whoever can read the install directory can read the answers file (it is
mode 0600 in a 0700 directory, as the .env it becomes is). The page is plain HTTP: on loopback
that is the machine itself; on a chosen address it is as private as that network. Whether an
address or an endpoint is a good choice is the person's to decide; only its shape is checked.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import ipaddress
import json
import logging
import os
import re
import secrets
import sys
import tempfile
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import zoneinfo
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

_LOGGER = logging.getLogger("aura.setup")

HERE = Path(__file__).resolve().parent
#: The installer's phases, in the order the page shows them (scripts/install.sh).
PHASES = ("preflight", "setup", "secrets", "n8n", "images", "start", "verify", "pair")
THEMES = ("aura", "jarvis")
#: The listening models the gateway image can be built with (deploy/Dockerfile, WHISPER_MODEL).
#: An install that pulls its images has the one they were built with: the installer says which
#: in facts.json ("stt"), and only those are offered.
STT_MODELS = ("tiny", "base", "small", "medium", "large-v3")
#: Hermes speaks through one of two engines (aura-schema, tts.provider): there is no third.
TTS = ("kokoro", "piper")
DEVICES = ("phone", "desktop", "car")
NEEDS_GPU = {"models.mode": "local", "voice.tts": "kokoro"}

#: The console's own parameters (aura-web/src/lib/server/consoleAuth.ts, SCRYPT), bound to it by
#: tests/contracts/console-password.json: a hash made here must be one the console verifies.
SCRYPT_N, SCRYPT_R, SCRYPT_P, SCRYPT_KEYLEN, SCRYPT_SALT = 16384, 8, 1, 32, 16
MIN_PASSWORD = 10

#: Every written value matches this: nothing that starts a line, a quote, a comment or a command.
SAFE_VALUE = re.compile(r"[A-Za-z0-9._~+/=:@,$-]*")
_URL = re.compile(r"https?://[A-Za-z0-9.-]{1,253}(?::[0-9]{1,5})?(?:/[A-Za-z0-9._~/-]*)?")
_TZ = re.compile(r"[A-Za-z][A-Za-z0-9_+-]*(?:/[A-Za-z0-9_+-]+){0,2}")
_MODEL = re.compile(r"[A-Za-z0-9._:/@+-]{1,128}")
_KEY = re.compile(r"[A-Za-z0-9._~+/=-]{0,512}")
_DETAIL = re.compile(r"[A-Za-z0-9 ._:/()-]{0,160}")
_FONT = re.compile(r"[a-z0-9-]+\.woff2")

MAX_BODY = 64 * 1024
#: How much of an oversized body is read and thrown away so the refusal can be delivered.
MAX_DRAIN = 1024 * 1024
MAX_BAD_TOKENS = 30
ENDPOINT_TIMEOUT = 8.0
ENDPOINT_MAX_BYTES = 1024 * 1024


# ---- answers ----------------------------------------------------------------------------------

def zone_exists(zone: str) -> bool:
    """False when this machine's zone database is there and does not hold the name.

    A container given a zone that does not exist keeps time in UTC and says nothing, so every
    routine would run hours off with no error to find. Not covered: a machine with no zone
    database (the page served from a slim container), where only the name's shape is checked.
    """
    try:
        zoneinfo.ZoneInfo("Etc/UTC")
    except Exception:  # noqa: BLE001  # no database here: nothing to look the name up in
        return True
    try:
        zoneinfo.ZoneInfo(zone)
    except (zoneinfo.ZoneInfoNotFoundError, ValueError, OSError):
        return False
    return True


def hash_password(password: str, salt: bytes | None = None) -> str:
    """``scrypt$N$r$p$salt$key`` (base64), the line the console stores and verifies.

    Not covered: a password that begins or ends with a character only one of Python's
    ``strip`` and JavaScript's ``trim`` calls a space (a control character, a byte-order mark).
    """
    salt = secrets.token_bytes(SCRYPT_SALT) if salt is None else salt
    # The console derives from the NFKC form of what is typed at sign-in. A hash made here from
    # the raw characters would refuse the same password typed with another keyboard's spelling
    # of an accent or a ligature, and the first sign-in is no place to be locked out.
    typed = unicodedata.normalize("NFKC", password.strip())
    key = hashlib.scrypt(typed.encode("utf-8"), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P,
                         dklen=SCRYPT_KEYLEN, maxmem=128 * SCRYPT_N * SCRYPT_R * 4)
    return "scrypt${}${}${}${}${}".format(SCRYPT_N, SCRYPT_R, SCRYPT_P, base64.b64encode(salt).decode(),
                                          base64.b64encode(key).decode())


def _text(section: Any, field: str) -> str:
    value = section.get(field) if isinstance(section, dict) else None
    return value if isinstance(value, str) else ""


def _address(text: str) -> str | None:
    try:
        ip = ipaddress.IPv4Address(text)
    except ValueError:
        return None
    if ip.is_unspecified or ip.is_multicast or ip.is_reserved or ip.is_link_local:
        return None
    return str(ip)


def validate(answers: Any, facts: dict[str, Any]) -> tuple[dict[str, str], dict[str, str]]:
    """The answers as ``SETUP_*`` values, or what is wrong with them by field. Never both.

    Not covered: whether the address is one of this machine's (a WireGuard address may come up
    later), or whether the endpoint answers (the page's Test button asks; the install does not
    depend on it).
    """
    errors: dict[str, str] = {}
    if not isinstance(answers, dict):
        return {}, {"answers": "expected one object with server, models, voice, wake, devices and theme"}
    server, models = answers.get("server"), answers.get("models")
    voice, wake = answers.get("voice"), answers.get("wake")
    gpu = bool(facts.get("gpu"))

    bind = _address(_text(server, "bind"))
    if bind is None:
        errors["server.bind"] = "choose one IPv4 address of this machine; never 0.0.0.0, which is every network at once"
    public = _text(server, "public_url").strip() or (f"http://{bind}:8643" if bind else "")
    if "server.bind" not in errors and not _URL.fullmatch(public):
        errors["server.public_url"] = "an http:// or https:// address, letters, digits, dots and slashes only"
    zone = _text(server, "timezone") or str(facts.get("timezone") or "Etc/UTC")
    if not _TZ.fullmatch(zone) or len(zone) > 64:
        errors["server.timezone"] = "an IANA name such as Europe/Berlin"
    elif not zone_exists(zone):
        errors["server.timezone"] = "no such time zone; an IANA name such as Europe/Berlin"
    password = _text(server, "password")
    if not MIN_PASSWORD <= len(password.strip()) <= 256:
        errors["server.password"] = f"at least {MIN_PASSWORD} characters"

    mode = _text(models, "mode")
    url, model, key = _text(models, "url").strip(), _text(models, "model").strip(), _text(models, "key").strip()
    if mode not in ("local", "endpoint"):
        errors["models.mode"] = "choose the model on this machine or an endpoint you have"
    elif mode == "local" and not gpu:
        errors["models.mode"] = "the model on this machine needs an NVIDIA card with the container toolkit; none was found"
    elif mode == "endpoint":
        if not _URL.fullmatch(url):
            errors["models.url"] = "the endpoint's address, starting http:// or https:// (for example http://192.0.2.20:11434/v1)"
        if not _MODEL.fullmatch(model):
            errors["models.model"] = "the model's name as the endpoint lists it"
        if not _KEY.fullmatch(key):
            errors["models.key"] = "a key is letters, digits and . _ ~ + / = - only; leave it empty if the endpoint needs none"

    tts, stt = _text(voice, "tts"), _text(voice, "stt")
    if tts not in TTS:
        errors["voice.tts"] = "choose how it speaks"
    elif tts == "kokoro" and not gpu:
        errors["voice.tts"] = "the natural voice needs an NVIDIA card with the container toolkit; none was found"
    if stt not in stt_choices(facts):
        errors["voice.stt"] = "choose a listening model"

    # On or off, nothing more: the word the house listens for follows the theme
    # (aura-core, PERSONA_WAKE_WORDS), so a word chosen here would be a choice that does nothing.
    enabled = wake.get("enabled") if isinstance(wake, dict) else None
    if not isinstance(enabled, bool):
        errors["wake.enabled"] = "say whether the house listens for its wake word"

    devices = answers.get("devices")
    if not isinstance(devices, list) or any(d not in DEVICES for d in devices):
        errors["devices"] = "phone, desktop, car, or none"
    theme = answers.get("theme")
    if theme not in THEMES:
        errors["theme"] = "choose a look"

    if errors:
        return {}, errors
    assert bind is not None and isinstance(devices, list)
    endpoint = mode == "endpoint"
    env = {
        "SETUP_BIND": bind,
        "SETUP_PUBLIC_URL": public,
        "SETUP_TZ": zone,
        "SETUP_CONSOLE_HASH": hash_password(password),
        "SETUP_MODEL": mode,
        "SETUP_LLM_URL": url if endpoint else "",
        "SETUP_LLM_MODEL": model if endpoint else "",
        "SETUP_LLM_KEY": key if endpoint else "",
        "SETUP_TTS": tts,
        "SETUP_STT": stt,
        "SETUP_WAKE": "on" if enabled else "off",
        "SETUP_DEVICES": ",".join(d for d in DEVICES if d in devices),
        "SETUP_THEME": str(theme),
    }
    # The last gate, whatever the checks above let through: one bad value and nothing is written.
    bad = [name for name, value in env.items() if not SAFE_VALUE.fullmatch(value)]
    if bad:
        return {}, {"answers": "a value could not be written safely: " + ", ".join(sorted(bad))}
    return env, {}


def stt_choices(facts: dict[str, Any]) -> list[str]:
    """The listening models this install can really have: the installer's list, or all of them."""
    named = facts.get("stt")
    if isinstance(named, list):
        return [m for m in STT_MODELS if m in named] or ["small"]
    return list(STT_MODELS)


def write_env(dest: Path, env: dict[str, str]) -> None:
    """One private file, whole or not at all: written beside and renamed."""
    dest.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(dest.parent, 0o700)
    fd, tmp = tempfile.mkstemp(dir=str(dest.parent), prefix=dest.name + ".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write("SETUP_VERSION=1\n")
            for name, value in env.items():
                fh.write(f"{name}={value}\n")
        os.chmod(tmp, 0o600)
        os.replace(tmp, dest)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ---- progress ---------------------------------------------------------------------------------

def read_progress(path: Path) -> dict[str, Any]:
    """What the installer last wrote, with only known words let through. Never raises.

    The installer writes this file and the page draws it, so it is checked like anything else
    that reaches a browser: an unknown phase is "waiting", an odd detail is dropped.
    """
    blank: dict[str, Any] = {"phase": "waiting", "state": "running", "done": [], "detail": "", "console": ""}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return blank
    if not isinstance(raw, dict):
        return blank
    out = dict(blank)
    known = PHASES + ("done", "failed")
    phase, state = raw.get("phase"), raw.get("state")
    if phase in known:
        out["phase"] = phase
        out["state"] = state if state in ("running", "done", "failed") else "running"
    out["done"] = [p for p in PHASES if isinstance(raw.get("done"), list) and p in raw["done"]]
    detail, console = raw.get("detail"), raw.get("console")
    if phase in known and isinstance(detail, str) and _DETAIL.fullmatch(detail):
        out["detail"] = detail
    if isinstance(console, str) and _URL.fullmatch(console):
        out["console"] = console
    return out


# ---- the endpoint test ------------------------------------------------------------------------

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect would carry the Authorization header to wherever it points: follow none."""

    def redirect_request(self, *_args: Any, **_kwargs: Any) -> None:
        return None


def probe_endpoint(url: str, key: str, model: str) -> dict[str, Any]:
    """Ask an OpenAI-compatible endpoint for its models. The key goes there and nowhere else.

    Not covered: that the model answers a prompt (a list is not a reply), or anything about an
    endpoint that speaks another protocol. Only http and https are let in, and a redirect is
    reported as the answer it is, not followed.
    """
    result: dict[str, Any] = {"ok": False, "models": [], "has_model": False, "error": ""}
    url = url.strip().rstrip("/")
    if not _URL.fullmatch(url):
        result["error"] = "the address must start with http:// or https://"
        return result
    request = urllib.request.Request(url + "/models", headers={"Accept": "application/json"})
    if key and _KEY.fullmatch(key):
        request.add_header("Authorization", "Bearer " + key)
    try:
        with urllib.request.build_opener(_NoRedirect).open(request, timeout=ENDPOINT_TIMEOUT) as response:
            body = response.read(ENDPOINT_MAX_BYTES + 1)
    except urllib.error.HTTPError as err:
        result["error"] = f"the endpoint answered {err.code}" + (": the key was refused" if err.code in (401, 403) else "")
        return result
    except (urllib.error.URLError, OSError, ValueError) as err:
        reason = getattr(err, "reason", err)
        result["error"] = "no answer from the endpoint: " + type(reason).__name__
        return result
    try:
        data = json.loads(body[:ENDPOINT_MAX_BYTES])
        names = [str(row["id"]) for row in data["data"] if isinstance(row, dict) and _MODEL.fullmatch(str(row.get("id", "")))]
    except (ValueError, KeyError, TypeError):
        result["error"] = "the endpoint answered, but not with a list of models (is the address the /v1 one?)"
        return result
    result.update(ok=True, models=names[:200], has_model=model in names)
    return result


# ---- the server -------------------------------------------------------------------------------

def _read_facts(state: Path) -> dict[str, Any]:
    try:
        facts = json.loads((state / "facts.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return facts if isinstance(facts, dict) else {}


class _Setup(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], *, public: str, token: str, state: Path, src: Path) -> None:
        super().__init__(address, _Handler)
        #: The address people open, which is the socket's own except inside a container.
        self.public = public
        self.token = token
        self.state = state
        self.src = src
        self.bad_tokens = 0
        self.lock = threading.Lock()

    def static(self) -> dict[str, tuple[Path, str]]:
        styles = self.src / "aura-web" / "src" / "lib" / "styles"
        return {
            "/": (HERE / "index.html", "text/html; charset=utf-8"),
            "/setup.css": (HERE / "setup.css", "text/css; charset=utf-8"),
            "/setup.js": (HERE / "setup.js", "text/javascript; charset=utf-8"),
            # The generated token files, read from the source tree: the page types no colour of its own.
            "/tokens.css": (styles / "tokens.css", "text/css; charset=utf-8"),
            "/themes.css": (styles / "themes.css", "text/css; charset=utf-8"),
            "/fonts.css": (styles / "fonts.css", "text/css; charset=utf-8"),
        }


class _Handler(BaseHTTPRequestHandler):
    server: _Setup
    server_version = "aura-setup"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    # -- plumbing

    def log_message(self, format: str, *args: Any) -> None:
        # The request line holds ?t=<token> on the first load: log the path alone.
        # A line that was never a request has no path: this is called for its refusal too, and
        # raising here left that caller with a dropped connection and the log with a traceback.
        path = urllib.parse.urlsplit(getattr(self, "path", "")).path
        _LOGGER.info("%s %s -> %s", self.command or "-", path, args[1] if len(args) > 1 else "")

    def _send(self, status: int, body: bytes, kind: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; img-src 'self' data:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, payload: dict[str, Any]) -> None:
        self._send(status, json.dumps(payload).encode("utf-8"), "application/json")

    def _named_right(self) -> bool:
        """Only under this server's own name: a page elsewhere cannot reach it through a rebound host."""
        port = self.server.server_address[1]
        names = {f"127.0.0.1:{port}", f"localhost:{port}", f"{self.server.public}:{port}"}
        if self.headers.get("Host", "") in names:
            return True
        self._json(HTTPStatus.FORBIDDEN, {"error": "this page answers only at the address the installer printed"})
        return False

    def _allowed(self) -> bool:
        with self.server.lock:
            if self.server.bad_tokens >= MAX_BAD_TOKENS:
                self._json(HTTPStatus.TOO_MANY_REQUESTS, {"error": "too many wrong tokens: run the installer again"})
                return False
            given = self.headers.get("Authorization", "")
            good = given.startswith("Bearer ") and hmac.compare_digest(given[7:].encode(), self.server.token.encode())
            # Only a token that was offered counts. A page open in another tab can send this
            # address requests, never this header: counted, thirty of them closed the setup
            # on the person doing it.
            if not good and given.startswith("Bearer "):
                self.server.bad_tokens += 1
        if not good:
            self._json(HTTPStatus.UNAUTHORIZED, {"error": "open the address the installer printed: it carries the token"})
        return good

    def _read(self) -> bytes | None:
        """The request's body, read before anything is answered; None when it is too large.

        Read first, even for a request about to be refused: answering and closing with the body
        unread resets the connection, and the caller sees a dropped line instead of the refusal.
        """
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = -1
        if length < 0 or length > MAX_DRAIN:
            self.close_connection = True       # the unread body must not be parsed as a next request
            return None
        raw = self.rfile.read(length)
        return None if length > MAX_BODY else raw

    # -- routes

    def do_GET(self) -> None:
        if not self._named_right():
            return
        path = urllib.parse.urlsplit(self.path).path
        if path == "/api/state":
            if self._allowed():
                self._json(HTTPStatus.OK, {
                    "facts": _read_facts(self.server.state),
                    "choices": {"themes": list(THEMES), "stt": stt_choices(_read_facts(self.server.state)),
                                "tts": list(TTS), "devices": list(DEVICES), "min_password": MIN_PASSWORD},
                    "answered": (self.server.state / "answers.env").exists(),
                })
            return
        if path == "/api/progress":
            if self._allowed():
                self._json(HTTPStatus.OK, read_progress(self.server.state / "progress.json"))
            return
        found = self.server.static().get(path)
        if found is None and path.startswith("/fonts/") and _FONT.fullmatch(path[7:]):
            found = (self.server.src / "aura-web" / "static" / "fonts" / path[7:], "font/woff2")
        if found is None or not found[0].is_file():
            self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        self._send(HTTPStatus.OK, found[0].read_bytes(), found[1])

    do_HEAD = do_GET

    def do_POST(self) -> None:
        raw = self._read()
        if not self._named_right():
            return
        path = urllib.parse.urlsplit(self.path).path
        if path not in ("/api/answers", "/api/test-model"):
            self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        if not self._allowed():
            return
        if raw is None:
            self._json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "too large"})
            return
        try:
            body = json.loads(raw or b"null")
        except ValueError:
            self._json(HTTPStatus.BAD_REQUEST, {"error": "not JSON"})
            return
        if path == "/api/test-model":
            section = body if isinstance(body, dict) else {}
            self._json(HTTPStatus.OK, probe_endpoint(_text(section, "url"), _text(section, "key"), _text(section, "model")))
            return
        dest = self.server.state / "answers.env"
        with self.server.lock:
            if dest.exists():
                # The installer is already working from the first set: changing them under it
                # would start services the env files do not describe.
                self._json(HTTPStatus.CONFLICT, {"error": "the install has started on the answers already given"})
                return
            env, errors = validate(body, _read_facts(self.server.state))
            if errors:
                self._json(HTTPStatus.UNPROCESSABLE_ENTITY, {"errors": errors})
                return
            write_env(dest, env)
        _LOGGER.info("answers written (%d values)", len(env))
        self._json(HTTPStatus.OK, {"ok": True})


def make_server(bind: str, port: int, *, token: str, state: Path, src: Path, listen: str = "") -> _Setup:
    """The server, not yet serving. Refuses a guessable token and the every-network address.

    ``listen`` is for a container only: there the socket must take every address of the
    container, and the one address of the machine is chosen by the publish rule that started it.
    """
    if len(token) < 32:
        raise ValueError("the setup token must be at least 32 characters")
    if ipaddress.ip_address(bind).is_unspecified:
        raise ValueError("the setup page binds one address, never all of them")
    return _Setup((listen or bind, port), public=bind, token=token, state=Path(state), src=Path(src))


def end_with_parent(httpd: ThreadingHTTPServer) -> None:
    """Stop serving once the process that started this one is gone.

    The installer stops the page when it ends; killed outright it runs no cleanup, and the page
    kept its port, so the next run found it taken. Does not cover a page served from a
    container, where the parent is not the installer: the installer removes a container of
    that name before it starts one.
    """
    parent = os.getppid()
    if parent <= 1:
        return

    def watch() -> None:
        while os.getppid() == parent:
            time.sleep(1)
        _LOGGER.info("the installer is gone: stopping")
        httpd.shutdown()

    threading.Thread(target=watch, daemon=True).start()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AURA's setup page, for the length of one install.")
    parser.add_argument("--state", required=True, help="the install's setup directory (facts.json in, answers.env out)")
    parser.add_argument("--src", default=str(HERE.parent.parent), help="the source tree (for the generated token files)")
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8198)
    parser.add_argument("--listen", default="", help="the address to listen on when it differs from --bind (inside a container)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="[setup] %(message)s", stream=sys.stderr)
    # From the environment, not argv: a command line is readable by every user of the machine.
    token = os.environ.get("AURA_SETUP_TOKEN", "")
    try:
        httpd = make_server(args.bind, args.port, token=token, state=Path(args.state), src=Path(args.src),
                            listen=args.listen)
    except (ValueError, OSError) as err:
        print(f"setup: {err}", file=sys.stderr)
        return 2
    # The installer waits for this line: it is how it knows the port is this server's.
    _LOGGER.info("listening on %s:%d", args.bind, args.port)
    end_with_parent(httpd)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
