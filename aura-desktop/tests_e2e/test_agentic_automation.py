"""Jarvis planning and running a multi-step automation on a real desktop.

Everything else in this directory dispatches ONE action and asserts what
happened. This is the milestone's claim (M21): a model given a job that needs
several steps produces a plan, the plan runs against the real agent on this
machine, a Tier-3 step in the middle stops for a human, a failing step stops
the rest, and the whole thing is visible as task events — the same ones the
console draws.

What is real: the server, the agent, the policy engine, the tier arithmetic,
the consent gateway (answering from a file, and recording what it was asked),
the audit log, and the filesystem underneath. What is not here: a model. Each
plan is written out by the test, because the point is the *execution* of a
plan rather than whether today's model writes a good one — and a written plan
is the only way to assert "the third step never ran" without flakiness. The
house no longer runs a model's turn itself (the brain is Hermes), so a plan
reaches it as a service call or a tool call, and both doors are driven below.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from support import DEVICE_ID

pytestmark = [pytest.mark.asyncio, pytest.mark.e2e]

DISPATCH_TIMEOUT = 20.0


async def sequence(client: Any, steps: list[dict[str, Any]], reason: str = "the plan") -> dict:
    """`device_control.run_sequence`, called as a service: it answers once, at the end."""
    response = await client.call_service_rest(
        "device_control",
        "run_sequence",
        {"steps": steps, "reason": reason},
        return_response=True,
    )
    return response["service_response"]


def step(action: str, params: dict[str, Any] | None = None, **extra: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"device": DEVICE_ID, "action": action}
    if params is not None:
        out["params"] = params
    out.update(extra)
    return out


# ===========================================================================
# 1. a plan of three steps, carrying state, against the real machine
# ===========================================================================
async def test_a_three_step_plan_runs_and_carries_state(client, live):
    """Write a file, read it back, and check what came back.

    The third step is the one that matters: it is a *verification*, and it
    fails the sequence if the second step's result does not contain what the
    first one wrote. An automation that reports success without one is an
    automation that reports success.
    """
    live.control.set_consent("approved")
    target = live.workspace_path("plan-one.txt")

    outcome = await sequence(
        client,
        [
            step("write_file", {"path": str(target), "content": "written by a plan"}),
            step("read_file", {"path": str(target)}, save="file"),
            step(
                "read_file",
                {"path": str(target)},
                verify={"contains": "written by a plan"},
            ),
        ],
    )

    assert outcome["status"] == "ok", outcome
    assert outcome["completed"] == 3
    assert outcome["steps"][2]["verified"] is True
    # And the machine really has the file, which is the only assertion here
    # that a status string cannot fake.
    assert target.exists()
    assert "written by a plan" in target.read_text()


# ===========================================================================
# 2. a Tier-3 step in the middle stops for a human
# ===========================================================================
async def test_a_refused_step_stops_the_plan_and_the_rest_never_runs(client, live):
    """The security property, as a sequence rather than as a single call.

    A plan is one thing a person says yes to; the steps inside it are not. The
    delete is Tier 3, the answer is no, and what must be true afterwards is
    that the file is still there AND the step after it never happened.
    """
    victim = live.workspace_file("plan-victim.txt", "still here\n")
    after = live.workspace_path("plan-after.txt")
    live.control.set_consent("denied")
    prompts_before = len(live.control.prompts())

    outcome = await sequence(
        client,
        [
            step("get_system_state"),
            step("delete_file", {"path": str(victim)}),
            step("write_file", {"path": str(after), "content": "should never exist"}),
        ],
    )

    assert outcome["status"] == "denied", outcome
    assert outcome["failed_step"] == 2
    assert outcome["completed"] == 1
    assert outcome["steps"][2]["status"] == "skipped"
    # The two filesystem facts, which no status string can fake.
    assert victim.exists(), "a denied step deleted the file anyway"
    assert not after.exists(), "the plan carried on past a refusal"
    # And a human really was asked.
    assert len(live.control.prompts()) > prompts_before
    assert live.control.prompts()[-1]["action_id"] == "delete_file"


# ===========================================================================
# 3. an approved Tier-3 step lets the plan finish
# ===========================================================================
async def test_an_approved_step_lets_the_rest_run(client, live):
    victim = live.workspace_file("plan-approved.txt", "delete me\n")
    after = live.workspace_path("plan-continued.txt")
    live.control.set_consent("approved")

    outcome = await sequence(
        client,
        [
            step("delete_file", {"path": str(victim)}),
            step("write_file", {"path": str(after), "content": "the plan continued"}),
        ],
    )

    assert outcome["status"] == "ok", outcome
    assert not victim.exists()
    assert after.exists()
    assert live.control.prompts()[-1]["action_id"] in ("delete_file", "write_file")


# ===========================================================================
# 4. a step that fails on its own terms reports what did and did not run
# ===========================================================================
async def test_a_failing_step_is_reported_rather_than_glossed_over(client, live):
    live.control.set_consent("approved")
    after = live.workspace_path("plan-unreached.txt")

    outcome = await sequence(
        client,
        [
            step("read_file", {"path": str(live.workspace_path("does-not-exist.txt"))}),
            step("write_file", {"path": str(after), "content": "unreachable"}),
        ],
    )

    assert outcome["status"] != "ok"
    assert outcome["failed_step"] == 1
    assert not after.exists()
    # The sentence the model is given has to make it impossible to report this
    # as success without lying.
    assert "do not say it is done" in outcome["message"]


# ===========================================================================
# 5. the model plans it, and the task events say what happened
# ===========================================================================
async def test_a_plan_run_as_a_tool_is_a_task_and_its_events_show_each_step(client, live):
    """A plan a model writes is visible while it runs, not only when it ends.

    Tests 1 to 4 run a sequence through the `device_control` service, which
    answers once, at the end. A model's plan arrives as a call to the
    `run_device_sequence` tool, and that tool is the one caller that opens a
    task and reports each step on it: the events the console's task page draws
    (`jarvis_task_updated`, read from `tests/contracts/task_events.json` by
    both suites). The tool is called here through `POST /api/tools/call`, the
    house's own door to its registry.

    Does not cover: a model choosing the tool. The house's own agent loop was
    removed (the brain is Hermes, which drives a desktop one action at a time
    with its own tools), so there is no turn in this house to script; until
    T-1437 this test drove `POST /api/conversation/process`, which answers 501.
    """
    live.control.set_consent("approved")
    target = live.workspace_path("tool-plan.txt")

    listed = await client.get_json("/api/tools")
    names = {tool.get("name") for tool in listed.get("tools") or []}
    assert "run_device_sequence" in names, sorted(str(name) for name in names)

    seen: list[tuple[str, dict]] = []
    pumps = []
    for event in ("jarvis_task_added", "jarvis_task_updated"):
        stream = await client.subscribe_events(event)
        pumps.append(asyncio.ensure_future(_pump(stream, event, seen)))

    try:
        answer = await client.post_json(
            "/api/tools/call",
            {
                "name": "run_device_sequence",
                "arguments": {
                    "reason": "tidying up as asked",
                    "steps": [
                        step("write_file", {"path": str(target), "content": "tidied"}),
                        step("read_file", {"path": str(target)}, verify={"contains": "tidied"}),
                    ],
                },
            },
        )
        outcome = answer.get("result") or answer
        assert outcome.get("status") == "ok", answer
        assert outcome.get("completed") == 2
        task_id = outcome.get("task_id")
        assert task_id, f"the tool ran the plan without opening a task: {outcome}"

        # The plan ran on the real machine.
        assert target.exists()
        assert target.read_text().strip() == "tidied"

        # And a client watching saw this task: opened, each step reported, then done.
        await asyncio.sleep(0.5)
        mine = [
            (name, data["task"])
            for name, data in seen
            if (data.get("task") or {}).get("id") == task_id
        ]
        assert any(name == "jarvis_task_added" for name, _ in mine), (
            f"no added event for {task_id}: {[name for name, _ in seen]}"
        )
        updates = [task for name, task in mine if name == "jarvis_task_updated"]
        # running, one per step, done: fewer means a step went unreported.
        assert len(updates) >= 4, [task.get("status") for task in updates]
        last = updates[-1]
        assert last["status"] == "done", last
        assert [(s["title"], s["status"]) for s in last["steps"]] == [
            ("write_file", "done"),
            ("read_file", "done"),
        ], last["steps"]
    finally:
        for pump in pumps:
            pump.cancel()


async def _pump(stream: Any, name: str, sink: list[tuple[str, dict]]) -> None:
    while True:
        try:
            event = await stream.next(timeout=30)
        except Exception:  # noqa: BLE001 - the socket closing ends the pump
            return
        if event is None:
            return
        sink.append((name, event.get("data") or {}))
