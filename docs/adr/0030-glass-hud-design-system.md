# ADR-0030: The Glass HUD is the design system

- **Status:** Accepted (Phase 2b, between the Phase 2 gates and Phase 3)
- **Reference:** the v2 plan §10 (D30); the brief is the Glass HUD brief (2026-09-03)

## Context

The owner's Glass HUD brief redesigns every surface — web, desktop, Android, console, notifications, chat templates — as layered glass in a dark room lit by the arc reactor, with every UI element a panel in a four-mode window manager (Tiled / Floating / Focus / Ambient) that JARVIS itself can arrange. It arrived after the Phase 2 plan was written, while [ADR-0023](0023-design-system-token-source.md) already fixed the token source of truth and the Programme 2 panel contract (`hud.panel.show/update/dismiss`, `tests/contracts/hud-panels.json`) already carried panel *content*. A redesign that touched every component before the shared primitives existed would have had to be done twice.

## Decision

The Glass HUD is the design system, and it lands as Phase 2b: after the Phase 2 gates, before Phase 3.

- The panel contract stays the *content* channel; the layout operations (`list_panels`, `open`, `close`, `focus`, `move`, `resize`, `arrange`, `highlight`, `pin`, `set_mode`, `undo`) are a second contract, `tests/contracts/hud-layout.json`, on the same event channel, exposed to the agent through a `hud_layout` plugin. "Lock layout" and the user's own arrangement always win.
- The §3.1 web and Kotlin primitives are built content-only and tokens-only (no hard-coded colour), so the glass migration re-skins one place.
- Layouts per surface × activity, the mode, the lock, and the reduced-transparency / reduced-motion overrides are fields of the settings schema ([ADR-0017](0017-one-settings-schema.md)), so they appear in the generated settings UI.
- Two JCB tasks (`hud-layout-001` rearrange-for-a-goal, `hud-layout-002` undo-and-lock) join the corpus after the baseline; the regression gate compares only the tasks both runs share ([ADR-0015](0015-jcb-baselined-first.md)).
- The reactor is one WebGL implementation shared by web and desktop, an AGSL shader with a Canvas fallback on Android, ANSI on the console — original geometry, no third-party assets.
- Design plan before code: the Glass HUD plan and the component inventory are reviewed against the brief's generic-default list before the first component lands, and the a11y and frame-time gates land before the migration.

## Consequences

Phase 3 starts on the glass surfaces, not before them. The token lint and the four screen states from ADR-0023 remain the gates; glass adds material, depth and motion tables to the token source rather than replacing it. Risk carried: the Android 120 Hz frame-time verification needs the owner's Pixel or an emulator on the runner, which the CI host lacks — it is reported as BLOCKED when reached, not skipped.
