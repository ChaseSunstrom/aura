// `panel.document` from a note, a code panel or a text panel.
//
// The notes screen was a list beside a reader; the panel is the reader, and
// the list is the same body's `items` rail — one note open, the rest a click
// away. A note's markdown goes through the sanitiser the body already uses.

import type { NoteLike } from '$lib/knowledge/graph';

/** One row of `jarvis/notes/list`; `body` only from `jarvis/notes/get`. */
export interface NoteRow extends NoteLike {
	updated: string;
	excerpt?: string;
	body?: string;
}

export interface DocumentView {
	kind: 'markdown' | 'text' | 'code';
	text: string;
	title: string;
	path: string;
}

export interface DocumentItem {
	id: string;
	title: string;
	/** A second line: the tags, or when it changed. */
	detail?: string;
}

export function noteDocument(note: NoteRow | null | undefined): DocumentView | null {
	if (!note) return null;
	return {
		kind: 'markdown',
		text: note.body ?? note.excerpt ?? '',
		title: note.title,
		path: note.tags?.length ? note.tags.map((t) => `#${t}`).join(' ') : ''
	};
}

export function noteItems(notes: readonly NoteRow[]): DocumentItem[] {
	return notes.map((note) => {
		const item: DocumentItem = { id: note.id, title: note.title };
		const detail = note.tags?.length ? note.tags.map((t) => `#${t}`).join(' ') : note.excerpt ?? '';
		if (detail) item.detail = detail;
		return item;
	});
}
