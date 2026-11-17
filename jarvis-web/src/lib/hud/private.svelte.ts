/**
 * Private chats (T-0462): a conversation that lives in this page's memory and nowhere else.
 *
 * The owner, 2026-10-30: "ephemeral or incognito chats, that won't be logged or stored
 * absolutely anywhere". A private turn never goes to Hermes (whose sessions, memory and logs
 * keep everything); the whole conversation is sent to the adapter's `/api/private/chat`, which
 * relays it to the model with no tools and writes nothing. Switching it off, or closing the
 * page, is the only copy gone. Not covered: the browser's own memory and swap while the page is
 * open, a phone keyboard's learned words, and the model server's GPU cache (docs/explanation/private-chats.md).
 */
import type { ChatMessage } from '$lib/chat';
import { freshTurn, type TurnState } from '$lib/hermes/hud';

/** The adapter's bounds on one request (`PRIVATE_MAX_TURNS`, `PRIVATE_MAX_CHARS`);
 * `tests/contracts/private_chat.json` binds the two, read by both suites. */
export const MAX_TURNS = 80;
export const MAX_CHARS = 40_000;
const ROUTE = '/hermes/api/private/chat';

type Turn = { role: ChatMessage['role']; content: string };

/** The newest finished lines that fit the adapter's bounds, oldest first. */
export function turnsFor(messages: readonly ChatMessage[]): Turn[] {
	const out: Turn[] = [];
	let chars = 0;
	for (let i = messages.length - 1; i >= 0 && out.length < MAX_TURNS; i--) {
		const m = messages[i];
		if (!m.content || m.pending || m.error) continue;
		chars += m.content.length;
		if (chars > MAX_CHARS) break;
		out.unshift({ role: m.role, content: m.content });
	}
	return out;
}

const idle = freshTurn;

let seq = 0;
const message = (role: ChatMessage['role'], content: string): ChatMessage => ({
	id: `private-${++seq}`,
	role,
	content,
	thinking: '',
	tools: [],
	at: Date.now(),
	pending: role === 'assistant',
	error: null
});

/** The reply's text, piece by piece, from the adapter's `data: {"content"}` stream. */
async function* pieces(body: ReadableStream<Uint8Array>): AsyncGenerator<string> {
	const reader = body.getReader();
	const text = new TextDecoder();
	let buf = '';
	for (;;) {
		const { value, done } = await reader.read();
		if (done) throw new Error('the reply was cut off');
		buf += text.decode(value, { stream: true });
		let nl: number;
		while ((nl = buf.indexOf('\n')) >= 0) {
			const line = buf.slice(0, nl).trim();
			buf = buf.slice(nl + 1);
			if (!line.startsWith('data:')) continue;
			const data = line.slice(5).trim();
			if (data === '[DONE]') return;
			const event = JSON.parse(data) as { content?: string; error?: string };
			if (event.error) throw new Error(event.error);
			if (event.content) yield event.content;
		}
	}
}

/** What an adapter refusal said (its `detail`), or, when that is not JSON, its status. */
export async function refusal(res: Response, what: string): Promise<string> {
	const detail = await res.json().then((b: { detail?: unknown }) => b?.detail, () => null);
	return typeof detail === 'string' ? detail : `${what} answered ${res.status}`;
}

export class PrivateChat {
	on = $state(false);
	messages = $state<ChatMessage[]>([]);
	/** Shaped like Hermes' turn so the voice speaks and stamps a private reply unchanged. */
	turn = $state<TurnState>(idle());
	/** The model a private turn runs on; '' is the adapter's default. Not part of the conversation, so `set` keeps it. */
	model = $state('');
	#abort: AbortController | null = null;
	/** Bumped on every switch, so a reply still arriving for a forgotten conversation is dropped. */
	#conversation = 0;

	constructor(private readonly fetcher: typeof fetch = (input, init) => fetch(input, init)) {}

	get busy(): boolean {
		return this.turn.running;
	}

	/** On or off, the conversation so far is forgotten. */
	set(on: boolean): void {
		this.stop();
		this.#abort = null;
		this.#conversation++;
		this.on = on;
		this.messages = [];
		this.turn = idle();
	}

	stop(): void {
		this.#abort?.abort();
	}

	async ask(text: string): Promise<void> {
		const said = text.trim();
		if (!this.on || !said) return;
		this.stop();
		const abort = new AbortController();
		const conversation = this.#conversation;
		this.#abort = abort;
		this.messages.push({ ...message('user', said), pending: false });
		const turns = turnsFor(this.messages);
		this.messages.push(message('assistant', ''));
		const i = this.messages.length - 1;
		this.turn = { ...idle(), running: true, startedAt: Date.now() };
		let error: string | null = null;
		try {
			if (turns.at(-1)?.content !== said) throw new Error('That is too long for a private conversation.');
			const res = await this.fetcher(ROUTE, {
				method: 'POST',
				headers: { 'content-type': 'application/json' },
				body: JSON.stringify(this.model ? { messages: turns, model: this.model } : { messages: turns }),
				cache: 'no-store',
				signal: abort.signal
			});
			if (!res.ok || !res.body) throw new Error(await refusal(res, 'the private chat'));
			for await (const piece of pieces(res.body)) {
				if (abort.signal.aborted) break;
				this.messages[i].content += piece;
				this.turn.text += piece;
				if (!this.turn.gotToken) {
					this.turn.gotToken = true;
					this.turn.firstTokenAt = Date.now();
				}
			}
		} catch (e) {
			if (!abort.signal.aborted) error = e instanceof Error ? e.message : String(e);
		}
		// Switched off since: this turn's lines are already forgotten.
		if (conversation !== this.#conversation) return;
		this.messages[i].pending = false;
		this.messages[i].error = error;
		// A newer question took over: the turn is its now.
		if (this.#abort !== abort) return;
		this.#abort = null;
		this.turn.running = false;
		this.turn.finishedAt = Date.now();
		this.turn.error = error;
	}
}
