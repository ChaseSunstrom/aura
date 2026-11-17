import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

import { LEVELS, levelRequest, parseCapabilities } from './capabilities';
import { parseMessages, threadOf } from './messages';

const contract = JSON.parse(readFileSync(new URL('../../../../../tests/contracts/capability-levels.json', import.meta.url), 'utf8'));

describe('parseCapabilities', () => {
	it('reads the shape /hud capabilities prints, with the contract’s levels', () => {
		expect([...LEVELS]).toEqual(contract.levels);
		const out = JSON.stringify({
			levels: contract.levels,
			capabilities: [
				{ id: 'mail', level: 'ask', tools: [{ name: 'mail_send', write: true }, { name: 'mail_search', write: false }] },
				{ id: 'house', level: null, tools: [] }
			]
		});
		const caps = parseCapabilities(out);
		expect(caps.map((c) => [c.id, c.name, c.level])).toEqual([
			['mail', 'Email', 'ask'],
			['house', 'The house', null]
		]);
		expect(caps[0].tools).toEqual([
			{ name: 'mail_send', write: true },
			{ name: 'mail_search', write: false }
		]);
		for (const key of contract.hud_capabilities.capability_keys) expect(caps[0]).toHaveProperty(key);
	});

	it('an unknown level is "as the matrix says", and a shape it does not know throws', () => {
		expect(parseCapabilities('{"capabilities":[{"id":"web","level":"everything"}]}')[0].level).toBeNull();
		expect(() => parseCapabilities('{"oops":1}')).toThrow(/shape/);
		expect(() => parseCapabilities('not json')).toThrow();
	});

	it('a change is a request to JARVIS naming the tool that asks the owner', () => {
		expect(levelRequest({ id: 'mail', name: 'Email' }, 'read')).toMatch(/mail.*read.*capability_set/);
	});
});

describe('parseMessages', () => {
	it('keeps whole rows and drops the rest; a thread reads oldest first', () => {
		const m = parseMessages({
			trust: 'untrusted',
			conversations: [{ conversation: 'ann', from: 'Ann', source: 'sms', at: 2, count: 2 }, { from: 'nobody' }],
			messages: [
				{ id: '2', conversation: 'ann', from: 'Ann', source: 'sms', text: 'see you', at: 2 },
				{ id: '1', conversation: 'ann', from: 'Ann', source: 'sms', text: 'hi', at: 1, urgent: true },
				{ id: '3', conversation: 'ann', text: '' }
			]
		});
		expect(m.conversations.map((c) => c.conversation)).toEqual(['ann']);
		expect(threadOf(m.messages, 'ann').map((x) => [x.text, x.urgent])).toEqual([
			['hi', true],
			['see you', false]
		]);
		expect(parseMessages(null)).toEqual({ conversations: [], messages: [] });
	});
});
