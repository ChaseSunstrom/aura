"""A scheduled job's output really reaches a messaging platform — proven, without a token.

Skipped unless ``AURA_LIVE_CRON_PROOF=1``: it starts real ``hermes gateway run`` processes
against throwaway HERMES_HOMEs and waits for the scheduler's own ticker, which takes several
minutes and needs Hermes installed. It is committed because it is the executable form of the
proof — the recipe the owner re-runs the day a real bot token exists, with only the three
lines under "the one configuration step" changed.

What it does *not* stub is the part that matters. The gateway, the Telegram platform adapter,
the cron store, the scheduler tick, the agent turn and ``_resolve_delivery_targets`` are all
the production code paths. The only substitution is the host at the far end of the Bot API:
PTB's ``base_url`` (the option that exists for people running a self-hosted
``telegram-bot-api``) points at :mod:`bench.fixtures.telegram_fake` instead of api.telegram.org.

Both shipped shapes are covered, because they fail differently. The **watchdog** is
``--script … --no-agent``: no model, stdout delivered verbatim. The **brief** is an agent job:
a real turn against the resident model, whose answer is then delivered. A proof of one is not
a proof of the other — the agentless path never touches the model client, and the agent path
never touches the script runner.

Two facts this pinned down, both of which cost an afternoon to find and neither of which is
obvious from the configuration:

* A bare ``--deliver telegram`` resolves through ``TELEGRAM_HOME_CHANNEL``. A bot token alone
  delivers nowhere — the job runs, reports ``ok``, and the owner is never told.
* The delivery only honours ``extra.base_url`` when a gateway is *running*. A standalone
  ``hermes cron tick`` uses ``send_message_tool``'s one-shot sender, which builds
  ``Bot(token=…)`` with the default base URL and goes to the real Telegram.

Three defects in the previous version of this file, all found by re-running it. They are
listed because each one is a way a *live* proof lies, and none of them is visible by reading:

* **It looked for a string the wire does not carry.** Hermes sends a cron delivery as
  MarkdownV2, so the alert arrives as ``GPU 0 temperature is 84\\.2 C`` and
  ``wait_for_message("84.2")`` matched nothing — for a delivery that was landing correctly
  every minute. The proof reported a capability failure that did not exist, which is the
  worst direction for a proof to fail in. Fixed in the fake:
  :func:`bench.fixtures.telegram_fake.unescape_markdown_v2`, so a test may look for the
  sentence a person reads.
* **It held the gateway's stdout on an unread pipe.** A failure was therefore undiagnosable,
  and a gateway that ever logged 64 KB would have deadlocked against a full pipe buffer with
  no way to tell that apart from a delivery that never came. The log goes to a file now and
  its tail is reported on failure — that log is how the escaping above was found.
* **Its negative test could not fail.** The "no home channel" case slept 90 s and asserted
  nothing had arrived, but a *working* delivery takes 75–100 s on an idle host, so the
  assertion was made before the thing it watched for could have happened: it would have gone
  green with delivery fully working. It now runs a control job with an explicit
  ``telegram:<chat_id>`` target in the same gateway, and judges the bare-target job only
  after the control has actually landed.
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from bench.fixtures.telegram_fake import FakeTelegram  # noqa: E402

pytestmark = pytest.mark.skipif(
    os.environ.get("AURA_LIVE_CRON_PROOF") != "1",
    reason="live proof: needs a Hermes install and several minutes; set AURA_LIVE_CRON_PROOF=1",
)

#: The line the watchdog-shaped job prints. Deliberately the real watcher's wording so a
#: change to that sentence has to be made in both places on purpose.
ALERT = "GPU 0 temperature is 84.2 C, above the 80.0 C limit (delivery proof)."

#: What the brief-shaped job is told to say. Short and fully specified: this is a test of the
#: delivery path, and a brief that asked the model to think would be measuring the model.
BRIEF_PROMPT = ("Reply with exactly this sentence and nothing else: "
                "Good morning, Sir. Today's brief is a test brief.")
BRIEF_MARK = "test brief"

#: Where a delivery is waited for. Measured against this host: an `every 1m` job created at T
#: first fires around T+60 (the schedule) and the ticker adds up to another 30 s, so a working
#: delivery lands 75–100 s after `cron create` when nothing else is running. The margin is for
#: the agent turn and for a loaded host — five helpers and a gradle build share these four
#: vCPUs — because the cost of a tight bound here is a red test that means nothing.
DELIVERY_TIMEOUT = 420.0

#: The gateway's first ``getMe`` is it saying the platform connected. Waiting on the real
#: signal rather than sleeping is why this does not flake on a slow start.
CONNECT_TIMEOUT = 180.0

CONFIG = """\
model:
  default: "{model}"
  provider: qwen-vllm
  base_url: "{llm_url}"
  api_key: ""
  context_length: 131072
  api_mode: chat_completions
