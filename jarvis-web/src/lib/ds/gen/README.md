# `$lib/ds/gen` — generative UI

The model projects a typed visual with the `hud_project` tool (jarvis-hud); the plugin
validates it and sends one `hud.projection` frame on the HUD socket; the console draws it
with `Projected`. What a type is, its fields and its limits are written once, in
`tests/contracts/projections.json`. This folder and the plugin each carry a byte-identical
copy (`projections.json`), and both suites fail when either drifts (`contract.test.ts`,
`hermes/plugins/jarvis-hud/tests/test_project.py`). Both validators give the same first
error for the same payload.

## The interface the projection layer mounts

```svelte
<script lang="ts">
	import { Projected, projectionFromEvent, type ProjectionFrame } from '$lib/ds';
	// for each hud.projection payload: const p = projectionFromEvent(payload); null → drop it
</script>

<Projected type={p.type} data={p.data} placement={{ core, edge: 'left', leaving }} />
```

| Prop | Type | Meaning |
|---|---|---|
| `type` | `string` | The frame's `type`. An unknown one draws the error state. |
| `data` | `unknown` | The frame's `data`. `undefined` draws the loading state. |
| `placement` | `{core?: {x, y}, edge?: 'top'\|'right'\|'bottom'\|'left', leaving?: boolean, from?: number}` | Passed to the `Projection` it sits on: where the core is (viewport px), the edge line, the exit and the stage's `from`. |
| `testid` | `string` | A `data-testid` on the projection. |

The frame is `{id, type, data, origin: "agent"}`. The same `id` replaces a projection: file
frames by id and give `Projected` the newest. New data remounts the type's component (its rows
and bars come in again, a custom frame restarts); the projection around it stays where it is
and does not replay its appear. The host places and sizes it, sets `leaving` to send it
back into the core, and removes it after `--jv-dur-exit`.

`[data-state]` on the inner `.gen` is the state: `loading`, `error` (the validator's first
complaint), `empty` (the type's `empty_when` fields are all empty) or `ready`. `[data-gen]`
is the type that drew.

## The types

| Type | Component | Draws |
|---|---|---|
| `timeline` | `Timeline` | Events in order, a tone dot each. |
| `stat` | `Stat` | One figure, its unit, a delta and a caption. |
| `line` | `Line` | Series as SVG lines, or areas, with hi/lo and x ticks. |
| `bar` | `Bar` | Bars that rise from the base; `picked` ones in the accent. |
| `map` | `MapView` | A route and places on a graticule, fitted to the data. No tiles, no Leaflet, no network. |
| `list` | `List` | Rows, virtualized past eight. |
| `table` | `Table` | Columns and rows, virtualized, numbers right-aligned. |
| `comparison` | `Comparison` | Options side by side; the picked one washed. |
| `image` | `Picture` | A `data:image/png;base64,` picture. Never a URL: the console fetches nothing a model names. |
| `model` | `Model` | A 3D point cloud or mesh in three.js, turning slowly (still under reduced motion). |
| `code` | `Code` | A block of code. |
| `diff` | `Diff` | A unified diff, the first 400 lines. |
| `approval` | `Approval` | What will be asked, in amber. It confirms nothing: the real approval is the `approval.request` frame. |
| `progress` | `Progress` | Steps and a measure of done. |
| `media` | `Media` | What is playing and how far in. |
| `device` | `Device` | One device and its state. |
| `person` | `Person` | A monogram, name, role and facts. |
| `scene` | `Scene` | The default "make me a visual": lines, polygons, circles, text and paths, flat or through a camera. |
| `custom_html` | `CustomHtml` | The escape hatch, sandboxed (below). |

Every string is drawn as text. Colours are by meaning (`tone`): violet is memory and
models, amber (`warn`) waits on Sir, pink (`danger`) is an error. Motion is the system's:
the projection grows out of the core (~0.5 s), bars rise (`--jv-dur-bar`), rows slide in
`--jv-stagger-project` apart; reduced motion drops all of it.

## `custom_html`

The HTML is model-written, so it only ever runs in
`<iframe sandbox="allow-scripts" src="/projection-frame">`:

- **No same-origin.** The frame's origin is opaque: no cookies, no storage, no
  `parent.document`.
- **No network.** `/projection-frame` (`src/routes/projection-frame/+server.ts`) is a static
  bootstrap served with its own CSP, `default-src 'none'` plus inline script and style only.
  The HTML arrives by `postMessage` once, on the bootstrap's first load. A `srcdoc` frame
  would inherit the console's CSP, which allows same-origin fetches.
- **Run first.** Nothing loads until the owner presses Run visual (`projection-run`): a
  sandboxed frame without site isolation shares the console's thread, so a `while (true) {}`
  the model wrote would stall the HUD with no way for the time cap to fire.
- **Caps.** `html` is at most 32 768 characters and `height` 80 to 720 px; the frame is
  removed `limits.custom_html_seconds` after Run and the moment `placement.leaving` turns
  true. Nothing renders on the server.

Does not cover a busy loop after Run: that still stalls the HUD until the browser offers
to stop the page.

## On the stage

The HUD reducer files each frame on the turn (`TurnState.visuals` in `$lib/hermes/hud.ts`: one
per id, the newest four) and the next turn clears them. `projectionsFor`
(`$lib/ds/shell/projections.ts`) puts them after the steps in thinking and working and first in
the answer scene, and `Stage.svelte` draws each in its slot (`stage-visual`), sized to the side
of the core it faces and stopped short of the core's light; on a phone the stack under the core
makes room for it. Does not cover keeping a visual past its turn (pinning it as a live panel).

`/gallery/projections` shows every type with its example, plus the loading, empty, error and
light states; `?live=1` draws what the socket sends (the e2e's 300 ms measurement).
