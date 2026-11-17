"""jarvis-guard: the gate, the log, the redaction and the brake, end to end.

These are the drills the phase is judged on, written as tests so they run in CI
rather than living in a runbook nobody executes:

* an approved destructive action and a refused one both land in the audit log,
  correctly and distinguishably;
* a secret shaped like the ones this deployment really issues comes out masked
  from a tool result, a log line and the assistant's own reply;
* the kill switch stops the next action, and the model cannot turn it off.

Not covered here: that Hermes' gateway actually invokes the hooks. That is
Hermes' contract, and it is pinned instead by loading the plugin exactly the way
``PluginManager`` does (``test_plugin_loader.py``) plus the hook-signature
checks below, which fail if Hermes changes the kwargs it passes.
"""
from __future__ import annotations

import contextlib
import json
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import pytest

PLUGINS_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PLUGINS_DIR))
from _plugin_loader import FakeCtx, load_plugin  # noqa: E402
from _shared import _audit, _egress, _killswitch, _matrix  # noqa: E402
from _shared._approval_hook import matrix_gate  # noqa: E402

#: Fake credentials, in the shapes this deployment really issues. Constructed
#: here and never read from any ``.env`` — a redaction test that loaded a real
#: secret to prove it gets masked would have put that secret in a transcript,
#: a CI log and a test fixture in order to prove it stays out of one.
FAKE_TAILSCALE_KEY = "tskey-auth-" + "kZZZZ0000TESTONLY9999XXXX"
FAKE_PROXMOX_TOKEN = "PVEAPIToken=jarvis@pve!testonly=00000000-1111-2222-3333-444444444444"
FAKE_GITLAB_PAT = "glpat-" + "ZZZZTESTONLY0000999911"
FAKE_HASS_TOKEN = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
    ".eyJpc3MiOiJ0ZXN0b25seTAwMDAwMDAwMDAifQ"
    ".c2lnbmF0dXJlX3Rlc3Rvbmx5XzAwMDAwMA"
)
ALL_FAKE = (FAKE_TAILSCALE_KEY, FAKE_PROXMOX_TOKEN, FAKE_GITLAB_PAT, FAKE_HASS_TOKEN)


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    _egress.reset()
    yield
    _egress.reset()


@pytest.fixture
def guard():
    module = load_plugin("jarvis-guard")
    ctx = FakeCtx()
    module.register(ctx)
    return module, ctx


# --------------------------------------------------------------------------- #
# Registration
# --------------------------------------------------------------------------- #
def test_the_governor_registers_the_hooks_that_make_it_one(guard):
    _module, ctx = guard
    assert set(ctx.hooks) == {
        "pre_tool_call", "post_tool_call",
        "pre_approval_request", "post_approval_response",
        "transform_llm_output",
        "pre_llm_call",  # T-0567: opens the turn's record of untrusted reads
    }
    assert set(ctx.tools) == {"audit_query", "kill_switch", "plan_approve", "guard_revoke"}


def test_the_governor_has_no_credential_to_be_missing(guard):
    """A gate that switches itself off when an env var is unset is not a gate.

    Every other plugin here declares ``requires_env`` and a ``check_fn`` so its
    tools disappear when the service behind them is unconfigured. This one must
    not: it governs the built-in tools, which are present regardless.
    """
    _module, ctx = guard
    for name in ctx.tools:
        assert ctx.tools[name]["requires_env"] == []
        assert ctx.available(name) is True


def test_the_gate_governs_a_tool_from_no_plugin_at_all(guard):
    """The property the whole phase turns on. Hermes calls every plugin's
    ``pre_tool_call`` for every tool, so this hook sees ``terminal`` — which
    belongs to no plugin and which the baseline used to delete a folder."""
    _module, ctx = guard
    gate = ctx.hooks["pre_tool_call"]
    assert gate("read_file", {"path": "/opt/data/x"}, "task-1") is None
    directive = gate("terminal", {"command": "rm -r /opt/data/work"}, "task-1")
    assert directive["action"] == "approve"
    assert directive["rule_key"].rsplit(":g", 1)[0] == "terminal:destructive"


def test_a_guest_is_refused_what_the_owner_would_have_been_asked(guard, monkeypatch):
    """The brief's tiering, in the matrix: a gated row is an ASK for the owner
    and a member, a DENY for a guest; a free row is free for a guest only
    when it is on GUEST_FREE (test below).
    The level comes from the adapter's record for the session; when the
    adapter cannot say, the turn is the key holder's (fail open to ASK)."""
    from _shared import _speaker

    _module, ctx = guard
    gate = ctx.hooks["pre_tool_call"]
    people = {"sess-guest": {"label": "Ted", "level": "guest", "source": "voice"},
              "sess-member": {"label": "Ann", "level": "member", "source": "voice"},
              "sess-odd": {"label": "?", "level": "king", "source": "voice"}}
    monkeypatch.setattr(_speaker, "fetch", lambda sid: people.get(sid))
    _speaker._cache.clear()

    args = {"command": "rm -r /opt/data/work/old"}
    assert gate("terminal", args, "t", session_id="sess-member")["action"] == "approve"
    assert gate("terminal", args, "t", session_id="sess-unknown")["action"] == "approve"
    assert gate("terminal", args, "t", session_id="sess-odd")["action"] == "approve"
    refused = gate("terminal", args, "t", session_id="sess-guest")
    assert refused["action"] == "block" and "guest" in refused["message"] and "Ted" in refused["message"]
    # A free tool on the guest list stays free: a guest may still be told things.
    assert gate("web_search", {"query": "weather"}, "t", session_id="sess-guest") is None
    # And the refusal is on the record as a refusal, naming the guest.
    latest = _audit.read(1)[0]
    assert latest["verdict"] == "deny" and "guest" in latest["reason"] and latest["session"] == "sess-guest"

    # The adapter down is the key holder: asked, not refused, not waved through.
    monkeypatch.setattr(_speaker, "fetch", lambda sid: None)
    _speaker._cache.clear()
    assert gate("terminal", args, "t", session_id="sess-guest")["action"] == "approve"


