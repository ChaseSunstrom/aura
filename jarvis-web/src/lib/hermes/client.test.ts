import { describe, it, expect, vi } from 'vitest';
import { HermesClient, hermesStatusLabel, type SocketLike } from './client';
import { HermesRpcError } from './protocol';

/** A socket the test drives by hand. */
class FakeSocket implements SocketLike {
	readyState = 0;
	sent: any[] = [];
	onopen: ((ev: unknown) => void) | null = null;
	onclose: ((ev: { code?: number; reason?: string }) => void) | null = null;
	onerror: ((ev: unknown) => void) | null = null;
	onmessage: ((ev: { data: unknown }) => void) | null = null;
	constructor(public url: string) {}
	send(data: string): void {
		this.sent.push(JSON.parse(data));
	}
	close(): void {
		this.readyState = 3;
		this.onclose?.({ code: 1000 });
	}
	open(): void {
		this.readyState = 1;
		this.onopen?.(null);
	}
	receive(frame: unknown): void {
		this.onmessage?.({ data: JSON.stringify(frame) });
	}
	/** Answer the last request with a result. */
	reply(result: unknown): void {
		const last = this.sent[this.sent.length - 1];
		this.receive({ jsonrpc: '2.0', id: last.id, result });
	}
	fail(code: number, message: string): void {
		const last = this.sent[this.sent.length - 1];
		this.receive({ jsonrpc: '2.0', id: last.id, error: { code, message } });
	}
}

function harness() {
	const sockets: FakeSocket[] = [];
	const timers: Array<{ fn: () => void; ms: number }> = [];
	const client = new HermesClient({
		url: 'ws://test/hermes/ws',
		openSocket: (url) => {
			const s = new FakeSocket(url);
			sockets.push(s);
			return s;
		},
		setTimeoutFn: ((fn: () => void, ms: number) => {
			timers.push({ fn, ms });
			return timers.length as any;
		}) as any,
		clearTimeoutFn: (() => {}) as any,
		random: () => 0
	});
	return { client, sockets, timers, last: () => sockets[sockets.length - 1] };
}

describe('HermesClient', () => {
	it('dials the relay, reports connected on open, and hands events to listeners', () => {
		const { client, last } = harness();
		const statuses: string[] = [];
		client.onStatus((s) => statuses.push(s));
		const events: string[] = [];
		client.onEvent((e) => events.push(e.type));
		client.start();
		expect(last().url).toBe('ws://test/hermes/ws');
		expect(statuses).toEqual(['idle', 'connecting']);
		last().open();
		expect(client.status).toBe('connected');
		last().receive({ jsonrpc: '2.0', method: 'event', params: { type: 'gateway.ready', payload: {} } });
		expect(events).toEqual(['gateway.ready']);
	});

	it('numbers requests monotonically and settles each by id, in any order', async () => {
		const { client, last } = harness();
		client.start();
		last().open();
		const a = client.request('session.list');
		const b = client.request('session.create', {});
		expect(last().sent.map((f: any) => [f.id, f.method, f.jsonrpc])).toEqual([
			[1, 'session.list', '2.0'],
			[2, 'session.create', '2.0']
		]);
		last().receive({ jsonrpc: '2.0', id: 2, result: { session_id: 's' } });
		last().receive({ jsonrpc: '2.0', id: 1, result: { sessions: [] } });
		await expect(b).resolves.toEqual({ session_id: 's' });
		await expect(a).resolves.toEqual({ sessions: [] });
	});

	it('rejects a request with a HermesRpcError carrying the code', async () => {
		const { client, last } = harness();
		client.start();
		last().open();
		const p = client.request('command.dispatch', { name: 'jgoal' });
		last().fail(4018, 'not a quick/plugin/bundle/skill command: jgoal');
		await expect(p).rejects.toBeInstanceOf(HermesRpcError);
		await expect(p).rejects.toMatchObject({ code: 4018 });
	});

	it('rejects at once when there is no link, rather than hanging', async () => {
		const { client } = harness();
		await expect(client.request('session.list')).rejects.toThrow('no link');
	});

	it('rejects what was pending when the socket closes, then reconnects on the console’s schedule', async () => {
		const { client, sockets, timers, last } = harness();
		client.start();
		last().open();
		const p = client.request('session.list');
		last().close();
		await expect(p).rejects.toThrow('link closed');
		expect(client.status).toBe('reconnecting');
		expect(timers[0].ms).toBe(600);
		timers[0].fn();
		expect(sockets).toHaveLength(2);
		last().close();
		expect(timers[1].ms).toBe(1200);
		timers[1].fn();
		last().close();
		// Third failure in a row: offline.
		expect(client.status).toBe('offline');
		expect(timers[2].ms).toBe(2400);
		timers[2].fn();
		last().open();
		expect(client.status).toBe('connected');
	});

	it('stop() closes the socket, does not redial, and says idle', () => {
		const { client, sockets, timers, last } = harness();
		client.start();
		last().open();
		client.stop();
		expect(client.status).toBe('idle');
		expect(timers).toHaveLength(0);
		expect(sockets).toHaveLength(1);
	});

	it('ignores frames from a socket it has replaced', () => {
		const { client, sockets, timers, last } = harness();
		client.start();
		const first = last();
		first.open();
		first.close();
		timers[0].fn();
		expect(sockets).toHaveLength(2);
		const events: string[] = [];
		client.onEvent((e) => events.push(e.type));
		first.receive({ jsonrpc: '2.0', method: 'event', params: { type: 'stale' } });
		expect(events).toEqual([]);
	});
});

