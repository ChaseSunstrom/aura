# JARVIS v2 — design and build package

Everything from the design session, in one place.

## What to give Sampo

Start with `PROMPT.md`. It's the full v2 build prompt. Then give it the four `*.dc.html` boards beside it.

## What's inside

| Path | What it is |
|---|---|
| `PROMPT.md` | The v2 prompt for Sampo: design language, the core, contextual UI, devices, integrations, approvals, car, calls and texts, the fix list, performance, the subagent plan, autonomy rules and the definition of done. |
| `Core2.dc.html` | **v2 Core.** The 3D reactor core in its `orbEngine` method (this is the reference implementation), cards that come out of and go back into the core, and the Working scene. |
| `Car.dc.html` | **v2 Car.** The live hologram drive with clipping and street labels, the fixed layout zones, and the contextual cards. |
| `Phone.dc.html` | **v2 Phone.** The presence strip on the lock screen, the assistant screen with a handoff to the desktop, and an approval. |
| `Control.dc.html` | **v2 Control.** JARVIS's cursor working on a desktop app, the devices list with the handoff animation, and the redesigned approval. |
| `Integrations.dc.html` | **v2 Integrations.** Connect anything, and permission levels for each capability. |
| `UI_INVENTORY.md` | Every UI part on every surface on 2026-11-08 and what v2 does with it; the brief's "old UI removed" check reads it. |
| `reference/orb_engine.js` | The core's rendering code on its own, for porting to WebGL or Three.js. |

## Note on the design files

The `.dc.html` files were made for the Claude design canvas and depend on its runtime (`support.js`, `<x-dc>`, `{{holes}}`), so they won't render on their own. Read them as the visual and motion spec: colors, sizes, layout, CSS animations, and the code in their scripts. You can view them live on the canvas in claude.ai.

Numbers and names in the designs are examples, except the GPU, model and telemetry values, which came from your own screenshots.
