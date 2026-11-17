// The owner's mirrored texts: what `GET /api/messages` answers (jarvis-core
// `api/rest.py`, `messages.py`), for the Texts screen.
//
// Every word here is someone else's (`trust: "untrusted"`): the screen shows
// them as text and never hands them to JARVIS as an instruction.
//
// Does not cover: sending a text (JARVIS's `texts_send`, behind its approval),
// or search (`/api/messages/search`).

export interface Message {
	id: string;
	conversation: string;
	source: string;
	from: string;
	text: string;
	/** Unix seconds. */
	at: number;
	urgent: boolean;
}

export interface Conversation {
	conversation: string;
	from: string;
	source: string;
	at: number;
	count: number;
}

export interface Messages {
	conversations: Conversation[];
	messages: Message[];
}

const str = (v: unknown): string => (typeof v === 'string' ? v : '');
const num = (v: unknown): number => (typeof v === 'number' && Number.isFinite(v) ? v : 0);

/** The house's answer, checked row by row: a row without its id, conversation or text is dropped. */
export function parseMessages(raw: unknown): Messages {
	const body = (raw ?? {}) as { conversations?: unknown; messages?: unknown };
	const conversations = (Array.isArray(body.conversations) ? body.conversations : [])
		.map((c) => ({ conversation: str(c?.conversation), from: str(c?.from), source: str(c?.source), at: num(c?.at), count: num(c?.count) }))
		.filter((c) => c.conversation);
	const messages = (Array.isArray(body.messages) ? body.messages : [])
		.map((m) => ({
			id: str(m?.id),
			conversation: str(m?.conversation),
			source: str(m?.source),
			from: str(m?.from),
			text: str(m?.text),
			at: num(m?.at),
			urgent: m?.urgent === true
		}))
		.filter((m) => m.id && m.conversation && m.text);
	return { conversations, messages };
}

/** One conversation's messages, oldest first, as a thread reads. */
export function threadOf(all: readonly Message[], conversation: string): Message[] {
	return all.filter((m) => m.conversation === conversation).sort((a, b) => a.at - b.at);
}
