// What the stage's conversation panel shows, and how a streaming reply reads while it arrives
// (T-0728). The owner, 2026-11-08: the reply was "one centered wall" under the core, with **
// showing until its pair arrived. The panel draws the session's last turns, labelled, and a
// streaming reply only as far as its last whole word, with any emphasis still open closed, so
// a word never shows its marker first and flips to bold after.
//
// Does not cover the drawing (Conversation.svelte) or which session's turns these are (the shell).

import type { ChatTurn } from '$lib/glass';

/** The turns the panel keeps drawn; older ones are the Chat screen's. */
export const SHOWN_TURNS = 12;

/** The session's last turns with words, and what is being heard and not yet sent, as the newest. */
export function visibleTurns(turns: readonly ChatTurn[], heard = ''): ChatTurn[] {
	// A reply with no words yet is the typing state, not an empty turn.
	const out = turns.filter((t) => t.role === 'you' || t.text.trim()).slice(-SHOWN_TURNS);
	// The voice keeps the last thing sent as heard, so it is new only until the session has it.
	const said = heard.trim();
	const sent = turns.findLast((t) => t.role === 'you');
	if (said && sent?.text.trim() !== said) out.push({ id: 'heard', role: 'you', text: said, streaming: true });
	return out.slice(-SHOWN_TURNS);
}

/** A turn is running and JARVIS has not said a word of it yet. */
export function typing(turns: readonly ChatTurn[], running: boolean): boolean {
	if (!running) return false;
	const last = turns.at(-1);
	return !last || last.role === 'you' || !last.text.trim();
}

/** A streaming reply cut after its last whole word: a half-arrived word is not drawn yet. */
export function wholeWords(text: string): string {
	const end = text.search(/\s\S*$/);
	return end < 0 ? '' : text.slice(0, end + 1);
}

/**
 * A streaming reply's open Markdown closed: an unclosed fence, inline code or ** gets its pair,
 * and a marker with nothing after it yet (a bare "- " or "**" at the end) is held back.
 */
export function closeOpen(md: string): string {
	if ((md.match(/^[ \t]*```/gm) ?? []).length % 2) {
		// Inside a code block: a fence with nothing under it yet is held back, else it is closed.
		const bare = md.match(/(^|\n)[ \t]*```[^\n]*$/);
		return bare ? md.slice(0, bare.index! + bare[1].length) : `${md.trimEnd()}\n\`\`\``;
	}
	// A mark at the very end that opens rather than closes has nothing to style yet.
	let out = md.replace(/(\*\*|__|~~|`)\s*$/, (m, mark: string, at: number) => (open(md.slice(0, at), mark) ? m : ''));
	out = out.replace(/(^|\n)[ \t]*(?:[-*+]|\d+[.)]|#{1,6}|>)[ \t]*$/, '$1');
	const prose = out.replace(/^[ \t]*```[\s\S]*?^[ \t]*```/gm, '');
	const outside = prose.replace(/`[^`]*`/g, '');
	const closers = open(prose, '`') ? '`' : ['**', '__', '~~'].filter((mark) => open(outside, mark)).join('');
	// Closed against the last word, not after the space that follows it.
	return out.replace(/(\s*)$/, (ws) => closers + ws);
}

/** An odd count of `mark` in `text`: the last one opened something not yet closed. */
function open(text: string, mark: string): boolean {
	return text.split(mark).length % 2 === 0;
}

/**
 * The words of `text` (from the `seen`th word of the reply on) split for the reveal. `born` holds,
 * per word of the reply, when its fade began on the page clock: a word not seen before is born
 * now, `stagger` behind the one before it in this delta; a word is wrapped (with the delay that
 * puts its fade where it already was) while its fade runs. The reply is redrawn whole on every
 * delta, so without the delay every word still fading would start again, or jump to the end.
 */
export function splitWords(text: string, seen: number, born: number[], first: number, now: number, stagger: number, life: number): { pieces: { text: string; delay?: number }[]; seen: number } {
	const pieces: { text: string; delay?: number }[] = [];
	for (const part of text.split(/(\s+)/)) {
		if (!part) continue;
		if (/^\s+$/.test(part)) {
			pieces.push({ text: part });
			continue;
		}
		born[seen] ??= now + stagger * (seen - first);
		const age = now - born[seen];
		pieces.push(age < life ? { text: part, delay: Math.round(-age) || 0 } : { text: part });
		seen += 1;
	}
	return { pieces, seen };
}

/** Wrap the words of `root` whose fade is running in `[data-reveal]` spans (see splitWords). */
export function revealFrom(root: HTMLElement, born: number[], now: number, stagger: number, life: number): void {
	const doc = root.ownerDocument;
	const walker = doc.createTreeWalker(root, NodeFilter.SHOW_TEXT);
	const nodes: Text[] = [];
	for (let n = walker.nextNode(); n; n = walker.nextNode()) nodes.push(n as Text);
	const first = born.length;
	let seen = 0;
	for (const node of nodes) {
		const split = splitWords(node.data, seen, born, first, now, stagger, life);
		seen = split.seen;
		if (!split.pieces.some((p) => p.delay !== undefined)) continue;
		const frag = doc.createDocumentFragment();
		for (const p of split.pieces) {
			if (p.delay === undefined) frag.append(p.text);
			else {
				const span = doc.createElement('span');
				span.dataset.reveal = '';
				span.style.animationDelay = `${p.delay}ms`;
				span.textContent = p.text;
				frag.append(span);
			}
		}
		node.replaceWith(frag);
	}
}
