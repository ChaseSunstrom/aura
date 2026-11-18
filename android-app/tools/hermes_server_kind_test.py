#!/usr/bin/env python3
"""Executable spec for finding the jarvis-adapter behind a URL.

The app can now be pointed at a third server: the jarvis-adapter in front of
Hermes (`docs/hermes-hud-protocol.md`). It shares the console relay's socket
path — both are `/ws` — and accepts the same bearer on the upgrade, so it
CANNOT be told apart by dialling: a pipeline client pointed at it "connects"
and then hears JSON-RPC it does not understand. It is told apart by asking:

    GET <base>/api/pair   (bearer)   → {"kind": "hermes-adapter", …}   HERMES
    GET <base>/health     (no auth)  → {"service": "jarvis-adapter"}   HERMES (wrong key)
    GET <base>/api/config            → the old rule (server_endpoint_test.py)

Two decisions live in `config/ServerEndpoint.kt` and are mirrored here:

  1. the body decides, never the status — a proxy can rewrite a status;
  2. HERMES is never a candidate for the clients that dial jarvis-core's
     protocol (the device channel, the companion voice client, the assist
     pipeline). `candidates(HERMES)` is the unknown-kind order.

And the pairing QR (`config/HermesPairing.kt`): `{url, key, kind}` is accepted
only with the adapter's own `kind`, the URL goes through the same validator the
typed field uses, and the key is stored as the token — there is no claim step.

Run:  python3 android-app/tools/hermes_server_kind_test.py
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ANDROID = Path(__file__).resolve().parents[1]
KOTLIN = ANDROID / "app/src/main/kotlin/ai/aura/app"
ENDPOINT = KOTLIN / "config/ServerEndpoint.kt"
PAIRING = KOTLIN / "config/HermesPairing.kt"
DISCOVERY = KOTLIN / "config/ServerDiscovery.kt"
CONVERSATION = KOTLIN / "assist/JarvisConversation.kt"

HERMES_KIND = "hermes-adapter"
HERMES_SERVICE = "jarvis-adapter"
HOUSE_KINDS = ["RELAY", "CORE"]


def _json(body):
    if body is None or not body.strip():
        return None
    try:
        parsed = json.loads(body)
    except (ValueError, TypeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def kind_from_pair_probe(status: int, body) -> str | None:
    parsed = _json(body)
    if parsed is None:
        return None
    return "HERMES" if parsed.get("kind") == HERMES_KIND else None


def kind_from_health(status: int, body) -> str | None:
    parsed = _json(body)
    if parsed is None:
        return None
    return "HERMES" if parsed.get("service") == HERMES_SERVICE else None


def candidates(known: str | None) -> list[str]:
    if known is None or known == "HERMES":
        return list(HOUSE_KINDS)
    return [known] + [k for k in HOUSE_KINDS if k != known]


#: The live adapter's answers, captured 2026-09-02.
PAIR_BODY = json.dumps({"url": "http://127.0.0.1:8643", "key": "devadapterkey0123456789",
                        "model": "qwen3.8-27b", "kind": "hermes-adapter"})
HEALTH_BODY = json.dumps({"status": "ok", "service": "jarvis-adapter", "uptime_s": 624.8})

PAIR_CASES = [
    ("the adapter, with the right key", 200, PAIR_BODY, "HERMES"),
    ("the adapter behind a proxy that rewrote the status", 401, PAIR_BODY, "HERMES"),
    ("the adapter refusing a wrong key", 401, json.dumps({"detail": "Unauthorized"}), None),
    ("jarvis-core, which has /api/pair/claim but no /api/pair", 404, "", None),
    ("something else claiming a kind", 200, json.dumps({"kind": "other"}), None),
    ("a captive portal", 200, "<html>sign in</html>", None),
    ("no body", 500, None, None),
]

HEALTH_CASES = [
    ("the adapter's health", 200, HEALTH_BODY, "HERMES"),
    ("another service's health", 200, json.dumps({"status": "ok", "service": "jarvis-core"}), None),
    ("an empty health", 200, "", None),
]


def check_rules() -> int:
    failures = 0
    for label, status, body, expected in PAIR_CASES:
        got = kind_from_pair_probe(status, body)
        if got != expected:
            print(f"FAIL  pair: {label}: expected {expected}, got {got}")
            failures += 1
    for label, status, body, expected in HEALTH_CASES:
        got = kind_from_health(status, body)
        if got != expected:
            print(f"FAIL  health: {label}: expected {expected}, got {got}")
            failures += 1
    return failures


def check_candidates() -> int:
    failures = 0
    if candidates("HERMES") != HOUSE_KINDS:
        print(f"FAIL  candidates(HERMES) is {candidates('HERMES')}")
        failures += 1
    for known in (None, "HERMES", "CORE", "RELAY"):
        if "HERMES" in candidates(known):
            print(f"FAIL  HERMES offered as a dialling candidate for known={known}")
            failures += 1
    return failures


def check_kotlin_agrees() -> int:
    failures = 0
    for path in (ENDPOINT, PAIRING, DISCOVERY):
        if not path.is_file():
            print(f"FAIL  {path} is missing")
            failures += 1
    if failures:
        return failures
    src = ENDPOINT.read_text(encoding="utf-8")
    if 'HERMES("/ws")' not in src:
        print("FAIL  ServerKind.HERMES does not dial /ws")
        failures += 1
    if f'const val HERMES_KIND = "{HERMES_KIND}"' not in src:
        print(f"FAIL  ServerEndpoint.kt no longer keys on kind == {HERMES_KIND!r}")
        failures += 1
    if f'const val HERMES_SERVICE = "{HERMES_SERVICE}"' not in src:
        print(f"FAIL  ServerEndpoint.kt no longer keys on service == {HERMES_SERVICE!r}")
        failures += 1
    m = re.search(r"val HOUSE_KINDS: List<ServerKind> = listOf\(([^)]*)\)", src)
    if not m:
        print("FAIL  ServerEndpoint.kt has no HOUSE_KINDS")
        failures += 1
    else:
        kinds = re.findall(r"ServerKind\.(\w+)", m.group(1))
        if kinds != HOUSE_KINDS:
            print(f"FAIL  HOUSE_KINDS is {kinds}, this file has {HOUSE_KINDS}")
            failures += 1
    if "null, ServerKind.HERMES -> HOUSE_KINDS" not in src:
        print("FAIL  candidates(HERMES) no longer falls back to the house kinds")
        failures += 1
    # The body decides: the pair probe never reads the status.
    probe = re.search(r"fun kindFromPairProbe\(status: Int, body: String\?\): ServerKind\? \{(.*?)\n    \}", src, re.S)
    if not probe or "status" in probe.group(1):
        print("FAIL  kindFromPairProbe reads the status code; the body must decide")
        failures += 1

    pairing = PAIRING.read_text(encoding="utf-8")
    if "ServerUrl.check(" not in pairing:
        print("FAIL  HermesPairing.kt does not validate the URL with ServerUrl.check")
        failures += 1
    if "ServerEndpoint.HERMES_KIND" not in pairing:
        print("FAIL  HermesPairing.kt does not require the adapter's kind")
        failures += 1

    discovery = DISCOVERY.read_text(encoding="utf-8")
    order = [discovery.find("pairProbeUrl"), discovery.find("healthUrl"), discovery.find("probeUrl(base)")]
    if any(i < 0 for i in order) or order != sorted(order):
        print("FAIL  ServerDiscovery.detect does not probe pair, then health, then /api/config")
        failures += 1
    if "followRedirects(false)" not in discovery:
        print("FAIL  ServerDiscovery follows redirects with the bearer attached")
        failures += 1

    convo = CONVERSATION.read_text(encoding="utf-8")
    if "ServerKind.HERMES -> startHermes()" not in convo:
        print("FAIL  JarvisConversation does not pick the Hermes engine for ServerKind.HERMES")
        failures += 1
    if "ServerDiscovery.detect(" not in convo:
        print("FAIL  JarvisConversation never asks which server is there when the kind is unknown")
        failures += 1
    return failures


def main() -> int:
    failures = check_rules() + check_candidates() + check_kotlin_agrees()
    if failures:
        print(f"\n{failures} failure(s)")
        return 1
    print(
        f"hermes server kind: {len(PAIR_CASES)} pair cases, {len(HEALTH_CASES)} health cases, "
        "the candidate order and the Kotlin agree"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