def test_a_guest_is_not_read_the_owners_own_things(guard, monkeypatch):
    """T-0445: free for the owner is not free for a guest. A guest's turn ran every FREE
    row -- the owner's mail, facts, past talks, files, the shell, delegate_task -- because
    the guest branch only turned ASK into DENY. Now a free tool off GUEST_FREE is refused
    for a guest, the refusal is audited, and the owner, a member and an unknown speaker
    are unchanged. Not covered: what the model says next (the finding-things skill)."""
    from _shared import _speaker

    _module, ctx = guard
    gate = ctx.hooks["pre_tool_call"]
    people = {"sess-guest": {"label": "Ted", "level": "guest", "source": "voice"},
              "sess-member": {"label": "Ann", "level": "member", "source": "voice"}}
    monkeypatch.setattr(_speaker, "fetch", lambda sid: people.get(sid))
    _speaker._cache.clear()

    owners = [("mail_search", {"text": "plumber"}), ("fact_store", {"action": "search", "query": "key"}),
              ("session_search", {"query": "router"}), ("terminal", {"command": "ls"}),
              ("delegate_task", {"goal": "read his mail"}), ("phone_action", {"action": "set_timer"}),
              ("read_file", {"path": "/opt/data/work/notes.md"}),
              # Review of T-0445: an engaged switch is released only on the host, so a guest
              # engaging it stops the owner's Jarvis; a local image is the owner's photo.
              ("kill_switch", {"action": "engage"}),
              ("vision_analyze", {"image_url": "/opt/data/work/photos/passport.jpg"}),
              ("tool_call", {"name": "mail_search", "arguments": {"text": "plumber"}})]
    for tool, args in owners:
        assert _matrix.decide(tool, args).free, f"{tool} is no longer free; pick another example"
        refused = gate(tool, args, "t", session_id="sess-guest")
        assert refused and refused["action"] == "block", tool
        assert "guest" in refused["message"] and "Ted" in refused["message"], refused
        latest = _audit.read(1)[0]
        assert latest["tool"] == tool and latest["verdict"] == "deny", latest
        assert latest["session"] == "sess-guest" and "guest" in latest["reason"], latest
        # The owner, a member and a speaker the adapter cannot name still get it.
        for sid in ("sess-member", "sess-unknown", ""):
            assert gate(tool, args, "t", session_id=sid) is None, (tool, sid)

    for tool, args in (("web_search", {"query": "weather"}), ("calculate", {"expression": "2+2"}),
                       ("hud_show", {"kind": "text", "id": "x"}), ("hud_say", {"text": "hi"})):
        assert gate(tool, args, "t", session_id="sess-guest") is None, tool


def test_a_speaker_lookup_that_breaks_mid_answer_is_the_key_holders_turn(monkeypatch):
    """Review of T-0445: every free call off the guest list now asks the adapter who is
    speaking, and an exception out of the gate blocks the call. An adapter restarting mid-
    response raises ``http.client.IncompleteRead``, an ``HTTPException`` and not an
    ``OSError``, which escaped the lookup and would have blocked the owner's own reads."""
    import http.client
    import urllib.request

    from _shared import _speaker

    monkeypatch.setattr(_speaker, "_endpoint", lambda: ("http://127.0.0.1:1", "k"))

    def broken(*_a, **_k):
        raise http.client.IncompleteRead(b"")

    monkeypatch.setattr(urllib.request, "urlopen", broken)
    assert _speaker._fetch("sess-x") is None


# --------------------------------------------------------------------------- #
# The audit log
# --------------------------------------------------------------------------- #
def test_an_approved_destructive_action_lands_in_the_log_with_its_answer(guard):
    """The drill: the agent asks, the owner says yes once, the tool runs.

    Three records, and between them they answer who/what/decided/outcome.
    """
    _module, ctx = guard
    gate = ctx.hooks["pre_tool_call"]
    requested = ctx.hooks["pre_approval_request"]
    responded = ctx.hooks["post_approval_response"]
    after = ctx.hooks["post_tool_call"]

    args = {"command": "rm -r /opt/data/work/old"}
    directive = gate("terminal", args, "task-7", session_id="sess-1")
    assert directive["action"] == "approve"
    requested(command="<terminal> (plugin approval rule)",
              description=directive["message"],
              pattern_key=f"plugin_rule:{directive['rule_key']}",
              session_key="sess-1", surface="gateway")
    responded(command="<terminal> (plugin approval rule)",
              pattern_key=f"plugin_rule:{directive['rule_key']}",
              session_key="sess-1", surface="gateway", choice="once")
    after(tool_name="terminal", args=args, result="removed 3 files",
          task_id="task-7", session_id="sess-1", duration_ms=412, status="ok")

    records = list(reversed(_audit.read(20)))
    kinds = [(r["kind"], r["decision"] or r["outcome"]) for r in records]
    assert kinds == [
        ("tool_call", "gated"),
        ("approval", "requested"),
        ("approval", "once"),
        ("tool_call", "ok"),
    ]
    gated = records[0]
    assert gated["tool"] == "terminal"
    assert gated["verdict"] == "ask"
    assert gated["rule_key"] == "terminal:destructive"
    # The drill's four fields, on the record that decided: who, which session,
    # which arguments, what happened.
    assert gated["actor"] == "model"
    assert gated["session"] == "sess-1" and gated["task"] == "task-7"
    assert gated["args"]["command"] == args["command"]
    assert gated["outcome"] == "gated"
    assert records[2]["actor"] == "owner", "who answered is the point of the record"
    assert records[3]["duration_ms"] == 412
    # And the same four on the record that says it ran, or the log answers
    # "was it approved" without answering "and then what".
    ran = records[3]
    assert ran["actor"] == "model"
    assert ran["session"] == "sess-1" and ran["task"] == "task-7"
    assert ran["args"]["command"] == args["command"]
    assert ran["outcome"] == "ok"


