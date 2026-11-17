import { describe, expect, it } from 'vitest';
import { initialHud, reduce, type HudState } from '$lib/hermes/hud';
import { liveSteps } from './live';

// The payloads are Hermes's own, as scripts/hermes-probes/activity_probe.py recorded them on
// production (2026-11-09) and as tui_gateway/tool_progress.py builds them: `duration_s`, and
// `result` parsed from the tool's JSON.
const ev = (type: string, payload: Record<string, unknown>) => ({ type, payload });

function run(events: { type: string; payload: Record<string, unknown> }[], now = 1000): HudState {
	let s = reduce(initialHud(), ev('message.start', {}), now);
	for (const [i, e] of events.entries()) s = reduce(s, e, now + (i + 1) * 100);
	return s;
}

const NAV = {
	tool_id: 'chatcmpl-tool-1',
	name: 'browser_navigate',
	args: { url: 'https://example.com' }
};
const NAV_DONE = {
	...NAV,
	duration_s: 2.8382408618927,
	result: { success: true, url: 'https://example.com/', title: 'Example Domain', snapshot: '- link "Learn more" [ref=e1]', element_count: 1 }
};
const SEARCH = { tool_id: 's1', name: 'web_search', args: { query: 'tide times', limit: 5 } };
const SEARCH_DONE = {
	...SEARCH,
	duration_s: 1.2,
	result: {
		success: true,
		data: {
			web: [
				{ title: 'Tide tables', url: 'https://tides.example/a', description: 'x', position: 1 },
				{ title: 'Harbour times', url: 'https://harbour.example/b', description: 'y', position: 2 }
			]
		}
	}
};
const SHELL = { tool_id: 't1', name: 'terminal', args: { command: 'ls -la /srv' } };
const lines = Array.from({ length: 12 }, (_, i) => `line ${i + 1}`).join('\n');
const DEVICE = { tool_id: 'd1', name: 'ha_call_service', args: { domain: 'light', service: 'turn_on', entity_id: 'light.lab' } };
const DEVICE_DONE = {
	...DEVICE,
	duration_s: 0.3,
	result: { result: { success: true, service: 'light.turn_on', affected_entities: [{ entity_id: 'light.lab', state: 'on' }] } }
};

