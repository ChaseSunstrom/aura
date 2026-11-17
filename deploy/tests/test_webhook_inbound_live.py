"""An n8n event really becomes a JARVIS turn, and an unsigned one really does not.

The other half of "n8n both ways" (V2_PLAN §5). Outbound is the ``n8n-trigger``
plugin, proven over a socket by ``scripts/verify/n8n-roundtrip.py``. Inbound is
Hermes' webhook platform, and this is its proof: a real ``hermes gateway run`` with
the route this repo's config template ships, and real HTTP POSTs at it.

Skipped unless ``AURA_LIVE_WEBHOOK_PROOF=1``: it needs Hermes installed and about
a minute. Committed because it is the executable form of what
``docs/runbooks/messaging.md`` claims, and the recipe to re-run against the real
port after a deploy.

What it asserts:

* a correctly signed POST is accepted (2xx) *and* the gateway opens an agent
  session named after the platform and the route, so the payload became a turn
  rather than being taken and dropped;
* a POST with no signature is refused, and one with a wrong signature is refused,
  so the HMAC is doing work rather than being decorative;
* a POST to a route that does not exist is 404, which is what an attacker
  guessing route names sees.

The agent behind the route is pointed at a closed port, so nothing here depends on
a model answering: the claim is about the door, not about what is said inside.

Not covered: `deliver_only` routes, the Svix and GitHub signature schemes Hermes
also accepts, and rate limiting.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import shutil
import signal
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("AURA_LIVE_WEBHOOK_PROOF") != "1",
    reason="live proof: needs a Hermes install and ~1 minute; set AURA_LIVE_WEBHOOK_PROOF=1",
)

SECRET = "b" * 32

#: The same route the config template ships, with the same prompt shape. Bound to
#: loopback: Hermes refuses to start a non-loopback route with the INSECURE_NO_AUTH
#: secret, and a real secret on loopback is what production does behind the tailnet
#: port anyway.
CONFIG = """\
model:
  default: "test-model"
  provider: qwen-vllm
  base_url: "http://127.0.0.1:9/v1"
  api_key: ""
  context_length: 65536
  api_mode: chat_completions
providers:
  qwen-vllm:
    api: "http://127.0.0.1:9/v1"
    transport: chat_completions
    default_model: "test-model"
    context_length: 65536
gateway:
  api_server:
    enabled: false
  platforms:
    webhook:
      enabled: true
      host: 127.0.0.1
      port: {port}
      extra:
        routes:
          n8n:
            secret: "{secret}"
            prompt: |
              An n8n workflow reported an event. Decide whether it needs action.

              {{__raw__}}
approvals:
  mode: smart
"""


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _hermes() -> str:
    found = shutil.which("hermes")
    if not found:
        pytest.skip("hermes is not on PATH")
    return found


def _post(url: str, body: bytes, headers: dict[str, str]) -> int:
    request = urllib.request.Request(url, data=body, method="POST",
                                     headers={"Content-Type": "application/json", **headers})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return int(response.status)
    except urllib.error.HTTPError as exc:
        return int(exc.code)


def _signed(body: bytes, secret: str = SECRET) -> dict[str, str]:
    """The V2 headers Hermes verifies: HMAC-SHA256 over ``"<timestamp>.<body>"``."""
    stamp = str(int(time.time()))
    digest = hmac.new(secret.encode(), stamp.encode() + b"." + body, hashlib.sha256).hexdigest()
    return {"X-Webhook-Timestamp": stamp, "X-Webhook-Signature-V2": digest}


@pytest.fixture()
def webhook():
    """A throwaway gateway with the n8n route listening: its URL and its HERMES_HOME."""
    # Not pytest's tmp_path: $HERMES_HOME/gateway.sock is an AF_UNIX path, capped at
    # 108 bytes, which pytest's directory names exceed.
    with tempfile.TemporaryDirectory(prefix="jwh-", dir="/tmp") as tmp:
        root = Path(tmp)
        home = root / "h"
        home.mkdir()
        port = _free_port()
        (home / "config.yaml").write_text(CONFIG.format(port=port, secret=SECRET),
                                          encoding="utf-8")
        log = root / "gateway.log"
        with log.open("w", encoding="utf-8") as sink:
            proc = subprocess.Popen([_hermes(), "gateway", "run"],
                                    env={**os.environ, "HERMES_HOME": str(home)},
                                    stdout=sink, stderr=subprocess.STDOUT, text=True,
                                    start_new_session=True)
            try:
                base = f"http://127.0.0.1:{port}"
                deadline = time.time() + 120
                while time.time() < deadline:
                    try:
                        with urllib.request.urlopen(f"{base}/health", timeout=3):
                            break
                    except (urllib.error.URLError, OSError):
                        time.sleep(1)
                else:
                    pytest.fail("the webhook listener never came up:\n"
                                + log.read_text(errors="replace")[-3000:])
                yield base, home
            finally:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                try:
                    proc.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)


def _wait_for(path: Path, needle: str, timeout: float = 60.0) -> None:
    """Block until ``path`` contains ``needle``, or fail saying what it held instead."""
    deadline = time.time() + timeout
    text = ""
    while time.time() < deadline:
        text = path.read_text(errors="replace") if path.exists() else ""
        if needle in text:
            return
        time.sleep(0.5)
    pytest.fail(f"{needle!r} never appeared in {path.name}:\n{text[-4000:]}")


def test_a_signed_n8n_event_is_accepted_and_becomes_a_turn(webhook):
    base, home = webhook
    body = json.dumps({"workflow": "Nightly backup", "status": "failed",
                       "message": "rsync exited 23"}).encode()
    assert _post(f"{base}/webhooks/n8n", body, _signed(body)) < 400
    # A 2xx only says the listener took the bytes; the first version of this test
    # stopped there and would have passed against a route that accepted the POST and
    # dropped it. What makes this the inbound half of "n8n both ways" is that the
    # route handed the body to the agent, and the gateway records that by opening a
    # session named after the platform and the route. The model behind it is a closed
    # port on purpose, so the turn fails after this point — the claim is about the
    # door, not about what is said inside.
    _wait_for(home / "sessions" / "sessions.json", "webhook:n8n")


def test_an_unsigned_event_is_refused(webhook):
    base, _home = webhook
    body = json.dumps({"workflow": "Nightly backup", "status": "failed"}).encode()
    assert _post(f"{base}/webhooks/n8n", body, {}) >= 400


def test_a_wrongly_signed_event_is_refused(webhook):
    base, _home = webhook
    body = json.dumps({"workflow": "Nightly backup", "status": "failed"}).encode()
    # A valid signature for the same body under a different secret: the shape is
    # right and only the key is wrong, which is the case a length check would miss.
    assert _post(f"{base}/webhooks/n8n", body, _signed(body, "c" * 32)) >= 400


def test_an_unknown_route_is_not_found(webhook):
    base, _home = webhook
    body = b"{}"
    assert _post(f"{base}/webhooks/not-a-route", body, _signed(body)) == 404
