# ADR-0031: Reactor II returns; the glass material is retired

- **Status:** Accepted (2026-09-06, on the owner's review of the deployed Glass HUD)
- **Supersedes:** the material, depth-blur and empty-state rules of [ADR-0030](0030-glass-hud-design-system.md); its layout contract, panel contract, token source and gates stand
- **Reference:** the C2 reactor reference (the mockup chosen then); memory of the review in the September 2026 build log (2026-09-06 evening)

## Context

The Glass HUD shipped on 2026-09-06 (pipeline 145 onward). The owner looked at it on the
console and said: "i'm not a fan of the glass look/layout, i want it to look kind of like how it
was before, no emojis, but with the arc reactor, nice layout, etc. make the panels look less
fake". Shown the deployed surface, the Aug-27 console and the Reactor II mockup side by side,
they chose **Reactor II as drawn**, and on the go said "similar to the c2 reactor but nice kind
of like the glass but better".

What they were reacting to, in the deployed surface: three grades of blurred, specular glass
over a grey gradient; large empty panels with a `[ ]` glyph; the reactor reduced to a 40 px
status mark in a control bar (and red, because the adapter could not read the kill-switch file,
6e0bea73); emoji in the Model panel from Hermes' toolset labels; no frame, no tabs, no readouts.
The design-system skill's §3 had said "flat hairline surfaces, no blur, no glass" since M01; the
Glass wave overrode it.

## Decision

1. **The material is one flat hairline surface.** `--jv-panel` solid, `1px solid --jv-line-hair`,
   `--jv-radius-md`; no backdrop filter, no specular, no gradient sheen; the blur budget is 0.
   The three grades keep their names in the token source so nothing generated breaks, but
   they render alike. Reduced-transparency is therefore the default, not a fallback.
2. **What Glass got right stays:** the light layer (a panel near the reactor catches its light as
   a faint rim, at a low mix), the depth order (the focused panel lifted), the boot ignition and
   the entry stagger, the four-mode layout manager and both HUD contracts, every panel body.
3. **The frame is Reactor II's:** a 56 px top bar with the wordmark, the layouts as uppercase
   tabs with a sliding accent underline, and link · model · stt · tts · kill as mono readouts;
   a bottom dock with the push-to-talk, "say or type", the VOICE/CHAT mode and the keys.
   Lock and undo live in the panel menu, not in a row of buttons.
4. **The reactor is the centrepiece.** The Working view composes as the mockup's chat view:
   the 320 px reactor at the top of the centre column with the state caption under it, the
   current exchange beneath in display type, the transcript panel left, the this-turn panel
   right. Panels the agent pulls up land in the side columns; the stage is never covered.
5. **No emoji anywhere.** Labels that arrive with one (Hermes' toolset labels, skills, notices)
   are stripped at the web data layer by Unicode property (`plainLabel`), never by a list.
6. **Type follows the mockup:** panel headers are 2xs uppercase tracked labels; data is mono;
   digits are tabular; body text is sentence case. GLASS_HUD §3.1's "no tracked caps" is
   withdrawn for headers and tabs only.

Order of work: web first (`jarvis-web`), then desktop and Android through `design/tokens.json`
and `tests/contracts/hud-layout.json`, each wave through the pipeline with the a11y,
frame-time, token and state gates unchanged. Gallery and boot snapshots are re-recorded in the
wave that moves them and the commit says so.

## Consequences

The blur-budget machinery (`budget.ts`, `--jv-material-blur-budget`) becomes inert and may be
deleted when the phone's grade is retired with it. The frame-time gate should only get easier.
The Android and desktop surfaces show the Glass material until wave 2 lands; the shared tokens
mean their change is a regeneration plus the frame. The console's 11 reactor states and the
layout op_cases are untouched by this decision; if a wave finds it must change an op_case,
that is a separate decision.

## Amendment, 2026-10-07 — what floats may be glass

The owner, of the desktop popup and the phone's overlay: *"it just looks like a box, no blur or
smoothing or anything, the animation is rough ... I want beautiful smooth transitions in and
out, when talking ... and around it"*. The two surfaces that float over someone else's screen
take one translucent, blurred grade, `material.floating` (fill, edge, radius, blur, halo), with
`motion.dur.float-in`, `float-out` and `glide`. Everything inside the console stays flat: a
panel, a tab, a dialog in the room is still a hairline surface with no blur.

- **Desktop popup** (`shell.rs`, `Hud.svelte` `.hud.floating`): the window is transparent (not
  on macOS, where Tauri gates it behind its private API), the page draws a rounded card with a
  backdrop blur, a halo in the reactor state's colour that follows the voice level, a light
  running round the rim while Jarvis listens or works, and a slow breath while it speaks or
  waits on you. The app cues the page's arrival and exit (`data-float`) and glides the window
  up into place. A Linux webview never sees the desktop behind it, so there the blur has nothing
  to blur; the fill is dark enough (0.86) to read without it.
- **Phone overlay** (`AssistOverlay.kt`): the card rises in, leaves before its window is
  removed, morphs to and from the badge, and its rows ease in and out (`LayoutTransition`). Its
  blur was already the platform's `FLAG_BLUR_BEHIND`.

Reduced motion (the browser's setting, the phone's animator scale) holds all of it still.