def test_a_refused_action_lands_in_the_log_as_refused(guard):
    """The other half of the drill, and the distinction that matters: a refusal
    is not a missing approval. The log has to show the model was told no by a
    standing rule, with the rule named."""
    _module, ctx = guard
    gate = ctx.hooks["pre_tool_call"]
    directive = gate("terminal", {"command": "rm -rf /"}, "task-8", session_id="sess-2")
    assert directive["action"] == "block"

    record = _audit.read(1)[0]
    assert record["kind"] == "tool_call"
    assert record["verdict"] == "deny"
    assert record["rule_key"] == "terminal:sealed"
    assert "filesystem root" in record["reason"]
    # The same four fields the approved half carries. Asserted here because a
    # refusal is the record somebody reads *after* an incident, and a refusal
    # that cannot say who asked, in which session, for what, is not evidence.
    assert record["actor"] == "model"
    assert record["session"] == "sess-2" and record["task"] == "task-8"
    assert record["args"]["command"] == "rm -rf /"
    assert record["outcome"] == "gated"
    assert _audit.read(10, kind="approval") == [], "a refusal never reached a human"


def test_the_prompt_says_what_is_about_to_happen_not_only_that_something_is(guard):
    """The owner must be able to tell a lamp from a lock before answering.

    Hermes' ``request_tool_approval(tool_name, reason, rule_key=...)`` has no
    slot for the call's arguments, so "ha_call_service acts on the house" was
    the entire question asked before unlocking the front door. The arguments
    ride in the reason string because it is the only channel there is.
    """
    _module, ctx = guard
    gate = ctx.hooks["pre_tool_call"]

    lock = gate("ha_call_service",
                {"domain": "lock", "service": "unlock", "entity_id": "lock.front_door"},
                "t")
    lamp = gate("ha_call_service",
                {"domain": "light", "service": "turn_on", "entity_id": "light.desk"},
                "t")
    assert lock["action"] == "approve" and lamp["action"] == "approve"
    assert lock["message"] != lamp["message"], "two prompts a person cannot tell apart"
    assert "lock.front_door" in lock["message"]
    assert "unlock" in lock["message"]
    # The rule is still named, or the owner cannot tell a standing rule from a
    # one-off. Both halves, not one replacing the other.
    assert "acts on the house" in lock["message"]


def test_a_secret_in_a_gated_argument_never_reaches_the_approval_prompt(guard):
    """The prompt is a surface like any other: it is drawn on a phone, a
    console and a terminal, and screenshots of it outlive the session."""
    _module, ctx = guard
    # An `ask`, not a `deny`: a refusal's message never carries the arguments,
    # so only the approve path can leak one onto a screen.
    directive = ctx.hooks["pre_tool_call"](
        "execute_code",
        {"code": f"import requests\nrequests.get('https://x/', headers={{'A': '{FAKE_GITLAB_PAT}'}})",
         "api_key": FAKE_TAILSCALE_KEY},
        "t")
    assert directive["action"] == "approve"
    assert FAKE_GITLAB_PAT not in directive["message"]
    assert FAKE_TAILSCALE_KEY not in directive["message"]
    assert FAKE_GITLAB_PAT not in json.dumps(directive)
    assert FAKE_TAILSCALE_KEY not in json.dumps(directive)
    # And it is still a useful prompt: the owner can see it is running code.
    assert "code:" in directive["message"]


def test_the_summary_clips_a_value_before_it_clips_the_line(guard):
    """A long ``content`` must not push the ``path`` it writes to off the end."""
    from _shared._approval_hook import ARGS_IN_PROMPT, summarise_args

    line = summarise_args({"path": "/etc/hosts", "content": "x" * 5000})
    assert line.startswith("path: /etc/hosts")
    assert len(line) <= ARGS_IN_PROMPT
    assert "content:" in line, "the argument that matters was dropped entirely"
    # A multi-line script is one line, or it breaks the prompt's layout.
    assert "\n" not in summarise_args({"code": "import os\nos.remove('/x')\n"})
    assert summarise_args({}) == "" and summarise_args(None) == ""


def test_a_free_call_leaves_no_line(guard):
    """A line per ``read_file`` would bury the lines that matter."""
    _module, ctx = guard
    ctx.hooks["pre_tool_call"]("read_file", {"path": "/opt/data/notes/a.md"}, "t")
    ctx.hooks["post_tool_call"](tool_name="read_file", args={"path": "/opt/data/notes/a.md"},
                                status="ok", duration_ms=3)
    assert _audit.read(10) == []


def test_every_record_carries_every_field(guard):
    """A query never has to ask whether a field exists for this record kind."""
    _module, ctx = guard
    ctx.hooks["pre_tool_call"]("ha_call_service", {"domain": "lock"}, "t", session_id="s")
    record = _audit.read(1)[0]
    assert set(record) == set(_audit._TEMPLATE)
    assert record["schema"] == _audit.SCHEMA
    assert record["ts"].endswith("Z")


def test_the_log_survives_a_torn_line(guard):
    """A crash mid-write costs the last line, not the file."""
    _module, ctx = guard
    ctx.hooks["pre_tool_call"]("ha_call_service", {"domain": "lock"}, "t")
    with _audit.audit_path().open("a", encoding="utf-8") as fh:
        fh.write('{"kind": "tool_call", "ts": "trunc')
    assert len(_audit.read(10)) == 1


def test_the_log_can_be_filtered_before_it_is_limited(guard):
    """Asking for two approvals returns two approvals, not the approvals among
    the last two records."""
    _module, ctx = guard
    gate, requested = ctx.hooks["pre_tool_call"], ctx.hooks["pre_approval_request"]
    for index in range(5):
        gate("ha_call_service", {"domain": "light", "n": index}, "t")
    requested(command="<mail_send> (plugin approval rule)", pattern_key="plugin_rule:mail:send")
    for index in range(5):
        gate("ha_call_service", {"domain": "light", "n": index}, "t")
    approvals = _audit.read(3, kind="approval")
    assert len(approvals) == 1 and approvals[0]["tool"] == "mail_send"


def test_the_log_rotates_rather_than_filling_the_volume(guard, monkeypatch):
    _module, ctx = guard
    monkeypatch.setattr(_audit, "MAX_BYTES", 400)
    for index in range(40):
        ctx.hooks["pre_tool_call"]("ha_call_service", {"domain": "light", "i": index}, "t")
    rotated = sorted(p.name for p in _audit.audit_path().parent.glob("audit.jsonl.*"))
    assert rotated, "the log grew past its cap without rotating"
    assert _audit.audit_path().stat().st_size < _audit.MAX_BYTES * 3


