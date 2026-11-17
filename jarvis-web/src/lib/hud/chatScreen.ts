// The chat screen's arithmetic (`ChatScreen.svelte`): which messages start a new day,
// how old a conversation is, which conversations a search matches, and one line for a
// tool call. Pure, so `chatScreen.test.ts` pins what the owner reads (2026-09-27: "add a
// complete chat interface to the console/mobile app, beautiful UI, expanded, consistent").

import type { ChatMessage, ChatToolCall } from '$lib/chat';
import type { SessionSummary } from '$lib/hermes/protocol';
import { STATE_WORDS, type ApiModel } from './data/models';

/** The message indices that begin a new calendar day, with the day's label. */
export function dayBreaks(messages: readonly ChatMessage[], now: number = Date.now()): Map<number, string> {
	const out = new Map<number, string>();
	let last = '';
	messages.forEach((m, i) => {
		if (!m.at) return;
		const key = new Date(m.at).toDateString();
		if (key !== last) out.set(i, dayLabel(m.at, now));
		last = key;
	});
	return out;
}

/** "Today", "Yesterday", a weekday within the week, else the date. */
export function dayLabel(at: number, now: number = Date.now()): string {
	const day = (t: number) => {
		const d = new Date(t);
		return new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
	};
	const days = Math.round((day(now) - day(at)) / 86_400_000);
	if (days <= 0) return 'Today';
	if (days === 1) return 'Yesterday';
	const d = new Date(at);
	if (days < 7) return d.toLocaleDateString(undefined, { weekday: 'long' });
	return d.toLocaleDateString(undefined, {
		day: 'numeric',
		month: 'short',
		year: d.getFullYear() === new Date(now).getFullYear() ? undefined : 'numeric'
	});
}

/** How long ago a conversation started, briefly: "now", "5 min", "3 h", then the day. */
export function ago(startedAtSeconds: number | undefined, now: number = Date.now()): string {
	if (!startedAtSeconds) return '';
	const s = Math.max(0, Math.round(now / 1000 - startedAtSeconds));
	if (s < 60) return 'now';
	if (s < 3600) return `${Math.floor(s / 60)} min`;
	if (s < 86_400) return `${Math.floor(s / 3600)} h`;
	return dayLabel(startedAtSeconds * 1000, now);
}

/** The conversations whose title or preview has every word of [query], newest first. */
export function filterSessions(sessions: readonly SessionSummary[], query: string): SessionSummary[] {
	const words = query.toLowerCase().split(/\s+/).filter(Boolean);
	return sessions
		.filter((s) => {
			const hay = `${s.title ?? ''} ${s.preview ?? ''}`.toLowerCase();
			return words.every((w) => hay.includes(w));
		})
		.sort((a, b) => (b.started_at ?? 0) - (a.started_at ?? 0));
}

/** A tool call in one line: its name without underscores, and its first text argument. */
export function toolLine(call: ChatToolCall): string {
	const name = call.name.replace(/_/g, ' ');
	const first = Object.values(call.arguments ?? {}).find((v) => typeof v === 'string' && v.trim()) as string | undefined;
	return first ? `${name} · ${first.length > 60 ? `${first.slice(0, 57)}...` : first}` : name;
}

/** What a new conversation offers to start with. */
export const SUGGESTIONS: readonly string[] = [
	"What's on my calendar today?",
	'Turn the lights down in the living room',
	'Every time I connect to my car, play my driving playlist on shuffle',
	'Summarise my unread mail'
];

/**
 * The model select's options: the models the manager can serve, each marked when picking it
 * means a cold start (llama-swap swaps it in and evicts the loaded one). A model still
 * downloading or with its weights gone is left out. The session's own model is kept even when
 * the manager does not list it, so the select never claims a model this chat is not on.
 */
export function modelOptions(
	models: readonly Pick<ApiModel, 'id' | 'name' | 'state'>[],
	current: string
): { value: string; label: string }[] {
	const out = models
		.filter((m) => m.state !== 'downloading' && m.state !== 'missing')
		.map((m) => ({ value: m.id, label: m.state === 'loaded' ? m.name || m.id : `${m.name || m.id}, ${STATE_WORDS[m.state].toLowerCase()}` }));
	if (current && !out.some((o) => o.value === current)) out.unshift({ value: current, label: current });
	return out;
}
