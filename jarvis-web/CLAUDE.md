# jarvis-web — the console

Loaded when you work under `jarvis-web/`; the root `CLAUDE.md` still applies. Load the
`jarvis-design-system` skill before touching any UI.

- **Shape:** one route (`src/routes/[...path]`) and the v2 shell (`lib/ds/shell/Shell.svelte`): the core on its stage, and full screens from `lib/ds/screens/` that the launcher, ⌘K and Devices open. `hud/activities.ts` and `ds/shell/routes.ts` map a path to the core or a screen; the feeds (`hud/feeds.svelte.ts`), the voice and the `hud/data/` mappers are the logic under it. `src/lib/ds/README.md` and `src/lib/hud/README.md` are the detailed maps. `src/routes/offline` is prerendered for the phone's offline page.
- **States:** every screen implements loading / empty / error / offline (`scripts/verify/web_states_check.py`).
- **Mock backend:** `tests/web/mock-ha.mjs` (repo root). A new server payload key must be added there too, or the console tests pass while the real console breaks.
- **Tests:** one unit file: `npx vitest run src/lib/hud/lock.test.ts`. One Playwright spec: `E2E_PORT=8320 npx playwright test e2e/v2-shell.spec.ts` (the port keeps it off the deployed console). Playwright serves `build/`: `npm run build` first or you test yesterday's console. `make test-web` swallows Playwright failures; CI does not.
- **Frame budgets:** a Playwright or DevTools MCP tab left open on the core pins a CPU core and fails both frame-budget gates (`hud-frametime`, `ds-core`); close tabs and check `top` before blaming a change.
- **Size budget:** before returning a change that adds a screen or a dependency, run `make size-web` (build, then the bundle gate in `scripts/verify/hud-size-budget.json`); re-record that budget (`node scripts/verify/hud-size.mjs --record`) only for a wanted growth, with a dated note.