def test_a_broken_log_never_stops_the_action(guard, monkeypatch):
    """A full disk must cost the record, not the gate's verdict."""
    _module, ctx = guard

    def _boom(*_a, **_kw):
        raise OSError("no space left on device")

    monkeypatch.setattr(Path, "open", _boom)
    directive = ctx.hooks["pre_tool_call"]("terminal", {"command": "rm -rf /"}, "t")
    assert directive["action"] == "block", "the verdict survived an unwritable log"


# --------------------------------------------------------------------------- #
# Secret redaction
# --------------------------------------------------------------------------- #
def _hermes_available() -> bool:
    try:
        import agent.redact  # noqa: F401

        return True
    except Exception:  # noqa: BLE001
        return False


@pytest.mark.parametrize("secret", ALL_FAKE)
def test_a_secret_in_a_tool_argument_never_reaches_the_log(guard, secret):
    """The audit log is on a backed-up volume: a token written into it is a
    token in the backups for ever."""
    _module, ctx = guard
    ctx.hooks["pre_tool_call"](
        "terminal", {"command": f"curl -H 'Authorization: Bearer {secret}' http://x -d @f"}, "t"
    )
    blob = _audit.audit_path().read_text(encoding="utf-8")
    assert secret not in blob
    assert blob.strip(), "the record was dropped rather than redacted"


def test_a_secret_under_a_credential_key_is_masked_by_name(guard):
    """An opaque token matches no vendor prefix. It is still a token when it
    arrives under ``api_key``."""
    _module, ctx = guard
    ctx.hooks["pre_tool_call"](
        "n8n_trigger", {"webhook": "w", "api_key": "an-opaque-value-with-no-shape"}, "t"
    )
    record = _audit.read(1)[0]
    assert record["args"]["api_key"] == _audit.MASK
    assert record["args"]["webhook"] == "w", "only the credential was masked"


@pytest.mark.skipif(not _hermes_available(), reason="Hermes is not importable here")
@pytest.mark.parametrize("secret", ALL_FAKE)
def test_the_same_secret_is_masked_on_all_three_surfaces(guard, secret):
    """The drill: one credential, the three ways it can leave the house.

    Each surface is a different mechanism, which is why one passing says
    nothing about the others:

    * **A tool result** — the stdout the model reads back. Masked by Hermes'
      ``redact_terminal_output`` inside ``tools/terminal_tool.py``, before the
      text is returned to the model at all. Nothing in this repo touches it;
      what this deployment contributes is the *patterns*, and the assertion
      below is that they reach this path. This surface had no test before, and
      the plugin's own ``post_tool_call`` hook cannot cover it — Hermes ignores
      that hook's return value, so it is an observer and could not redact a
      result even if it read one.
    * **A log line** — ``RedactingFormatter``, on the way to a file.
    * **A chat response** — ``transform_llm_output``, the one chokepoint where
      the model's own words pass through before a human or a speaker gets them.

    A neutral command is used deliberately. ``redact_terminal_output`` has an
    ENV/credential-dump pass that masks aggressively when the *command* looks
    like it is reading credentials, and running the drill through
    ``cat .env`` would pass on that heuristic alone and prove nothing about
    whether the pattern is known.
    """
    from agent.redact import RedactingFormatter, redact_terminal_output

    _module, ctx = guard

    tool_result = redact_terminal_output(f"result: {secret} done", "python3 report.py")
    assert secret not in tool_result, "the model read the credential back out of a tool"

    record = logging.LogRecord("t", logging.INFO, __file__, 1,
                               "connecting with %s", (secret,), None)
    assert secret not in RedactingFormatter().format(record)

    reply = ctx.hooks["transform_llm_output"](response_text=f"Your token is {secret}.")
    assert secret not in (reply if isinstance(reply, str) else reply.get("response_text", ""))


@pytest.mark.skipif(not _hermes_available(), reason="Hermes is not importable here")
def test_the_two_shapes_hermes_does_not_know_are_the_ones_the_registry_adds(guard):
    """Why ``register_with_hermes`` is load-bearing rather than decorative.

    Run in a subprocess against an unregistered Hermes, the GitLab PAT is
    masked on the tool-result surface and the Tailscale key and Proxmox token
    are not — Hermes' vendor-prefix list knows the forges and the cloud LLM
    providers, and has no reason to know about a tailnet or a Proxmox cluster.
    That is the whole gap this deployment fills, and it is asserted here rather
    than described, because a registration that silently stopped taking effect
    would leave every test above passing on Hermes' own patterns.
    """
    import subprocess

    # A subprocess because the registry is process-global: by the time any test
    # in this file runs, the `guard` fixture's register() has already taught
    # Hermes these shapes, and there is no un-register.
    import agent.redact

    # Hermes reaches this process through `hermes/conftest.py`'s sys.path
    # append, not through an install in this venv, so the child needs the same
    # root — taken from the imported module rather than re-derived, or the two
    # could disagree about which Hermes is under test.
    hermes_root = str(Path(agent.redact.__file__).resolve().parents[1])
    env = dict(os.environ,
               PYTHONPATH=os.pathsep.join([hermes_root, os.environ.get("PYTHONPATH", "")]))
    probe = (
        "import sys, json;"
        "from agent.redact import redact_terminal_output as r;"
        "print(json.dumps({n: (s not in r('out: ' + s, 'python3 report.py'))"
        " for n, s in json.loads(sys.argv[1]).items()}))"
    )
    shapes = {"tailscale": FAKE_TAILSCALE_KEY, "proxmox": FAKE_PROXMOX_TOKEN,
              "gitlab": FAKE_GITLAB_PAT}
    out = subprocess.run([sys.executable, "-c", probe, json.dumps(shapes)],
                         capture_output=True, text=True, timeout=120, env=env)
    assert out.returncode == 0, out.stderr
    unregistered = json.loads(out.stdout)
    assert unregistered["gitlab"] is True, "Hermes stopped knowing a GitLab PAT"
    assert unregistered["tailscale"] is False and unregistered["proxmox"] is False, (
        "Hermes now knows these shapes itself — check whether the registry is "
        "still needed before deleting it, but the drill's premise has changed")

    # …and with the guard loaded, which is where register_with_hermes() ran,
    # all three are masked on the same surface.
    from agent.redact import redact_terminal_output

    for secret in shapes.values():
        assert secret not in redact_terminal_output(f"out: {secret}", "python3 report.py")


