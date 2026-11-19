# screens — the terminal console's frames

The old HUD's pictures and recordings that lived here (before and after per route, `web.webm`,
the boot and task-view stills) were deleted in T-0665 with the scripts that took them. The web
console's pictures are now `make sweep`, into `docs/screens/v2/console/` (written on demand, not kept in the tree: T-1522).

What stays is the text a terminal has instead of pixels:

| File | What | Produced by |
|---|---|---|
| `console-<mode>.txt` | the terminal surface in each layout mode, as text | `scripts/console-frames.sh`; `aura-desktop/tests/test_console_app.py` fails when a frame is not what the code draws |
| `overhaul/input-matrix.md` | the overhaul's input matrix | a record: its writer (`e2e/input-matrix.spec.ts`) is gone |
