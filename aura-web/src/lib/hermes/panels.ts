// The panel surface, as data — the typed cards JARVIS pulls up on the HUD.
//
// A panel arrives as `hud.panel.show`, is refreshed by `hud.panel.update`, and
// leaves by `hud.panel.dismiss` (its own id, or `*` for the lot). The rules are
// small and all here, so `panels.test.ts` can drive them without a socket:
//
//   - `rev` is monotonic per panel; an event with a lower rev than the one held
//     is ignored (a reconnect replays the stream, and a stale frame must not
//     undo a newer one).
//   - `*` dismisses everything.
//   - `ttl_s`, when set, is the emitter's job — it dismisses the panel itself —
//     but the client keeps `expiresAt` so a surface can time it out locally if
//     the dismiss never comes (a dropped socket, a crashed plugin).
//
// The shape both apps and the emitter agree on is `tests/contracts/hud-panels.json`;
// `docs/hermes-hud-protocol.md` (“Panels”) is its prose. Nothing here draws —
// the surface is `$lib/components/hud/PanelSurface.svelte`.

/** The fourteen kinds the contract names; a kind off the list is still carried. */
export type PanelKind =
	| 'text'
	| 'table'
	| 'image'
	| 'chart'
	| 'camera'
	| 'code'
	| 'terminal'
	| 'web'
	| 'device'
	| 'telemetry'
	| 'goal'
	| 'approval'
	| 'clarify'
	| 'notice';

/** Who put the panel up: a tool call, a person, or an event (approval/goal/job). */
export type PanelOrigin = 'agent' | 'user' | 'system';

export interface Panel {
	id: string;
	kind: PanelKind | string;
	title: string;
	/** The kind's own body, verbatim from the event. */
	payload: Record<string, unknown>;
	origin: PanelOrigin | string;
	/** Epoch ms, from the event's `created_at` when it parses, else when it arrived. */
	createdAt: number;
	/** Monotonic per panel; a lower rev than the one held is ignored. */
	rev: number;
	/** Seconds, as the emitter set it; null when the panel stays until dismissed. */
	ttlS: number | null;
	/** Epoch ms this panel times out locally, or null — the ttl fallback. */
	expiresAt: number | null;
	/** The owner asked to keep it: a tab on every screen, not a launch (absent otherwise). */
	kept?: true;
}

/** The id `hud.panel.dismiss` uses to clear the whole surface. */
export const DISMISS_ALL = '*';

function str(value: unknown): string {
	if (value === null || value === undefined) return '';
	return typeof value === 'string' ? value : String(value);
}

function asRecord(value: unknown): Record<string, unknown> {
	return value && typeof value === 'object' && !Array.isArray(value)
		? (value as Record<string, unknown>)
		: {};
}

/** A `created_at` ISO string as epoch ms, or `now` when it is missing or unparseable. */
function createdMs(value: unknown, now: number): number {
	if (typeof value === 'number' && Number.isFinite(value)) return value;
	const parsed = Date.parse(str(value));
	return Number.isFinite(parsed) ? parsed : now;
}

function ttlOf(value: unknown): number | null {
	const n = typeof value === 'number' ? value : Number(value);
	return Number.isFinite(n) && n > 0 ? n : null;
}

/**
 * A `Panel` from a `hud.panel.show` payload (or one row of a `hud list`).
 *
 * `null` when there is no id — a panel with no id cannot be updated or
 * dismissed, so it is not one we can hold. Everything else is read loosely:
 * a field the emitter left out is a sensible default, never a crash.
 */
export function panelFromShow(payload: unknown, now: number): Panel | null {
	const p = asRecord(payload);
	const id = str(p.id);
	if (!id) return null;
	const rev = typeof p.rev === 'number' ? p.rev : Number(p.rev);
	const ttlS = ttlOf(p.ttl_s);
	// The ttl clock starts when the panel arrives here: a `hud list` seeded
	// after a reconnect gives a whole window again rather than a stale one,
	// and the emitter's own dismiss is still the authority.
	return {
		id,
		kind: str(p.kind) || 'text',
		title: str(p.title),
		payload: asRecord(p.payload),
		origin: str(p.origin) || 'agent',
		createdAt: createdMs(p.created_at, now),
		rev: Number.isFinite(rev) ? rev : 0,
		ttlS,
		expiresAt: ttlS !== null ? now + ttlS * 1000 : null,
		...(p.kept === true ? { kept: true as const } : {})
	};
}

