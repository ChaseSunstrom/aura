# docs/design — the design system's own documents

The current design is **v2**: [`v2/`](v2/README.md) holds the brief (`PROMPT.md`), its boards,
the core's reference engine and the UI inventory. Everything the old HUD (Glass, Reactor II, the
v3 plan) described is history; its decisions are recorded in the ADRs.

| File | What it is |
|---|---|
| [`v2/`](v2/README.md) | **The current design**: the v2 brief, its boards, the core's reference engine, the UI inventory. The Build Brief that came before it is finished and not in this tree. |
| [`HUD.md`](HUD.md) | The v3 design plan the console followed before the v2 shell: palette with hex, the two typefaces, the three emission grades, the motion rules, the reactor's eleven states. Still cited by the ADRs and the tokens; where it disagrees with `v2/PROMPT.md`, v2 wins. |
| [`VOICE_BRIEF.md`](VOICE_BRIEF.md) | How JARVIS speaks — the written brief that drives instruction-controlled synthesis. |
| [`GLASS_INVENTORY.md`](GLASS_INVENTORY.md) | The v2-era (Glass) component inventory, kept for the dispositions the ADRs cite. |
| `screens/` | The terminal console's frames (`console-*.txt`, written by `scripts/console-frames.sh`) and the overhaul's input matrix. The web console's pictures are `make sweep` (`docs/screens/v2/console/`). |

Removed with the old HUD (T-0665): the Glass plan, its motion reference and the Reactor II mockup of
[ADR-0031](../adr/0031-reactor-ii-returns.md), with their renders and the scripts that took them.

Screenshot any running screen for review, which is how a screen is checked before it is called
done:

```bash
node aura-web/scripts/shot.mjs http://127.0.0.1:8199/ /tmp/room.png 1440 900
```
