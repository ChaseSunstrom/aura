// The connection card's rules, which is where they can be wrong.
//
// The component draws the card and the solver decides where it goes. What is
// left — which cards are up, why, and what happens to the ones that will not
// fit — is here, and none of it needs a GPU to be tested.
import { describe, expect, it } from 'vitest';

import {
	advance,
	AUTO_MS,
	CHIP_FROM,
	dimOf,
	fresh,
	hide,
	METRIC_CEILING,
	metricsOf,
	place,
	priorityOf,
	settle,
	show,
	crosses,
	uncross,
	DIM_FLOOR,
	type Connection,
	type Metric
} from './connectionCard';

const metric = (n: number): Metric[] =>
	Array.from({ length: n }, (_, i) => ({ label: `m${i}`, value: String(i) }));

/** A card of each kind is the same card, which is the point of the component. */
const card = (metrics: number, kind: Connection['kind'] = 'companion'): Connection => ({
	id: 'aura',
	kind,
	title: 'AURA',
	to: 'GitLab API',
	doing: 'opening a merge request',
	metrics: metric(metrics)
});

describe('a card is a glance', () => {
	it('shows three metrics however many it is given', () => {
		// "Three metrics is the ceiling, not a target."
		expect(metricsOf(card(7))).toHaveLength(METRIC_CEILING);
		expect(metricsOf(card(2))).toHaveLength(2);
	});

	it('is the same card for a node, a companion, a satellite and a memory node', () => {
		// ADR-0038's rule one level along: four variants drift, and the fourth is
		// always the one nobody updated.
		for (const kind of ['node', 'companion', 'satellite', 'memory'] as const) {
			expect(metricsOf(card(5, kind))).toHaveLength(METRIC_CEILING);
		}
	});
});

describe('why a card is up decides whether it leaves', () => {
	it('fades an auto-shown card on its own', () => {
		let s = show(fresh(), 'aura', 'auto', 0);
		expect(settle(s, AUTO_MS - 1).shown).toHaveLength(1);
		s = settle(s, AUTO_MS + 1);
		expect(s.shown).toHaveLength(0);
	});

	it('keeps a hovered card up however long it has been there', () => {
		const s = show(fresh(), 'aura', 'hover', 0);
		expect(settle(s, AUTO_MS * 100).shown).toHaveLength(1);
	});

	it('promotes an auto card the person then looks at', () => {
		// The "AURA → GitLab API" card announces itself; the person hovers it. It
		// must stop being on a timer, or it vanishes under the pointer.
		let s = show(fresh(), 'aura', 'auto', 0);
		s = show(s, 'aura', 'hover', AUTO_MS - 1);
		expect(settle(s, AUTO_MS * 10).shown.map((c) => c.id)).toEqual(['aura']);
	});

	it('does not demote a held card when the same thing announces itself again', () => {
		let s = show(fresh(), 'aura', 'focus', 0);
		s = show(s, 'aura', 'auto', 10);
		expect(s.shown[0].reason).toBe('focus');
		expect(settle(s, AUTO_MS * 10).shown).toHaveLength(1);
	});

	it('takes a card down when the pointer leaves', () => {
		const s = hide(show(fresh(), 'aura', 'hover', 0), 'aura');
		expect(s.shown).toHaveLength(0);
	});
});

describe('a card that will not fit is never dropped', () => {
	function crowded(autos: number, held = 0) {
		let s = fresh();
		for (let i = 0; i < held; i++) s = show(s, `held${i}`, 'hover', 0);
		for (let i = 0; i < autos; i++) s = show(s, `auto${i}`, 'auto', 0);
		return s;
	}

	/** The solver's verdict, as `place()` in place.ts returns it. */
	const fitted = (...ids: string[]) => new Set(ids);

	it('tells the solver the person’s cards come first', () => {
		expect(priorityOf('hover')).toBeGreaterThan(priorityOf('auto'));
		expect(priorityOf('focus')).toBe(priorityOf('hover'));
	});

	it('queues an auto card rather than suppressing it, and says so', () => {
		// Everything else in the HUD stays a node and shows "crowded". These do
		// not, because nobody asked for them and nobody would know they were gone.
		// The chip shows for even one, because in a boxed-in console layout "one
		// waiting card is worth waiting for" turned out to mean the person saw
		// nothing at all while a tool was running.
		const p = place(crowded(1), fitted());
		expect(p.visible).toEqual([]);
		expect(p.queued).toEqual(['auto0']);
		expect(p.chip, 'a "1 new connection" chip, not silence').toBe(1);
	});

	it('collapses several waiting cards into one chip', () => {
		const p = place(crowded(3), fitted());
		expect(p.queued).toHaveLength(3);
		expect(p.chip, 'the "3 new connections" chip').toBe(3);
		expect(CHIP_FROM).toBe(1);
	});

	it('shows what the solver placed and queues the rest', () => {
		const p = place(crowded(4), fitted('auto0', 'auto1'));
		expect(p.visible).toEqual(['auto0', 'auto1']);
		expect(p.queued).toEqual(['auto2', 'auto3']);
	});

	it('puts held cards before auto ones in what it hands back', () => {
		const p = place(crowded(1, 1), fitted('auto0', 'held0'));
		expect(p.visible).toEqual(['held0', 'auto0']);
	});

	it('a held card the solver could not place stays a node and says it is crowded', () => {
		const p = place(crowded(0, 3), fitted('held0'));
		expect(p.visible).toEqual(['held0']);
		expect(p.crowded, 'not silently gone').toEqual(['held1', 'held2']);
		expect(p.chip, 'the chip is for auto cards only').toBe(0);
	});

	it('never loses one: visible plus queued plus crowded is everything', () => {
		// The invariant that caught the first draft slicing held cards off the end
		// of the list, where they simply ceased to exist.
		const s = crowded(3, 2);
		for (const f of [fitted(), fitted('held0'), fitted('auto1', 'held1'), fitted(...s.shown.map((c) => c.id))]) {
			const p = place(s, f);
			expect([...p.visible, ...p.queued, ...p.crowded].sort()).toEqual(
				s.shown.map((c) => c.id).sort()
			);
		}
	});
});

