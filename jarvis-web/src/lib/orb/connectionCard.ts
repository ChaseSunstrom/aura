// The connection card's rules (the orb HUD brief, CONNECTION CARDS).
//
// "When something is active, or when I look at it, I want a small card tethered
// to it saying what it is connected to. **This is one component, not four.**"
// Orb nodes, companions, satellites and memory-graph nodes share it, for the
// reason ADR-0038 keeps one reactor: four variants drift, and the fourth is
// always the one nobody updated.
//
// The component owns the card's shape, its leader line and where the solver put
// it. This owns *which cards are up, why, and what happens to the ones that do
// not fit* — the parts that can be wrong without a GPU being involved.

/** Every kind of thing a card can be tethered to. One card serves all four. */
export type CardKind = 'node' | 'companion' | 'satellite' | 'memory';

/**
 * Why a card is up, which decides whether it goes away on its own.
 *
 * The brief: *"Auto-shown cards fade on their own; a hovered or focused card
 * persists until I leave it."*
 */
export type Reason = 'hover' | 'focus' | 'auto';

export interface Metric {
	label: string;
	value: string;
}

export interface Connection {
	id: string;
	kind: CardKind;
	/** What this is. */
	title: string;
	/** What it is connected to: a service, host, URL, model, file or parent agent. */
	to: string;
	/** What it is doing right now. */
	doing: string;
	metrics: Metric[];
}

/**
 * Three metrics, and it is a ceiling rather than a target.
 *
 * The brief says so in as many words — *"a card is a glance, and the panel
 * behind it is where detail belongs"* — so this truncates rather than trusting
 * every caller to have read that sentence.
 */
export const METRIC_CEILING = 3;

/** How long an auto-shown card stays up before it fades on its own. */
export const AUTO_MS = 6000;

/**
 * How many queued auto-cards it takes to show the chip.
 *
 * One. The brief's words are *"several collapsed into one '3 new connections'
 * chip"*, and the first draft read that as two or more — one waiting card was
 * "worth waiting for". Then the console showed what that means in practice: a
 * tool started, the orb was boxed in by panels on both sides, controls above and
 * the caption beneath, the card had nowhere to go, and the person saw **nothing
 * at all** — no card, no chip, no sign a connection had been made. That is the
 * exact silent suppression the queue exists to prevent, and "so I never have to
 * open anything to see it" is the reason the card exists. A chip saying "1 new
 * connection" is small, honest, and better than silence.
 */
export const CHIP_FROM = 1;

export const metricsOf = (card: Connection): Metric[] => card.metrics.slice(0, METRIC_CEILING);

export interface Shown {
	id: string;
	reason: Reason;
	/** When it went up. Only `auto` cards are timed. */
	since: number;
}

export interface CardState {
	shown: Shown[];
}

export const fresh = (): CardState => ({ shown: [] });

/**
 * Put a card up, or change why an existing one is up.
 *
 * A hover or focus arriving over an auto-shown card **promotes** it: the person
 * is now looking at the thing that announced itself, and it must stop being on a
 * timer. The reverse is not true — an auto event does not demote a card the
 * person is holding.
 */
export function show(state: CardState, id: string, reason: Reason, now: number): CardState {
	const at = state.shown.findIndex((s) => s.id === id);
	if (at === -1) return { shown: [...state.shown, { id, reason, since: now }] };
	const existing = state.shown[at];
	if (existing.reason !== 'auto' && reason === 'auto') return state;
	// Nothing to change: return the *same object*, not an equal one. A caller
	// that re-announces a live connection every time its list is rebuilt would
	// otherwise replace this state on every pass, and anything watching it — the
	// fade's own interval, for one — would be torn down and restarted before it
	// could ever fire. The card then never faded, which is exactly what happened.
	if (existing.reason === reason) return state;
	const shown = [...state.shown];
	shown[at] = { id, reason, since: reason === 'auto' ? existing.since : now };
	return { shown };
}

/** Take a card down now: the pointer left, or focus moved on. */
export function hide(state: CardState, id: string): CardState {
	return { shown: state.shown.filter((s) => s.id !== id) };
}

