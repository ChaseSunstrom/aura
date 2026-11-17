// Markdown reads the same on the console and the phone (T-0727).
//
// The phone draws a reply's Markdown natively (android-app ui/MarkdownText.kt, spans on a
// TextView), so the two renderers are bound by one table both suites read:
// tests/contracts/markdown_text.json, each case's visible text and its bold, italic, code and
// link runs. This reads them off renderMarkdown's HTML by the table's rules (blocks on their
// own lines, '• ' bullets, numbers kept, cells joined by ' · ', headings bold).
//
// Does not cover the look (sizes, colours, spacing): those are each surface's own.

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';

import { renderMarkdown } from './markdown';

interface Case {
	name: string;
	md: string;
	text: string;
	bold: string[];
	italic: string[];
	code: string[];
	links: { text: string; href: string }[];
}

const PATH = fileURLToPath(new URL('../../../tests/contracts/markdown_text.json', import.meta.url));
const { cases } = JSON.parse(readFileSync(PATH, 'utf8')) as { cases: Case[] };

const unescape = (s: string) =>
	s.replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&amp;/g, '&');
const strip = (s: string) => unescape(s.replace(/<[^>]+>/g, ''));
const runs = (html: string, tag: RegExp) => [...html.matchAll(tag)].map((m) => strip(m[1]));

/** The visible text, by the table's rules. */
function visible(html: string): string {
	let out = html
		.replace(/<ol>([\s\S]*?)<\/ol>/g, (_, items: string) => {
			let n = 0;
			return items.replace(/<li>/g, () => `${++n}. `);
		})
		.replace(/<li>/g, '• ')
		.replace(/<\/t[hd]>(?=<t[hd])/g, ' · ')
		.replace(/<br>/g, '\n')
		.replace(/<\/(p|li|h[1-6]|tr|pre|blockquote)>/g, '\n');
	out = strip(out);
	return out
		.split('\n')
		.map((l) => l.trimEnd())
		.filter((l) => l !== '')
		.join('\n');
}

describe('Markdown as both surfaces show it (tests/contracts/markdown_text.json)', () => {
	it('holds the owner’s reply', () => {
		expect(cases.some((c) => c.md.includes('NUFI'))).toBe(true);
	});
	for (const c of cases) {
		it(c.name, () => {
			const html = renderMarkdown(c.md);
			expect(visible(html)).toBe(c.text);
			expect([...runs(html, /<strong>([\s\S]*?)<\/strong>/g), ...runs(html, /<h[1-6]>([\s\S]*?)<\/h[1-6]>/g)]).toEqual(c.bold);
			expect(runs(html, /<em>([\s\S]*?)<\/em>/g)).toEqual(c.italic);
			expect(runs(html, /<code>([\s\S]*?)<\/code>/g)).toEqual(c.code);
			expect([...html.matchAll(/<a href="([^"]+)"[^>]*>([\s\S]*?)<\/a>/g)].map((m) => ({ text: strip(m[2]), href: unescape(m[1]) }))).toEqual(c.links);
			// Nothing of the source's syntax is left to read.
			expect(visible(html)).not.toMatch(/\*\*|(^|\s)- |```|^#/m);
		});
	}
});
