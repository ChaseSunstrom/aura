# Screens

Pictures of the running product, written by the suites and never edited by hand. They are a
record for a reviewer to read against the design boards; no test compares against them.

| Folder | What it holds | Written by |
|---|---|---|
| `aura/`, `jarvis/` | the two shipped themes, each worn whole: the core, a card, an approval, settings and the car | `jarvis-web/e2e/aura-screens.spec.ts` |
| `v2/console/` | every console screen in every state at 390, 768 and 1440 wide | `make sweep` (`jarvis-web/e2e/v2-sweep.spec.ts`) |
| `v2/core/`, `v2/projections/`, `v2/lines/`, `v2/markdown/`, `v2/conversation/` | the core's states, each projection type, leader lines, a markdown reply and a conversation | the spec of the same name under `jarvis-web/e2e/`, with its `*_SCREENS=1` switch |
| `v2/car/` | the car's screens | the Android golden tests (`CarNavGoldenTest` and its neighbours) |
| `v2/activity/` | the activity tile while a tool runs | `scripts/verify/activity-live.mjs` |
| `site/` | the Pages site's first screen at 1440 and 390 wide. It shows the project's public install line, which names the public repository | `SITE_SHOTS=<folder> npx playwright test e2e/site.spec.ts`, then copied by hand when the page's look changes |
| `v2/phone/`, `v2/desktop/` | the phone's screens and the desktop agent's overlay | taken once by hand, from an emulator and a virtual display; nothing rewrites them |

Everything here is drawn from the suites' made-up house: no real name, place or address appears.
