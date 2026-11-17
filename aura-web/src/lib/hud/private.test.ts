import { readFileSync } from 'node:fs';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { ChatMessage } from '$lib/chat';
import { MAX_CHARS, MAX_TURNS, PrivateChat, turnsFor } from './private.svelte';

// Private chats (T-0462): the owner asked for "ephemeral or incognito chats, that won't be
// logged or stored absolutely anywhere". The console's half keeps the conversation in this
// page's memory only and sends it, whole, to the adapter's private route each turn.

const line = (role: 'user' | 'assistant', content: string, extra: Partial<ChatMessage> = {}): ChatMessage => ({
	id: `${role}-${content}`,
	role,
	content,
	thinking: '',
	tools: [],
	at: 0,
	pending: false,
	...extra
});

function sse(...events: string[]): Response {
	const body = new ReadableStream<Uint8Array>({
		start(c) {
			const enc = new TextEncoder();
			// Split mid-line, as a network does, so the reader must buffer.
			const all = events.map((e) => `data: ${e}\n\n`).join('');
			c.enqueue(enc.encode(all.slice(0, 7)));
			c.enqueue(enc.encode(all.slice(7)));
			c.close();
		}
	});
	return new Response(body, { status: 200, headers: { 'content-type': 'text/event-stream' } });
}

afterEach(() => vi.unstubAllGlobals());

describe('the turns a private chat sends', () => {
	it("are trimmed to the bounds the adapter refuses past (tests/contracts/private_chat.json)", () => {
		const bounds = JSON.parse(readFileSync(new URL('../../../../tests/contracts/private_chat.json', import.meta.url), 'utf8'));
		expect({ max_turns: MAX_TURNS, max_chars: MAX_CHARS }).toEqual({ max_turns: bounds.max_turns, max_chars: bounds.max_chars });
	});

	it('are the person and Jarvis only, without failed or unfinished lines', () => {
		const turns = turnsFor([
			line('user', 'one'),
			line('assistant', 'broken', { error: 'the model stopped answering' }),
			line('assistant', ''),
			line('user', 'two'),
			line('assistant', 'half', { pending: true })
		]);
		expect(turns).toEqual([
			{ role: 'user', content: 'one' },
			{ role: 'user', content: 'two' }
		]);
	});

	it("keep the newest lines inside the adapter's bounds", () => {
		const many = Array.from({ length: MAX_TURNS + 10 }, (_, i) => line(i % 2 ? 'assistant' : 'user', `m${i}`));
		many.push(line('user', 'last'));
		const turns = turnsFor(many);
		expect(turns).toHaveLength(MAX_TURNS);
		expect(turns.at(-1)).toEqual({ role: 'user', content: 'last' });
		const big = turnsFor([line('user', 'x'.repeat(MAX_CHARS - 10)), line('assistant', 'y'.repeat(20)), line('user', 'z'.repeat(5))]);
		expect(big).toEqual([
			{ role: 'assistant', content: 'y'.repeat(20) },
			{ role: 'user', content: 'zzzzz' }
		]);
	});
});

