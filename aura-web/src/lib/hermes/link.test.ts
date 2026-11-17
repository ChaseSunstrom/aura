import { describe, it, expect, beforeEach } from 'vitest';
import { resetIds } from '$lib/chat';
import { HermesClient, SURFACE, type SocketLike } from './client';
import { HermesLink, SESSION_KEY } from './link';
import { StatusPoller } from './status';

class FakeSocket implements SocketLike {
	readyState = 0;
	sent: any[] = [];
	onopen: ((ev: unknown) => void) | null = null;
	onclose: ((ev: { code?: number; reason?: string }) => void) | null = null;
	onerror: ((ev: unknown) => void) | null = null;
	onmessage: ((ev: { data: unknown }) => void) | null = null;
	send(data: string): void {
		const frame = JSON.parse(data);
		this.sent.push(frame);
		this.answer?.(frame);
	}
	close(): void {
		this.readyState = 3;
		this.onclose?.({});
	}
	open(): void {
		this.readyState = 1;
		this.onopen?.(null);
	}
	receive(frame: unknown): void {
		this.onmessage?.({ data: JSON.stringify(frame) });
	}
	event(type: string, session_id: string, payload: Record<string, unknown> = {}): void {
		this.receive({ jsonrpc: '2.0', method: 'event', params: { type, session_id, payload } });
	}
	/** A scripted backend: answer each request as it is sent. */
	answer: ((frame: any) => void) | null = null;
	reply(frame: any, result: unknown): void {
		queueMicrotask(() => this.receive({ jsonrpc: '2.0', id: frame.id, result }));
	}
	fail(frame: any, code: number, message: string): void {
		queueMicrotask(() => this.receive({ jsonrpc: '2.0', id: frame.id, error: { code, message } }));
	}
}

function memoryStorage() {
	const map = new Map<string, string>();
	return {
		getItem: (k: string) => map.get(k) ?? null,
		setItem: (k: string, v: string) => void map.set(k, v),
		removeItem: (k: string) => void map.delete(k),
		map
	};
}

function harness(script?: (socket: FakeSocket, frame: any) => void) {
	const sockets: FakeSocket[] = [];
	const client = new HermesClient({
		url: 'ws://t/hermes/ws',
		openSocket: () => {
			const s = new FakeSocket();
			if (script) s.answer = (frame) => script(s, frame);
			sockets.push(s);
			return s;
		},
		// A retry timer that fires on the next microtask, so a drop redials
		// without the test having to drive a clock.
		setTimeoutFn: ((fn: () => void) => {
			queueMicrotask(fn);
			return 1 as any;
		}) as any,
		clearTimeoutFn: (() => {}) as any,
		random: () => 0
	});
	const poller = new StatusPoller({
		fetchFn: (async () => ({ ok: true, status: 200, json: async () => ({ ok: true, model: { model: 'm-status', state: 'ready' } }) })) as any,
		setTimeoutFn: (() => 0) as any,
		clearTimeoutFn: (() => {}) as any
	});
	const storage = memoryStorage();
	const link = new HermesLink({ client, poller, storage, now: () => 1000 });
	return { link, sockets, storage, last: () => sockets[sockets.length - 1] };
}

const settle = () => new Promise((r) => setTimeout(r, 5));