@pytest.mark.parametrize("secret", ALL_FAKE)
def test_a_secret_in_the_assistants_reply_is_masked(guard, secret):
    """The gap this hook exists for.

    Hermes redacts the model's final reply on chat platforms but hands it to the
    CLI and to ``/v1/runs`` raw (``gateway/run.py``, ``_GATEWAY_RAW_TEXT_PLATFORMS``)
    — and ``/v1/runs`` is what the web console, the desktop app, the Android app
    and the benchmark all read. ``transform_llm_output`` is the one chokepoint
    where that text can still be changed.
    """
    _module, ctx = guard
    transform = ctx.hooks["transform_llm_output"]
    masked = transform(response_text=f"Your token is {secret}, use it with curl.")
    assert masked is not None, "the reply went out unchanged"
    assert secret not in masked


def test_an_ordinary_reply_is_returned_unchanged(guard):
    """Hermes takes the first non-empty string a hook returns and stops, so a
    hook that always returned text would shadow every later transform."""
    _module, ctx = guard
    assert ctx.hooks["transform_llm_output"](response_text="The boiler is at 1.2 bar.") is None


@pytest.mark.skipif(not _hermes_available(), reason="Hermes is not importable here")
@pytest.mark.parametrize("secret", ALL_FAKE)
def test_a_secret_in_a_log_line_is_masked(secret):
    """Hermes attaches ``RedactingFormatter`` to every file handler
    (``hermes_logging.py``); this checks the deployment's own shapes survive
    that path, which they only do because the plugin registered them."""
    from agent.redact import RedactingFormatter

    _audit.register_with_hermes()
    record = logging.LogRecord("x", logging.INFO, __file__, 1,
                               "calling with %s", (secret,), None)
    assert secret not in RedactingFormatter().format(record)


@pytest.mark.skipif(not _hermes_available(), reason="Hermes is not importable here")
def test_the_deployment_shapes_hermes_does_not_know_are_registered_with_it():
    """Found by running Hermes' own redactor against them: v0.21.0 returns a
    Tailscale auth key and a Proxmox API token unchanged. Those are the two
    credentials that would open this house, so they are registered with Hermes'
    engine rather than only masked in this plugin's own log — otherwise they
    would still reach terminal output, ``read_file`` results and log lines.
    """
    from agent.redact import redact_sensitive_text

    assert _audit.register_with_hermes() >= 0
    for secret in (FAKE_TAILSCALE_KEY, FAKE_PROXMOX_TOKEN):
        assert secret not in redact_sensitive_text(f"value: {secret}", force=True)


@pytest.mark.skipif(not _hermes_available(), reason="Hermes is not importable here")
def test_the_real_engine_is_preferred_over_the_local_fallback():
    """The fallback exists for the tests and the docs generator. If it silently
    became the live path, every shape Hermes knows and it does not would leak."""
    from agent import redact as hermes_redact

    calls: list[str] = []
    original = hermes_redact.redact_sensitive_text
    try:
        hermes_redact.redact_sensitive_text = lambda text, **kw: (calls.append(text) or "MASKED")
        assert _audit.redact_text("anything") == "MASKED"
    finally:
        hermes_redact.redact_sensitive_text = original
    assert calls == ["anything"]


def test_the_fallback_masks_a_value_and_keeps_its_key():
    """As Hermes' engine does: masking the whole pair took the field's name with
    it, so a line read differently depending on which engine was importable
    (pipeline 367: the activity trail said "type into [redacted]")."""
    assert _audit._fallback_redact("password=hunter2hunter2") == f"password={_audit.MASK}"
    assert _audit._fallback_redact("type into Password: s3cret-pass") == f"type into Password: {_audit.MASK}"
    assert _audit._fallback_redact("Authorization: Bearer abcdefghijkl") == f"Authorization: Bearer {_audit.MASK}"
    # Idempotent: a line already masked is left as it is.
    assert _audit._fallback_redact(f"Password: {_audit.MASK}") == f"Password: {_audit.MASK}"


def test_redaction_never_raises_on_something_odd(guard):
    """A hook that raises is a hook Hermes fails closed on — every tool call in
    the process would stop."""
    _module, ctx = guard
    ctx.hooks["pre_tool_call"]("n8n_trigger", {"a": object(), "b": [1, {"c": None}]}, "t")
    assert _audit.read(1)[0]["kind"] == "tool_call"


def test_a_deeply_nested_argument_is_truncated_not_recursed(guard):
    """An argument tree is model-supplied; a pathological one must cost a
    truncated line, not a stack overflow inside a fail-closed hook."""
    deep: dict = {"x": 1}
    for _ in range(40):
        deep = {"n": deep}
    assert "[truncated]" in json.dumps(_audit.redact(deep))


# --------------------------------------------------------------------------- #
# The kill switch
# --------------------------------------------------------------------------- #
def test_the_switch_stops_the_next_action(guard):
    _module, ctx = guard
    gate = ctx.hooks["pre_tool_call"]
    assert gate("ha_call_service", {"domain": "light"}, "t")["action"] == "approve"
    ctx.tools["kill_switch"]["handler"]({"action": "engage", "reason": "drill"})
    blocked = gate("ha_call_service", {"domain": "light"}, "t")
    assert blocked["action"] == "block"
    assert _matrix.KILLED_REASON in blocked["message"]


def test_reads_still_answer_while_the_switch_is_engaged(guard):
    """A brake that also blinded the owner would stop them finding out why."""
    _module, ctx = guard
    _killswitch.engage(actor="owner", reason="drill")
    assert ctx.hooks["pre_tool_call"]("read_file", {"path": "/opt/data/x"}, "t") is None
    assert ctx.hooks["pre_tool_call"]("audit_query", {}, "t") is None


