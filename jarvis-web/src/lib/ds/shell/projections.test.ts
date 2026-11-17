import { describe, expect, it } from 'vitest';

import { SCENES } from '../stage';
import type { LiveStep, StepState } from '../activity/live';
import { SLOTS, projectionsFor, stripFor } from './projections';

const call = (id: string, state: StepState = 'acting'): LiveStep => ({ id, text: `Step ${id}`, state, kind: 'search', ms: null });

describe('projectionsFor', () => {
	it('an idle stage with no data projects nothing', () => {
		expect(projectionsFor('idle', {})).toEqual([]);
	});

	it('idle shows the sky, named for what it is, and the next job, each on a board slot facing the core', () => {
		const shown = projectionsFor('idle', {
			sky: [{ id: 'moon', label: 'Moon, waxing gibbous', value: 98.14, unit: '%' }],
			jobs: [{ id: 'j', title: 'Water the garden', next: '07:00', state: 'idle' }]
		});
		expect(shown.map((s) => [s.label, s.at, s.edge])).toEqual([
			['Moon, waxing gibbous', [20, 24], 'right'],
			['Next', [80, 24], 'left']
		]);
		expect(shown[0].value).toBe('98.1');
	});

	it('thinking gathers every step of the turn into one ordered Steps projection beside the core', () => {
		const steps = ['a', 'b', 'c', 'd', 'e'].map((id, i) => call(id, i < 4 ? 'done' : 'acting'));
		const shown = projectionsFor('thinking', { steps });
		expect(shown).toHaveLength(1);
		expect(shown[0]).toMatchObject({ id: 'steps', label: 'Steps', rows: [], edge: 'left' });
		expect(shown[0].steps?.map((s) => [s.text, s.state])).toEqual([
			['Step a', 'done'],
			['Step b', 'done'],
			['Step c', 'done'],
			['Step d', 'done'],
			['Step e', 'acting']
		]);
		// Beside the core (x 50, y 44 in thinking), never in a corner.
		const [x, y] = shown[0].at;
		expect(x).toBeGreaterThan(60);
		expect(Math.abs(y - 44)).toBeLessThan(10);
	});

	it('a failed step turns the projection to danger; one waiting on you, to amber', () => {
		expect(projectionsFor('thinking', { steps: [call('a', 'failed'), call('b', 'waiting')] })[0].tone).toBe('danger');
		expect(projectionsFor('thinking', { steps: [call('a', 'done'), call('b', 'waiting')] })[0].tone).toBe('warn');
		expect(projectionsFor('thinking', { steps: [call('a', 'done'), call('b', 'thinking')] })[0].tone).toBe('accent');
	});

	it('helpers keep their own projections, beside the steps', () => {
		const helpers = [{ id: 'sub-1', who: 'AURA', goal: 'review the diff' }];
		for (const scene of ['thinking', 'working'] as const) {
			const shown = projectionsFor(scene, { helpers, steps: [call('a')] });
			expect(shown.map((s) => s.id), scene).toEqual(['steps', 'helper:sub-1']);
			expect(shown[1], scene).toMatchObject({ label: 'AURA', tone: 'violet', rows: ['review the diff'] });
		}
		const idle = projectionsFor('idle', { helpers, sky: [{ id: 't', label: 'T', value: 1 }] });
		expect(idle[0]).toMatchObject({ id: 'helper:sub-1' });
		// More helpers than slots: the steps keep theirs, the rest fill what is left.
		const full = Array.from({ length: 5 }, (_, i) => ({ id: `s${i}`, who: 'AURA', goal: 'g' }));
		expect(projectionsFor('working', { helpers: full, steps: [call('a')] }).map((s) => s.id)).toEqual(['steps', 'helper:s0', 'helper:s1']);
		// No calls yet: the helpers alone.
		expect(projectionsFor('thinking', { helpers }).map((s) => s.id)).toEqual(['helper:sub-1']);
		// Answer is the reply's own content; listening and music project nothing.
		for (const scene of ['answer', 'listening', 'music'] as const) expect(projectionsFor(scene, { helpers })).toEqual([]);
	});

	it('a visual of the turn comes out after the steps, before the helpers, and leads the answer', () => {
		const visuals = [{ id: 'weather', type: 'line', data: { title: 'Next 24 hours · home', x: [], series: [] } }];
		const helpers = [{ id: 'sub-1', who: 'AURA', goal: 'g' }];
		for (const scene of ['thinking', 'working'] as const) {
			const shown = projectionsFor(scene, { steps: [call('a')], visuals, helpers });
			expect(shown.map((s) => s.id), scene).toEqual(['steps', 'visual:weather', 'helper:sub-1']);
			expect(shown[1], scene).toMatchObject({ label: 'Next 24 hours · home', visual: { type: 'line', data: visuals[0].data } });
		}
		const content = [{ spec: { id: 'p1' }, title: 'Page', body: { type: 'browser', url: 'https://example.test', summary: 'A page' } }];
		const answer = projectionsFor('answer', { visuals, content: content as never });
		expect(answer.map((s) => s.id)).toEqual(['visual:weather', 'content:p1']);
		// A visual with no title is named for its type; idle keeps its own readings.
		expect(projectionsFor('answer', { visuals: [{ id: 'v', type: 'bar', data: {} }] })[0].label).toBe('bar');
		expect(projectionsFor('idle', { visuals })).toEqual([]);
	});

	it('a panel pulled up from another session leads the idle stage, ahead of the sky and the next job', () => {
		const content = [{ spec: { id: 'p1' }, title: 'Page', body: { type: 'browser', url: 'https://example.test', summary: 'A page' } }];
		const sky = [{ id: 'moon', label: 'Moon', value: 98 }];
		const jobs = [{ id: 'j', title: 'Water the garden', next: '07:00', state: 'idle' as const }];
		// One panel takes the first slot; the sky keeps the other, the next job waits.
		expect(projectionsFor('idle', { content: content as never, sky, jobs }).map((s) => s.id)).toEqual(['content:p1', 'reading:moon']);
		expect(projectionsFor('idle', { content: content as never })[0]).toMatchObject({ label: 'Page', rows: ['A page'], at: [20, 24] });
		// With no panel, idle is as before.
		expect(projectionsFor('idle', { content: [], sky, jobs }).map((s) => s.id)).toEqual(['reading:moon', 'job:j']);
	});

	it('scenes with no slots never project', () => {
		for (const scene of SCENES) {
			if (SLOTS[scene].length) continue;
			expect(projectionsFor(scene, { steps: [call('a')] })).toEqual([]);
		}
	});
});

describe('stripFor', () => {
	it('no goal, no strip', () => {
		expect(stripFor(null)).toBeNull();
	});

	it('the current step is the first subgoal not done', () => {
		const strip = stripFor({
			id: 'g',
			title: 'Book a table',
			state: 'running',
			subgoals: [
				{ id: '1', label: 'Find', state: 'done' },
				{ id: '2', label: 'Pick', state: 'running' },
				{ id: '3', label: 'Confirm', state: 'queued' }
			]
		});
		expect(strip).toEqual({ task: 'Book a table', steps: [{ label: 'Find' }, { label: 'Pick' }, { label: 'Confirm' }], current: 1 });
	});

	it('a goal with no subgoals still has one step to show', () => {
		expect(stripFor({ id: 'g', title: 'Tidy', state: 'running', turn: 3 })?.steps).toEqual([{ label: 'Turn 3' }]);
	});
});
