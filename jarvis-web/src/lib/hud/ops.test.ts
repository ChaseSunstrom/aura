import { describe, expect, it } from 'vitest';
import { isoBars, opsData, type OpsSources } from './ops';

const NOW = 1_800_000_000_000;
const base: OpsSources = { gpus: [], crew: [], model: '', toolsets: [], memory: [], states: [], calls: [], jobs: [], feeds: [], now: NOW };

describe('the Ops board data', () => {
	it('counts memory kept per hour over the last day, newest last', () => {
		const at = (h: number) => NOW / 1000 - h * 3600 - 10;
		const d = opsData({ ...base, memory: [0, 0, 1, 30].map((h, i) => ({ id: `${i}`, text: '', tags: [], created: at(h), source: i ? 'chat' : 'notes' })) });
		expect(d.memory.total).toBe(4);
		expect(d.memory.hourly).toHaveLength(24);
		expect(d.memory.hourly[23]).toBe(2);
		expect(d.memory.hourly[22]).toBe(1);
		expect(d.memory.kinds[0]).toMatchObject({ k: 'chat', v: 3 });
	});

	it('lights the rooms whose light is on and lists who is home', () => {
		const d = opsData({
			...base,
			states: [
				{ entity_id: 'light.kitchen', state: 'on', attributes: { friendly_name: 'Kitchen' } },
				{ entity_id: 'light.office', state: 'off' },
				{ entity_id: 'device_tracker.pixel', state: 'not_home' }
			]
		});
		expect(d.rooms).toEqual([{ name: 'Kitchen', on: true }, { name: 'office', on: false }]);
		expect(d.devices).toEqual([{ name: 'pixel', detail: 'not home', away: true }]);
	});

	it('pulses the agents that are working and greys the idle', () => {
		const d = opsData({ ...base, crew: [{ id: 'a', type: 'researcher', goal: 'x', tools: 1, state: 'running' }, { id: 'b', type: null, goal: 'y', tools: 0, state: 'done' }] as unknown as OpsSources['crew'] });
		expect(d.agents.map((a) => [a.name, a.working, a.tone])).toEqual([['researcher', true, 'accent'], ['helper 2', false, 'tick']]);
	});

	it('routes the enabled toolsets to the model in use, and nothing without one', () => {
		expect(opsData({ ...base, toolsets: [{ name: 'web' }] }).routes).toEqual([]);
		const d = opsData({ ...base, model: 'qwen', toolsets: [{ name: 'web' }, { name: 'house', enabled: false }] });
		expect(d.routes.map((r) => `${r.from}→${r.to}`)).toEqual(['web→qwen']);
	});

	it('reads the feeds as a health grid', () => {
		const d = opsData({ ...base, feeds: [{ name: 'a', status: 'ready' }, { name: 'b', status: 'error' }, { name: 'c', status: 'loading' }] });
		expect(d.health.map((h) => h.state)).toEqual(['ok', 'failing', 'running']);
	});

	it('draws three faces a bar, one bar a GPU segment', () => {
		const faces = isoBars([{ name: 'GPU 0', usedPct: 80, segments: [{ label: 'chat', pct: 60 }, { label: 'embed', pct: 20 }] }]);
		expect(faces).toHaveLength(6);
		expect(faces[2].shade).toBe(1);
	});
});