/** The usual backend: creates and resumes sessions, streams a reply to a prompt. */
function backend(socket: FakeSocket, frame: any) {
	switch (frame.method) {
		case 'session.create':
			socket.reply(frame, { session_id: 'live-1', stored_session_id: 'stored-1', messages: [], info: { model: 'qwen' } });
			break;
		case 'session.resume':
			if (frame.params.session_id === 'stored-old') {
				socket.reply(frame, {
					session_id: 'live-old',
					messages: [{ role: 'user', text: 'earlier' }, { role: 'assistant', text: 'yes' }],
					info: { model: 'qwen', title: 'Earlier' }
				});
			} else socket.fail(frame, 4001, 'session not found');
			break;
		case 'prompt.submit':
			socket.reply(frame, { status: 'streaming' });
			break;
		case 'image.attach_bytes':
			if (frame.params.content_base64 === 'not-an-image') socket.fail(frame, 4017, 'data is not valid base64');
			else socket.reply(frame, { attached: true, path: '/images/upload_1.jpg', count: 1 });
			break;
		case 'command.dispatch':
			if (frame.params.name === 'goal' && frame.params.arg === 'status') {
				socket.reply(frame, { type: 'exec', output: 'No active goal.' });
			} else if (frame.params.name === 'goal') {
				socket.reply(frame, { type: 'send', notice: `⊙ Goal set (20-turn budget): ${frame.params.arg}`, message: `Continue: ${frame.params.arg}` });
			} else if (frame.params.name === 'jgoal' && String(frame.params.arg).startsWith('@live-1 ')) {
				socket.reply(frame, { type: 'plugin', output: `plugin: ${String(frame.params.arg).slice(8)}` });
			} else socket.fail(frame, 4018, `not a quick/plugin/bundle/skill command: ${frame.params.name}`);
			break;
		case 'approval.respond':
		case 'clarify.respond':
		case 'session.interrupt':
			socket.reply(frame, { ok: true });
			break;
		case 'subagent.interrupt':
			socket.reply(frame, { found: true, subagent_id: frame.params.subagent_id });
			break;
		case 'session.list':
			socket.reply(frame, { sessions: [{ id: 'stored-old', title: 'Earlier' }] });
			break;
		default:
			socket.fail(frame, -32601, `unknown method: ${frame.method}`);
	}
}

beforeEach(() => resetIds());

