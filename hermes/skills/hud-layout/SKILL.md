---
name: hud-layout
description: How JARVIS arranges the HUD — which hud_* layout tool answers which moment, the choreography presets, and the three rules (lock, the person's arrangement wins, every change is undoable).
version: 0.1.0
---

# Arranging the HUD

The HUD is a window manager of panels (chat, timeline, goals, approvals, model, image, browser,
voice, telemetry …). You arrange it with the `hud_layout` tools; the person arranges it by hand.
Detail lives on screen, so a spoken reply stays one sentence.

## The choreography (use `hud_arrange` with the preset)
- A goal starts → `hud_arrange preset:goal-start` (goals and the tool timeline come forward beside the chat).
- An image is generated or a camera still arrives → `hud_arrange preset:image-open` (the viewer opens beside the chat).
- An approval is needed → `hud_arrange preset:approval-focus` (the approvals panel takes Focus), then `hud_highlight id:approvals`.
- A model cold start begins → `hud_arrange preset:model-warming` (the model panel appears with its warming state).
- Voice is active → `hud_arrange preset:voice-ambient` (Ambient: the reactor expands, one strip).
- A browser task runs → `hud_arrange preset:browser-task` (the browser view tiles next to the timeline).
- Back to normal work → `hud_arrange preset:working`.

For one panel: `hud_open` (say the type and what to sit beside), `hud_close`, `hud_focus`, `hud_move`,
`hud_resize`, `hud_pin`, `hud_set_mode`. `hud_list_panels` first, so you use a real panel id.
"Pair a phone", "show the pairing QR" → `hud_open type:pairing`: the console issues a fresh
single-use code and draws its QR itself, so there is nothing to look up or read out first.
`hud_highlight` pulses a panel's rim to draw the eye; it is not a scroll and never a modal.

## The three rules
1. **Lock layout.** If a tool answers `refused: lock_layout`, the person locked the layout: say so in one
   clause and do not retry. `hud_highlight` still works while locked.
2. **Their arrangement wins.** If `hud_arrange` answers `kept: "user"`, the person's own saved layout for
   that activity was applied instead of yours — that is the right outcome, not an error. If it answers
   `kept: "declined"`, they undid your last arrangement for that activity: leave it alone. Only pass
   `force` when they ask you to arrange it anyway.
3. **Everything is undoable.** "Undo that" / "put it back" → `hud_undo`. It remembers the decline.

## Learning a preference
When you find a better arrangement for an activity (a tree you built with `hud_open`/`hud_move`),
store it once with `hud_arrange layout:<the document> activity:<name> remember:true`; from then on
`hud_arrange` for that activity uses it. Never store a layout the person made — the HUD saves those itself.
