// The data layer's mappings, one describe per body, against the shapes the
// contracts and the reducers produce.
//
// `mocks.test.ts` beside this drives the two mock backends through the real
// clients; this file pins the arithmetic on hand fixtures so a failure names
// the mapping rather than the socket.

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';

import { initialHud, type HudState, type ToolCard } from '$lib/hermes/hud';
import { panelFromShow } from '$lib/hermes/panels';
import { parseGoalText } from '$lib/hermes/goal';
import type { HermesSnapshot } from '$lib/hermes/link';
import type { TaskRow } from '$lib/tasks';
import type { ScheduledJob } from '$lib/schedule';
import { PANEL_TYPES } from '$lib/glass/panelTypes';

import {
	agoOf,
	approvalRequests,
	approvalSourceOf,
	auditView,
	automationsView,
	chatTurns,
	clockOf,
	contentPanel,
	contentPanels,
	describeTrigger,
	DOWNLOAD_MIMES,
	servedAsDownload,
	devicesView,
	goalView,
	integrationsView,
	jobSourceOf,
	jobsView,
	kanbanTasks,
	memoryGraph,
	memoryNotes,
	modelView,
	plainLabel,
	noteDocument,
	noteItems,
	noticesView,
	pairingView,
	readingsView,
	settingsCatalogue,
	settingsValues,
	settingWrite,
	skyView,
	taskView,
	timelineCalls,
	untilOf,
	voiceStages
} from './index';

const contract = (name: string) =>
	JSON.parse(readFileSync(fileURLToPath(new URL(`../../../../../tests/contracts/${name}`, import.meta.url)), 'utf8'));

const NOW = Date.UTC(2026, 8, 3, 9, 41, 0);

function tool(over: Partial<ToolCard> = {}): ToolCard {
	return {
		id: 't1',
		name: 'ha_call_service',
		context: '',
		args: { domain: 'light', service: 'turn_on' },
		state: 'done',
		progress: '',
		duration: 0.412,
		error: null,
		result: null,
		startedAt: NOW,
		finishedAt: NOW + 412,
		...over
	};
}

function hud(over: Partial<HudState> = {}): HudState {
	return { ...initialHud(), ...over };
}

describe('time', () => {
	it('gives rows a clock and copy a relative time', () => {
		expect(clockOf(NOW)).toMatch(/^\d\d:\d\d:\d\d$/);
		expect(clockOf(0)).toBe('');
		expect(agoOf(NOW - 120_000, NOW)).toBe('2 minutes ago');
		expect(agoOf(NOW + 5_000, NOW)).toBe('just now');
		expect(untilOf(NOW + 4 * 60_000, NOW)).toBe('in 4 minutes');
		expect(untilOf(NOW - 1, NOW)).toBe('now');
	});
});

describe('chat', () => {
	it('turns the transcript into turns, shaping the reply and keeping the person verbatim', () => {
		const turns = chatTurns([
			{ id: 'u1', role: 'user', content: '  warm the study ', thinking: '', tools: [], at: NOW, pending: false },
			{ id: 'a1', role: 'assistant', content: 'Done.\n\n\n\nThe heater is on.', thinking: 'the setpoint was 17', tools: [tool() as never], at: NOW, pending: true }
		]);
		expect(turns).toEqual([
			{ id: 'u1', role: 'you', text: '  warm the study ', at: clockOf(NOW) },
			{ id: 'a1', role: 'jarvis', text: 'Done.\n\nThe heater is on.', at: clockOf(NOW), reasoning: 'the setpoint was 17', streaming: true }
		]);
	});
});

describe('timeline', () => {
	it('merges the turn tools and the background rows in start order', () => {
		const calls = timelineCalls(
			hud({
				turn: { ...initialHud().turn, tools: [tool({ startedAt: NOW + 10 }), tool({ id: 't2', state: 'running', duration: null, startedAt: NOW + 30 })] },
				background: [{ id: 'p1', kind: 'process', label: 'npm test', state: 'failed', detail: 'exit 1', at: NOW + 20 }]
			})
		);
		expect(calls.map((c) => [c.id, c.state, c.ms])).toEqual([
			['tool:t1', 'ok', 412],
			['process:p1', 'failed', null],
			['tool:t2', 'running', null]
		]);
		expect(calls[0].args).toBe('domain: light · service: turn_on');
	});
});

