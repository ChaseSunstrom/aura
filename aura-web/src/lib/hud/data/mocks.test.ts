// The data layer against the two mock backends the e2e suite runs on
// (`tests/web/mock-ha.mjs`, `tests/web/mock-hermes.mjs`), through the real
// clients.
//
// `mappings.test.ts` pins the arithmetic on hand fixtures. This is the other
// half: the mocks are what the console is tested against in a browser, and a
// mapping that reads a field the mock does not send (or the mock a field the
// real backend renamed) is a panel that renders blank in every e2e run while
// every unit test passes. Each case asks the mock for what a screen used to
// ask it for and checks the mapped rows have the words a body draws.

import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { WebSocket } from 'ws';

import { HermesClient } from '$lib/hermes/client';
import { JarvisClient } from '$lib/jarvisClient';
import { startMockHA, MOCK_TOKEN } from '../../../../../tests/web/mock-ha.mjs';
import { startMockHermes } from '../../../../../tests/web/mock-hermes.mjs';
import type { CronJob, Toolset } from '$lib/hermes/protocol';
import { parseSettingsOutput, type SettingsListItem } from '$lib/hermes/settings';
import { pluginArg } from '$lib/hermes/protocol';

import {
	approvalRequests,
	automationsView,
	devicesView,
	integrationsView,
	jobsView,
	kanbanTasks,
	memoryNotes,
	modelView,
	noteItems,
	noticesView,
	readingsView,
	settingsCatalogue,
	settingsValues,
	tokensView,
	type EnvVariable,
	type MemoryEntry,
	type NoteRow
} from './index';

type Mock = Awaited<ReturnType<typeof startMockHA>>;
type Hermes = Awaited<ReturnType<typeof startMockHermes>>;

let mock: Mock;
let hermes: Hermes;
let socket: WebSocket;
let client: JarvisClient;

/** An authenticated house socket, the way `connection.ts` opens one through the relay. */
async function openHouse(url: string): Promise<{ socket: WebSocket; client: JarvisClient }> {
	const ws = new WebSocket(`${url.replace(/^http/, 'ws')}/api/websocket`);
	const jarvis = new JarvisClient((data) => ws.send(data));
	await new Promise<void>((resolve, reject) => {
		ws.on('message', (data) => {
			const text = data.toString();
			const msg = JSON.parse(text);
			if (msg.type === 'auth_required') ws.send(JSON.stringify({ type: 'auth', access_token: MOCK_TOKEN }));
			else if (msg.type === 'auth_ok') resolve();
			else if (msg.type === 'auth_invalid') reject(new Error('auth refused'));
			else jarvis.handleMessage(text);
		});
		ws.on('error', reject);
	});
	return { socket: ws, client: jarvis };
}

beforeAll(async () => {
	mock = await startMockHA({});
	hermes = await startMockHermes({});
	({ socket, client } = await openHouse(mock.url));
}, 20_000);

afterAll(async () => {
	socket?.close();
	await mock?.close();
	await hermes?.close();
});