describe('dispatchCommand', () => {
	it('a `send` result is followed by prompt.submit of the message, and reports the notice', async () => {
		const { client, last } = harness();
		client.start();
		last().open();
		const outcome = client.dispatchCommand('s1', 'goal', 'tidy the notes');
		await Promise.resolve();
		expect(last().sent[0]).toMatchObject({ method: 'command.dispatch', params: { session_id: 's1', name: 'goal', arg: 'tidy the notes' } });
		last().reply({ type: 'send', notice: '⊙ Goal set (20-turn budget): tidy the notes', message: 'Continue toward the goal' });
		await vi.waitFor(() => expect(last().sent).toHaveLength(2));
		expect(last().sent[1]).toMatchObject({ method: 'prompt.submit', params: { session_id: 's1', text: 'Continue toward the goal' } });
		last().reply({ status: 'streaming' });
		await expect(outcome).resolves.toEqual({ kind: 'sent', notice: '⊙ Goal set (20-turn budget): tidy the notes', message: 'Continue toward the goal' });
	});

	it('a `plugin` result is output to show, like exec', async () => {
		const { client, last } = harness();
		client.start();
		last().open();
		const outcome = client.dispatchCommand('s1', 'jgoal', '@s1 gate add npm test');
		await Promise.resolve();
		expect(last().sent[0].params).toEqual({ session_id: 's1', name: 'jgoal', arg: '@s1 gate add npm test' });
		last().reply({ type: 'plugin', output: '✓ Gate 1 added: npm test' });
		await expect(outcome).resolves.toEqual({ kind: 'output', output: '✓ Gate 1 added: npm test' });
	});

	it('an `exec` result is output to show', async () => {
		const { client, last } = harness();
		client.start();
		last().open();
		const outcome = client.dispatchCommand('s1', 'goal', 'status');
		await Promise.resolve();
		last().reply({ type: 'exec', output: 'No active goal. Set one with /goal <text>.' });
		await expect(outcome).resolves.toEqual({ kind: 'output', output: 'No active goal. Set one with /goal <text>.' });
		expect(last().sent).toHaveLength(1);
	});

	it('a `prefill` result is text for the composer and nothing is sent', async () => {
		const { client, last } = harness();
		client.start();
		last().open();
		const outcome = client.dispatchCommand('s1', 'prompt', 'draft');
		await Promise.resolve();
		last().reply({ type: 'prefill', message: 'draft this' });
		await expect(outcome).resolves.toEqual({ kind: 'prefill', message: 'draft this' });
		expect(last().sent).toHaveLength(1);
	});

	it('a JSON-RPC error and an `error` result both come back as an error to show, with the code', async () => {
		const { client, last } = harness();
		client.start();
		last().open();
		const a = client.dispatchCommand('s1', 'jgoal', 'show');
		await Promise.resolve();
		last().fail(4018, 'not a quick/plugin/bundle/skill command: jgoal');
		await expect(a).resolves.toEqual({ kind: 'error', message: 'not a quick/plugin/bundle/skill command: jgoal', code: 4018 });
		const b = client.dispatchCommand('s1', 'goal', 'x');
		await Promise.resolve();
		last().reply({ type: 'error', message: 'goal loop is off' });
		await expect(b).resolves.toEqual({ kind: 'error', message: 'goal loop is off' });
	});
});

describe('hermesStatusLabel', () => {
	it('gives every status a distinct word', () => {
		const labels = (['idle', 'connecting', 'connected', 'reconnecting', 'offline'] as const).map(hermesStatusLabel);
		expect(new Set(labels).size).toBe(5);
	});
});