describe('approvals', () => {
	it('lists Hermes requests and the house\'s held tools in one vocabulary, with the source in the id', () => {
		const rows = approvalRequests(
			hud({
				approvals: [{ requestId: 'r1', sessionId: 's', tool: 'terminal', detail: 'rm -rf build', reason: 'cleaning the old build', choices: ['once', 'deny'], at: NOW }],
				clarifies: [{ requestId: 'c1', sessionId: 's', question: 'Which study?', options: ['upstairs', 'hall'], at: NOW }]
			}),
			[
				{ request_id: 'h1', tool: 'lock.lock', description: 'Lock the front door?', arguments: { entity_id: 'lock.front' }, created: NOW / 1000 },
				{ id: 'h2', tool: 'ask', description: 'Is anybody home?', answerable: 'answer', choices: ['yes', 'no'] }
			]
		);
		expect(rows.map((r) => [r.id, r.kind, r.title])).toEqual([
			['hermes:r1', 'approval', 'cleaning the old build'],
			['hermes:c1', 'clarify', 'Which study?'],
			['core:h1', 'approval', 'Lock the front door?'],
			['core:h2', 'clarify', 'Is anybody home?']
		]);
		expect(rows[0].tool).toBe('terminal');
		expect(rows[1].detail).toBe('Options: upstairs, hall');
		expect(rows[2].detail).toBe('entity_id: lock.front');
		expect(approvalSourceOf('core:h1')).toEqual({ source: 'core', requestId: 'h1' });
		expect(approvalSourceOf('hermes:r1')).toEqual({ source: 'hermes', requestId: 'r1' });
	});

	it('lists a phone\'s own prompt as an approval answered through the phone relay', () => {
		const [row] = approvalRequests(null, [
			{ id: 'abc-12345', tool: 'send_sms', description: 'Send a text message', arguments: { to: 'Grandma', body: 'On my way' }, created: NOW / 1000, phone: 'Pixel' }
		]);
		expect(row).toMatchObject({ id: 'phone:abc-12345', kind: 'approval', title: 'On Pixel: Send a text message', detail: 'to: Grandma, body: On my way', tool: 'send_sms' });
		expect(approvalSourceOf(row.id)).toEqual({ source: 'phone', requestId: 'abc-12345' });
	});
});

describe('goals', () => {
	it('is empty for no goal and a cleared one, and tells the goal from the judge', () => {
		expect(goalView(parseGoalText('No active goal.'))).toBeNull();
		expect(goalView(parseGoalText('✓ Goal cleared.'))).toBeNull();
		const set = goalView(parseGoalText('⊙ Goal set: keep the study at 21° (20-turn budget)'));
		expect(set).toMatchObject({ title: 'keep the study at 21°', state: 'running', turns: 20 });
		expect(set?.reason).toBeUndefined();
		const going = goalView(parseGoalText('↻ Goal continuing (3/20): the room is 19°'), 'keep the study at 21°');
		expect(going).toMatchObject({ title: 'keep the study at 21°', reason: 'the room is 19°', turn: 3, turns: 20, state: 'running' });
		expect(goalView(parseGoalText('⏸ Goal paused (5/20) — the heater failed twice'))?.state).toBe('held');
		expect(goalView(parseGoalText('✓ Goal achieved: both files exist'))?.state).toBe('done');
	});
});

