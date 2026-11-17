// The command palette's search (BUILD_BRIEF §1: "⌘K command palette … searches
// actions, models, routines and settings, and Tab hands the query to JARVIS").
// Pure, so the ranking is tested without a browser; `ds/shell/Palette.svelte`
// draws it and the shell builds the items from what it already holds.

export type PaletteGroup = 'Sections' | 'Actions' | 'Models' | 'Routines' | 'Settings';

export interface PaletteItem {
	id: string;
	group: PaletteGroup;
	title: string;
	/** A quieter second phrase: a model's repo, a routine's trigger. */
	sub?: string;
	/** The chord that does the same, printed at the row's end. */
	keys?: string;
	run: () => void;
}

const GROUPS: readonly PaletteGroup[] = ['Sections', 'Actions', 'Models', 'Routines', 'Settings'];

/**
 * How well `query` matches `text`: 4 a prefix, 3 a word's prefix, 2 a substring,
 * 1 the letters in order, 0 no match. Case-folded; an empty query matches all at 1.
 */
export function score(text: string, query: string): number {
	const t = text.toLowerCase();
	const q = query.trim().toLowerCase();
	if (!q) return 1;
	if (t.startsWith(q)) return 4;
	if (t.split(/[\s·/:_-]+/).some((w) => w.startsWith(q))) return 3;
	if (t.includes(q)) return 2;
	let at = 0;
	for (const ch of t) if (ch === q[at]) at++;
	return at === q.length ? 1 : 0;
}

/** The items that match, best first; ties keep the groups' order, then the host's. */
export function rank(items: readonly PaletteItem[], query: string, limit = 40): PaletteItem[] {
	return items
		.map((item, i) => ({ item, i, s: Math.max(score(item.title, query), score(item.sub ?? '', query) - 1) }))
		.filter((r) => r.s > 0)
		.sort((a, b) => b.s - a.s || GROUPS.indexOf(a.item.group) - GROUPS.indexOf(b.item.group) || a.i - b.i)
		.slice(0, limit)
		.map((r) => r.item);
}