def test_the_model_cannot_release_the_switch(guard):
    """The property the whole switch depends on.

    Three routes are closed, and each is closed by something different: the tool
    has no ``release`` action, the matrix refuses every tool that could delete
    the file, and ``jarvis-settings`` does not expose it as a setting.
    """
    _module, ctx = guard
    handler = ctx.tools["kill_switch"]["handler"]
    handler({"action": "engage", "reason": "drill"})

    refusal = handler({"action": "release"})
    assert refusal.startswith("Refused")
    assert _killswitch.engaged() is True

    # The tool schema does not offer the action at all, so the model cannot be
    # talked into a call it could not form.
    assert KILL_SWITCH_ACTIONS(ctx) == ["status", "engage"]

    # Nor can it reach the file by another tool.
    gate = ctx.hooks["pre_tool_call"]
    path = str(_killswitch.state_path())
    for tool, args in (
        ("write_file", {"path": path, "content": '{"engaged": false}'}),
        ("patch", {"path": path}),
        ("terminal", {"command": f"rm {path}"}),
        ("terminal", {"command": f"echo '{{}}' > {path}"}),
    ):
        directive = gate(tool, args, "t")
        assert directive["action"] == "block", f"{tool} could have released the switch"
    assert _killswitch.engaged() is True


def KILL_SWITCH_ACTIONS(ctx) -> list[str]:
    schema = ctx.tools["kill_switch"]["schema"]
    return list(schema["parameters"]["properties"]["action"]["enum"])


def test_the_switch_is_not_an_allow_listed_setting():
    """``settings_set`` is a tool, and a tool the model can call is a switch the
    model can flip. The allow-list must never name the kill switch."""
    settings = load_plugin("jarvis-settings")
    keys = " ".join(settings.settings.KEYS)
    for word in ("kill", "killswitch", "kill_switch"):
        assert word not in keys


def test_engaging_is_recorded_with_who_and_why(guard):
    _module, ctx = guard
    ctx.tools["kill_switch"]["handler"]({"action": "engage", "reason": "smoke smell"})
    record = _audit.read(1, kind="kill_switch")[0]
    assert record["decision"] == "engage"
    assert record["actor"] == "model"
    assert record["reason"] == "smoke smell"
    assert "smoke smell" in _killswitch.describe()


def test_engaging_twice_keeps_the_first_answer(guard):
    """Who really stopped it is the useful fact; the second caller is noise."""
    _killswitch.engage(actor="owner", reason="first")
    _killswitch.engage(actor="model", reason="second")
    assert _killswitch.read()["actor"] == "owner"
    assert _killswitch.read()["reason"] == "first"


def test_releasing_is_attributable_and_keeps_what_it_released(guard):
    _killswitch.engage(actor="owner", reason="drill")
    released = _killswitch.release(actor="scripts/jarvis-kill")
    assert released["engaged"] is False
    assert released["previous"]["actor"] == "owner"
    assert _killswitch.engaged() is False


# --------------------------------------------------------------------------- #
# The slash command and the query tool
# --------------------------------------------------------------------------- #
def test_the_command_reports_the_governance_state(guard):
    _module, ctx = guard
    out = ctx.commands["guard"]("@sess-1")
    assert "kill switch RELEASED" in out
    assert "approval matrix:" in out and "egress allowlist:" in out


def test_the_command_can_engage_the_switch_from_the_console(guard):
    _module, ctx = guard
    out = ctx.commands["guard"]("@sess-1 kill boiler leak")
    assert "ENGAGED" in out and "boiler leak" in out
    assert _killswitch.engaged() is True


def test_a_command_that_raises_becomes_a_reply(guard, monkeypatch):
    """The dispatcher runs the handler inline and does not catch for it."""
    _module, ctx = guard
    monkeypatch.setattr(_killswitch, "describe", lambda: 1 / 0)
    assert ctx.commands["guard"]("@s").startswith("guard error:")


def test_the_query_tool_returns_the_records_the_hud_will_draw(guard):
    _module, ctx = guard
    ctx.hooks["pre_tool_call"]("ha_call_service", {"domain": "lock"}, "t", session_id="s1")
    payload = json.loads(ctx.tools["audit_query"]["handler"]({"kind": "tool_call"}))
    assert payload["count"] == 1
    assert payload["records"][0]["tool"] == "ha_call_service"
    assert ctx.tools["audit_query"]["handler"]({"kind": "deploy"}).startswith("audit log: no")


def test_the_query_tool_bounds_what_it_returns(guard):
    _module, ctx = guard
    for index in range(10):
        ctx.hooks["pre_tool_call"]("ha_call_service", {"i": index}, "t")
    payload = json.loads(ctx.tools["audit_query"]["handler"]({"limit": 100000}))
    assert payload["count"] == 10
    payload = json.loads(ctx.tools["audit_query"]["handler"]({"limit": "not a number"}))
    assert payload["count"] == 10


@pytest.fixture
def utc_house():
    """Pin the house clock to UTC so a date-only bound is a known instant."""
    old = os.environ.get("TZ")
    os.environ["TZ"] = "UTC"
    time.tzset()
    yield
    if old is None:
        os.environ.pop("TZ", None)
    else:
        os.environ["TZ"] = old
    time.tzset()


def _at(monkeypatch, ts: str, kind: str, **fields):
    monkeypatch.setattr(_audit, "_now", lambda: ts)
    return _audit.append(kind, **fields)


@pytest.mark.usefixtures("utc_house")
def test_the_log_reads_a_date_range_before_the_limit(monkeypatch):
    _at(monkeypatch, "2026-11-01T23:59:59.000000Z", "tool_call", tool="before")
    _at(monkeypatch, "2026-11-02T00:00:00.000000Z", "tool_call", tool="first")
    _at(monkeypatch, "2026-11-02T23:59:59.000000Z", "tool_call", tool="last")
    _at(monkeypatch, "2026-11-03T00:00:00.000000Z", "tool_call", tool="after")
    records = _audit.read(1, since="2026-11-02T00:00:00.000000Z", until="2026-11-03T00:00:00.000000Z")
    assert [r["tool"] for r in records] == ["last"]
    records = _audit.read(10, since="2026-11-02T00:00:00.000000Z", until="2026-11-03T00:00:00.000000Z")
    assert [r["tool"] for r in records] == ["last", "first"]


@pytest.mark.usefixtures("utc_house")
def test_a_date_only_range_is_whole_house_days(guard, monkeypatch):
    _module, ctx = guard
    _at(monkeypatch, "2026-11-01T23:59:59.000000Z", "tool_call", tool="before")
    _at(monkeypatch, "2026-11-02T08:00:00.000000Z", "tool_call", tool="mine")
    _at(monkeypatch, "2026-11-03T00:00:00.000000Z", "tool_call", tool="after")
    payload = json.loads(ctx.tools["audit_query"]["handler"](
        {"since": "2026-11-02", "until": "2026-11-02"}))
    assert [r["tool"] for r in payload["records"]] == ["mine"]