describe('liveSteps: states', () => {
	it('is empty with no turn and no calls', () => {
		expect(liveSteps(null)).toEqual([]);
		expect(liveSteps(initialHud())).toEqual([]);
	});

	it('a running call is acting, a finished one done with its duration_s, and thinking trails while the model works', () => {
		const acting = run([ev('tool.start', NAV)]);
		expect(liveSteps(acting).map((s) => s.state)).toEqual(['acting']);

		const after = run([ev('tool.start', NAV), ev('tool.complete', NAV_DONE)]);
		const steps = liveSteps(after);
		expect(steps.map((s) => s.state)).toEqual(['done', 'thinking']);
		expect(steps[0].ms).toBe(2838);
	});

	it('thinking is not shown once the reply streams after the last call, nor after the turn ends', () => {
		const streaming = run([ev('tool.start', NAV), ev('tool.complete', NAV_DONE), ev('message.delta', { text: 'The title' })]);
		expect(liveSteps(streaming).map((s) => s.state)).toEqual(['done']);
		const ended = run([ev('tool.start', NAV), ev('tool.complete', NAV_DONE), ev('message.complete', { text: 'Done.' })]);
		expect(liveSteps(ended).map((s) => s.state)).toEqual(['done']);
	});

	it('text written before a call does not hide the thinking after it', () => {
		const s = run([ev('message.delta', { text: 'Let me look.' }), ev('tool.start', NAV), ev('tool.complete', NAV_DONE)]);
		expect(liveSteps(s).map((x) => x.state)).toEqual(['done', 'thinking']);
	});

	it('a turn with no call yet is one thinking step', () => {
		expect(liveSteps(run([])).map((s) => [s.kind, s.state])).toEqual([['other', 'thinking']]);
	});

	it('fails on an error, a success:false, an ok:false or a non-zero exit_code', () => {
		const cases = [
			{ ...SHELL, error: 'denied by the user' },
			{ ...SHELL, result: { output: 'no such file', exit_code: 2, error: null } },
			{ ...NAV, result: { success: false, error: 'net::ERR_NAME_NOT_RESOLVED' } },
			{ ...DEVICE, result: { ok: false } },
			{ ...DEVICE, result: { result: { success: false, service: 'light.turn_on', affected_entities: [] } } }
		];
		for (const done of cases) {
			const s = run([ev('tool.start', { tool_id: done.tool_id, name: done.name, args: done.args }), ev('tool.complete', done), ev('message.complete', {})]);
			expect(liveSteps(s)[0].state, JSON.stringify(done)).toBe('failed');
		}
		const fine = run([ev('tool.start', SHELL), ev('tool.complete', { ...SHELL, result: { output: 'ok', exit_code: 0, error: null } }), ev('message.complete', {})]);
		expect(liveSteps(fine)[0].state).toBe('done');
	});

	it('a call still open when the turn is stopped ends failed, "stopped", and nothing is left running', () => {
		const s = run([ev('tool.start', NAV), ev('message.complete', { text: '', status: 'interrupted' })]);
		expect(liveSteps(s).map((x) => [x.state, x.error])).toEqual([['failed', 'stopped']]);
		expect(s.turn.tools.some((t) => t.state === 'running')).toBe(false);
		expect(s.messages.at(-1)?.tools.map((t) => t.state)).toEqual(['failed']);
	});

	it('an open approval marks the newest running call waiting; with none running, a trailing waiting step', () => {
		const held = run([
			ev('tool.start', NAV),
			ev('tool.complete', NAV_DONE),
			ev('tool.start', SHELL),
			ev('approval.request', { request_id: 'r1', command: 'ls -la /srv', description: 'shell' })
		]);
		expect(liveSteps(held).map((s) => s.state)).toEqual(['done', 'waiting']);

		const asked = run([ev('tool.start', NAV), ev('tool.complete', NAV_DONE), ev('clarify.request', { request_id: 'c1', question: 'Which page?' })]);
		expect(liveSteps(asked).map((s) => s.state)).toEqual(['done', 'waiting']);
	});
});

