import { describe, expect, it } from 'vitest';

import { chatTurns } from './chat';

describe('who spoke a reply', () => {
	it('labels a reply with the persona that gave it, and JARVIS by its role', () => {
		const turns = chatTurns([
			{ id: 'u', role: 'user', content: 'hi', thinking: '', tools: [], at: 0, pending: false },
			{ id: 'a', role: 'assistant', content: 'Hello.', thinking: '', tools: [], at: 0, pending: false, persona: 'aura' },
			{ id: 'j', role: 'assistant', content: 'Sir.', thinking: '', tools: [], at: 0, pending: false, persona: 'jarvis' },
			{ id: 's', role: 'assistant', content: 'Stored.', thinking: '', tools: [], at: 0, pending: false }
		]);
		expect(turns.map((t) => t.who ?? t.role)).toEqual(['you', 'aura', 'jarvis', 'jarvis']);
	});
});