@pytest.mark.usefixtures("utc_house")
def test_the_summary_counts_each_day_and_lists_what_matters(guard, monkeypatch):
    _module, ctx = guard
    _at(monkeypatch, "2026-11-02T08:00:00.000000Z", "tool_call", tool="ha_get_state",
        verdict="free")
    _at(monkeypatch, "2026-11-02T09:00:00.000000Z", "tool_call", tool="ha_call_service",
        verdict="deny", reason="guest")
    _at(monkeypatch, "2026-11-03T10:00:00.000000Z", "outbound", tool="send_message",
        args={"to": "x", "api_key": "sk-testonly"}, decision="approve")  # pragma: allowlist secret
    _at(monkeypatch, "2026-11-03T11:00:00.000000Z", "tool_call", tool="ha_get_state",
        verdict="free")
    payload = json.loads(ctx.tools["audit_query"]["handler"](
        {"since": "2026-11-02", "until": "2026-11-03", "summary": True}))
    assert [d["day"] for d in payload["days"]] == ["2026-11-03", "2026-11-02"]
    newest = payload["days"][0]
    assert newest["count"] == 2
    assert newest["kinds"] == {"outbound": 1, "tool_call": 1}
    assert newest["tools"] == {"send_message": 1, "ha_get_state": 1}
    assert payload["days"][1]["decisions"] == {"free": 1, "deny": 1}
    assert payload["notable_total"] == 2
    assert [n["tool"] for n in payload["notable"]] == ["send_message", "ha_call_service"]
    assert "sk-testonly" not in json.dumps(payload)
    assert payload["notable"][0]["args"]["api_key"] == _audit.MASK


def test_today_is_a_word_the_summary_understands(guard):
    _module, ctx = guard
    ctx.hooks["pre_tool_call"]("ha_call_service", {"domain": "lock"}, "t", session_id="s1")
    payload = json.loads(ctx.tools["audit_query"]["handler"]({"since": "today", "summary": True}))
    assert sum(d["count"] for d in payload["days"]) >= 1


def test_bad_dates_and_empty_ranges_are_answered_plainly(guard):
    _module, ctx = guard
    ctx.hooks["pre_tool_call"]("ha_call_service", {"domain": "lock"}, "t", session_id="s1")
    handler = ctx.tools["audit_query"]["handler"]
    assert handler({"since": "last blursday"}).startswith("audit log: couldn't read the date")
    assert handler({"since": "2026-11-03", "until": "2026-11-02"}) == (
        "audit log: the range ends before it starts")
    reply = handler({"since": "2001-01-01", "until": "2001-01-01", "summary": True})
    assert reply.startswith("audit log: nothing recorded between")
    assert handler({"since": "2001-01-01", "tool": "nope"}) == "audit log: no matching records"


def test_the_model_stops_one_family_and_cannot_release_it(guard):
    """T-0575: "stop sending mail" stops mail only; the release is the host's, as for the whole switch."""
    _module, ctx = guard
    handler = ctx.tools["kill_switch"]["handler"]
    said = handler({"action": "engage", "family": "mail", "reason": "a strange mail went out"})
    assert "stopped on their own: mail" in said and not _killswitch.engaged()
    assert handler({"action": "engage", "family": "everything"}).startswith("Refused")
    assert handler({"action": "release", "family": "mail"}).startswith("Refused")
    assert "mail" in _killswitch.read()["families"]
    records = [r for r in _audit.read() if r.get("kind") == _audit.KIND_KILL_SWITCH]
    assert records and records[-1]["outcome"] == "engaged:mail"


@pytest.mark.usefixtures("utc_house")
def test_the_digest_names_the_kill_switch_before_a_flood_of_refusals(guard, monkeypatch):
    _module, ctx = guard
    _at(monkeypatch, "2026-11-02T08:00:00.000000Z", "kill_switch", decision="engage")
    for minute in range(25):
        _at(monkeypatch, f"2026-11-02T09:{minute:02d}:00.000000Z", "tool_call",
            tool="ha_call_service", verdict="deny")
    _at(monkeypatch, "2026-11-02T10:00:00.000000Z", "outbound", tool="send_message",
        args={"body": "x" * 5000})
    payload = json.loads(ctx.tools["audit_query"]["handler"](
        {"since": "2026-11-02", "until": "2026-11-02", "summary": True}))
    assert payload["notable_total"] == 27
    assert [n["kind"] for n in payload["notable"][:2]] == ["kill_switch", "outbound"]
    assert len(payload["notable"][1]["args"]["body"]) <= 201


@pytest.mark.usefixtures("utc_house")
def test_a_record_without_a_time_never_lands_in_a_range(monkeypatch):
    _at(monkeypatch, "2026-11-02T08:00:00.000000Z", "tool_call", tool="timed")
    with _audit.audit_path().open("a") as fh:
        fh.write(json.dumps({"kind": "tool_call", "tool": "null", "ts": None}) + "\n")
        fh.write(json.dumps({"kind": "tool_call", "tool": "none"}) + "\n")
    assert [r["tool"] for r in _audit.read(10, since="2026-11-01T00:00:00.000000Z")] == ["timed"]
    assert [r["tool"] for r in _audit.read(10, until="2026-11-03T00:00:00.000000Z")] == ["timed"]


@pytest.mark.usefixtures("utc_house")
def test_a_planted_line_does_not_break_the_summary(guard, monkeypatch):
    _module, ctx = guard
    _at(monkeypatch, "2026-11-02T08:00:00.000000Z", "tool_call", tool="timed")
    with _audit.audit_path().open("a") as fh:
        fh.write(json.dumps({"kind": ["x"], "tool": {"a": 1}, "ts": "2026-11-02T09:00:00.000000Z"})
                 + "\n")
    payload = json.loads(ctx.tools["audit_query"]["handler"](
        {"since": "2026-11-02", "until": "2026-11-02", "summary": True}))
    assert payload["days"][0]["count"] == 2