/** Insert or replace a panel, honouring its rev; a lower rev than held is ignored. */
export function applyShow(panels: Panel[], panel: Panel): Panel[] {
	const index = panels.findIndex((p) => p.id === panel.id);
	if (index === -1) return [...panels, panel];
	if (panel.rev < panels[index].rev) return panels;
	const next = panels.slice();
	next[index] = panel;
	return next;
}

/**
 * Fold a `hud.panel.update` in: a new payload and/or title on an existing panel.
 *
 * An update for a panel we never saw is ignored — there is nothing to draw it
 * on, and `hud list` is how a late joiner catches up. A lower rev than held is
 * ignored too.
 */
export function applyUpdate(panels: Panel[], payload: unknown, now: number): Panel[] {
	const p = asRecord(payload);
	const id = str(p.id);
	if (!id) return panels;
	const index = panels.findIndex((one) => one.id === id);
	if (index === -1) return panels;
	const held = panels[index];
	const rev = typeof p.rev === 'number' ? p.rev : Number(p.rev);
	if (Number.isFinite(rev) && rev < held.rev) return panels;
	const ttlS = 'ttl_s' in p ? ttlOf(p.ttl_s) : held.ttlS;
	const next = panels.slice();
	next[index] = {
		...held,
		rev: Number.isFinite(rev) ? rev : held.rev,
		title: 'title' in p ? str(p.title) : held.title,
		payload: 'payload' in p ? asRecord(p.payload) : held.payload,
		ttlS,
		// A fresh ttl on an update restarts the local clock; otherwise the panel
		// keeps the deadline it had.
		expiresAt: 'ttl_s' in p ? (ttlS !== null ? now + ttlS * 1000 : null) : held.expiresAt
	};
	return next;
}

/** Remove one panel by id, or every panel when the id is `*`. */
export function applyDismiss(panels: Panel[], id: string): Panel[] {
	if (id === DISMISS_ALL) return panels.length ? [] : panels;
	const next = panels.filter((p) => p.id !== id);
	return next.length === panels.length ? panels : next;
}

/**
 * Seed the surface from a `hud list` reply: a JSON array of show-shaped rows.
 *
 * Applied through `applyShow`, so a row with a lower rev than one already held
 * (an event that beat the list back) does not clobber it.
 */
export function seedFromList(panels: Panel[], list: unknown, now: number): Panel[] {
	if (!Array.isArray(list)) return panels;
	let next = panels;
	for (const row of list) {
		const panel = panelFromShow(row, now);
		if (panel) next = applyShow(next, panel);
	}
	return next;
}

/** Parse a `hud list` plugin output (a JSON array) into panel rows, tolerating junk. */
export function parsePanelList(output: unknown): unknown[] {
	if (Array.isArray(output)) return output;
	if (typeof output !== 'string' || !output.trim()) return [];
	try {
		const parsed = JSON.parse(output);
		if (Array.isArray(parsed)) return parsed;
		// Some plugins wrap the list: `{panels: [...]}`.
		const wrapped = asRecord(parsed).panels;
		return Array.isArray(wrapped) ? wrapped : [];
	} catch {
		return [];
	}
}

/** Drop panels whose local ttl has passed — the fallback when no dismiss arrived. */
export function pruneExpired(panels: Panel[], now: number): Panel[] {
	const kept = panels.filter((p) => p.expiresAt === null || now < p.expiresAt);
	return kept.length === panels.length ? panels : kept;
}

/** Panels in the order the surface stacks them: newest first, stable on update. */
export function stackOrder(panels: Panel[]): Panel[] {
	return panels
		.slice()
		.sort((a, b) => b.createdAt - a.createdAt || (a.id < b.id ? 1 : a.id > b.id ? -1 : 0));
}