describe('model', () => {
	const snap = (over: Partial<HermesSnapshot>): HermesSnapshot => ({
		status: 'connected',
		everConnected: true,
		ready: true,
		hud: hud({ provider: 'custom' }),
		report: { ok: true, model: { model: 'qwen3-30b', state: 'ready', loaded: ['qwen3-30b', 'embed-small'] } },
		statusError: null,
		warmingUp: false,
		model: 'qwen3-30b',
		sessionError: '',
		...over
	});

	it('strips the pictograph Hermes puts in front of a toolset label, and only that', () => {
		// The shapes tests/web/mock-hermes.mjs serves, which are Hermes' own labels.
		expect(plainLabel('🔍 Web Search & Scraping')).toBe('Web Search & Scraping');
		expect(plainLabel('💻 Terminal & Processes')).toBe('Terminal & Processes');
		expect(plainLabel('🏠 Home Assistant')).toBe('Home Assistant');
		expect(plainLabel('⚙️ Settings')).toBe('Settings');
		expect(plainLabel('👁️  Vision / Image Analysis')).toBe('Vision / Image Analysis');
		expect(plainLabel('Code Execution')).toBe('Code Execution');
		expect(modelView(snap({}), [{ name: 'web', label: '🔍 Web Search & Scraping', tools: [] }]).toolsets[0].label).toBe('Web Search & Scraping');
		// A label that is only a pictograph falls back to the name rather than to nothing.
		expect(modelView(snap({}), [{ name: 'x_search', label: '🐦', tools: [] }]).toolsets[0].label).toBe('x_search');
	});

	it('reads ready, starting and offline off the snapshot and applies the toolset lists', () => {
		expect(modelView(snap({}), [{ name: 'web', label: 'Web', tools: ['a', 'b'] }, { name: 'code', enabled: false }], { enabled: [], disabled: ['web'] })).toEqual({
			model: 'qwen3-30b',
			provider: 'custom',
			state: 'ready',
			progress: null,
			context: 'Also loaded: embed-small.',
			toolsets: [
				{ id: 'web', label: 'Web', enabled: false, tools: 2 },
				{ id: 'code', label: 'code', enabled: false, tools: 0 }
			]
		});
		expect(modelView(snap({ warmingUp: true })).state).toBe('starting');
		expect(modelView(snap({ status: 'reconnecting' })).state).toBe('offline');
		// The adapter answers while the model server is down (2026-10-06): the link is up,
		// and the panel said `ready` while every turn waited three minutes on a dead host.
		expect(modelView(snap({ report: { ok: true, model: { model: 'qwen3-30b', state: 'unreachable', loaded: [] } } })).state).toBe('offline');
		expect(modelView(null).model).toBe('No model yet');
	});
});

const task = (over: Partial<TaskRow> = {}): TaskRow => ({
	id: 'k1',
	kind: 'research',
	title: 'Warm the study',
	status: 'running',
	steps: [
		{ title: 'Read the thermostat', status: 'done' },
		{ title: 'Raise the setpoint', status: 'running', detail: 'to 21' }
	],
	created: NOW / 1000,
	updated: NOW / 1000,
	fraction: 0.5,
	done_steps: 1,
	total_steps: 2,
	finished: false,
	...over
});

describe('kanban and task', () => {
	it('files the six statuses into five columns and says why a task stopped', () => {
		const cards = kanbanTasks([
			task(),
			task({ id: 'k2', status: 'blocked', detail: 'waiting on an approval' }),
			task({ id: 'k3', status: 'error', error: 'the embedder was down', finished: true }),
			task({ id: 'k4', status: 'cancelled', finished: true }),
			task({ id: 'k5', status: 'queued', total_steps: 0, done_steps: 0, steps: [] })
		]);
		expect(cards.map((c) => [c.id, c.state])).toEqual([
			['k1', 'running'],
			['k2', 'held'],
			['k3', 'failed'],
			['k4', 'failed'],
			['k5', 'queued']
		]);
		expect(cards[0].detail).toMatch(/1 of 2|1\/2/);
		expect(cards[1].detail).toBe('waiting on an approval');
		expect(cards[2].detail).toBe('the embedder was down');
	});

	it('draws one task as a plan, its output and its own timeline', () => {
		const view = taskView(
			task(),
			[
				{ at: NOW / 1000, kind: 'status', text: 'running' },
				{ at: NOW / 1000 + 1, kind: 'tool', text: 'ha_get_state' },
				{ at: NOW / 1000 + 2, kind: 'output', text: 'The study is 17°.' }
			],
			{ repo: '', instruction: '', branch: '', plan: [], files_changed: [], diff_stat: '', diff: '--- a\n+++ b', checks: [], trail: [], rounds: 1, summary: 'Set to 21.' }
		);
		expect(view.title).toBe('Warm the study');
		expect(view.steps.map((s) => [s.label, s.state])).toEqual([
			['Read the thermostat', 'done'],
			['Raise the setpoint — to 21', 'running']
		]);
		expect(view.output).toBe('The study is 17°.\nSet to 21.');
		expect(view.diff).toBe('--- a\n+++ b');
		expect(view.calls.map((c) => c.name)).toEqual(['ha_get_state']);
	});
});

