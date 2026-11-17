// The panel types and their size envelopes (GLASS_HUD §9.1).
//
// Every panel has a stable id, a type and a min / preferred / max size in grid
// units — one unit is 80 px on web and desktop, a column on the phone, a cell
// on the console — so a layout saved on one surface still means something on
// another. The table lives here, once, as data: the window manager (wave 3)
// clamps to it, the frame advertises it as `data-min`/`data-max`, and the
// gallery renders every type from it, so a type added to the spec without an
// envelope shows up as a TypeScript error rather than a panel that can shrink
// to nothing.
//
// `null` is the table's `∞`: the panel fills whatever it is given.

/**
 * One grid unit in CSS pixels (§9.1: "one unit = 80 px on web/desktop, one
 * column on the phone, one cell on the console"). It is a layout constant of
 * the window manager, not a spacing step, which is why it is here beside the
 * envelopes and not in the token table — the phone and the console resolve the
 * same envelope against a column and a cell, and a `--jv-*` length would only
 * be right on one of the three surfaces.
 */
export const GRID_UNIT_PX = 80;

/** A width × height in grid units; `null` on either axis means "fills". */
export interface Extent {
	w: number | null;
	h: number | null;
}

export interface Envelope {
	min: Extent;
	preferred: Extent;
	max: Extent;
}

const ext = (w: number | null, h: number | null): Extent => ({ w, h });

/** The §9.1 table, verbatim: min / preferred / max, width × height. */
export const PANEL_TYPES = {
	chat: { min: ext(4, 4), preferred: ext(6, 8), max: ext(null, null) },
	timeline: { min: ext(3, 3), preferred: ext(4, 8), max: ext(6, null) },
	approvals: { min: ext(4, 2), preferred: ext(5, 3), max: ext(6, 4) },
	goals: { min: ext(3, 2), preferred: ext(4, 4), max: ext(6, null) },
	kanban: { min: ext(6, 4), preferred: ext(8, 6), max: ext(null, null) },
	task: { min: ext(4, 3), preferred: ext(6, 6), max: ext(null, null) },
	memory: { min: ext(4, 3), preferred: ext(6, 6), max: ext(null, null) },
	model: { min: ext(3, 2), preferred: ext(3, 3), max: ext(4, 4) },
	voice: { min: ext(3, 2), preferred: ext(4, 3), max: ext(6, 4) },
	audit: { min: ext(4, 3), preferred: ext(6, 6), max: ext(null, null) },
	settings: { min: ext(4, 4), preferred: ext(6, 8), max: ext(null, null) },
	image: { min: ext(3, 3), preferred: ext(4, 4), max: ext(null, null) },
	browser: { min: ext(4, 3), preferred: ext(6, 6), max: ext(null, null) },
	document: { min: ext(4, 3), preferred: ext(6, 8), max: ext(null, null) },
	telemetry: { min: ext(3, 2), preferred: ext(4, 3), max: ext(null, null) },
	notifications: { min: ext(3, 2), preferred: ext(4, 4), max: ext(6, null) },
	house: { min: ext(4, 3), preferred: ext(6, 6), max: ext(null, null) },
	automations: { min: ext(4, 3), preferred: ext(6, 6), max: ext(null, null) },
	integrations: { min: ext(4, 3), preferred: ext(6, 6), max: ext(null, null) },
	// The model manager: cards with a form under each, the GPUs, a search. Tall and wide.
	models: { min: ext(4, 4), preferred: ext(6, 8), max: ext(null, null) },
	jobs: { min: ext(3, 2), preferred: ext(4, 4), max: ext(6, null) },
	pairing: { min: ext(3, 3), preferred: ext(3, 4), max: ext(4, 5) },
	gallery: { min: ext(6, 4), preferred: ext(8, 8), max: ext(null, null) },
	// Command and control (HUD.md §4): what focusing the reactor opens. Tall
	// rather than wide — six sections read down, and none of them is a table.
	c2: { min: ext(4, 6), preferred: ext(5, 8), max: ext(6, null) },
	// The one task store, read three ways (ADR-0034). Wide, because the board is.
	tasks: { min: ext(5, 4), preferred: ext(7, 6), max: ext(null, null) },
	// The capability map (Part 3): rows of one line each, so it wants width.
	capability: { min: ext(5, 4), preferred: ext(7, 6), max: ext(null, null) },
	// The media panels (hud-panels.json's `video` and `audio` kinds). A video
	// wants a 16:9-ish frame with its controls under it; an audio player is one
	// row of controls and a caption, and a tall one would be mostly blank.
	video: { min: ext(3, 3), preferred: ext(5, 4), max: ext(null, null) },
	audio: { min: ext(3, 2), preferred: ext(4, 2), max: ext(6, 3) },
	// The car's panels (`/car`, fed by the phone): web-only, like c2.
	media: { min: ext(3, 2), preferred: ext(4, 3), max: ext(6, 4) },
	calls: { min: ext(3, 2), preferred: ext(4, 2), max: ext(6, 3) },
	messages: { min: ext(3, 3), preferred: ext(4, 4), max: ext(null, null) },
	carmap: { min: ext(4, 3), preferred: ext(6, 5), max: ext(null, null) },
	speed: { min: ext(3, 2), preferred: ext(4, 3), max: ext(null, null) },
	clock: { min: ext(2, 1), preferred: ext(3, 2), max: ext(null, null) },
	// The driver's phone apps, a grid of big targets (T-0326). Last, so the car's six
	// older panels keep the sides they alternate into.
	apps: { min: ext(3, 2), preferred: ext(4, 3), max: ext(null, null) }
} as const satisfies Record<string, Envelope>;