/**
 * Drop the auto-shown cards whose window has passed.
 *
 * Hover and focus are untouched however long they have been up, which is the
 * whole distinction: one of them the person asked for.
 */
export function settle(state: CardState, now: number): CardState {
	const shown = state.shown.filter((s) => s.reason !== 'auto' || now - s.since < AUTO_MS);
	return shown.length === state.shown.length ? state : { shown };
}

/**
 * The chip's click: bring the queue forward.
 *
 * The auto-shown cards on screen have had their turn, so they come down and
 * their places go to the queued ones on the next placement. A card the person
 * is holding (hover, focus) stays: the chip is about what announced itself.
 * The chip had no action at all before this, a button that did nothing.
 */
export function advance(state: CardState, visible: readonly string[]): CardState {
	const done = new Set(visible);
	const shown = state.shown.filter((s) => s.reason !== 'auto' || !done.has(s.id));
	return shown.length === state.shown.length ? state : { shown };
}

export interface Placement {
	/** The cards the solver placed, held ones first. */
	visible: string[];
	/** Auto-shown cards with nowhere to go. Never dropped. */
	queued: string[];
	/**
	 * Held cards the solver could not place.
	 *
	 * These follow the HUD's ordinary rule rather than the auto-card exception:
	 * *"if a card cannot clear, it stays a node and shows a 'crowded' indicator
	 * rather than overlapping"*. They are named here rather than left out, because
	 * an earlier draft simply sliced them off the end and the invariant test —
	 * visible plus queued plus crowded is everything that was up — is what noticed.
	 * A card the person is holding must not disappear without the node saying so.
	 */
	crowded: string[];
	/** How many queued cards the chip stands for; 0 when there is no chip. */
	chip: number;
}

/**
 * What a card is worth to the solver, so held cards get the good spots.
 *
 * `place()` in `place.ts` takes a `priority` and breaks ties on id, so this is
 * the whole of "the person's cards come first" — expressed once, here, rather
 * than by whoever happens to build the array.
 */
export const priorityOf = (reason: Reason): number => (reason === 'auto' ? 0 : 1);

/**
 * Sort the cards that are up into placed, queued and crowded.
 *
 * `fitted` is the solver's verdict — the ids `place()` found room for. This does
 * not do its own packing: the orb already has one screen-space solver, it
 * already returns `fitted` and the `offset` the leader line needs, and a second
 * one would be a second set of answers to drift from the first.
 *
 * The queue is the interesting part. Everything else in the HUD that cannot be
 * placed "stays a node and shows a crowded indicator", and the brief carves out
 * exactly one exception: *"Auto-shown connection cards are the exception … I did
 * not ask for them and would never know they had been suppressed — the 'AURA →
 * GitLab API' moment the card exists for would be silently swallowed exactly
 * when the scene is busiest."* So they queue, and past `CHIP_FROM` they collapse
 * into one chip. Never dropped.
 */
export function place(state: CardState, fitted: ReadonlySet<string>): Placement {
	const held = state.shown.filter((s) => s.reason !== 'auto');
	const auto = state.shown.filter((s) => s.reason === 'auto');
	const queued = auto.filter((s) => !fitted.has(s.id)).map((s) => s.id);

	return {
		visible: [
			...held.filter((s) => fitted.has(s.id)).map((s) => s.id),
			...auto.filter((s) => fitted.has(s.id)).map((s) => s.id)
		],
		queued,
		crowded: held.filter((s) => !fitted.has(s.id)).map((s) => s.id),
		chip: queued.length >= CHIP_FROM ? queued.length : 0
	};
}

/**
 * The floor a card's opacity never goes below.
 *
 * A label may fade to nothing as its node turns away — `project.ts` drops it at
 * `LABEL_THRESHOLD` rather than smear two per cent of unreadable text over the
 * orb. A card cannot follow that rule and still do its job: the brief's reason
 * for it is *"so I never have to open anything to see it"*, and a card that
 * appears only when its sphere happens to be facing you is one you have to turn
 * the orb to find. Measured in the console: seven of twenty nodes face the
 * camera at the default view, so five times in seven a running tool would have
 * shown nothing at all.
 */