describe('house', () => {
	it('names, places and words each entity, switching only the switchable', () => {
		const devices = devicesView({
			states: [
				{ entity_id: 'light.kitchen', state: 'on', attributes: { friendly_name: 'Kitchen light' } },
				{ entity_id: 'sensor.study_t', state: '21.2', attributes: { unit_of_measurement: '°C' } },
				{ entity_id: 'cover.study', state: 'open', attributes: {} },
				{ entity_id: 'light.hidden', state: 'off', attributes: {} },
				{ entity_id: 'switch.gone', state: 'unavailable', attributes: {} }
			],
			entries: [
				{ entity_id: 'light.kitchen', area_id: 'kitchen' },
				{ entity_id: 'sensor.study_t', name: 'Study temperature', device_id: 'd1' },
				{ entity_id: 'light.hidden', hidden: true }
			],
			devices: [{ id: 'd1', name: 'Thermostat', area_id: 'study' }],
			areas: [
				{ id: 'kitchen', name: 'Kitchen' },
				{ area_id: 'study', name: 'Study' }
			]
		});
		expect(devices).toEqual([
			{ id: 'light.kitchen', name: 'Kitchen light', area: 'Kitchen', state: 'on', on: true },
			{ id: 'sensor.study_t', name: 'Study temperature', area: 'Study', state: '21.2 °C' },
			{ id: 'cover.study', name: 'cover.study', state: 'open' },
			{ id: 'switch.gone', name: 'switch.gone', state: 'unavailable' }
		]);
	});
});

describe('automations', () => {
	it('says what sets each one off in a sentence', () => {
		expect(describeTrigger({ platform: 'state', entity_id: 'binary_sensor.front_door', to: 'on' })).toBe('When the front door turns on');
		expect(describeTrigger({ platform: 'numeric_state', entity_id: 'sensor.study_t', below: 18 })).toBe('When the study t drops below 18');
		// A clock, not a stopwatch: Home Assistant's `07:00:00` loses its seconds.
		expect(describeTrigger({ platform: 'time', at: '07:00:00' })).toBe('Every day at 07:00');
		expect(describeTrigger({ platform: 'time', at: '07:00:30' })).toBe('Every day at 07:00:30');
		expect(describeTrigger({ platform: 'sun', event: 'sunset', offset: '-00:30' })).toBe('At sunset (-00:30)');
		expect(describeTrigger({ platform: 'frobnicate' })).toBe('On a frobnicate trigger');
		const rows = automationsView([
			{ id: 'a1', entity_id: 'automation.warm', alias: 'Warm the study', trigger: [{ platform: 'time', at: '07:00' }], condition: [], action: [], editable: true, enabled: true },
			{ id: 'a2', entity_id: 'automation.lock', alias: 'Lock up', trigger: [], condition: [], action: [], editable: false, enabled: false, needs_approval: true, reach: 'can lock' }
		]);
		expect(rows).toEqual([
			{ id: 'a1', title: 'Warm the study', when: 'Every day at 07:00', enabled: true, surface: 'house' },
			{ id: 'a2', title: 'Lock up', when: 'Run by hand · asks before it runs · can lock', enabled: false, surface: 'house' }
		]);
		// The entity's live state wins over the row: a turn_off reaches the switch
		// through state_changed before any list is re-read.
		const live = automationsView(
			[{ id: 'a1', entity_id: 'automation.warm', alias: 'Warm the study', trigger: [], condition: [], action: [], editable: true, enabled: true }],
			[{ entity_id: 'automation.warm', state: 'off', attributes: {} }]
		);
		expect(live[0].enabled).toBe(false);
	});
});

describe('jobs', () => {
	const scheduled = (over: Partial<ScheduledJob> = {}): ScheduledJob => ({
		id: 'brief',
		title: 'Morning briefing',
		kind: 'notify',
		when: { mode: 'daily', at: '07:00', days: [], minutes: 0 },
		describes: 'daily at 07:00',
		payload: {},
		enabled: true,
		next_at: NOW / 1000 + 240,
		last_at: NOW / 1000 - 86_400,
		last_result: 'told you: Good morning.',
		missed: 0,
		created: 0,
		source: 'console',
		editable: true,
		...over
	});

	it('lists both schedulers soonest first with two sentences each', () => {
		const rows = jobsView(
			[scheduled(), scheduled({ id: 'off', title: 'Paused one', enabled: false, next_at: null })],
			[{ id: 'reflect', name: 'Overnight reflection', next_run_at: new Date(NOW + 60_000).toISOString(), last_run_at: new Date(NOW - 3_600_000).toISOString(), last_status: 'ok', enabled: true }],
			NOW
		);
		expect(rows.map((r) => [r.id, r.state, r.next, r.last])).toEqual([
			['hermes:reflect', 'idle', 'in a minute', 'passed an hour ago'],
			['core:brief', 'idle', 'in 4 minutes', 'passed yesterday'],
			['core:off', 'paused', undefined, 'passed yesterday']
		]);
		expect(jobSourceOf('hermes:reflect')).toEqual({ source: 'hermes', jobId: 'reflect' });
	});
});

