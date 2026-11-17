---
name: build-capability
description: "Scaffold a plugin, build a capability JARVIS lacks, or propose any change to JARVIS's own repo (a README line, a doc, a skill) as a merge request: selfext_plan, selfext_scaffold, selfext_verify, selfext_open_mr. Use when a request needs a tool that does not exist or asks for a branch / merge request."
version: 0.1.0
author: JARVIS rebuild
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [selfext, plugins, gitlab, capability, merge-request]
    related_skills: [hermes-plugin-authoring, planning-a-request]
---

# Build a capability JARVIS does not have

Use this when a request cannot be answered because **no tool exists for it** — not
when a tool exists and is misconfigured, and not when the answer is a shell
command. The end of this loop is a merge request a person reads, so the bar is the
bar for a change you would open by hand.

## A change that is not a new capability

"Add a line to the README and open a merge request", "fix this skill's wording", "document X":
edit the file in the self-extension checkout (`selfext_status` says where it is), then
`selfext_open_mr` with `name` as a short branch label (`readme-note`) and `paths` naming the
files. It carries top-level markdown, `docs/`, `hermes/skills/`, `hermes/home/`, `bench/tasks/`
and ordinary plugins -- never CI, deploy, scripts, build files or the plugins that govern you,
because the merge request's pipeline would run them on the production host before anyone
reviewed them. Do not `git push` or call the GitLab API yourself: the sandbox has no network
and no token, and the typed tool is the approved path.

## The loop

1. **Say what is missing.** One sentence: the request you could not answer, and the
   tool that would have answered it. If a `n8n` workflow would do the job in five
   minutes, use `n8n_create_workflow` instead — a workflow is a smaller change than
   a plugin and does not need a deploy.
2. **`selfext_plan`** — name, tools, whether anything changes the world, where the
   files land, which branch will carry them. This writes nothing. Show the plan and
   let the owner correct it before any file exists.
3. **`selfext_scaffold`** — writes `hermes/plugins/<name>/` and, with `bench_prompt`,
   a benchmark task under `bench/tasks/coding-selfext/`. Every generated handler
   returns its arguments and carries a `TODO`.
4. **Fill in the handlers.** This is the actual work; the scaffold is not the
   capability. Read a sibling plugin first — `hermes/plugins/n8n-trigger/` is the
   house shape — and follow `hermes-plugin-authoring` for the `ctx` API. Rules that
   are not negotiable:
   - configuration comes from `aura_schema.from_env()`, never `os.environ`;
   - a handler returns a JSON **string** and never raises;
   - anything that changes the world registers a `pre_tool_call` gate whose message
     names what will happen, concretely;
   - no credential appears in a result, an error or a log line.
5. **`selfext_verify`** — ruff and pytest over the new plugin, in the checkout.
   Repeat 4 and 5 until both are clean. An unverified merge request wastes the
   reviewer's time and will be sent back.
6. **`selfext_open_mr`** — needs approval. It creates `jarvis/capability-<name>`,
   commits **only** the capability's own files, pushes, and asks GitLab to open the
   merge request. Put the verify output in the description: what was missing, what
   this adds, what it does not cover, and the test result.
7. **Stop there.** Nothing merges. CI runs on the merge request; the owner reads it
   and merges or does not. Report the merge request URL and say plainly that the
   capability is not live yet.
8. **After the owner merges and the pipeline deploys**, the plugin is in the image.
   Run its benchmark task to prove the deployed capability answers:
   `python bench/run.py --task selfext-<name>-001`.

## Distilling a chain you ran by hand

After any task that took three or more tool calls and worked, and that you would not
want to repeat by hand, call **`skill_propose`**: the task in a sentence, the tool
names in the order that worked, the dead ends, what should vary, the plugin name it
would become, the request that would prove it as a benchmark task, and `against`: the
strongest case for not building it (an existing skill or tool that covers it, how
rarely it recurs, what it costs to keep). A proposal without a real case against is
refused. That writes a row to the review queue and shows a notice on the HUD with
For, Against and the skills and tools whose names overlap it. It builds nothing, and it is
free — the weekly "Skill curator" job does the same over the last seven days of
sessions.

The person answers with **`skill_proposals`** (or `/skill_proposals`): `list`,
`approve <id>`, `drop <id>`. Approving runs steps 2 and 3 above for the proposal on
its branch — the plugin, its test, a `SKILL.md` under `hermes/skills/<name>/` and the
benchmark task, uncommitted — and then the loop is the loop: fill in the handler,
`selfext_verify`, `selfext_open_mr`. Never approve your own proposal; the queue exists
so a person does.

## What will refuse you, and why

- **`selfext_open_mr` refuses while the checkout carries edits outside your
  capability's files.** The checkout is shared. Sweeping someone else's
  half-finished work into your merge request is the worst thing this loop could do,
  so it declines instead of guessing. Report the paths it named and stop.
- **The branch must carry the configured prefix and the target branch is never
  pushed to.** There is no force push and no merge tool. If you think you need one,
  you are not doing self-extension any more — ask the owner.
- **A plugin name must be lowercase words joined by single hyphens.** That is the
  alphabet Hermes' loader can turn into a module.

## Checking your work

`selfext_status` says which branch the checkout is on, what its head commit is, and
what is uncommitted. Run it before you start, so you know whether the tree was
already dirty when you arrived — the refusal in step 6 is much easier to read when
you know it was not you.