providers:
  qwen-vllm:
    api: "{llm_url}"
    transport: chat_completions
    default_model: "{model}"
    context_length: 131072
    request_timeout_seconds: 900
gateway:
  api_server:
    enabled: false
  platforms:
    telegram:
      enabled: true
      extra:
        base_url: "{base_url}"
approvals:
  mode: smart
"""


def _deployed(name: str) -> str:
    """One value out of ``deploy/.env.example``.

    The brief needs a model to answer it, and the model server this stack talks to is
    recorded in exactly one place. Reading it beats a hard-coded default: the previous
    version of this file defaulted to ``127.0.0.1:9000``, where nothing has ever listened —
    harmless while only the agentless job was covered, and a guaranteed red the moment an
    agent job needed a real completion.
    """
    example = Path(__file__).resolve().parents[1] / ".env.example"
    for line in example.read_text(encoding="utf-8").splitlines():
        head, sep, tail = line.partition("=")
        if sep and head.strip() == name:
            return tail.split("#")[0].strip()
    raise AssertionError(f"{name} is not in {example}")


def _hermes() -> str:
    found = shutil.which("hermes")
    if not found:
        pytest.skip("hermes is not on PATH")
    return found


def _run(home: Path, *args: str, timeout: float = 60.0) -> subprocess.CompletedProcess:
    return subprocess.run([_hermes(), *args], env={**os.environ, "HERMES_HOME": str(home)},
                          capture_output=True, text=True, timeout=timeout)


def _create(home: Path, name: str, *extra: str) -> None:
    created = _run(home, "cron", "create", "every 1m", *extra, "--name", name)
    assert created.returncode == 0, f"{name}: {created.stdout}\n{created.stderr}"


@contextmanager
def _gateway(tmp_path: Path, *, home_channel: bool):
    """A throwaway Hermes whose Telegram platform talks to the fake, and the fake itself.

    ``home_channel`` is the one configuration step under test: with it, a bare ``telegram``
    target resolves; without it, the same target resolves to nothing at all.
    """
    home = tmp_path / ("home" if home_channel else "home-nochannel")
    (home / "scripts").mkdir(parents=True)
    with FakeTelegram(host="127.0.0.1") as fake:
        (home / "config.yaml").write_text(CONFIG.format(
            base_url=fake.base_url,
            llm_url=os.environ.get("LLM_URL") or _deployed("LLM_URL"),
            model=os.environ.get("LLM_MODEL") or _deployed("LLM_MODEL"),
        ), encoding="utf-8")
        # The one configuration step. In production these three are real: the token from
        # BotFather, the owner's numeric id in the allowlist, and the chat to deliver into.
        env = ("TELEGRAM_BOT_TOKEN=424242:fake-token-for-the-delivery-proof\n"
               "TELEGRAM_ALLOWED_USERS=4242\n")
        if home_channel:
            env += "TELEGRAM_HOME_CHANNEL=4242\n"
        (home / ".env").write_text(env, encoding="utf-8")
        (home / "scripts" / "proof.sh").write_text(f"#!/usr/bin/env bash\necho '{ALERT}'\n",
                                                   encoding="utf-8")
        (home / "scripts" / "proof.sh").chmod(0o755)

        # To a file, never an unread pipe: a gateway that logs more than the pipe buffer
        # would otherwise block forever, and that looks exactly like a missing delivery.
        log = home.parent / f"{home.name}-gateway.log"
        with log.open("wb") as sink:
            proc = subprocess.Popen([_hermes(), "gateway", "run"],
                                    env={**os.environ, "HERMES_HOME": str(home)},
                                    stdout=sink, stderr=subprocess.STDOUT,
                                    stdin=subprocess.DEVNULL, start_new_session=True)
            try:
                assert fake.wait_for_method("getMe", timeout=CONNECT_TIMEOUT), (
                    f"the gateway never connected; log tail:\n{_tail(log)}")
                yield home, fake, log
            finally:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                try:
                    proc.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)


def _tail(log: Path, lines: int = 30) -> str:
    try:
        return "\n".join(log.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:])
    except OSError as exc:  # the gateway may not have written anything at all
        return f"(no log: {exc})"


def test_both_shipped_job_shapes_reach_the_platform(tmp_path):
    """The watchdog and the brief, in one gateway: no model, and a real model turn.

    Created together and waited for together, because two gateway starts to prove two jobs
    would double a proof that already takes minutes.
    """
    with _gateway(tmp_path, home_channel=True) as (home, fake, log):
        _create(home, "proof-alert", "--script", "proof.sh", "--no-agent",
                "--deliver", "telegram")
        _create(home, "proof-brief", BRIEF_PROMPT, "--deliver", "telegram")

        alert = fake.wait_for_message("84.2", timeout=DELIVERY_TIMEOUT)
        assert alert is not None, (
            f"the agentless alert never arrived; methods: {fake.methods}\n{_tail(log)}")
        assert alert["chat_id"] == "4242", "a bare target resolves to TELEGRAM_HOME_CHANNEL"
        # Hermes wraps a cron delivery with the job name. The needles here are the plain
        # sentences; the fake matches them against the unescaped text, because what goes
        # over the wire is MarkdownV2 and carries neither the hyphen nor the decimal point.
        assert fake.wait_for_message("proof-alert", timeout=1) is not None, alert
        assert "84\\.2" in alert["text"], "the raw message is still MarkdownV2"

        brief = fake.wait_for_message(BRIEF_MARK, timeout=DELIVERY_TIMEOUT)
        assert brief is not None, (
            f"the agent brief never arrived; methods: {fake.methods}\n{_tail(log)}")
        assert brief["chat_id"] == "4242"

        listed = _run(home, "cron", "list")
        assert "Delivery failed" not in listed.stdout, listed.stdout


def test_a_job_with_no_home_channel_delivers_nowhere(tmp_path):
    """The trap: a bot token alone is not a delivery target, and the job still reports ok.

    The control job is what makes this mean anything. It is the same script on the same
    schedule in the same gateway, differing only in carrying an explicit chat id — so when it
    lands, the clock has demonstrably passed the point where the bare-target job would have
    delivered if it were going to.
    """
    with _gateway(tmp_path, home_channel=False) as (home, fake, log):
        _create(home, "proof-homeless", "--script", "proof.sh", "--no-agent",
                "--deliver", "telegram")
        _create(home, "proof-control", "--script", "proof.sh", "--no-agent",
                "--deliver", "telegram:4242")

        control = fake.wait_for_message("proof-control", timeout=DELIVERY_TIMEOUT)
        assert control is not None, (
            f"the control never arrived, so this test proves nothing; {fake.methods}\n{_tail(log)}")

        homeless = [entry for entry in fake.sent if "homeless" in entry["text"]]
        assert homeless == [], f"a bare target resolved without a home channel: {homeless}"
        # And the job reported success the whole time, which is the trap itself: the run
        # status is not the delivery status. What saves the operator is the warning line
        # under it, so the runbook's diagnostic is pinned here rather than described.
        listed = _run(home, "cron", "list").stdout
        assert "ok" in listed
        assert "Delivery failed: no delivery target resolved" in listed, listed


def test_the_fake_records_what_a_real_telegram_would_have_received(tmp_path):
    """A sanity check on the proof itself: the transcript is readable, not just a status."""
    with _gateway(tmp_path, home_channel=True) as (home, fake, log):
        _create(home, "proof-readable", "--script", "proof.sh", "--no-agent",
                "--deliver", "telegram")
        assert fake.wait_for_message("84.2", timeout=DELIVERY_TIMEOUT) is not None, _tail(log)
        assert json.dumps(fake.sent)  # serialisable, which is what the report quotes
