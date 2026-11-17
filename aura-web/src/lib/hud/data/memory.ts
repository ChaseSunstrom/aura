// `panel.memory` from jarvis-core's memory entries and the notes list.
//
// The graph is the panel's picture, built from the
// same two lists the old sticky graph shell built it from; the entries are the
// notes under it. An entry's first line is its title, because memories are
// written as sentences and the body draws a title over a body.

import { buildGraph, type GraphEdge, type GraphNode, type MemoryLike, type NoteLike } from '$lib/knowledge/graph';
import type { MemoryNote } from '$lib/glass';
import { agoOf } from './time';

/** One row of `jarvis/memory/list`. */
export interface MemoryEntry {
	id: string;
	text: string;
	tags: string[];
	/** Epoch seconds. */
	created: number;
	source: string;
	pinned?: boolean;
	person?: string;
	expires?: number | null;
	/**
	 * The turn this came from, when Jarvis worked it out rather than being told.
	 *
	 * The server has sent it since M100 and the mock backend carries it; the
	 * console's own type had simply never declared it. It is what lets the
	 * memory graph say two entries came out of the same conversation.
	 */
	conversation_id?: string;
}

const SOURCE_WORDS: Record<string, string> = {
	conversation: 'a conversation',
	reflection: 'the overnight reflection',
	user: 'you',
	console: 'the console',
	voice: 'something you said',
	// Facts the house pulls out of a conversation on its own: read raw, the row said
	// "From extracted about Casey" (the sweep, 2026-10-06).
	extracted: 'something you mentioned'
};

export function sourceWord(entry: MemoryEntry): string {
	const base = SOURCE_WORDS[entry.source] ?? (entry.source ? entry.source.replace(/_/g, ' ') : '');
	const who = entry.person ? ` about ${entry.person}` : '';
	return base ? `From ${base}${who}` : who.trim();
}

/** The first sentence, or the first 72 characters, as the title. */
export function titleOf(text: string): string {
	const first = text.trim().split(/(?<=[.!?])\s+|\n/)[0] ?? '';
	return first.length > 72 ? `${first.slice(0, 71)}…` : first;
}

export function memoryNotes(entries: readonly MemoryEntry[], now = Date.now()): MemoryNote[] {
	return entries.map((entry) => {
		const title = titleOf(entry.text);
		const text = entry.text.trim();
		const note: MemoryNote = {
			id: entry.id,
			title: entry.pinned ? `${title} (pinned)` : title,
			// The whole text, title included: a body that started mid-sentence
			// because the title took the first one read as truncated. Nothing at
			// all when the memory IS its first sentence, which most of them are —
			// the row printed the same words twice, once as its name and once as
			// its body.
			body: text === title ? '' : text,
			at: entry.created ? agoOf(entry.created * 1000, now) : undefined
		};
		const source = sourceWord(entry);
		if (source) note.source = source;
		return note;
	});
}

export function memoryGraph(notes: readonly NoteLike[], entries: readonly MemoryEntry[]): { nodes: GraphNode[]; edges: GraphEdge[] } {
	const memory: MemoryLike[] = entries.map((e) => ({ id: e.id, text: e.text, tags: e.tags }));
	return buildGraph([...notes], memory);
}