describe('depth', () => {
	it('recedes with the sphere but never below the floor', () => {
		// A label may fade to nothing; a card may not. "So I never have to open
		// anything to see it" is the whole reason it exists, and seven of twenty
		// spheres face the console's default view.
		expect(dimOf(1)).toBe(1);
		expect(dimOf(0)).toBe(DIM_FLOOR);
		expect(dimOf(0.5)).toBeGreaterThan(DIM_FLOOR);
		expect(dimOf(0.5)).toBeLessThan(1);
	});

	it('stays legible for a sphere that has turned right round', () => {
		expect(dimOf(-1), 'clamped, not negative').toBe(DIM_FLOOR);
		expect(DIM_FLOOR).toBeGreaterThan(0.3);
	});
});

describe('re-announcing a live connection', () => {
	it('changes nothing, and returns the very same object', () => {
		// Identity, not equality. A caller rebuilding its list every frame calls
		// this constantly; a new object each time restarts everything watching it.
		// The fade's interval was being cleared and recreated before it could
		// fire, so an auto card never went away.
		const first = show(fresh(), 'aura', 'auto', 0);
		expect(show(first, 'aura', 'auto', 500)).toBe(first);
	});

	it('still promotes when the reason really changes', () => {
		const first = show(fresh(), 'aura', 'auto', 0);
		const held = show(first, 'aura', 'hover', 500);
		expect(held).not.toBe(first);
		expect(held.shown[0].reason).toBe('hover');
	});
});

describe('tethers do not cross', () => {
	const box = (id: string, x: number, y: number) => ({ id, x, y, w: 100, h: 60 });
	const at = (pairs: Record<string, [number, number]>) =>
		new Map(Object.entries(pairs).map(([id, [x, y]]) => [id, { x, y }]));

	function anyCrossing(placed: ReturnType<typeof box>[], anchors: ReturnType<typeof at>) {
		const segs = placed.map((c) => {
			const a = anchors.get(c.id)!;
			return { x1: c.x + c.w / 2, y1: c.y + c.h / 2, x2: a.x, y2: a.y };
		});
		for (let i = 0; i < segs.length; i++)
			for (let j = i + 1; j < segs.length; j++) if (crosses(segs[i], segs[j])) return true;
		return false;
	}

	it('knows a crossing from a near miss', () => {
		expect(crosses({ x1: 0, y1: 0, x2: 10, y2: 10 }, { x1: 0, y1: 10, x2: 10, y2: 0 })).toBe(true);
		expect(crosses({ x1: 0, y1: 0, x2: 10, y2: 0 }, { x1: 0, y1: 5, x2: 10, y2: 5 })).toBe(false);
		// A shared endpoint is two tethers meeting, not crossing.
		expect(crosses({ x1: 0, y1: 0, x2: 5, y2: 5 }, { x1: 5, y1: 5, x2: 10, y2: 0 })).toBe(false);
	});

	it('swaps two cards that are on each other’s side of the orb', () => {
		// Not collinear: two leaders along one line never "properly" cross, and a
		// fixture like that proves nothing. The first draft of this test was
		// exactly that, and the guard below is what said so.
		const placed = [box('a', 300, 0), box('b', 0, 0)];
		const anchors = at({ a: [40, 200], b: [340, 200] });
		expect(anyCrossing(placed, anchors), 'the fixture really does cross').toBe(true);

		const routed = uncross(placed, anchors);
		expect(anyCrossing(routed, anchors)).toBe(false);
		expect(routed.map((c) => `${c.id}@${c.x}`).sort()).toEqual(['a@0', 'b@300']);
	});

	it('leaves an arrangement that already reads correctly alone', () => {
		const placed = [box('a', 0, 100), box('b', 300, 100)];
		const anchors = at({ a: [40, 130], b: [340, 130] });
		expect(uncross(placed, anchors)).toEqual(placed);
	});

	it('clears every crossing among several, not just the first', () => {
		const placed = [box('a', 300, 0), box('b', 0, 0), box('c', 300, 300), box('d', 0, 300)];
		const anchors = at({ a: [40, 160], b: [340, 160], c: [40, 200], d: [340, 200] });
		expect(anyCrossing(placed, anchors), 'the fixture really does cross').toBe(true);
		expect(anyCrossing(uncross(placed, anchors), anchors)).toBe(false);
	});

	it('does nothing for a card whose sphere it cannot find', () => {
		const placed = [box('a', 0, 0), box('b', 100, 0)];
		expect(uncross(placed, at({}))).toEqual(placed);
	});
});

describe('the chip brings the queue forward', () => {
	it('takes down the auto cards that had their turn and leaves held and queued ones', () => {
		let state = fresh();
		for (const id of ['a', 'b', 'c']) state = show(state, id, 'auto', 0);
		state = show(state, 'held', 'hover', 0);
		// a and held were placed; b and c queued behind the chip.
		const next = advance(state, ['a', 'held']);
		expect(next.shown.map((s) => s.id)).toEqual(['b', 'c', 'held']);
		expect(place(next, new Set(['b', 'c', 'held'])).chip).toBe(0);
		// Nothing visible to advance past: the same state, not an equal copy.
		expect(advance(next, [])).toBe(next);
	});
});
