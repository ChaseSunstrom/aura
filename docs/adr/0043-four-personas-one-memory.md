# ADR-0043: any of the four can be primary, and they share everything except who is speaking

- **Status:** Accepted (2026-09-09)
- **Comes from:** the v3 brief PART 2, which asks for this decision in as many words: *"Record that as an ADR, and argue the alternative rather than assuming it"*
- **Builds on:** [ADR-0038](0038-one-reactor-component.md) (one reactor, three configurations), [ADR-0042](0042-the-2d-reactor-is-the-fallback-not-a-legacy.md) (one instrument, two renderers)

## Context

*"JARVIS is not permanently the assistant with the other three orbiting it. The
primary identity is switchable between JARVIS, Ultron, Ada and Friday, and back,
at any time."*

That sentence has a consequence the brief then makes explicit and asks to have
written down: if any of the four can be the one you are talking to, what does
becoming primary actually change?

## Decision

**A persona owns four things and nothing else:** its SOUL file (name, voice,
manner, greeting style), its voice profile, its hue, and the accent tokens the
HUD derives from that hue.

**Everything else is shared** — memory, tools, skills, the task store,
integrations, history. Switching changes *who is speaking*, not *what they know*.

Two structural consequences follow, and both are invariants rather than
behaviours:

- **The primary is never in the companion pool.** Four identities, one primary,
  three companions. Switching moves one identity out of the pool and the previous
  primary back into it, so two of the same identity can never be on screen.
- **A switch is a fact about the system, not a client preference.** It is
  remembered across restarts and is the same switch on web, desktop, Android and
  the console — *"a console session that still says JARVIS after switching to Ada
  is the same defect as a stale accent colour in the HUD"*.

## The alternative, argued

The brief asks for this rather than an assertion, so: **per-persona memory.** Each
identity gets its own facts, its own retrieval store, its own history.

It is coherent, and it is what you would build if the four were meant to be four
*assistants*. It costs:

- **Four retrieval stores** rather than one, and the graph the HUD draws becomes
  four graphs — or one graph with an ownership column on every edge.
- **A rule for what a shared tool writes.** `memory_write` called during an
  Ultron turn writes... to Ultron? To whoever asked? To all four? Every answer is
  defensible and every answer is a rule somebody has to remember. Today there is
  no question to answer.
- **Four sets of the same fact, diverging.** "The user's timezone is
  America/Winnipeg" learned by Ada is not known by Friday. The first time that
  matters it looks like amnesia, and the fix is a sync rule — which is shared
  memory, arrived at by a longer road.
- **A migration.** 157 entries today, none of them owned by anybody.

What would make the case *for* it, and does not hold today:

- If a persona were a **different trust level** — a "guest" identity that must
  not see private facts — separation would be a security boundary and not a
  preference. Nothing in the brief describes one. If that changes, this ADR
  should be revisited rather than worked around, because a boundary bolted onto
  shared storage is the failure mode this project has already written down twice.
- If two personas were meant to **hold different opinions** of the same fact, for
  a rehearsal or an adversarial review. That is a real use, and it is served by a
  scratch context rather than by four permanent stores.

So: shared, until something breaks that only separation fixes. The brief's own
instruction is the standard — *"I do not want it unless the ADR makes the case
that shared memory actually breaks something"* — and this ADR does not make that
case, because I could not find one.

## What this rules out

- A `personas/<name>/memories/` layout, or an owner column on facts.
- A per-client "which persona am I talking to" setting. The switch is server-side
  state, and a surface that disagrees with it is stale, not configured.
- Four SOUL files that duplicate anything but name, voice, manner and greeting.
  Shared instructions live once, where they already live.

## The cost, stated plainly

Switching persona mid-conversation means the new voice inherits a history it did
not speak. Ada answering a question JARVIS was halfway through is either
delightful or jarring, and the brief does not say which. Nothing here prevents
it; if it reads badly in use, the fix is a rule about *when* a switch takes
effect — at a turn boundary, say — and not a change to what is shared.