describe('HermesLink', () => {
	it('creates a session on connect, remembers its stored id, and is ready', async () => {
		const { link, last, storage } = harness(backend);
		const seen: string[] = [];
		link.subscribe((s) => seen.push(`${s.status}:${s.ready}`));
		link.start();
		last().open();
		await settle();
		expect(link.current.ready).toBe(true);
		expect(link.current.hud.sessionId).toBe('live-1');
		expect(link.current.model).toBe('qwen');
		expect(storage.map.get(SESSION_KEY)).toBe('stored-1');
		expect(seen[seen.length - 1]).toBe('connected:true');
	});

	it("notifies nobody for another session's event, and does for its own", async () => {
		// Every event used to notify every subscriber, so each of another
		// session's events re-derived the whole HUD for nothing (2026-09-23).
		const { link, last } = harness(backend);
		link.start();
		last().open();
		await settle();
		let calls = 0;
		link.subscribe(() => calls++);
		const base = calls;
		last().event('hud.panel.show', 'live-other', { id: 'p1', kind: 'text', title: 'theirs', payload: { text: 'x' } });
		expect(calls).toBe(base);
		expect(link.current.hud.panels).toHaveLength(0);
		last().event('hud.panel.show', 'live-1', { id: 'p1', kind: 'text', title: 'ours', payload: { text: 'x' } });
		expect(calls).toBe(base + 1);
		expect(link.current.hud.panels.map((p) => p.title)).toEqual(['ours']);
	});

	it('resumes the remembered session with its transcript, and starts fresh when it is gone', async () => {
		const { link, last, storage } = harness(backend);
		storage.setItem(SESSION_KEY, 'stored-old');
		link.start();
		last().open();
		await settle();
		expect(link.current.hud.sessionId).toBe('live-old');
		expect(link.current.hud.messages.map((m) => m.content)).toEqual(['earlier', 'yes']);
		expect(link.current.hud.title).toBe('Earlier');

		storage.setItem(SESSION_KEY, 'stored-gone');
		await link.openSession('stored-gone');
		await settle();
		expect(link.current.hud.sessionId).toBe('live-1');
		expect(link.current.sessionError).toContain('stored-gone');
		expect(storage.map.get(SESSION_KEY)).toBe('stored-1');
	});

	it('starts fresh without a word when the remembered session is gone (T-0886)', async () => {
		// Hermes writes a session at its first message: one opened and left unspoken is gone
		// after its socket closes, and every open after it said "That conversation is not on
		// this Jarvis any more".
		const { link, last, storage } = harness(backend);
		storage.setItem(SESSION_KEY, 'stored-gone');
		link.start();
		last().open();
		await settle();
		expect(link.current.hud.sessionId).toBe('live-1');
		expect(link.current.sessionError).toBe('');
		expect(storage.map.get(SESSION_KEY)).toBe('stored-1');
	});

	it('submits a prompt as a turn and streams the events into the transcript', async () => {
		const { link, last } = harness(backend);
		link.start();
		last().open();
		await settle();
		await link.submit('turn on the lab lights');
		expect(last().sent.find((f) => f.method === 'prompt.submit').params).toEqual({ session_id: 'live-1', text: 'turn on the lab lights', surface: SURFACE });
		expect(link.current.hud.turn.running).toBe(true);
		last().event('message.delta', 'live-1', { text: 'On.' });
		last().event('message.complete', 'live-1', { text: 'On.' });
		expect(link.current.hud.turn.running).toBe(false);
		expect(link.current.hud.messages.map((m) => m.content)).toEqual(['turn on the lab lights', 'On.']);
	});

	it('says a turn was spoken with voice:true, and a typed one never does (T-0644)', async () => {
		const { link, last } = harness(backend);
		link.start();
		last().open();
		await settle();
		await link.submit('lights off', null, null, true);
		await link.submit('lights off');
		const prompts = last().sent.filter((f) => f.method === 'prompt.submit');
		expect(prompts[0].params).toEqual({ session_id: 'live-1', text: 'lights off', surface: SURFACE, voice: true });
		expect('voice' in prompts[1].params).toBe(false);
	});

	it('attaches a photo to the session before the prompt that asks about it', async () => {
		// Hermes queues an attached image for the next prompt.submit on that session
		// and has no detach, so the photo goes only at the moment the words do.
		const { link, last } = harness(backend);
		link.start();
		last().open();
		await settle();
		await link.submit('what is this plant', null, 'aGVsbG8=');
		const methods = last().sent.map((f) => f.method);
		expect(methods.indexOf('image.attach_bytes')).toBeLessThan(methods.indexOf('prompt.submit'));
		expect(last().sent.find((f) => f.method === 'image.attach_bytes').params).toEqual({ session_id: 'live-1', content_base64: 'aGVsbG8=', ext: 'jpg' });
		expect(link.current.hud.turn.running).toBe(true);
	});

	it('fails the turn and submits nothing when the photo is refused', async () => {
		// A prompt sent without its photo would be answered about nothing.
		const { link, last } = harness(backend);
		link.start();
		last().open();
		await settle();
		const outcome = await link.submit('what is this plant', null, 'not-an-image');
		expect(outcome).toMatchObject({ kind: 'error' });
		expect(last().sent.map((f) => f.method)).not.toContain('prompt.submit');
		expect(link.current.hud.turn.running).toBe(false);
		expect(link.current.hud.turn.error).toContain('base64');
	});

	it('dispatches a goal phrase as /goal, and the send result becomes the turn', async () => {
		const { link, last } = harness(backend);
		link.start();
		last().open();
		await settle();
		const outcome = await link.submit('keep working on the notes until they are tidy');
		expect(outcome).toMatchObject({ kind: 'sent', notice: '⊙ Goal set (20-turn budget): keep working on the notes until they are tidy' });
		const methods = last().sent.map((f) => f.method);
		expect(methods).toContain('command.dispatch');
		expect(methods).toContain('prompt.submit');
		expect(methods).not.toContain('prompt.submit:twice');
		expect(link.current.hud.messages[0].content).toBe('Continue: keep working on the notes until they are tidy');
	});

	it('reports a missing plugin command by its code, and an exec result as output', async () => {
		const { link, last } = harness(backend);
		link.start();
		last().open();
		await settle();
		expect(await link.dispatch('subgoal', 'x')).toEqual({ kind: 'error', message: 'not a quick/plugin/bundle/skill command: subgoal', code: 4018 });
		expect(await link.dispatch('goal', 'status')).toEqual({ kind: 'output', output: 'No active goal.' });
		// A plugin verb carries the session in its arg, and its `plugin` reply is output.
		expect(await link.dispatchPlugin('subgoal add a test')).toEqual({ kind: 'output', output: 'plugin: subgoal add a test' });
		expect(last().sent.find((f) => f.method === 'command.dispatch' && f.params.name === 'jgoal').params.arg).toBe('@live-1 subgoal add a test');
	});

	it('a plugin verb asked the moment the link opens waits for the session instead of failing (T-0570)', async () => {
		const { link, last } = harness(backend);
		link.start();
		last().open();
		// The Approvals screen asks the guard on open, before the attach has landed; it got "no session".
		const early = link.dispatchPlugin('list');
		await settle();
		expect(await early).toEqual({ kind: 'output', output: 'plugin: list' });
		expect(last().sent.find((f) => f.method === 'command.dispatch').params.arg).toBe('@live-1 list');
	});

	it('answers an approval on the session that raised it and drops it from the queue', async () => {
		const { link, last } = harness(backend);
		link.start();
		last().open();
		await settle();
		last().event('approval.request', 'live-1', { request_id: 'r1', tool: 'terminal', command: 'rm x', choices: ['once', 'deny'] });
		expect(link.current.hud.approvals).toHaveLength(1);
		await link.respondApproval('r1', 'once');
		expect(last().sent.find((f) => f.method === 'approval.respond').params).toEqual({ session_id: 'live-1', request_id: 'r1', choice: 'once' });
		expect(link.current.hud.approvals).toEqual([]);
		last().event('clarify.request', 'live-1', { request_id: 'q1', question: 'which?', options: ['a', 'b'] });
		await link.respondClarify('q1', 'a');
		expect(last().sent.find((f) => f.method === 'clarify.respond').params).toEqual({ session_id: 'live-1', request_id: 'q1', answer: 'a' });
		expect(link.current.hud.clarifies).toEqual([]);
	});

	it('takes over an activity: stopped here at once, the turn interrupted, the late end ignored', async () => {
		const { link, last } = harness(backend);
		link.start();
		last().open();
		await settle();
		last().event('activity.start', 'live-1', { id: 'a1', surface: 'browser', tool: 'browser_navigate', intent: 'open the login', started_at: 't0' });
		expect(link.current.hud.activity?.status).toBe('running');
		await link.takeOver();
		expect(link.current.hud.activity?.status).toBe('stopped');
		expect(last().sent.find((f) => f.method === 'session.interrupt').params).toEqual({ session_id: 'live-1' });
		last().event('activity.end', 'live-1', { id: 'a1', steps: 1, status: 'done', ended_at: 't1' });
		expect(link.current.hud.activity?.status).toBe('stopped');
	});

	it('stops one subagent by its id and leaves the session running', async () => {
		const { link, last } = harness(backend);
		link.start();
		last().open();
		await settle();
		last().event('subagent.start', 'live-1', { subagent_id: 'sa-1', goal: 'pack the lab' });
		last().event('subagent.start', 'live-1', { subagent_id: 'sa-2', goal: 'pack the study' });
		expect(await link.interruptSubagent('sa-1')).toEqual({ found: true, subagent_id: 'sa-1' });
		expect(last().sent.find((f) => f.method === 'subagent.interrupt').params).toEqual({ subagent_id: 'sa-1' });
		expect(last().sent.some((f) => f.method === 'session.interrupt')).toBe(false);
		// The stop lands as Hermes reports it: that child's own complete, nobody else's.
		last().event('subagent.complete', 'live-1', { subagent_id: 'sa-1', status: 'interrupted' });
		expect(link.current.hud.background.map((r) => r.state)).toEqual(['interrupted', 'running']);
	});

	it('goes offline when the socket drops, and re-attaches the session when it is back', async () => {
		const { link, sockets, last } = harness(backend);
		link.start();
		last().open();
		await settle();
		last().close();
		expect(link.current.status).toBe('reconnecting');
		expect(link.current.everConnected).toBe(true);
		// The shell's data-session-ready reads this: false after a drop, and still false
		// when the socket is back but the session is not yet resumed (T-0525).
		expect(link.current.ready).toBe(false);
		await settle();
		// The client dialled again (the retry timer is a microtask in this harness).
		expect(sockets).toHaveLength(2);
		last().open();
		expect(link.current.ready).toBe(false);
		await settle();
		expect(link.current.ready).toBe(true);
		expect(link.current.status).toBe('connected');
		expect(last().sent[0].method).toBe('session.resume');
		expect(link.current.hud.sessionId).toBe('live-1');
	});

	// AUDIT B5: a prompt typed during a redial failed at once ("no link to Hermes")
	// although the link was seconds from back; and the old live id is not the new one.
	it('holds a prompt typed while the link redials and sends it on the re-attached session', async () => {
		const { link, sockets, last } = harness((s, f) =>
			f.method === 'session.resume' ? s.reply(f, { session_id: 'live-2', stored_session_id: 'stored-1', messages: [] }) : backend(s, f)
		);
		link.start();
		last().open();
		await settle();
		last().close();
		expect(link.current.status).toBe('reconnecting');
		const sent = link.submit('lights off');
		await settle();
		expect(sockets).toHaveLength(2);
		last().open();
		expect(await sent).toBeNull();
		expect(last().sent.find((f) => f.method === 'prompt.submit')?.params).toMatchObject({ session_id: 'live-2', text: 'lights off' });
	});

	// T-1192: a re-attach that failed left hud.sessionId as the dead socket's id, and a
	// prompt went out on it.
	it('attaches again, and never sends on the old id, when the re-attach after a redial failed', async () => {
		let refuse = false;
		const { link, sockets, last } = harness((s, f) => {
			if (refuse && (f.method === 'session.resume' || f.method === 'session.create')) s.fail(f, 5000, 'busy');
			// The failed resume forgot the stored id, so the next attach creates.
			else if (f.method === 'session.create' && s !== sockets[0]) s.reply(f, { session_id: 'live-2', stored_session_id: 'stored-2', messages: [] });
			else backend(s, f);
		});
		link.start();
		last().open();
		await settle();
		expect(link.current.hud.sessionId).toBe('live-1');
		refuse = true;
		last().close();
		await settle();
		last().open();
		await settle();
		refuse = false;
		expect(await link.submit('lights off')).toBeNull();
		expect(last().sent.find((f) => f.method === 'prompt.submit')?.params).toMatchObject({ session_id: 'live-2' });
	});

	it('still fails a prompt at once once the link has given up', async () => {
		const { link, last } = harness(backend);
		link.start();
		last().open();
		await settle();
		// No settle after the last drop: this harness redials on the next microtask.
		for (let i = 0; i < 3; i++) {
			if (i) await settle();
			last().close();
		}
		expect(link.current.status).toBe('offline');
		expect(await link.submit('lights off')).toEqual({ kind: 'error', message: 'no link to Hermes' });
	});

	it('takes the model from the status report until a session says otherwise', async () => {
		const { link } = harness(backend);
		link.start();
		await settle();
		expect(link.current.model).toBe('m-status');
		expect(link.current.warmingUp).toBe(false);
	});

	it('a new session forgets the old one and clears the transcript', async () => {
		const { link, last, storage } = harness(backend);
		storage.setItem(SESSION_KEY, 'stored-old');
		link.start();
		last().open();
		await settle();
		expect(link.current.hud.messages).toHaveLength(2);
		await link.newSession();
		await settle();
		expect(link.current.hud.messages).toEqual([]);
		expect(link.current.hud.sessionId).toBe('live-1');
		expect(storage.map.get(SESSION_KEY)).toBe('stored-1');
	});
});