export type PanelType = keyof typeof PANEL_TYPES;

/** The types, in the table's order — the gallery's iteration order. */
export const PANEL_TYPE_NAMES = Object.keys(PANEL_TYPES) as PanelType[];

/**
 * The `hud.panel.show` kinds of `tests/contracts/hud-panels.json`, mapped to
 * panel types (§9.1's last paragraph). A kind the contract adds later lands
 * on `document` — a readable fallback — rather than on nothing.
 */
export const KIND_TO_TYPE: Readonly<Record<string, PanelType>> = {
	text: 'document',
	code: 'document',
	table: 'telemetry',
	chart: 'telemetry',
	telemetry: 'telemetry',
	image: 'image',
	camera: 'image',
	terminal: 'task',
	web: 'browser',
	device: 'house',
	goal: 'goals',
	approval: 'approvals',
	clarify: 'approvals',
	notice: 'notifications',
	video: 'video',
	audio: 'audio',
	document: 'document'
};

export function typeForKind(kind: string): PanelType {
	return KIND_TO_TYPE[kind] ?? 'document';
}

/** The envelope for a type; a type the table lacks is a programming error. */
export function envelopeOf(type: PanelType): Envelope {
	const envelope = PANEL_TYPES[type];
	if (!envelope) throw new Error(`unknown panel type ${type}`);
	return envelope;
}

/**
 * Clamp a requested extent into a type's envelope. Used by the manager on a
 * resize and by the frame on its `style` so a panel can never be dragged
 * below the size its content is legible at.
 */
export function clampExtent(type: PanelType, wanted: Extent): Extent {
	const { min, max } = envelopeOf(type);
	const clamp = (value: number | null, lo: number | null, hi: number | null): number | null => {
		if (value === null) return hi;
		let v = value;
		if (lo !== null && v < lo) v = lo;
		if (hi !== null && v > hi) v = hi;
		return v;
	};
	return { w: clamp(wanted.w, min.w, max.w), h: clamp(wanted.h, min.h, max.h) };
}

/** A grid extent as a CSS length, or `undefined` for the axis that fills. */
export function unitsToCss(units: number | null): string | undefined {
	return units === null ? undefined : `${units * GRID_UNIT_PX}px`;
}
