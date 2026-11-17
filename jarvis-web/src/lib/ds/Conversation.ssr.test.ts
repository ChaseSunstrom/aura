// The conversation panel draws labelled turns, JARVIS's in Markdown, a streaming reply to its
// last whole word with no open marker, and a typing state (T-0728).
import { render } from 'svelte/server';
import { describe, expect, it } from 'vitest';

import Conversation from './Conversation.svelte';

const you = (id: string, text: string) => ({ id, role: 'you' as const, text });
const jarvis = (id: string, text: string, streaming = false) => ({ id, role: 'jarvis' as const, text, streaming });
const text = (html: string) => html.replace(/<!--[\s\S]*?-->/g, '').replace(/<[^>]+>/g, ' ');

describe('Conversation', () => {
	it('labels each turn and draws JARVIS’s Markdown', () => {
		const { body } = render(Conversation, {
			props: { turns: [you('1', 'what is on the page?'), jarvis('2', 'Two ways:\n\n- **Google**\n- **Facebook**')] }
		});
		expect(body).toContain('data-turn="you"');
		expect(body).toContain('data-turn="jarvis"');
		expect(body.match(/<li>/g)).toHaveLength(2);
		expect(body).toContain('<strong>Google</strong>');
		expect(text(body)).toMatch(/You\s+what is on the page\?/);
		expect(text(body)).toMatch(/AURA\s+Two ways:/);
	});

	it('draws a streaming reply to its last whole word, its open bold closed', () => {
		const { body } = render(Conversation, { props: { turns: [you('1', 'hi'), jarvis('2', 'The **Wallet ext', true)], running: true } });
		expect(body).toContain('<strong>Wallet</strong>');
		expect(text(body)).not.toContain('**');
		expect(text(body)).not.toContain('ext');
	});

	it('types while a turn runs with no words, and shows nothing with no turns', () => {
		expect(render(Conversation, { props: { turns: [you('1', 'hi')], running: true } }).body).toContain('data-testid="conversation-typing"');
		expect(render(Conversation, { props: { turns: [you('1', 'hi'), jarvis('2', 'Hello.')], running: false } }).body).not.toContain('conversation-typing');
		expect(render(Conversation, { props: { turns: [] } }).body).not.toContain('data-testid="conversation"');
	});

	it('keeps the user’s words as heard, never as markup', () => {
		const { body } = render(Conversation, { props: { turns: [], heard: 'say **this** <b>now</b>' } });
		expect(body).not.toContain('<strong>');
		expect(body).toContain('&lt;b>now&lt;/b>');
	});
});