describe('memory and notes', () => {
	it('titles an entry by its first sentence and builds the graph from both lists', () => {
		const notes = memoryNotes([{ id: 'm1', text: 'The study runs cold. Below 8° it needs a degree more.', tags: ['study'], created: NOW / 1000 - 60, source: 'conversation', person: 'Ann' }], NOW);
		expect(notes[0]).toMatchObject({ id: 'm1', title: 'The study runs cold.', source: 'From a conversation about Ann', at: 'a minute ago' });
		expect(notes[0].body).toContain('Below 8°');
		const graph = memoryGraph([{ id: 'n1', title: 'Study', tags: ['study'] }], [{ id: 'm1', text: 'x', tags: ['study'], created: 0, source: '' }]);
		expect(graph.nodes.length).toBeGreaterThanOrEqual(2);
	});

	it('opens one note as a markdown document and lists the rest', () => {
		expect(noteDocument({ id: 'n1', title: 'Study', tags: ['house'], updated: '', body: '# Study\n\nCold.' })).toEqual({ kind: 'markdown', text: '# Study\n\nCold.', title: 'Study', path: '#house' });
		expect(noteDocument(null)).toBeNull();
		expect(noteItems([{ id: 'n1', title: 'Study', tags: [], updated: '', excerpt: 'Cold.' }])).toEqual([{ id: 'n1', title: 'Study', detail: 'Cold.' }]);
	});
});

describe('telemetry and notifications', () => {
	it('reads sensors as figures, percentages as meters, the sky as two readings', () => {
		expect(readingsView({ configured: true, readings: [{ entity_id: 'sensor.a', name: 'Humidity', value: 55.25, unit: '%', device_class: 'humidity', area: 'Hall', age_s: 1, available: true }, { entity_id: 'sensor.b', name: 'Gone', value: 1, unit: 'W', device_class: '', area: '', age_s: 9, available: false }] })).toEqual([
			{ id: 'sensor.a', label: 'Humidity (Hall)', value: 55.3, unit: '%', percent: 55.3 },
			{ id: 'sensor.b', label: 'Gone', value: null, unit: 'W' }
		]);
		// The area is dropped when the sensor's own name already says it, which
		// is how Home Assistant names most of them.
		expect(
			readingsView({
				configured: true,
				readings: [{ entity_id: 'sensor.c', name: 'Garage Humidity', value: 61, unit: '%', device_class: 'humidity', area: 'Garage', age_s: 1, available: true }]
			})[0].label
		).toBe('Garage Humidity');
		expect(skyView({ configured: true, satellite: 'ISS', now: '', moon: { state: 'waxing_gibbous', reason: '', illumination: 71, next_full: '', next_new: '' }, pass: null }).map((r) => r.label)).toEqual(['Moon, waxing gibbous']);
		expect(skyView({ configured: false, satellite: '', now: '', moon: null, pass: null })).toEqual([]);
		// A word state is shown as the word, not as a dash beside a working sensor.
		const words = readingsView({
			configured: true,
			readings: [
				{ entity_id: 'binary_sensor.up', name: 'Model Server Up', value: 'on', unit: '', device_class: '', area: '', age_s: 1, available: true },
				{ entity_id: 'sensor.gone', name: 'Gone', value: 'unavailable', unit: '', device_class: '', area: '', age_s: 1, available: false }
			]
		});
		expect(words.map((r) => r.text)).toEqual(['On', undefined]);
	});

	it('orders moments newest first and colours them by kind', () => {
		const rows = noticesView([{ id: 'a', kind: 'alert', title: 'Disk full', body: '', at: NOW / 1000 - 60, read: false }, { id: 'b', kind: 'reflection', title: 'A lesson', body: 'x', at: NOW / 1000, read: true }], NOW);
		expect(rows.map((r) => [r.id, r.level])).toEqual([
			['b', 'info'],
			['a', 'danger']
		]);
	});
});