export const DIM_FLOOR = 0.45;

/**
 * How much a card dims for a sphere that has turned away.
 *
 * *"A card for a far-side sphere dims with the same depth falloff its sphere
 * gets"* — dims, not disappears. It recedes to `DIM_FLOOR` and stays legible;
 * `project.ts`'s `z` puts it behind the orb's silhouette, and the leader line is
 * the part that is dropped for a far-side sphere, because a tether from a card
 * to a point behind the orb would draw straight across the instrument.
 *
 * The card still says what it belongs to in its own text, which is what the
 * labels' own comment gives as the reason a leader is decorative rather than
 * load-bearing.
 */
export const dimOf = (facing: number): number =>
	DIM_FLOOR + (1 - DIM_FLOOR) * Math.max(0, Math.min(1, facing));

export interface Seg {
	x1: number;
	y1: number;
	x2: number;
	y2: number;
}

/**
 * Do two segments properly cross? Touching at a shared endpoint does not count.
 *
 * Exported so the e2e that asserts *"no leader line crosses another"* and the
 * code that prevents it are the same predicate. Two implementations of "cross"
 * would eventually disagree, and the test would be the one that was wrong.
 */
export function crosses(a: Seg, b: Seg): boolean {
	const side = (px: number, py: number, qx: number, qy: number, rx: number, ry: number) =>
		Math.sign((qx - px) * (ry - py) - (qy - py) * (rx - px));
	const d1 = side(a.x1, a.y1, a.x2, a.y2, b.x1, b.y1);
	const d2 = side(a.x1, a.y1, a.x2, a.y2, b.x2, b.y2);
	const d3 = side(b.x1, b.y1, b.x2, b.y2, a.x1, a.y1);
	const d4 = side(b.x1, b.y1, b.x2, b.y2, a.x2, a.y2);
	return d1 !== 0 && d2 !== 0 && d3 !== 0 && d4 !== 0 && d1 !== d2 && d3 !== d4;
}

/** A placed card, as far as the tethers are concerned. */
export interface Tethered {
	id: string;
	x: number;
	y: number;
	w: number;
	h: number;
}

/**
 * Swap card positions until no two leader lines cross.
 *
 * The brief asks for tethers *"routed to avoid crossing other tethers"*, and the
 * cheapest routing is not to route at all: if two straight leaders cross, the
 * two cards are on each other's side of the orb, and exchanging their boxes
 * uncrosses them. It is the classic 2-opt move, and it terminates for the reason
 * 2-opt does — for straight segments, swapping a crossing pair strictly shortens
 * the total leader length, so no arrangement can repeat.
 *
 * The iteration bound is belt and braces. Floating point can in principle make
 * "strictly shorter" not strictly shorter; the bound means a pathological case
 * leaves one crossing rather than hanging the frame.
 */
export function uncross<T extends Tethered>(
	placed: readonly T[],
	anchors: ReadonlyMap<string, { x: number; y: number }>
): T[] {
	const out = [...placed];
	const seg = (c: T): Seg | null => {
		const a = anchors.get(c.id);
		return a ? { x1: c.x + c.w / 2, y1: c.y + c.h / 2, x2: a.x, y2: a.y } : null;
	};

	for (let pass = 0; pass < out.length * out.length; pass++) {
		let swapped = false;
		for (let i = 0; i < out.length && !swapped; i++) {
			for (let j = i + 1; j < out.length && !swapped; j++) {
				const a = seg(out[i]);
				const b = seg(out[j]);
				if (!a || !b || !crosses(a, b)) continue;
				// Exchange the boxes, not the ids: each card keeps its own tether,
				// and the two simply trade places on screen.
				const { x, y } = out[i];
				out[i] = { ...out[i], x: out[j].x, y: out[j].y };
				out[j] = { ...out[j], x, y };
				swapped = true;
			}
		}
		if (!swapped) break;
	}
	return out;
}
