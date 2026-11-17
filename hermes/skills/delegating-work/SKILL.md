---
name: delegating-work
description: When to hand work to parallel subagents with delegate_task instead of doing it inline — the two-or-more-independent-parts rule, what a child needs in its brief, and when delegation is the wrong tool.
version: 0.1.0
---

# Delegating work

`delegate_task` spawns child agents with their own context. Children run in parallel; the parent keeps
the plan and writes the answer.

## Delegate when

- **Two or more independent parts.** "Summarise these three topics", "check these four services",
  "compare these products" — one child per part, sent in a single batch. This is the common case and the
  one most often missed: three sequential lookups in the parent take three times as long and fill the
  parent's context with material only one paragraph of the answer needs.
- **Long work the user should not wait on.** A report across many devices, a build, a large download.
  Acknowledge, delegate, report when it finishes.
- **A part that will produce a lot of noise.** Reading a big file, crawling a site, grepping a tree —
  the child absorbs the output and hands back the conclusion.

## Do not delegate

- One indivisible question, or anything you can answer in a single tool call. A child costs a whole
  agent's startup for nothing.
- Anything needing approval, a credential, or a decision that is the user's — those belong in your turn,
  where the gate and the person are.
- Steps that depend on each other. Children do not talk to each other; sequence those yourself.

## The brief a child needs

Children share the model server, so more than a few queue rather than finish sooner; the configured cap
is deliberate. Give each child:

1. **One outcome**, stated as what to return ("one line on the history of the abacus").
2. **The form of the answer** — a sentence, a number, a table, a file path. Say it explicitly; you are
   going to paste the result into a reply and cannot re-ask cheaply.
3. **Its share of the context** — the file, the URL, the device, the constraint. A child sees none of
   this conversation.
4. **What not to do** — no approvals, no writes outside its own working area, no follow-up questions.

Then assemble the children's answers yourself. Say what each one found; if one came back empty, say that
rather than filling the gap from your own guess.

## Worker types and the crew's budget

Start a child's goal with its type when its work is narrow: `[researcher] …` may only read (search,
fetch, read files and sessions); `[planner] …` reads and keeps the task list and the board. Anything that
acts goes to an untagged child. A child a researcher starts is a researcher too, whatever it is called.

One goal's helpers share one budget: eight helpers, 120 tool calls between them, twenty minutes. When a
helper is refused for the budget, use what it reported and finish the goal yourself; do not try to work
around the refusal. The person can stop every helper with `/jgoal crew stop`.
