# ADR-0037: the orb is WebGL, the panels are DOM

- **Status:** Accepted (2026-09-08)
- **Comes from:** the orb HUD brief ("RENDERING ARCHITECTURE — the decision that
  determines whether this works"), the v3 plan §15
- **Relation to ADR-0036:** that one replaced the tiler with the map. The orb replaces the map.

## Context

The HUD becomes a literal three-dimensional orb: an icosphere with concentric shell geometry, a node
per capability on its surface, tethers, and atmosphere — all of which a person can grab and rotate.
Panels hang off it. The question that decides whether the thing is usable is where panel *content*
is rendered.

**(a) Everything in WebGL.** Panel text becomes a texture or SDF glyphs in the 3D scene. It
composites perfectly with the orb, sorts by depth for free, and needs no projection pass.

**(b) Everything in DOM, with CSS 3D transforms.** The orb is a canvas; panels are DOM elements
transformed in 3D by the same matrices.

**(c) Hybrid.** WebGL renders the orb, the node field, the tethers and the atmosphere. Panel content
is DOM, absolutely positioned over the canvas at coordinates derived by projecting each panel's 3D
anchor to screen space every frame.

## Decision

**Take (c).**

The reasons are not aesthetic. Text rendered into WebGL gives up, concretely:

- **Crisp text at any DPR.** A texture is authored at one scale and resampled at every other. It is
  legible at 1:1 axis-aligned and blurry the moment it is not — which, on a rotating orb, is
  immediately and permanently.
- **Selection.** A person cannot select, copy or search text that is pixels in a framebuffer.
- **Input.** A text field, a search box and a `<select>` are the browser's; reimplementing them over
  a canvas reimplements an IME, an autofill contract and a mobile keyboard.
- **Native scrolling**, with its momentum, its scrollbars and its overscroll behaviour.
- **The accessibility tree.** This is the decisive one. A screen reader reads the DOM. A panel drawn
  into a canvas is not in the accessibility tree at all — no role, no label, no focus order, not
  reachable by keyboard. The console's own gates (`a11y-axe`, `a11y-keyboard`) could not see it
  either, so the loss would be silent as well as total.

(b) keeps the accessibility tree but loses the compositing: a DOM element transformed in 3D cannot be
interleaved with WebGL geometry by depth, so a panel is always wholly in front of or wholly behind
the orb, never correctly occluded by it. It also puts text through a 3D transform, which is where the
blur it was meant to avoid comes back.

So: **the 3D quality lives in the orb, the nodes, the tethers, the lighting and the motion. The
panels are legible surfaces floating in that space**, which is what they need to be.

### Consequences to handle explicitly

1. **One shared projection pass per frame** computes every anchor's screen position — not
   `project()` called per panel from separate places, which is N matrix multiplications scattered
   across N components that drift out of step by a frame.
2. **DOM updates are transform-only.** `translate3d` and `opacity`, never `top`/`left`/`width`, so
   panels stay on the compositor and a rotation does not relayout the document.
3. **z-index is derived from projected depth**, so a panel anchored to a far-side node sits behind
   the orb's silhouette — via a clip or mask, since a DOM element cannot be partially occluded by
   WebGL geometry.
4. **Pointer events** pass through the canvas to panels and back correctly: the canvas takes drags on
   empty space, panels take their own events, and a scroll inside a panel does not dolly the camera.

## three.js, from npm

The brief names `InstancedMesh` and quaternion slerp — three.js's API. Hand-writing an icosphere,
per-instance attributes, depth-sorted transparency, a quaternion camera and context-loss recovery is
a large amount of code to own for no gain over an MIT library that is bundled and self-hosted. It is
a dependency, not a third-party *asset*: no CDN, no font service, nothing fetched from outside this
host at build time or run time.

**The cost is stated rather than discovered later.** The client bundle was re-recorded at 201.1 kB
gzipped on 2026-09-08. three's core adds roughly 170 kB gzipped, which is not a rounding error — it
is close to doubling. The commit that adds it re-records `hud-size` with the measured number and the
reason, per that budget's own rule that a number only ever moves with one. If the figure turns out
to be intolerable, the lever is a custom build of the modules actually used, not a different
architecture.

## What this does not decide

The orb's *appearance*, which is the v3 plan §13's inventory of the current reactor and is a
separate question from where pixels are produced.

Android: the same split applies — AGSL and the Compose graphics layer for the orb and nodes, real
composables for panel content, sharing the projection math with the web — but the Compose side is its
own work and its own evidence, and nothing here has been proved on a device.
