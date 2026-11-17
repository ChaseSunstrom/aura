// A settings change asked for in words, on the approval card (T-1317): the card draws the
// value now and the value it would become with the proposal card's rows, and is the plain
// card whenever the approval's text does not carry them.
import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { render } from 'svelte/server';

import { initialHud, type ApprovalRequest, type HudState } from '$lib/hermes/hud';
import ApprovalCard from '../shell/ApprovalCard.svelte';
import { asksFrom } from '../shell/approval';
import ChangeRows from './ChangeRows.svelte';

// The reasons are the contract's, which the gate's own test writes byte for byte.
const CASES = (
	JSON.parse(readFileSync(new URL('../../../../../tests/contracts/approval-event.json', import.meta.url), 'utf8')) as {
		change: { secret: string; cases: { reason: string; change: object | null }[] };
	}
).change;
const [CHANGE, SECRET, PLAIN] = CASES.cases.map((c) => c.reason);

function hudWith(approval: Partial<ApprovalRequest>): HudState {
	return {
		...initialHud(),
		approvals: [
			{ requestId: 'r1', sessionId: 's', tool: 'settings_set', detail: '', reason: CHANGE, choices: ['once', 'deny'], at: 1, onPhone: true, ...approval }
		]
	};
}
const askOf = (approval: Partial<ApprovalRequest> = {}) => asksFrom(hudWith(approval))[0];
const html = (approval: Partial<ApprovalRequest> = {}) => render(ApprovalCard, { props: { ask: askOf(approval), onanswer: () => {} } }).body;
/** A reason like the contract's, about another key and other values. */
const about = (key: string, before: string, after: string) =>
	CHANGE.replaceAll('approvals.mode', key).replace('Now: smart', `Now: ${before}`).replace('Becomes: off', `Becomes: ${after}`);

describe('a settings approval, as its card', () => {
	it('draws the value now and the value it becomes, under the question', () => {
		expect(askOf().change).toEqual({ key: 'approvals.mode', secret: false, before: 'smart', after: 'off' });
		const body = html();
		expect(body).toMatch(/data-testid="approval-before"[^>]*>smart</);
		expect(body).toMatch(/data-testid="approval-after"[^>]*>off</);
		expect(body).toContain('Change approvals.mode?');
		// Who may say yes is not this card's to change: the fingerprint line stays, and no button says Approve.
		expect(body).toContain('data-testid="approval-on-phone"');
		expect(body).not.toMatch(/>Approve</);
	});

	it('does not say the two values a second time behind "Show me first"', () => {
		expect(askOf().detail).toBe('settings_set changes a security setting (auth, approvals, device access)');
	});

	it('is the plain card when the text carries no change', () => {
		expect(askOf({ reason: PLAIN }).change).toBeUndefined();
		const body = html({ reason: PLAIN });
		expect(body).not.toMatch(/approval-(before|after|secret)/);
		expect(body).toContain('Change [redacted]?');
		expect(askOf({ reason: 'Run settings_set?' }).change).toBeUndefined();
	});

	it('reads the lines only for a settings tool: anywhere else they stay text', () => {
		const ask = askOf({ tool: 'mail_send' });
		expect(ask.change).toBeUndefined();
		expect(ask.detail).toContain('Now: smart');
		expect(html({ tool: 'mail_send' })).not.toMatch(/approval-(before|after)/);
		expect(askOf({ tool: 'settings_undo' }).change).toMatchObject({ before: 'smart', after: 'off' });
	});

	it('shows the values of a key the console’s schema does not name: the gate has masked a secret itself', () => {
		expect(askOf({ reason: about('voice.speaker.mode', 'strict', 'relaxed') }).change).toEqual({
			key: 'voice.speaker.mode',
			secret: false,
			before: 'strict',
			after: 'relaxed'
		});
	});

	it('draws neither value of a secret, masked or not', () => {
		expect(askOf({ reason: SECRET }).change).toMatchObject({ secret: true, before: '', after: '' });
		const body = html({ reason: SECRET });
		expect(body).toContain('data-testid="approval-secret"');
		expect(body).not.toMatch(/approval-(before|after)/);
		expect(body).not.toContain(CASES.secret);
		// A clear value on a key the schema calls a secret is the gate failing; the card still must not draw it.
		const leaked = about('house.token', 'the-value-now', 'the-value-asked-for');
		expect(askOf({ reason: leaked }).change).toEqual({ key: 'house.token', secret: true, before: '', after: '' });
		expect(html({ reason: leaked })).not.toMatch(/the-value-(now|asked-for)/);
		expect(askOf({ reason: leaked }).detail ?? '').not.toMatch(/the-value-(now|asked-for)/);
	});
});

describe('the rows both cards draw', () => {
	const rows = (props: { secret: boolean; before: string; after: string; testid: string }) => render(ChangeRows, { props }).body;

	it('one value under the other, named by the card that draws them', () => {
		const body = rows({ secret: false, before: 'smart', after: 'manual', testid: 'proposal' });
		expect(body).toMatch(/<dt[^>]*>Now<\/dt>\s*<dd[^>]*data-testid="proposal-before"[^>]*>smart</);
		expect(body).toMatch(/<dt[^>]*>Becomes<\/dt>\s*<dd[^>]*data-testid="proposal-after"[^>]*>manual</);
	});

	it('a secret is a sentence and no value', () => {
		const body = rows({ secret: true, before: 'x-now', after: 'x-then', testid: 'proposal' });
		expect(body).toContain('data-testid="proposal-secret"');
		expect(body).not.toMatch(/x-now|x-then|<dl/);
	});
});
