# `$lib/hud` — the console's logic

Nothing here draws. Since T-0611 every surface is the v2 shell on `$lib/ds`
(`src/lib/ds/README.md` is its map); this folder is what the shell reads and
calls: the route table, the sockets as runes, the feeds, the voice loop and the
pure mappings from live sources to what a screen shows.

```
src/routes/[...path]/+page.svelte  the one route; +page.server.ts 404s a path with no screen
src/lib/hud/activities.ts          every path the console ever had (and its aliases) → an activity; ds/shell/routes.ts maps that to the core or a screen
src/lib/hud/feeds.svelte.ts        the feeds the screens read, each loading / ready / empty / error / offline, offline on a drop
src/lib/hud/voice.svelte.ts        push-to-talk, the open mic, its mute and its wake gate (lib/audio/wake.ts), the transcriber, the speech queue, barge-in
src/lib/hud/links.svelte.ts        the Hermes and house sockets as runes
src/lib/hud/pairing.svelte.ts      the console lock, the secret hand-over, the pairing code
src/lib/hud/car.svelte.ts          what the phone hands the car's display through window.JarvisCar
src/lib/hud/palette.ts             what ⌘K ranks
src/lib/hud/data/                  one pure mapping per source: the Hermes snapshot, the house, the console's endpoints, GitLab's releases
```

A navigation inside the console changes the path without remounting the shell:
the sockets, the microphone and a turn in flight survive it.

## What JARVIS pulls up

A `hud.panel.show` (the content contract, `docs/hermes-hud-protocol.md`) is
named by a panel type (`$lib/glass/panelTypes.ts`); the shell draws it as a
projection out of the core, not as a panel. An approval or a clarify request is
the core's card on the core and a docked card on any screen.

## Verifying

```bash
cd aura-web && npx vitest run src/lib/hud            # the mappings against fixtures and both mocks, the feeds, the voice loop
cd aura-web && npm run check && npx eslint . && npx knip --no-gitignore
python3 scripts/verify/web_states_check.py
cd aura-web && npm run build && E2E_PORT=8320 npx playwright test e2e/v2-shell.spec.ts e2e/v2-screens.spec.ts
```

`make sweep` re-takes every screen and state at 390, 768 and 1440 into
`docs/screens/v2/console/` (`e2e/v2-sweep.spec.ts`, skipped unless `V2_SCREENS=1`; the folder is
written on demand, not kept in the tree: T-1522).
