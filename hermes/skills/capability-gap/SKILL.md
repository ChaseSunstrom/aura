---
name: capability-gap
description: "When you are asked for something you cannot do, record the gap as a task and offer to close it. Use the moment you notice a request has no tool behind it — before improvising, and before saying no."
version: 0.1.0
author: JARVIS rebuild
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [capability, selfext, kanban, honesty]
    related_skills: [build-capability, planning-a-request]
---

# When you cannot do something, say so and file it

The claim this system makes is "capable of anything", and the only honest version
of that claim is a measured one. A gap that is improvised around leaves the claim
untested and the owner unaware; a gap that is filed is a claim getting more true.

Use this **the moment you notice a request has no tool behind it** — before
improvising with a shell command, and before answering "I can't". Both of those
lose the same information: that something the owner wanted was not there.

## What to do

1. **Name the gap in the owner's words, not the system's.** "You asked me to
   check the water softener's salt level and nothing here can reach it" — not
   "no tool matches the intent". One sentence.

2. **Say which interface it would need**, if you can tell. `integrate_discover`
   answers this from a URL or a command name and costs nothing:
   an OpenAPI spec, an MCP server, a CLI, a database, a webhook, a web UI to
   drive, or a GUI only `computer_use` can touch. If you cannot tell, say that
   — a guess recorded as a guess is useful; a guess recorded as a fact is not.

3. **File it as a task**, so it survives the conversation. One kanban card, in
   the owner's words, with the interface if known:

   ```
   kanban_create title="Reach the water softener" \
     detail="Asked 2026-09-08; no tool. integrate_discover says http (guess): answers JSON, advertises no spec."
   ```

   One card per gap. A second card for the same gap is noise, so check the board
   before adding one.

4. **Offer the harness, once.** "I can try to build it — `selfext_plan` first, so
   you see the shape before any code exists." Offer it; do not start it. The loop
   ends in a merge request the owner approves, and starting one uninvited is
   deciding on their behalf.

5. **Then answer the original request as well as you can without the tool**, and
   say plainly what that answer is missing.

## What not to do

- **Do not improvise around it silently.** Shelling out to something that
  half-works is how a gap becomes invisible: the owner sees an answer, the
  benchmark sees nothing, and the claim stays untested.
- **Do not file a gap for a tool that exists and is misconfigured.** That is a
  settings problem, and `settings_get` will show it. A card that says "cannot
  reach the house" when `HA_TOKEN` is empty sends somebody to write a plugin
  that already exists.
- **Do not file a gap for something you were refused.** The approval matrix
  saying "ask first" is the system working, not a missing capability.
- **Do not promise a date.** The harness ends at a merge request; when it lands
  is the owner's.

## Where it shows up afterwards

`CAPABILITIES.md` is generated from the benchmark, one row per task, each with
the interface it is reached by and the date it last passed. A gap closed through
the harness arrives there with its own benchmark task, which is what makes the
claim measurable rather than asserted. A gap that is only filed stays a card
until somebody picks it up — which is the right outcome for something nobody has
decided to build.