describe('integrations', () => {
	it('folds the five lists into one, the source in the id', () => {
		const rows = integrationsView({
			tools: [{ name: 'ha_call_service', description: 'Call a service', tier: 3, editable: false }],
			skills: [{ name: 'read-pdf', description: 'Read a PDF', allowed_tools: [], metadata: {}, version: '1', resources: [], path: '', body_chars: 0 }],
			hermesSkills: [{ name: 'browse', category: 'web' }],
			mcp: [{ name: 'fs', transport: 'http', url: '', command: '', args: [], tier: 2, enabled: true, editable: true, has_token: false, connected: true, error: '', tools: [], tool_count: 3 }],
			extensions: [{ id: 'e1', kind: 'plugin', key: 'n8n', version: '2', enabled: false }],
			catalogue: [{ id: 'searxng', kind: 'plugin', installed: false }]
		});
		expect(rows.map((r) => [r.id, r.kind, r.installed])).toEqual([
			['tool:ha_call_service', 'plugin', true],
			['skill:read-pdf', 'skill', true],
			['hermes-skill:browse', 'skill', true],
			['mcp:fs', 'mcp', true],
			['extension:e1', 'extension', true],
			['catalogue:searxng', 'catalogue', false]
		]);
		expect(rows[0].detail).toBe('Call a service · asks before it runs');
		expect(rows[3].detail).toBe('3 tools, connected');
	});
});

describe('settings', () => {
	const schema = contract('hermes-settings.json');

	it('carries every schema group and row, settable for all but the leaves the compose file fixes', () => {
		const { groups, specs } = settingsCatalogue();
		expect(groups.map((g) => g.id)).toEqual(schema.groups.map((g: { id: string }) => g.id));
		expect(specs.map((s) => s.key)).toEqual(schema.settings.map((s: { key: string }) => s.key));
		// The plugin writes every leaf but the host's (ADR-0044); the contract's
		// own `settable` is the agent tool's allow-list and is not the panel's.
		const compose = schema.settings.filter((s: { applies: string }) => s.applies === 'compose').map((s: { key: string }) => s.key);
		expect(compose.length).toBeGreaterThan(20);
		expect(specs.filter((s) => !s.settable).map((s) => s.key)).toEqual(compose);
		const select = specs.find((s) => s.widget === 'select');
		expect(select?.options?.length).toBeGreaterThan(0);
		// The bounds and kinds the menu validates by come through.
		const turns = specs.find((s) => s.key === 'goals.max_turns');
		expect([turns?.type, turns?.min, turns?.max, turns?.applies]).toEqual(['int', 1, 200, 'now']);
	});

	it('reads each value from its own store and routes a write back to it', () => {
		const { specs } = settingsCatalogue([{ key: 'assistant.name', label: 'Its name', group: 'Assistant', type: 'string', apply: 'live', value: 'Jarvis', yaml_value: 'Jarvis', source: 'yaml' }]);
		const values = settingsValues(specs, {
			// Every schema leaf comes from the plugin's list, keyed by the schema key
			// whatever env name or Hermes key backs it.
			hermes: [
				{ key: 'model.default', value: 'qwen3-30b', source: 'env' },
				{ key: 'tts.engine', value: 'openai', source: 'override' },
				{ key: 'goals.max_turns', value: 20 },
				{ key: 'tool_loop_guardrails.hard_stop_enabled', value: true }
			],
			core: [{ key: 'assistant.name', label: 'Its name', group: 'Assistant', type: 'string', apply: 'live', value: 'Jarvis', yaml_value: '', source: 'yaml' }]
		});
		expect(values['model.default']).toBe('qwen3-30b');
		expect(values['tts.engine']).toBe('openai');
		expect(values['goals.max_turns']).toBe('20');
		expect(values['tool_loop_guardrails.hard_stop_enabled']).toBe(true);
		expect(values['core:assistant.name']).toBe('Jarvis');
		const by = new Map(specs.map((s) => [s.key, s]));
		expect(settingWrite(by.get('goals.max_turns')!, '30')).toEqual({ target: 'hermes', key: 'goals.max_turns', value: 30 });
		expect(settingWrite(by.get('tools.enabled')!, 'web, code')).toEqual({ target: 'hermes', key: 'tools.enabled', value: ['web', 'code'] });
		expect(settingWrite(by.get('tts.engine')!, 'openai')).toEqual({ target: 'hermes', key: 'tts.engine', value: 'openai' });
		// A leaf the compose file fixes has no write path from the console.
		expect(settingWrite(by.get('adapter.port')!, '9000')).toBeNull();
		expect(settingWrite(by.get('core:assistant.name')!, 'Vera')).toEqual({ target: 'core', key: 'assistant.name', value: 'Vera' });
	});
});

