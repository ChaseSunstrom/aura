// JARVIS's subtitle draws its Markdown (T-0727): the owner's NUFI reply showed literal ** and
// inline "- " bullets under the core. The user's line stays plain text: it is what was heard.
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { render } from 'svelte/server';
import { describe, expect, it } from 'vitest';

import Subtitles from './Subtitles.svelte';

const PATH = fileURLToPath(new URL('../../../../tests/contracts/markdown_text.json', import.meta.url));
const NUFI = (JSON.parse(readFileSync(PATH, 'utf8')) as { cases: { md: string }[] }).cases.find((c) => c.md.includes('NUFI'))!.md;

const text = (html: string) => html.replace(/<!--[\s\S]*?-->/g, '').replace(/<[^>]+>/g, ' ');

describe('Subtitles', () => {
	it('draws JARVIS’s Markdown: a list with bold labels, no ** and no inline bullets', () => {
		const { body } = render(Subtitles, { props: { text: NUFI } });
		expect(body.match(/<li>/g)).toHaveLength(3);
		expect(body).toContain('<strong>Wallet extension</strong>');
		expect(text(body)).not.toContain('**');
		expect(text(body)).not.toMatch(/\s- /);
	});

	it('keeps the user’s words as they were heard, never as markup', () => {
		const { body } = render(Subtitles, { props: { text: 'say **this** <b>now</b>', speaker: 'user' } });
		expect(body).not.toContain('<strong>');
		expect(body).toContain('&lt;b>now&lt;/b>');
	});
});
