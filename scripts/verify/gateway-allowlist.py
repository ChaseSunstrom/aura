#!/usr/bin/env python3
"""Prove that Hermes' own gateway refuses a message from an unlisted chat account.

The allowlist is not JARVIS code. Hermes' gateway decides who may talk to the agent,
in ``gateway/authz_mixin.py::GatewayAuthorizationMixin._is_user_authorized``, and
``gateway/run.py::_handle_message`` calls it *before* a session is created or a
model is asked anything — an unauthorized sender is logged and dropped there. So the
only proof worth having drives that real function with the configuration this repo
ships, rather than re-implementing the rule in a test that would then agree with
itself forever.

This script builds a bare object carrying the mixin (the gateway's own tests use the
same shape), points it at a ``{PLATFORM}_ALLOWED_USERS`` value, and asks the real
function about a listed id and an unlisted one. It also checks the fail-open flags
Hermes honours — ``{PLATFORM}_ALLOW_ALL_USERS`` and ``GATEWAY_ALLOW_ALL_USERS`` —
because those are the two ways an allowlist stops meaning anything, and both must be
absent from a JARVIS deployment.

Run it where Hermes is importable (this dev box, the JARVIS image, or CI's
plugin-discovery job, which runs in that image):

    python3 scripts/verify/gateway-allowlist.py            # uses ~/.hermes/hermes-agent
    HERMES_AGENT_DIR=/opt/hermes python3 scripts/verify/gateway-allowlist.py

Exit 0 when every case holds, 1 when one does not, 2 when Hermes is not importable
(so a caller can tell "not proven here" from "proven false").
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

#: Where the installed Hermes lives. The image and the dev box differ, and neither
#: puts the gateway on ``sys.path`` for an unrelated interpreter.
DEFAULT_DIRS = (
    Path(os.environ.get("HERMES_AGENT_DIR", "")),
    Path.home() / ".hermes" / "hermes-agent",
    Path("/opt/hermes"),
    Path("/app"),
)


def _import_gateway():
    for candidate in DEFAULT_DIRS:
        if candidate and (candidate / "gateway" / "authz_mixin.py").is_file():
            sys.path.insert(0, str(candidate))
            break
    from gateway.authz_mixin import GatewayAuthorizationMixin  # noqa: PLC0415
    from gateway.config import Platform  # noqa: PLC0415
    from gateway.session import SessionSource  # noqa: PLC0415
    # The authorization path imports gateway.run on its first call, and that module
    # needs python-dotenv: import it here so a host without it is a SKIP, not a
    # traceback halfway through the cases.
    import gateway.run  # noqa: F401, PLC0415

    return GatewayAuthorizationMixin, Platform, SessionSource


def _host(mixin):
    """The smallest object the authorization path will accept.

    ``pairing_store``/``pairing_stores`` empty means "nobody has been paired", and
    ``adapters`` empty means no adapter is claiming to own its own access policy —
    i.e. the configuration a fresh deployment has, which is the one worth testing.
    """

    class _Gateway(mixin):
        pairing_store = None
        pairing_stores: dict = {}
        adapters: dict = {}

    return _Gateway()


def main(argv: list[str]) -> int:
    del argv
    try:
        mixin, platform_enum, session_source = _import_gateway()
    except ImportError as exc:
        print(f"SKIP: Hermes is not importable here ({exc}). "
              f"Set HERMES_AGENT_DIR to the install directory.", file=sys.stderr)
        return 2

    host = _host(mixin)
    failures: list[str] = []

    def check(label: str, expected: bool, **env: str) -> None:
        saved = {k: os.environ.get(k) for k in env}
        os.environ.update({k: v for k, v in env.items()})
        for key, value in env.items():
            if value == "":
                os.environ.pop(key, None)
        try:
            source = session_source(
                platform=getattr(platform_enum, label.split(":")[0].upper()),
                chat_id=label.split(":")[-1], user_id=label.split(":")[-1],
                chat_type="dm",
            )
            got = bool(host._is_user_authorized(source))
        finally:
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
        verdict = "OK  " if got is expected else "FAIL"
        if got is not expected:
            failures.append(f"{label}: expected authorized={expected}, got {got}")
        print(f"{verdict} {label:<28} authorized={got!s:<5} expected={expected}")

    print("Hermes gateway authorization (gateway/authz_mixin.py) with this repo's settings:\n")
    # Telegram: two listed ids, one stranger.
    check("telegram:111", True, TELEGRAM_ALLOWED_USERS="111,222", GATEWAY_ALLOWED_USERS="",
          TELEGRAM_ALLOW_ALL_USERS="", GATEWAY_ALLOW_ALL_USERS="")
    check("telegram:222", True, TELEGRAM_ALLOWED_USERS="111,222", GATEWAY_ALLOWED_USERS="",
          TELEGRAM_ALLOW_ALL_USERS="", GATEWAY_ALLOW_ALL_USERS="")
    check("telegram:999", False, TELEGRAM_ALLOWED_USERS="111,222", GATEWAY_ALLOWED_USERS="",
          TELEGRAM_ALLOW_ALL_USERS="", GATEWAY_ALLOW_ALL_USERS="")
    # Discord, same shape on its own variable.
    check("discord:333", True, DISCORD_ALLOWED_USERS="333", GATEWAY_ALLOWED_USERS="",
          DISCORD_ALLOW_ALL_USERS="", GATEWAY_ALLOW_ALL_USERS="")
    check("discord:444", False, DISCORD_ALLOWED_USERS="333", GATEWAY_ALLOWED_USERS="",
          DISCORD_ALLOW_ALL_USERS="", GATEWAY_ALLOW_ALL_USERS="")
    # An empty allowlist authorizes nobody — the default-deny JARVIS relies on if a
    # bot token ever leaks before the ids are filled in.
    check("telegram:111", False, TELEGRAM_ALLOWED_USERS="", GATEWAY_ALLOWED_USERS="",
          TELEGRAM_ALLOW_ALL_USERS="", GATEWAY_ALLOW_ALL_USERS="")
    check("discord:333", False, DISCORD_ALLOWED_USERS="", GATEWAY_ALLOWED_USERS="",
          DISCORD_ALLOW_ALL_USERS="", GATEWAY_ALLOW_ALL_USERS="")
    # The two ways to turn the allowlist off. Proven to work so that the tests which
    # assert JARVIS never sets them are testing something real.
    check("telegram:999", True, TELEGRAM_ALLOWED_USERS="111", TELEGRAM_ALLOW_ALL_USERS="true",
          GATEWAY_ALLOWED_USERS="", GATEWAY_ALLOW_ALL_USERS="")
    check("telegram:999", True, TELEGRAM_ALLOWED_USERS="", TELEGRAM_ALLOW_ALL_USERS="",
          GATEWAY_ALLOWED_USERS="", GATEWAY_ALLOW_ALL_USERS="true")

    print()
    if failures:
        for line in failures:
            print(f"FAILED: {line}", file=sys.stderr)
        return 1
    print("All cases hold. An unlisted id is refused in gateway/authz_mixin.py, which "
          "gateway/run.py::_handle_message calls before a session exists.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