describe('audit, pairing and voice', () => {
	it('lists what ran and what was decided, newest first, with internal names', () => {
		const rows = auditView(hud({ turn: { ...initialHud().turn, tools: [tool({ startedAt: NOW - 1000, state: 'failed', error: 'boom' })] } }), [{ requestId: 'r1', tool: 'terminal', choice: 'deny', at: NOW }]);
		expect(rows.map((r) => [r.actor, r.action, r.outcome])).toEqual([
			['you', 'denied', 'refused'],
			['ha_call_service', 'ran', 'failed']
		]);
		expect(rows[1].at).toMatch(/^2026-09-03 /);
	});

	it('draws a live code as a QR and a grouped code, and nothing once it expired', () => {
		const live = pairingView({ url: 'http://jarvis.local:8199', code: '48129917', expiresAt: NOW / 1000 + 125, now: NOW });
		expect(live.code).toBe('4812 9917');
		expect(live.qr.startsWith('data:image/svg+xml')).toBe(true);
		expect(live.expires).toBe('Good for 2 minutes and 5 seconds.');
		// The desktop app's "Open in the desktop app" (ADR-0051) is the QR's own payload as a link.
		expect(live.link).toBe('jarvis://pair?v=1&u=http%3A%2F%2Fjarvis.local%3A8199&c=48129917');
		expect(pairingView({ url: 'x', code: '48129917', expiresAt: NOW / 1000 - 1, now: NOW })).toEqual({ qr: '', link: '', code: '', expires: '', secondsLeft: 0 });
	});

	it('reports only the stages that have both ends', () => {
		expect(voiceStages({ 'ptt-start': 0, 'ptt-end': 1200, submit: 1300, 'first-token': 2100 })).toEqual([
			{ key: 'listen', label: 'Listening', ms: 1200 },
			{ key: 'first-token', label: 'First token', ms: 800 }
		]);
	});
});