describe('a private chat', () => {
	it('runs on the model picked for it, kept when the conversation is forgotten', async () => {
		const bodies: unknown[] = [];
		const fetcher = vi.fn(async (_url: string, init: RequestInit) => {
			bodies.push(JSON.parse(String(init.body)));
			return sse('{"content":"Hi."}', '[DONE]');
		});
		const chat = new PrivateChat(fetcher as unknown as typeof fetch);
		chat.model = 'qwen3.8-27b-uncensored';
		chat.set(true);
		await chat.ask('Hello');
		expect(bodies[0]).toEqual({ messages: [{ role: 'user', content: 'Hello' }], model: 'qwen3.8-27b-uncensored' });
	});

	it('streams the reply into memory, sending only the conversation', async () => {
		const calls: { url: string; init: RequestInit }[] = [];
		const fetcher = vi.fn(async (url: string, init: RequestInit) => {
			calls.push({ url, init });
			return sse('{"content":"Good "}', '{"content":"evening."}', '[DONE]');
		});
		const chat = new PrivateChat(fetcher as unknown as typeof fetch);
		chat.set(true);
		await chat.ask('  Hello there  ');
		expect(calls).toHaveLength(1);
		expect(calls[0].url).toBe('/hermes/api/private/chat');
		expect(JSON.parse(String(calls[0].init.body))).toEqual({ messages: [{ role: 'user', content: 'Hello there' }] });
		expect(calls[0].init.cache).toBe('no-store');
		expect(chat.messages.map((m) => [m.role, m.content, m.pending])).toEqual([
			['user', 'Hello there', false],
			['assistant', 'Good evening.', false]
		]);
		expect(chat.busy).toBe(false);
		expect(chat.turn).toMatchObject({ running: false, gotToken: true, text: 'Good evening.', error: null });
		expect(chat.turn.finishedAt).not.toBeNull();

		await chat.ask('And you?');
		expect(JSON.parse(String(calls[1].init.body)).messages).toEqual([
			{ role: 'user', content: 'Hello there' },
			{ role: 'assistant', content: 'Good evening.' },
			{ role: 'user', content: 'And you?' }
		]);
	});

	it("shows the adapter's reason when it refuses, and a stream that broke", async () => {
		const refused = new PrivateChat((async () =>
			new Response(JSON.stringify({ detail: 'the model is unreachable: ConnectError' }), { status: 503 })) as unknown as typeof fetch);
		refused.set(true);
		await refused.ask('hi');
		expect(refused.messages.at(-1)?.error).toBe('the model is unreachable: ConnectError');
		expect(refused.turn.error).toBe('the model is unreachable: ConnectError');

		const broke = new PrivateChat((async () => sse('{"content":"Par"}', '{"error":"the model stopped answering"}')) as unknown as typeof fetch);
		broke.set(true);
		await broke.ask('hi');
		expect(broke.messages.at(-1)).toMatchObject({ content: 'Par', pending: false, error: 'the model stopped answering' });
	});

	it('forgets everything when switched off, and asks nothing while off', async () => {
		const fetcher = vi.fn(async () => sse('{"content":"ok"}', '[DONE]'));
		const chat = new PrivateChat(fetcher as unknown as typeof fetch);
		await chat.ask('not private');
		expect(fetcher).not.toHaveBeenCalled();
		chat.set(true);
		await chat.ask('secret');
		chat.set(false);
		expect(chat.on).toBe(false);
		expect(chat.messages).toEqual([]);
		expect(chat.turn.text).toBe('');
	});

	it('stops a reply mid-stream when asked, and writes nothing to browser storage', async () => {
		const stored: string[] = [];
		const storage = { getItem: () => null, setItem: (k: string) => stored.push(k), removeItem: () => {} };
		vi.stubGlobal('localStorage', storage);
		vi.stubGlobal('sessionStorage', storage);
		const fetcher = vi.fn(async (_url: string, init: RequestInit) => {
			const body = new ReadableStream<Uint8Array>({
				start(c) {
					c.enqueue(new TextEncoder().encode('data: {"content":"Start"}\n\n'));
					init.signal?.addEventListener('abort', () => c.error(new DOMException('aborted', 'AbortError')));
				}
			});
			return new Response(body, { status: 200 });
		});
		const chat = new PrivateChat(fetcher as unknown as typeof fetch);
		chat.set(true);
		const asked = chat.ask('long one');
		await vi.waitFor(() => expect(chat.messages.at(-1)?.content).toBe('Start'));
		chat.stop();
		await asked;
		expect(chat.busy).toBe(false);
		expect(chat.messages.at(-1)).toMatchObject({ content: 'Start', pending: false, error: null });
		expect(stored).toEqual([]);
	});
});

describe('a private voice turn', () => {
	it('is written down privately and asked of the private chat, never of Hermes', async () => {
		const posted: { url: string; body: Record<string, unknown> }[] = [];
		vi.stubGlobal('localStorage', { getItem: () => null, setItem: () => {} });
		vi.stubGlobal(
			'FileReader',
			class {
				result = 'data:audio/wav;base64,UklGRg==';
				onload: (() => void) | null = null;
				readAsDataURL() {
					queueMicrotask(() => this.onload?.());
				}
			}
		);
		vi.stubGlobal('fetch', async (url: string, init: { body: string }) => {
			posted.push({ url, body: JSON.parse(init.body) });
			return new Response(JSON.stringify({ ok: true, transcript: 'turn the heat up', speaker: null }));
		});
		const { VoiceController } = await import('./voice.svelte');
		const submitted: string[] = [];
		const link = { submit: async (text: string) => (submitted.push(text), { kind: 'sent', notice: '' }) };
		const voice = new VoiceController(link as never);
		const asked: string[] = [];
		const chat = new PrivateChat((async () => sse('[DONE]')) as unknown as typeof fetch);
		chat.ask = async (text: string) => void asked.push(text);
		voice.privately = chat;
		chat.on = true;
		await (voice as unknown as { heard(b: Blob): Promise<void> }).heard(new Blob(['x'], { type: 'audio/wav' }));
		expect(posted[0]).toMatchObject({ url: '/hermes/api/audio/transcribe', body: { private: true } });
		expect(asked).toEqual(['turn the heat up']);
		expect(submitted).toEqual([]);

		chat.on = false;
		await voice.submit('lights off');
		expect(submitted).toEqual(['lights off']);
		await (voice as unknown as { heard(b: Blob): Promise<void> }).heard(new Blob(['x'], { type: 'audio/wav' }));
		expect(posted[1].body.private).toBeUndefined();
	});

	it("shows the adapter's reason when a private turn cannot be written down here", async () => {
		vi.stubGlobal('localStorage', { getItem: () => null, setItem: () => {} });
		vi.stubGlobal(
			'FileReader',
			class {
				result = 'data:audio/wav;base64,UklGRg==';
				onload: (() => void) | null = null;
				readAsDataURL() {
					queueMicrotask(() => this.onload?.());
				}
			}
		);
		vi.stubGlobal('fetch', async () =>
			new Response(JSON.stringify({ detail: "no private transcription here; type it, or use the phone's own" }), { status: 503 }));
		const { VoiceController } = await import('./voice.svelte');
		const voice = new VoiceController({} as never);
		voice.privately = new PrivateChat();
		voice.privately.on = true;
		await (voice as unknown as { heard(b: Blob): Promise<void> }).heard(new Blob(['x'], { type: 'audio/wav' }));
		expect(voice.notice).toBe("Could not transcribe that: no private transcription here; type it, or use the phone's own");
	});
});
