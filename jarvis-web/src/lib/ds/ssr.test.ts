// Every ds component renders on the server, and none types a value.
//
// The console's first paint is server-rendered, where there is no window or
// document; a component that touches one outside an effect takes the page down
// only in production. The list is the folder, so a component added without a
// row here fails.
import { readdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { createRawSnippet } from 'svelte';
import { render } from 'svelte/server';
import { describe, expect, it } from 'vitest';

import * as ds from './index';

const text = (s: string) => createRawSnippet(() => ({ render: () => `<span>${s}</span>` }));

const props: Record<string, Record<string, unknown>> = {
	Projection: { children: text('Outside'), core: { x: 10, y: 10 }, edge: 'left', label: 'Weather' },
	Label: { children: text('Living room') },
	Figure: { value: 21, unit: '°C' },
	LeaderLine: { points: [[50, 44], [70, 30]], waiting: true },
	Connector: { start: [50, 17], end: [30, 60], beat: 3 },
	PresenceStrip: {
		task: 'Booking a table',
		steps: [{ label: 'Open site' }, { label: 'Confirm', confirm: true }],
		current: 1,
		ontakeover: () => {}
	},
	Subtitles: { text: 'Three lights are on.' },
	HoldButton: { label: 'Unlock the door', onconfirm: () => {} },
	RuleChip: { text: 'Dim the hall after 23:00', onremove: () => {} },
	Core: { state: 'thinking' },
	Starfield: {},
	ThemeTile: { pack: { id: 'draft', name: 'Dusk', color: { bg: '#101018', accent: '#ff8800' } }, worn: true, note: 'Drafted', children: text('Apply') },
	Conversation: {
		turns: [
			{ id: '1', role: 'you', text: 'Is the porch light on?' },
			{ id: '2', role: 'jarvis', text: 'It is, at **40%**.' }
		]
	}
};

describe('ds components on the server', () => {
	const files = ['.', 'core'].flatMap((dir) =>
		readdirSync(fileURLToPath(new URL(`${dir}/`, import.meta.url)))
			.filter((f) => f.endsWith('.svelte'))
			.map((f) => f.replace(/\.svelte$/, ''))
	);

	it('the barrel exports every component in the folder, and each has a row here', () => {
		for (const name of files) {
			expect(ds, name).toHaveProperty(name);
			expect(props, name).toHaveProperty(name);
		}
	});

	it.each(Object.keys(props))('%s renders', (name) => {
		const component = (ds as Record<string, unknown>)[name];
		const { body } = render(component as never, { props: props[name] as never });
		expect(body.length).toBeGreaterThan(0);
	});

	it('a leader line with fewer than two drawable points draws nothing', () => {
		const { body } = render(ds.LeaderLine, { props: { points: [[Number.NaN, 1], [4, 4]] } });
		expect(body).not.toContain('<svg');
	});

	it('the core says its state on the server, before any renderer exists', () => {
		const { body } = render(ds.Core, { props: { state: 'alert' } });
		expect(body).toContain('data-state="alert"');
		expect(body).toContain('aria-label="AURA, alert"');
		expect(body).not.toContain('data-renderer');
	});

	it('the core says which theme it wears', () => {
		const { body } = render(ds.Core, { props: { state: 'idle', theme: 'jarvis' } });
		expect(body).toContain('aria-label="JARVIS, idle"');
	});

	it('a figure that is not a number says so with a dash', () => {
		expect(render(ds.Figure, { props: { value: Number.NaN } }).body).toContain('—');
	});

	it('the presence strip marks the step under way and offers the take-over', () => {
		const { body } = render(ds.PresenceStrip, { props: props.PresenceStrip as never });
		expect(body).toContain('aria-current="step"');
		expect(body).toContain('to take over');
		expect(body).toContain('data-step="1"');
	});
});