describe('liveSteps: what each kind carries', () => {
	it('browse: the latest frame (picture, url, title) on the newest browse step, its action as the text', () => {
		const s = run([
			ev('tool.start', NAV),
			ev('activity.start', { id: 'a1', surface: 'browser', tool: 'browser_navigate', intent: 'open https://example.com', started_at: '2026-11-09T12:33:15Z' }),
			ev('activity.step', { id: 'a1', seq: 1, tool: 'browser_navigate', action: 'open https://example.com', ok: true, at: '2026-11-09T12:33:18Z' }),
			ev('activity.frame', { id: 'a1', seq: 2, url: 'https://example.com/', title: 'Example Domain', text: '- link', image: 'data:image/jpeg;base64,AAAA', at: '2026-11-09T12:33:18Z' }),
			ev('tool.complete', NAV_DONE)
		]);
		const [step] = liveSteps(s);
		expect(step.kind).toBe('browse');
		expect(step.url).toBe('https://example.com/');
		expect(step.title).toBe('Example Domain');
		expect(step.image).toBe('data:image/jpeg;base64,AAAA');
	});

	it('browse: an older browse step keeps its own url and no picture', () => {
		const second = { tool_id: 'n2', name: 'browser_click', args: { ref: '@e1' } };
		const s = run([
			ev('tool.start', NAV),
			ev('tool.complete', NAV_DONE),
			ev('tool.start', second),
			ev('activity.frame', { id: 'a1', seq: 3, url: 'https://iana.org/', title: 'IANA', text: '', image: 'data:image/jpeg;base64,BBBB', at: 'x' })
		]);
		const [first, latest] = liveSteps(s);
		expect([first.url, first.image]).toEqual(['https://example.com/', undefined]);
		expect([latest.url, latest.image]).toEqual(['https://iana.org/', 'data:image/jpeg;base64,BBBB']);
	});

	it("browse: the last turn's page is not this turn's picture", () => {
		const first = run([
			ev('tool.start', NAV),
			ev('activity.frame', { id: 'a1', seq: 2, url: 'https://example.com/', title: 'Example Domain', text: '', image: 'data:image/jpeg;base64,OLD', at: 'x' }),
			ev('tool.complete', NAV_DONE),
			ev('message.complete', { text: 'Done.' })
		]);
		// The next turn opens another page; its own frame has not come yet.
		let s = reduce(first, ev('message.start', {}), 5000);
		s = reduce(s, ev('tool.start', { tool_id: 'n9', name: 'browser_navigate', args: { url: 'https://iana.org/' } }), 5100);
		const [step] = liveSteps(s);
		expect([step.url, step.image]).toEqual(['https://iana.org/', undefined]);
		s = reduce(s, ev('activity.frame', { id: 'a1', seq: 3, url: 'https://iana.org/', title: 'IANA', text: '', image: 'data:image/jpeg;base64,NEW', at: 'y' }), 5200);
		expect(liveSteps(s)[0].image).toBe('data:image/jpeg;base64,NEW');
	});

	it('search: the query, then the sources as they come back, capped', () => {
		const running = liveSteps(run([ev('tool.start', SEARCH)]))[0];
		expect([running.kind, running.query, running.sources]).toEqual(['search', 'tide times', []]);
		const many = { ...SEARCH_DONE, result: { success: true, data: { web: Array.from({ length: 9 }, (_, i) => ({ title: `T${i}`, url: `https://s${i}.example/` })) } } };
		const done = liveSteps(run([ev('tool.start', SEARCH), ev('tool.complete', SEARCH_DONE)]))[0];
		expect(done.sources).toEqual([
			{ title: 'Tide tables', url: 'https://tides.example/a' },
			{ title: 'Harbour times', url: 'https://harbour.example/b' }
		]);
		expect(liveSteps(run([ev('tool.start', SEARCH), ev('tool.complete', many)]))[0].sources).toHaveLength(4);
	});

	it('shell: the command, the progress line while it runs, the output tail after, capped', () => {
		const running = run([ev('tool.start', SHELL), ev('tool.progress', { tool_id: 't1', text: 'building…' })]);
		expect(liveSteps(running)[0]).toMatchObject({ kind: 'shell', command: 'ls -la /srv', tail: ['building…'] });
		const done = run([ev('tool.start', SHELL), ev('tool.complete', { ...SHELL, duration_s: 0.1, result: { output: lines, exit_code: 0, error: null } })]);
		expect(liveSteps(done)[0].tail).toEqual(['line 9', 'line 10', 'line 11', 'line 12']);
		const wide = run([ev('tool.start', SHELL), ev('tool.complete', { ...SHELL, result: { output: 'x'.repeat(500), exit_code: 0 } })]);
		expect(liveSteps(wide)[0].tail?.[0].length).toBeLessThanOrEqual(160);
	});

	it('device: the entity, the service and the state it left', () => {
		const s = run([ev('tool.start', DEVICE), ev('tool.complete', DEVICE_DONE)]);
		expect(liveSteps(s)[0]).toMatchObject({ kind: 'device', entity: 'light.lab', service: 'light.turn_on', left: 'on' });
	});

	it('any other tool is a generic step with its name in words and a capped argument line, never dropped', () => {
		const other = { tool_id: 'o1', name: 'frob_append_entry', args: { text: 'y'.repeat(400) } };
		const [step] = liveSteps(run([ev('tool.start', other)]));
		expect(step.kind).toBe('other');
		expect(step.text).toBe('Frob append entry');
		expect(step.line?.length).toBeLessThanOrEqual(120);
	});

	it('a tool with words of its own carries no raw argument line', () => {
		const extract = { tool_id: 'x1', name: 'web_extract', args: { urls: ['https://a.example/'], char_limit: 4000 } };
		const [step] = liveSteps(run([ev('tool.start', extract)]));
		expect(step.line).toBeUndefined();
		expect(step.text).not.toMatch(/char_limit|urls:/);
	});

	it('a presenting call (hud_*) is left out, as the steps projection always did', () => {
		const say = { tool_id: 'h1', name: 'hud_say', args: { text: 'hello' } };
		expect(liveSteps(run([ev('tool.start', say), ev('tool.complete', { ...say, result: 'ok' }), ev('message.complete', {})]))).toEqual([]);
	});
});
