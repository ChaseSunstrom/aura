import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import {
	applyDismiss,
	applyShow,
	applyUpdate,
	panelFromShow,
	parsePanelList,
	pruneExpired,
	seedFromList,
	stackOrder,
	type Panel
} from './panels';

/** The contract both this renderer and the emitter read. */
const CONTRACT = JSON.parse(
	readFileSync(
		fileURLToPath(new URL('../../../../tests/contracts/hud-panels.json', import.meta.url)),
		'utf-8'
	)
) as {
	kinds: string[];
	dismiss_all_id: string;
	required_show_fields: string[];
	examples: Record<string, Record<string, unknown>>;
};

const show = (over: Record<string, unknown> = {}) => ({
	id: 'p1',
	kind: 'text',
	title: 'A panel',
	payload: { markdown: 'hi' },
	origin: 'agent',
	created_at: '2026-09-03T00:41:12Z',
	rev: 1,
	...over
});

describe('panelFromShow', () => {
	it('reads a whole show payload, created_at as epoch ms', () => {
		const panel = panelFromShow(show(), 5000);
		expect(panel).toMatchObject({
			id: 'p1',
			kind: 'text',
			title: 'A panel',
			payload: { markdown: 'hi' },
			origin: 'agent',
			rev: 1,
			ttlS: null,
			expiresAt: null
		});
		expect(panel!.createdAt).toBe(Date.parse('2026-09-03T00:41:12Z'));
	});

	it('is null without an id — a panel with no id cannot be updated or dismissed', () => {
		expect(panelFromShow(show({ id: '' }), 0)).toBeNull();
		expect(panelFromShow({}, 0)).toBeNull();
	});

	it('defaults a missing kind, origin and created_at', () => {
		const panel = panelFromShow({ id: 'x', rev: 2 }, 9000);
		expect(panel).toMatchObject({ kind: 'text', origin: 'agent', createdAt: 9000, rev: 2 });
	});

	it('sets a local ttl deadline from ttl_s, measured from arrival', () => {
		const panel = panelFromShow(show({ ttl_s: 30 }), 1000);
		expect(panel).toMatchObject({ ttlS: 30, expiresAt: 31_000 });
	});

	it('accepts every kind the contract names, with its example payload', () => {
		for (const [kind, example] of Object.entries(CONTRACT.examples)) {
			expect(CONTRACT.kinds).toContain(kind);
			const panel = panelFromShow(show({ id: `p-${kind}`, kind, payload: example }), 0);
			expect(panel).not.toBeNull();
			expect(panel!.kind).toBe(kind);
			expect(panel!.payload).toEqual(example);
		}
	});

	it('carries a media panel — video, audio, a document by url — with its url, mime and provenance intact', () => {
		// The three media kinds are appended to the contract, never reordered:
		// the Android mirror compares the list element-wise.
		expect(CONTRACT.kinds.slice(-3)).toEqual(['video', 'audio', 'document']);
		for (const kind of ['video', 'audio', 'document']) {
			const example = CONTRACT.examples[kind];
			expect(example, kind).toMatchObject({
				url: expect.stringMatching(/^\/hermes\/api\/artifacts\/[0-9a-f]{64}$/),
				mime: expect.stringMatching(/^[a-z]+\/[a-z0-9.+-]+$/),
				title: expect.any(String),
				provenance: { tool: expect.any(String), source_url: expect.stringMatching(/^https?:\/\//) }
			});
			const panel = panelFromShow(show({ id: `p-${kind}`, kind, payload: example }), 0)!;
			expect(panel.payload.mime).toBe(example.mime);
			expect((panel.payload.provenance as Record<string, unknown>).tool).toBe((example.provenance as Record<string, unknown>).tool);
		}
	});
});

describe('applyShow', () => {
	it('appends a new panel and replaces one with the same id', () => {
		const a = panelFromShow(show({ id: 'a' }), 0)!;
		const b = panelFromShow(show({ id: 'b' }), 0)!;
		let panels = applyShow(applyShow([], a), b);
		expect(panels.map((p) => p.id)).toEqual(['a', 'b']);
		const a2 = panelFromShow(show({ id: 'a', rev: 2, title: 'renamed' }), 0)!;
		panels = applyShow(panels, a2);
		expect(panels.map((p) => p.id)).toEqual(['a', 'b']); // in place, not moved
		expect(panels[0].title).toBe('renamed');
	});

	it('ignores a show with a lower rev than the one held', () => {
		const held = panelFromShow(show({ rev: 5 }), 0)!;
		const stale = panelFromShow(show({ rev: 3, title: 'stale' }), 0)!;
		const panels = applyShow([held], stale);
		expect(panels[0].title).toBe('A panel');
	});
});

describe('applyUpdate', () => {
	const base = () => [panelFromShow(show({ rev: 1 }), 0)!];

	it('merges a new payload and title, and bumps the rev', () => {
		const panels = applyUpdate(base(), { id: 'p1', rev: 2, payload: { markdown: 'now' }, title: 'GPUs (live)' }, 0);
		expect(panels[0]).toMatchObject({ rev: 2, title: 'GPUs (live)', payload: { markdown: 'now' } });
	});

	it('keeps the title when the update carries only a payload', () => {
		const panels = applyUpdate(base(), { id: 'p1', rev: 2, payload: { markdown: 'now' } }, 0);
		expect(panels[0].title).toBe('A panel');
	});

	it('ignores an update with a lower rev than held', () => {
		const held = [panelFromShow(show({ rev: 5 }), 0)!];
		const panels = applyUpdate(held, { id: 'p1', rev: 2, title: 'stale' }, 0);
		expect(panels[0].title).toBe('A panel');
	});

	it('ignores an update for a panel it never saw', () => {
		const held = base();
		expect(applyUpdate(held, { id: 'nope', rev: 9, title: 'x' }, 0)).toBe(held);
	});

	it('restarts the ttl clock when an update carries a new ttl_s', () => {
		const panels = applyUpdate(base(), { id: 'p1', rev: 2, ttl_s: 10 }, 2000);
		expect(panels[0]).toMatchObject({ ttlS: 10, expiresAt: 12_000 });
	});
});

describe('applyDismiss', () => {
	it('removes one panel by id', () => {
		const panels = [panelFromShow(show({ id: 'a' }), 0)!, panelFromShow(show({ id: 'b' }), 0)!];
		expect(applyDismiss(panels, 'a').map((p) => p.id)).toEqual(['b']);
	});

	it('clears every panel on the contract’s dismiss-all id', () => {
		const panels = [panelFromShow(show({ id: 'a' }), 0)!, panelFromShow(show({ id: 'b' }), 0)!];
		expect(applyDismiss(panels, CONTRACT.dismiss_all_id)).toEqual([]);
		expect(CONTRACT.dismiss_all_id).toBe('*');
	});

	it('is a no-op for an id that is not there', () => {
		const panels = [panelFromShow(show({ id: 'a' }), 0)!];
		expect(applyDismiss(panels, 'z')).toBe(panels);
	});
});

describe('seedFromList and parsePanelList', () => {
	it('parses a JSON array, a wrapped list, and refuses junk', () => {
		expect(parsePanelList('[{"id":"a"}]')).toEqual([{ id: 'a' }]);
		expect(parsePanelList('{"panels":[{"id":"b"}]}')).toEqual([{ id: 'b' }]);
		expect(parsePanelList('not json')).toEqual([]);
		expect(parsePanelList('')).toEqual([]);
		expect(parsePanelList([{ id: 'c' }])).toEqual([{ id: 'c' }]);
	});

	it('seeds panels from a list without clobbering a higher rev already held', () => {
		const held = [panelFromShow(show({ id: 'p1', rev: 9, title: 'live' }), 0)!];
		const list = [show({ id: 'p1', rev: 1, title: 'stale' }), show({ id: 'p2', rev: 1 })];
		const panels = seedFromList(held, list, 0);
		expect(panels.map((p) => p.id)).toEqual(['p1', 'p2']);
		expect(panels[0].title).toBe('live');
	});
});

describe('pruneExpired and stackOrder', () => {
	it('drops panels whose local ttl has passed', () => {
		const panels = [panelFromShow(show({ id: 'a', ttl_s: 10 }), 0)!, panelFromShow(show({ id: 'b' }), 0)!];
		expect(pruneExpired(panels, 5_000).map((p) => p.id)).toEqual(['a', 'b']);
		expect(pruneExpired(panels, 20_000).map((p) => p.id)).toEqual(['b']);
	});

	it('stacks newest first, stable across ids', () => {
		const panels: Panel[] = [
			panelFromShow(show({ id: 'old', created_at: '2026-09-03T00:00:00Z' }), 0)!,
			panelFromShow(show({ id: 'new', created_at: '2026-09-03T01:00:00Z' }), 0)!
		];
		expect(stackOrder(panels).map((p) => p.id)).toEqual(['new', 'old']);
	});
});
