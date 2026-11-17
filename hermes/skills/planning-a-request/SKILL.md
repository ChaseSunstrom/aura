---
name: planning-a-request
description: "Plan this, break it down, todo list, goal: when a todo list is enough, when it is a goal with a completion contract, when a plan for a missing capability is selfext_plan instead, and the rule about verifying before saying it is done."
version: 0.1.0
---

# Planning a request

Three sizes of work, three shapes.

## One step

Answer it. No list, no goal. A todo list on a single-step request is noise.

## Several steps in this turn — `todo`

Anything the user will work through, or you will: "break this down", "plan X", "what do I need to do
for Y", "help me organise Z", and any request of your own that takes more than two or three tool calls.
Write the list with the `todo` tool rather than as prose in the reply: the list survives the turn, the
user can see what is left, and you cannot quietly drop an item.

One exception: "plan the capability that would answer this", a plan for a tool JARVIS does not have, is
`selfext_plan`, not `todo`. It drafts the plugin (name, tools, gates, where the files land) without writing
a file, and the build-capability skill carries on from there.

One item per line, each an action with an outcome. Mark items done as you finish them — a list where
everything is still open at the end of a turn in which you did the work is worse than no list.

## Several gated steps — one card with `plan_approve`

When a request needs two or more actions that each ask the owner (lights in several rooms, a
setting plus a schedule), call `plan_approve` first with every step as `{tool, args}` --
the exact arguments you will then pass (`{"domain": "light", "service": "turn_on", "entity_id":
"light.kitchen"}`). The owner sees one card with the whole plan; once approved, each listed call
runs without another card, once, for ten minutes, and only with those arguments verbatim. A call
whose arguments differ in any way still asks, and anything refused outright stays refused. One
gated step: just call it.

## Work that outlives the turn — a goal

"Keep at it until…", "get X done", anything that needs several turns, a build, or someone else's reply.
Use the `goal_set` tool for anything non-trivial (it drafts a contract), and give it a
**completion contract**: the commands or observations that will prove it is finished, passed as
its `gates`. (`/goal` is the person's slash command; you cannot type it — the tool is your way.) Subgoals for parts that can be
checked separately. Report progress at each turn boundary. Stop and ask when a step needs a credential,
an approval, or a design decision with real trade-offs.

Parts that are independent of each other go to parallel subagents — see the delegating-work skill.

## Verify before claiming done

Never report a step as done on the strength of having attempted it.

- Wrote a file → read it back, or list it.
- Ran a command → say what it printed, not that it "should" have worked.
- Changed something in the house or the homelab → read the state back with a tool.
- A tool call is waiting for approval → that is the honest end of your turn. Say what is waiting.

If you could not verify, say which part is unverified and why. A confident wrong "done" costs more than
an honest "I wrote it but could not read it back".
