import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { CHANGE_TOOLS, parseApprovalChange, parseApprovalCard, reasonWithoutCard } from './approvalCard';

const CONTRACT = JSON.parse(
	readFileSync(fileURLToPath(new URL('../../../../tests/contracts/approval-event.json', import.meta.url)), 'utf8')
);

// The cases are tests/contracts/approval-event.json `card`; the Python parse
// (hermes/plugins/_shared/_messages.parse_card) reads the same table.
const CARD = (
	CONTRACT as {
		card: {
			cases: {
				name: string;
				reason: string;
				card: { what: string; why: string; impact: string; undoable: string; home: string } | null;
			}[];
		};
	}
).card;

describe('the approval card in a reason', () => {
	it('has cases', () => expect(CARD.cases.length).toBeGreaterThan(3));

	for (const c of CARD.cases) {
		it(c.name, () => {
			const got = parseApprovalCard(c.reason);
			if (c.card === null) return expect(got).toBeNull();
			expect(got).toEqual({
				what: c.card.what,
				why: c.card.why,
				impact: c.card.impact,
				undoable: c.card.undoable === 'yes',
				home: c.card.home === 'yes'
			});
		});
	}

	it('leaves the rule sentence and its prefixes when the card is cut off', () => {
		const c = CARD.cases[1];
		expect(reasonWithoutCard(c.reason).startsWith('This turn read outside text')).toBe(true);
		expect(reasonWithoutCard(c.reason)).not.toContain('Undoable:');
	});
});

// `change` in the same file (T-1317); the Python side writes and reads it in
// hermes/plugins/aura-settings/tests/test_aura_settings.py.
const CHANGE = (
	CONTRACT as {
		change: {
			tools: string[];
			cases: { name: string; reason: string; change: { key: string; before: string; after: string } | null }[];
		};
	}
).change;

describe('the settings change in a reason', () => {
	it('has cases, and is read only for the tools the contract names', () => {
		expect(CHANGE.cases.length).toBeGreaterThan(3);
		expect([...CHANGE_TOOLS].sort()).toEqual([...CHANGE.tools].sort());
	});

	for (const c of CHANGE.cases) {
		it(c.name, () => expect(parseApprovalChange(c.reason)).toEqual(c.change));
	}

	it('goes with the card when the card is cut off, so the detail does not say it twice', () => {
		const c = CHANGE.cases.find((x) => x.change)!;
		const rest = reasonWithoutCard(c.reason, true);
		expect(rest).not.toMatch(/Setting:|Now:|Becomes:|Undoable:/);
		expect(rest.length).toBeGreaterThan(0);
		// Not drawn as rows (another tool's approval): the lines stay where the person can read them.
		expect(reasonWithoutCard(c.reason)).toContain('Becomes: off');
	});
});