describe('the house socket', () => {
	it('gives the house panel named, placed devices with a switch where there is one', async () => {
		const [states, entries, areas, devices] = await Promise.all([client.getStates(), client.listEntities(), client.listAreas(), client.listDevices()]);
		const rows = devicesView({ states, entries, areas, devices });
		expect(rows.length).toBeGreaterThan(3);
		expect(rows.every((d) => d.id && d.name && d.state)).toBe(true);
		expect(rows.some((d) => d.on !== undefined)).toBe(true);
		expect(rows.some((d) => d.area)).toBe(true);
	});

	it('gives the Devices section a paired device, an older token and the approver switch (T-1386)', async () => {
		const rows = tokensView(await client.listTokens()) ?? [];
		expect(rows.map((r) => r.state).sort()).toEqual(['older', 'older', 'paired']);
		const phone = rows.find((r) => r.state === 'paired');
		expect(phone).toMatchObject({ approver: true, revocable: true });
		// The mock's console token has a socket open: the row that must not offer a revoke.
		expect(rows.find((r) => !r.revocable)?.state).toBe('older');
		// The mock house marks exactly one row as the caller's own (T-1407), and that row is the one without revoke.
		expect(rows.filter((r) => r.current).map((r) => [r.id, r.word, r.revocable])).toEqual([['tok-console', 'This console', false]]);
		expect(rows.filter((r) => r.revocable).length).toBe(rows.length - 1);

		await expect(client.updateToken(phone!.id, false)).resolves.toEqual({ id: phone!.id, approver: false });
		expect(tokensView(await client.listTokens())?.find((r) => r.id === phone!.id)?.approver).toBe(false);
		// The house takes a real boolean only, and says so for a token it does not hold.
		await expect(client.command({ type: 'config/token/update', token_id: phone!.id, approver: 'false' })).rejects.toMatchObject({ code: 'invalid_format' });
		await expect(client.updateToken('no-such-token', true)).rejects.toMatchObject({ code: 'not_found' });
		await client.updateToken(phone!.id, true);
	});

	it('gives the kanban every task with a state column', async () => {
		// The mock starts with no tasks; a held one is the shape the board draws
		// most of — a queued task with its steps.
		await client.command({ type: 'jarvis/test/task_run', title: 'Read twelve pages', hold: true });
		const rows = kanbanTasks(await client.listTasks());
		expect(rows.length).toBeGreaterThan(0);
		expect(rows.every((t) => ['queued', 'running', 'held', 'done', 'failed'].includes(t.state))).toBe(true);
	});

	it('gives the automations panel a sentence per rule', async () => {
		const rows = automationsView(await client.listAutomations());
		expect(rows.length).toBeGreaterThan(0);
		expect(rows.every((a) => a.title && a.when && !/^\s*$/.test(a.when))).toBe(true);
	});

	it('gives the jobs panel the scheduled jobs with a next run', async () => {
		const rows = jobsView(await client.listScheduled(), []);
		expect(rows.length).toBeGreaterThan(0);
		expect(rows.some((j) => j.next)).toBe(true);
	});

	it('gives the memory panel titled notes and the document panel a list', async () => {
		const memory = await client.command<{ entries: MemoryEntry[] }>({ type: 'jarvis/memory/list', query: '' });
		const notes = memoryNotes(memory.entries);
		expect(notes.length).toBeGreaterThan(0);
		// A title always; a body only when the memory says more than its first
	// sentence — one-sentence memories are most of them, and a body there is the
	// title printed twice.
	expect(notes.every((n) => n.title)).toBe(true);
	expect(notes.every((n) => !n.body || n.body !== n.title)).toBe(true);
		const listed = await client.command<{ notes: NoteRow[] }>({ type: 'jarvis/notes/list', query: '' });
		const items = noteItems(listed.notes);
		expect(items.length).toBeGreaterThan(0);
		expect(items.every((i) => i.id && i.title)).toBe(true);
	});

	it('gives the telemetry panel readings and the notifications panel moments', async () => {
		const readings = readingsView(await client.sensorReadings());
		expect(readings.length).toBeGreaterThan(0);
		expect(readings.every((r) => r.id && r.label)).toBe(true);
		const notices = noticesView(await client.listMoments(6));
		expect(notices.every((n) => n.id && n.title && n.level)).toBe(true);
	});

	it('gives the integrations panel the tools, skills and MCP servers as one list', async () => {
		const [tools, skills, mcp] = await Promise.all([client.listToolRows(), client.listSkills(), client.listMcpServers()]);
		const rows = integrationsView({ tools, skills: skills.skills, mcp: mcp.servers });
		expect(rows.filter((r) => r.kind === 'plugin').length).toBeGreaterThan(0);
		expect(rows.filter((r) => r.kind === 'mcp').length).toBeGreaterThan(0);
		expect(rows.every((r) => r.id.includes(':') && r.name)).toBe(true);
	});

	it('gives the approvals panel the held tools with a title a person can answer', async () => {
		const rows = approvalRequests(null, await client.pendingApprovals());
		expect(rows.every((r) => r.id.startsWith('core:') && r.title)).toBe(true);
	});

	it('gives the settings panel the core rows, and still serves the environment store the console no longer writes', async () => {
		// The env store stays on the house (other tools read it); the console's
		// settings rows come from the Hermes plugin now (ADR-0044), so only the
		// core rows are read from here.
		const env = await client.command<{ variables: EnvVariable[] }>({ type: 'jarvis/environment/list' });
		expect(env.variables.length).toBeGreaterThan(0);
		const core = (await client.listSettings()).settings;
		const { specs } = settingsCatalogue(core);
		const values = settingsValues(specs, { core });
		expect(core.length).toBeGreaterThan(0);
		expect(core.every((row) => values[`core:${row.key}`] !== undefined)).toBe(true);
	});
});

describe('the Hermes adapter', () => {
	it('gives the model panel the toolsets with their on/off lists applied', async () => {
		const res = await fetch(`${hermes.url}/v1/toolsets`, { headers: { authorization: `Bearer ${hermes.key}` } });
		const body = (await res.json()) as { data: Toolset[] };
		const view = modelView(null, body.data, { enabled: [], disabled: [body.data[0].name] });
		expect(view.toolsets.length).toBe(body.data.length);
		expect(view.toolsets[0].enabled).toBe(false);
	});

	it('gives the jobs panel the cron jobs beside the house\'s', async () => {
		const res = await fetch(`${hermes.url}/api/jobs`, { headers: { authorization: `Bearer ${hermes.key}` } });
		const body = (await res.json()) as { jobs?: CronJob[] } | CronJob[];
		const cron = Array.isArray(body) ? body : (body.jobs ?? []);
		expect(cron.length).toBeGreaterThan(0);
		const rows = jobsView([], cron);
		expect(rows.every((j) => j.id.startsWith('hermes:') && j.title)).toBe(true);
	});

	it('gives the settings panel the plugin\'s values through the same list the console reads', async () => {
		const gateway = new HermesClient({
			url: `${hermes.url.replace(/^http/, 'ws')}/ws?key=${hermes.key}`,
			openSocket: (target) => new WebSocket(target) as never
		});
		gateway.start();
		try {
			await new Promise<void>((resolve, reject) => {
				const stop = gateway.onStatus((status) => {
					if (status === 'connected') {
						stop();
						resolve();
					}
				});
				setTimeout(() => reject(new Error('the mock gateway never connected')), 5000);
			});
			const created = await gateway.request<{ session_id: string }>('session.create', {});
			const result = await gateway.request<{ output?: string }>('command.dispatch', {
				session_id: created.session_id,
				name: 'jsettings',
				arg: pluginArg(created.session_id, 'list')
			});
			const list = parseSettingsOutput<SettingsListItem[]>(result?.output);
			const { specs } = settingsCatalogue();
			const values = settingsValues(specs, { hermes: list });
			// The plugin lists every schema leaf, with its source and `applies`.
			expect(list.length).toBe(specs.length);
			expect(list.every((item) => item.source && item.applies)).toBe(true);
			// A key the plugin lists with no value is unset, and unset is drawn
			// as an empty field; every key it does value must reach the panel.
			const valued = list.filter((item) => item.value !== undefined && item.value !== null);
			expect(valued.length).toBeGreaterThan(0);
			expect(valued.every((item) => values[item.key] !== undefined)).toBe(true);
		} finally {
			gateway.stop();
		}
	});
});
