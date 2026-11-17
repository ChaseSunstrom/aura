// The conversation panel's logic (T-0728): which turns show, the typing state, and a streaming
// reply that never shows a Markdown marker before the style it opens.
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';

import { renderMarkdown } from '$lib/markdown';
import { SHOWN_TURNS, closeOpen, splitWords, typing, visibleTurns, wholeWords } from './conversation';

const PATH = fileURLToPath(new URL('../../../../tests/contracts/markdown_text.json', import.meta.url));
const NUFI = (JSON.parse(readFileSync(PATH, 'utf8')) as { cases: { md: string }[] }).cases.find((c) => c.md.includes('NUFI'))!.md;
// The phone's ReplyReveal.kt reads the same table (ReplyRevealTest).
type Case = { in: string; out: string };
const STREAMING = JSON.parse(readFileSync(fileURLToPath(new URL('../../../../tests/contracts/streaming_markdown.json', import.meta.url)), 'utf8')) as { whole_words: Case[]; close_open: Case[] };
const you = (text: string) => ({ id: text, role: 'you' as const, text });
const jarvis = (text: string) => ({ id: text, role: 'jarvis' as const, text });
const visible = (html: string) => html.replace(/<[^>]+>/g, '');

describe('visibleTurns', () => {
	it('keeps the last turns and adds what is being heard as the newest', () => {
		const many = Array.from({ length: 20 }, (_, i) => (i % 2 ? jarvis(`r${i}`) : you(`q${i}`)));
		const shown = visibleTurns(many, 'turn off the hall');
		expect(shown).toHaveLength(SHOWN_TURNS);
		expect(shown.at(-1)).toMatchObject({ role: 'you', text: 'turn off the hall', streaming: true });
		expect(shown.at(-2)).toEqual(many.at(-1));
	});
	it('does not draw a heard line twice once it is sent', () => {
		expect(visibleTurns([you('lights on')], 'lights on')).toHaveLength(1);
		expect(visibleTurns([you('lights on'), jarvis('Done.')], 'lights on')).toHaveLength(2);
	});
});

describe('typing', () => {
	it('types while a turn runs and JARVIS has no words yet', () => {
		expect(typing([you('hi')], true)).toBe(true);
		expect(typing([you('hi'), jarvis('')], true)).toBe(true);
		expect(typing([you('hi'), jarvis('Hello')], true)).toBe(false);
		expect(typing([you('hi')], false)).toBe(false);
	});
});

describe('a streaming reply', () => {
	it('is drawn to its last whole word', () => {
		for (const c of STREAMING.whole_words) expect(wholeWords(c.in), JSON.stringify(c.in)).toBe(c.out);
	});

	it('closes what is open and holds back a marker with nothing after it', () => {
		for (const c of STREAMING.close_open) expect(closeOpen(c.in), JSON.stringify(c.in)).toBe(c.out);
	});

	it('never shows ** or an inline "- " at any point of the NUFI reply, streamed by the character', () => {
		for (let n = 1; n <= NUFI.length; n++) {
			const text = visible(renderMarkdown(closeOpen(wholeWords(NUFI.slice(0, n)))));
			expect(text, `after ${n} characters`).not.toContain('**');
			expect(text, `after ${n} characters`).not.toMatch(/(^|\s)- /m);
		}
		expect(closeOpen(NUFI)).toBe(NUFI);
	});
});

describe('splitWords', () => {
	it('starts each new word a stagger behind the last and leaves settled words plain', () => {
		const born = [0];
		const { pieces, seen } = splitWords('one two three', 0, born, 1, 1000, 35, 350);
		expect(seen).toBe(3);
		expect(born).toEqual([0, 1000, 1035]);
		expect(pieces.filter((p) => p.text.trim())).toEqual([{ text: 'one' }, { text: 'two', delay: 0 }, { text: 'three', delay: 35 }]);
		expect(pieces.map((p) => p.text).join('')).toBe('one two three');
	});
	it('keeps a word fading where it was when the reply is redrawn', () => {
		const born = [1000];
		expect(splitWords('one', 0, born, 1, 1200, 35, 350).pieces).toEqual([{ text: 'one', delay: -200 }]);
		expect(splitWords('one', 0, born, 1, 1400, 35, 350).pieces).toEqual([{ text: 'one' }]);
	});
});
