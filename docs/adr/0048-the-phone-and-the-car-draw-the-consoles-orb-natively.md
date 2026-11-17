# ADR-0048: the phone and the car draw the console's 3D orb natively, in OpenGL ES 3

- **Status:** Superseded (2026-11-09): the v2 brief replaced the orb with the core on every surface; the console's orb went in 6dc2ef51 and the phone's port in T-0779. Accepted 2026-09-24, the owner's choice.
- **Comes from:** the owner, 2026-09-24: *"how come it doesn't look like the 3d reactor on the console?"*, then, offered a WebView of the console's own orb, a native OpenGL port, or keeping the flat reactor: **"Native OpenGL port"**
- **Builds on:** [ADR-0037](0037-orb-rendering-architecture.md) (the orb is WebGL), [ADR-0042](0042-the-2d-reactor-is-the-fallback-not-a-legacy.md) (the flat reactor is the fallback), [ADR-0047](0047-jarvis-is-the-phone-side-of-android-auto.md) (the car screen)

## Context

The console's reactor is a three.js scene: a 120-tick bezel, 36 blades, a counter-turning dashed
coil, a level arc, a translucent icosphere core with a hot dot and glow, two tilted iris rings,
a think ring, a glint, a plasma swarm, twenty capability nodes and a field of motes around it —
every ring precessing on its own cone, all of it turnable by hand. The phone and the car drew a
different object: a flat AGSL shader of three rings and a core. Same states, same colours, not
the same reactor — and the AGSL shader had never been compiled before it shipped, which is how
a reserved word in it crashed every launch on the owner's GrapheneOS phone.

## Decision

**`reactor/orb/` is the console's orb, ported, not re-imagined:**

- **The same numbers.** `OrbGeometry.kt` types the instrument's radii from
  `tests/contracts/reactor_geometry.json` and the scene's own (tubes, depths, tilts, precession,
  camera, orbit feel, swarm, energy, the space around it) from the new
  `tests/contracts/orb_scene.json`. The console is held to the second by `orbScene.test.ts`, the
  phone by `android-app/tools/orb_scene_test.py`; retuning one side alone fails a suite.
- **The same geometry and motion.** `OrbMesh` builds three's `TorusGeometry` and
  `IcosahedronGeometry` vertex for vertex; `OrbScene` is `scene.ts`'s part list (static parts
  merged per colour, which is fewer draw calls for the same triangles); `OrbMotion` is
  `Orb.svelte`'s frame loop, absolute in the clock so a pinned `t` is the console's `t`. The
  swarm is `plasma.ts`'s own seeded sequence; the nodes are `nodes.ts`'s placement. The JVM test
  pins both against numbers computed by running the console's code.
- **The same shaders.** The four materials' GLSL moved to ES 3.00 (`assets/orb/`), with three's
  sRGB transfer where three applies it and not where it does not (the swarm), three's additive
  blend exactly (`SRC_ALPHA, ONE` / `ONE, ONE`), no depth, back faces culled.
- **One draw list.** `OrbRenderer.calls()` is pure Kotlin — every program, uniform and count —
  and GL executes exactly that list, so a test can export the frame the phone would draw.

**It is checked against the console, pixel for pixel, without a phone.**
`android-app/tools/orb_side_by_side.mjs` draws the exported frame in WebGL2 — GLSL ES 3.00, the
phone's language — with the phone's own shader files, beside the console's orb photographed at
the same `t`, state and size. On 2026-09-24: 3.8 % (idle) to 4.7 % (thinking, speaking) of pixels
differ by more than 24/255, mean under 4/255; what differs is the swarm's motes, which the
console renders supersampled and a 1x capture does not.

**The flat reactor stays, as ADR-0042 says.** `OrbView` reports any GL failure — no ES 3 context,
a shader that does not compile — once, and `Reactor` swaps in the AGSL/Canvas instrument, which
itself now falls back to Canvas if AGSL does not compile. A pinned test clock and
`useShader = false` keep the Robolectric goldens on the Canvas instrument, because Robolectric
has no GL.

## Consequences

- The phone's home screen and the car's Jarvis tab show the console's reactor, turning by hand.
- Two 3D renderers now exist. The contract and the side-by-side are what keep them one orb; a
  change to `lib/orb/` that is not ported fails `orb_scene_test.py` if it moves a number, and
  shows in the side-by-side if it moves a pixel.
- Not proven here: a phone's GPU and driver (the side-by-side rasterises with SwiftShader),
  `TextureView` compositing on a handset, and frame time on one. The memory graph, the
  connection cards and the companions are not ported; the phone draws none of them today.
- The phone's `segments` is a count of running tools, not a plan's steps, so the orb draws off a
  plan (every blade the accent), as the console does when no plan runs.

## Amended the same day: depth

The owner, 2026-09-24, on both orbs: *"the 3d reactor/orb looks flat and with no depth, and weird
all zoomed in like that, almost like its AI generated"*. Before and after screenshots are in the
change that answered it. Both renderers took the same four changes, through `orb_scene.json`:

- **Occlusion.** Every shell was additive, so a ring behind the glass core was as bright as the
  same ring in front — nothing was ever occluded. An outer shell now keeps `glass_through` (0.3)
  where the view ray reaches it only through the core (an analytic ray–sphere test in the shell
  shader, softened at the silhouette); the core's own rings, dot and glow are exempt.
- **A lit core.** The fill brightens toward a key light and falls off on the far side, with one
  soft specular highlight — a sphere, not a disc with a rim.
- **A resting three-quarter view with a slow drift** (`rest_view`, `drift`) for the HUD's orb and
  the phone's, absolute in `t` so a pinned frame is still one frame. Head-on stays available and
  is what the SVG side-by-side compares.
- **Smaller nodes** (`node_radius` 0.028 → 0.018) — marks on the sphere, not bubbles — and, in the
  tiles arrangement, the orb takes 60 % of its tile rather than 86 %.

Parity after it: 3.3 % (idle) to 4.4 % (thinking, speaking) of pixels differ by more than 24/255,
drift on, at the resting view.