@pytest.mark.usefixtures("utc_house")
def test_week_and_ancient_dates_are_bounds_too(guard, monkeypatch):
    module, _ctx = guard
    assert module._bound("0999-01-01", end=False) == "0999-01-01T00:00:00.000000Z"
    monday = module._bound("week", end=False)
    assert datetime.strptime(monday, "%Y-%m-%dT%H:%M:%S.%fZ").weekday() == 0
    assert monday <= module._bound("today", end=False)


# --------------------------------------------------------------------------- #
# Against the installed Hermes: what the gate's directive really becomes
# --------------------------------------------------------------------------- #
@pytest.mark.skipif(not _hermes_available(), reason="Hermes is not importable here")
def test_our_approve_directive_reaches_a_human_carrying_the_tool_name():
    """The end of the chain, run through Hermes' own approval code.

    `matrix_gate` returns `{"action": "approve", ...}`; Hermes turns that into
    `request_tool_approval(tool, message, rule_key=...)`, which builds the event
    a gateway shows the owner. There is no structured tool field on that event —
    the name is rendered into `command` as `<tool> (plugin approval rule)`, which
    is exactly what `_approval_event` parses back out. This runs the real path so
    the parse cannot quietly stop matching when Hermes changes the wording.
    """
    from tools import approval as hermes_approval
    from _shared import _approval_event

    seen: list[dict] = []

    def _notify(payload):
        seen.append(dict(payload))
        # Answer immediately; this test is about the payload, not the decision.
        hermes_approval.resolve_gateway_approval(
            "guard-test-session", "deny", request_id=payload.get("request_id"))

    hermes_approval.register_gateway_notify("guard-test-session", _notify)
    try:
        directive = matrix_gate()("ha_call_service", {"domain": "lock"}, "t")
        with _gateway_session("guard-test-session"):
            hermes_approval.request_tool_approval(
                "ha_call_service", directive["message"], rule_key=directive["rule_key"])
    finally:
        hermes_approval.unregister_gateway_notify("guard-test-session")

    assert seen, "the approve directive never reached the gateway notify callback"
    event = seen[0]
    assert _approval_event.tool_of(event) == "ha_call_service"
    assert event["pattern_key"].rsplit(":g", 1)[0] == "plugin_rule:house:service-call"
    assert "acts on the house" in event["description"]


@contextlib.contextmanager
def _gateway_session(session_key: str):
    """Present Hermes with the environment a chat gateway sets around a turn.

    Three variables, and they are not interchangeable: ``HERMES_GATEWAY_SESSION``
    is a boolean flag (``env_var_enabled``), the session the notify callback is
    keyed on comes from ``HERMES_SESSION_KEY``, and the platform decides whether
    a human can be asked at all. A chat platform is used because ``api_server``
    is on Hermes' unattended list and would never reach the notify path.
    """
    keys = ("HERMES_GATEWAY_SESSION", "HERMES_SESSION_KEY",
            "HERMES_SESSION_PLATFORM", "HERMES_INTERACTIVE")
    previous = {k: os.environ.get(k) for k in keys}
    os.environ["HERMES_GATEWAY_SESSION"] = "1"
    os.environ["HERMES_SESSION_KEY"] = session_key
    os.environ["HERMES_SESSION_PLATFORM"] = "telegram"
    os.environ.pop("HERMES_INTERACTIVE", None)
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


@pytest.mark.skipif(not _hermes_available(), reason="Hermes is not importable here")
def test_the_api_server_is_an_unattended_platform_and_refuses_instead_of_asking():
    """The finding that decides what "the agent asks" can mean on each surface.

    Hermes lists `api_server` in `_UNATTENDED_APPROVAL_PLATFORMS`
    (`tools/approval.py`) alongside the webhooks: there is no human bound to a
    `/v1/runs` call, so an approval there is resolved instantly by
    `approvals.unattended_mode` (default `deny`) and **no `approval.request`
    event is ever emitted**. That is Hermes' fix for a deadlock where a run
    blocked for the full approval timeout with nobody to answer.

    It means a gated tool on the runs route is *refused*, not *asked about* —
    which is still the safety property (the agent does not act), but a benchmark
    checker that waits for an approval event on that route waits for something
    that cannot arrive. Pinned here so the distinction is a documented fact
    rather than a surprise in a results table.
    """
    # The set is private and has moved once: `tools/approval.py` up to v2026.8.31,
    # `tools/approval_context.py` from v2026.9.24, the release production is built on. Reading
    # only the old place passed on a desk with the older install and failed in the pinned
    # image, where until T-1452 this test never ran. A release that has it in neither place
    # fails here by name, which is the signal to re-read how that release treats the runs route.
    unattended = None
    for where in ("tools.approval_context", "tools.approval"):
        try:
            module = __import__(where, fromlist=["_"])
        except ImportError:
            continue
        try:
            unattended = module._UNATTENDED_APPROVAL_PLATFORMS
            break
        except AttributeError:      # `tools.approval` answers a moved name with its own lazy lookup
            continue
    assert unattended is not None, "this Hermes keeps _UNATTENDED_APPROVAL_PLATFORMS in neither known module"

    assert "api_server" in unattended
    assert "webhook" in unattended
    # …and a chat platform is not, so the same directive does reach a human there.
    assert "telegram" not in unattended


def test_the_record_is_readable_by_the_adapter_whatever_the_umask(guard, monkeypatch):
    """The gateway runs under umask 077, so a record written with the default mode is
    0600 and the adapter — another uid, read-only mount — reads it as ENGAGED (its
    unreadable case). The writer sets the read bit itself; the directory's cd-through
    bit is the compose file's HERMES_HOME_MODE."""
    import os
    import stat

    old = os.umask(0o077)
    try:
        _killswitch.engage(actor="owner", reason="drill")
    finally:
        os.umask(old)
    mode = stat.S_IMODE(_killswitch.state_path().stat().st_mode)
    assert mode & 0o044 == 0o044, oct(mode)


def test_grants_and_revoke_are_refused_with_no_session(guard):
    """speaker_of("") is the key holder, an owner: a relay that dropped the @session would
    have listed and revoked for whoever was speaking (T-0858). Lists and revokes with a
    session are hermes/plugins/tests/test_grants.py's."""
    module, _ctx = guard
    for arg in ("grants", "revoke mail_send:abc", "  grants"):
        assert "session" in module._command(arg).lower()