describe('content panels', () => {
	const examples = contract('hud-panels.json').examples as Record<string, Record<string, unknown>>;

	it('maps every contract example onto a body of the type the spec names', () => {
		for (const [kind, payload] of Object.entries(examples)) {
			const panel = panelFromShow({ id: `p-${kind}`, kind, title: kind, payload, origin: 'agent', created_at: NOW, rev: 1 }, NOW)!;
			const content = contentPanel(panel, ['https://n8n.example.ts.net']);
			expect(content.spec.id).toBe(`p-${kind}`);
			expect(content.spec.type in PANEL_TYPES, kind).toBe(true);
			expect(content.body.type, kind).toBe(content.spec.type);
		}
	});

	it('reads each payload into what its body draws', () => {
		const of = (kind: string, payload = examples[kind]) =>
			contentPanel(panelFromShow({ id: kind, kind, title: kind, payload, origin: 'agent', created_at: NOW, rev: 1 }, NOW)!, ['https://n8n.example.ts.net']).body;
		expect(of('text')).toMatchObject({ type: 'document', kind: 'markdown', text: 'The kitchen is **22 °C**.' });
		expect(of('code')).toMatchObject({ type: 'document', kind: 'code', text: "print('hi')", path: 'hi.py, python' });
		expect(of('table')).toMatchObject({ type: 'telemetry', readings: [{ id: 'row-0', label: 'GPU 0', value: 50, unit: '°C' }, { id: 'row-1', label: 'GPU 1', value: 50, unit: '°C' }] });
		expect(of('chart')).toMatchObject({ type: 'telemetry', readings: [{ id: 'series-0', label: 'load1', value: 0.9 }] });
		expect(of('telemetry')).toMatchObject({ type: 'telemetry', readings: [{ id: 'gpu0-util', value: 0 }, { id: 'gpu0-vram', value: 93.6, percent: 93.6 }, { id: 'gpu0-temp', value: 50 }, { id: 'gpu0-power', value: 22 }] });
		expect(of('image')).toMatchObject({ type: 'image', src: '/hermes/api/files/out/jarvis_00001_.png', alt: 'a red fox' });
		expect(of('camera')).toMatchObject({ type: 'image', alt: 'Garden still', caption: 'Garden' });
		expect(of('terminal')).toMatchObject({ type: 'task', title: '$ uptime', output: ' 00:41 up 4 days', steps: [{ label: 'exit 0', state: 'done' }] });
		expect(of('web')).toMatchObject({ type: 'browser', url: 'https://n8n.example.ts.net', title: 'n8n', embed: true });
		expect(of('web', { url: 'https://evil.example', title: 'x' })).toMatchObject({ embed: false });
		expect(of('device')).toMatchObject({ type: 'house', devices: [{ id: 'light.kitchen', name: 'Kitchen', state: 'on', on: true }] });
		expect(of('notice')).toMatchObject({ type: 'notifications', notices: [{ id: 'notice', title: 'notice', body: 'Model warmed up', level: 'info' }] });
		expect(of('goal', { text: '⊙ Goal set: keep the study warm' })).toMatchObject({ type: 'goals', goal: { title: 'keep the study warm' } });
		expect(of('approval', { request_id: 'r9', tool: 'terminal', command: 'rm -rf build', reason: 'cleaning up' })).toMatchObject({ type: 'approvals', requests: [{ id: 'hermes:r9', kind: 'approval', title: 'cleaning up', detail: 'rm -rf build', tool: 'terminal' }] });
		expect(of('clarify', { request_id: 'c9', question: 'Which one?', options: ['a', 'b'] })).toMatchObject({ type: 'approvals', requests: [{ id: 'hermes:c9', kind: 'clarify', title: 'Which one?', detail: 'Options: a, b' }] });
		expect(of('mystery', { any: 1 })).toMatchObject({ type: 'document', kind: 'code', path: 'mystery' });
	});

	it('plays a media panel from its artifact url, captioned with where it came from', () => {
		const of = (kind: string, payload = examples[kind]) =>
			contentPanel(panelFromShow({ id: kind, kind, title: kind, payload, origin: 'agent', created_at: NOW, rev: 1 }, NOW)!).body;
		expect(of('video')).toEqual({ type: 'video', src: examples.video.url, mime: 'video/mp4', title: 'The garden at dusk', caption: 'fetch_video · www.youtube.com' });
		expect(of('audio')).toEqual({ type: 'audio', src: examples.audio.url, mime: 'audio/mpeg', title: 'Episode 12: the cold study', caption: 'fetch_audio · example.org' });
		expect(of('document')).toEqual({ type: 'document', kind: 'file', url: examples.document.url, mime: 'application/pdf', title: 'Attention is all you need', caption: 'extract_page · arxiv.org', download: false });
		// The title falls back to the panel's; provenance may be partial or absent.
		expect(of('video', { url: '/hermes/api/artifacts/x', mime: 'video/webm' })).toMatchObject({ title: 'video', caption: '' });
		expect(of('audio', { url: '/a', mime: 'audio/ogg', provenance: { tool: 'text_to_speech' } })).toMatchObject({ caption: 'text_to_speech' });
		expect(of('document', { url: '/d', mime: 'text/markdown', provenance: { source_url: 'notes/study.md' } })).toMatchObject({ caption: 'notes/study.md' });
	});

	it('offers a download, not an embed, for what the adapter serves as an attachment', () => {
		// The one table both sides read: `download_mimes` is the adapter's EXECUTABLE_MIME.
		const contractMimes = contract('hud-panels.json').download_mimes as string[];
		expect([...DOWNLOAD_MIMES].sort()).toEqual([...contractMimes].sort());
		expect(servedAsDownload('text/html; charset=utf-8')).toBe(true);
		expect(servedAsDownload('application/pdf')).toBe(false);
		const of = (mime: string) =>
			contentPanel(panelFromShow({ id: 'd', kind: 'document', title: 'd', payload: { url: '/d', mime }, origin: 'agent', created_at: NOW, rev: 1 }, NOW)!).body;
		expect(of('image/svg+xml')).toMatchObject({ kind: 'file', download: true });
		expect(of('application/pdf')).toMatchObject({ kind: 'file', download: false });
	});

	it('keeps the emitter\'s id unless a shipped panel already holds it', () => {
		const chat = panelFromShow({ id: 'chat', kind: 'text', title: 'x', payload: {}, created_at: NOW, rev: 1 }, NOW)!;
		expect(contentPanel(chat).spec.id).toBe('chat:content');
	});

	it('orders the surface oldest first so a new panel lands at the end', () => {
		const a = panelFromShow({ id: 'a', kind: 'text', title: 'a', payload: {}, created_at: NOW + 5, rev: 1 }, NOW)!;
		const b = panelFromShow({ id: 'b', kind: 'text', title: 'b', payload: {}, created_at: NOW, rev: 1 }, NOW)!;
		expect(contentPanels([a, b]).map((p) => p.panelId)).toEqual(['b', 'a']);
	});
});
