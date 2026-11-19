# JARVIS documentation

The docs follow the Diátaxis split: a quick path in, task guides,
look-it-up reference, understanding, decisions, operations. Every relative link in
this tree is checked by `make docs-check` (`scripts/check-links.py`); the generated
reference pages are rebuilt by `make docs` and CI fails if a committed copy drifts.

| Section | What it holds |
| --- | --- |
| [Getting started](getting-started/README.md) | install, first run, pairing a phone and the console |
| [How-to guides](how-to/README.md) | one task each: Android, Android Auto, earpiece, cross-device, wake-word training, the house's models, testing |
| [Reference](reference/README.md) | **generated**: tools, plugins, MCP servers, slash commands, API endpoints, HUD protocol index, approval matrix, paired-device access, settings; authored: the HUD programme, Android Tasker parity |
| [Explanation](explanation/README.md) | architecture, standalone mode, security, threat model, prompt injection, voice identity, private chats, tooling decisions, what was removed |
| [Decisions (ADRs)](adr/README.md) | one MADR record per plan decision: D1–D14 from the migration plan, D15+ from the v2 plan |
| [Runbooks](runbooks/README.md) | one per compose service: start/stop, health, logs, the failures that actually happened, rollback |
| [Design](design/README.md) | the v2 package (`design/v2/`: the brief, its four boards, the UI inventory), the voice brief, the component inventory, and the earlier HUD plans until their code goes |
| [Car](car/protocol-notes.md) | the Android Auto stack folded in from Headway (ADR-0047): protocol notes, its ADRs (`car/adr/`), blockers and the drive checklist |
| [Privacy](privacy/egress.md) | what JARVIS sends out of the house: every outside host from the code and a live capture, what each is sent, its off switch and a verdict (T-0767) |
| [Research](research/local-intelligence.md) | background research: [what other assistants can do](research/assistant-capabilities.md) (the capability matrix, T-0799), local intelligence, [what the pipeline's Python jobs skip](research/ci-skips-2026-11-15.md) (T-1449), [what the house still remembers about a turn](research/house-turn-memory.md) (T-1489), and one measurement: [speech-to-text models](measurements/stt-models-2026-10-06.md) |

## Documents cited by name only

Comments, ADRs and the claims register cite "the migration plan", "the v2 plan", "the v3 plan", the
briefs, the build logs and the audits by name. Those are the project's planning documents from before
this repository was published, and they are not part of it. Each ADR restates the decision it records,
so nothing here needs them to be read.

## Files that keep their path

These are read by scripts, contracts or skills at a fixed location and are not moved:

- [`hermes-hud-protocol.md`](hermes-hud-protocol.md) — the HUD wire contract the web and Android suites pin against `tests/contracts/`.
- [`verification.md`](verification.md) — the claims register (Automated / Containerised / Scripted / Manual / Unproven); `/claims` reconciles it.
- [`ci.md`](ci.md) — the GitLab pipeline, its runners, schedules and switches.
- [`design/v2/PROMPT.md`](design/v2/PROMPT.md) — the current brief (v2), with its boards beside it. The
  earlier briefs (V2, V3, Glass HUD, Orb HUD, Scene, Media, Overhaul, the 2026-09-18 completion brief,
  their amendments and the 2026-09-24 plans) are finished and are not in this tree.
- [`../site/`](../site/README.md): the project's Pages site, built by `make site` and deployed by
  `.github/workflows/pages.yml`; the README's film links point into `site/media`.
- At the repository root: `README.md`, `CLAUDE.md`, `DEVIATIONS.md` (the judgement calls; the `/pr` and `/claims` skills read it there), the v2 brief's ledgers (`DECISIONS.md`, `GAPS.md`), and the generated [`CAPABILITIES.md`](../CAPABILITIES.md).

## Review material

Captured for a person's eyes, never read by a test: `make sweep` takes every v2 console screen
and state at 390, 768 and 1440 into `screens/v2/console/` (written on demand, not kept in the tree: T-1522).
