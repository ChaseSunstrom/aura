// A settings change JARVIS asks about in words (T-1317), in the browser against the mock:
// the approval card shows the value now and the value it would become, with the rows the
// settings proposal card uses, on the core and on the Approvals screen; a secret shows
// neither; nothing overflows sideways at 1440 or 390. The text is the contract's
// (tests/contracts/approval-event.json `change`), which the Python gate writes.
// Not covered: the real gate writing that text (hermes/plugins/jarvis-settings/tests), or
// the phone's fingerprint, which is what answers a settings change: the card here only shows.
import { readFileSync } from 'node:fs';

import { expect, test, type Page } from '@playwright/test';

import { emit, openShell, resetHermes, settledAt } from './v2';

const CASES: { reason: string }[] = JSON.parse(readFileSync(new URL('../../tests/contracts/approval-event.json', import.meta.url), 'utf8')).change.cases;
const [CHANGE, SECRET] = CASES.map((c) => c.reason);

/** The frame as the adapter relays it for a sensitive plugin-gated call. */
const asks = (id: string, reason: string) => ({
	request_id: id,
	command: '<settings_set> (plugin approval rule)',
	reason,
	choices: ['once', 'deny'],
	confirm: 'phone'
});

const overflow = (page: Page) => page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);

test.afterEach(async ({ page }) => {
	await resetHermes(page);
});

for (const size of [
	{ width: 1440, height: 900 },
	{ width: 390, height: 844 }
]) {
	test(`a settings approval shows the value now and the value it becomes (${size.width})`, async ({ page }) => {
		await page.setViewportSize(size);
		await openShell(page);
		await emit(page, 'approval.request', asks('e2e-setting', CHANGE));
		await settledAt(page, 'alert');
		const card = page.getByRole('alertdialog', { name: 'Change approvals.mode?' });
		await expect(card).toBeVisible();
		await expect(card.getByTestId('approval-before')).toHaveText('smart');
		await expect(card.getByTestId('approval-after')).toHaveText('off');
		await expect(card.getByTestId('approval-on-phone')).toBeVisible();
		// The rows say it once: the three lines are not repeated as prose on the card.
		await expect(card).not.toContainText('Becomes: off');
		// The whole card is on the screen, values included.
		const box = (await card.boundingBox())!;
		expect(box.y).toBeGreaterThanOrEqual(0);
		expect(box.y + box.height).toBeLessThanOrEqual(size.height);
		expect(await overflow(page)).toBeLessThanOrEqual(0);
		await page.screenshot({ path: `test-results/v2-settings-approval-${size.width}.png`, animations: 'disabled' });
	});

	test(`on the Approvals screen a secret's approval shows neither value (${size.width})`, async ({ page }) => {
		await page.setViewportSize(size);
		await openShell(page, '/approvals');
		await emit(page, 'approval.request', asks('e2e-setting', CHANGE));
		await emit(page, 'approval.request', asks('e2e-secret', SECRET));
		const cards = page.getByTestId('approvals-card');
		await expect(cards).toHaveCount(2);
		const plain = cards.filter({ hasText: 'approvals.mode' });
		await expect(plain.getByTestId('approval-before')).toHaveText('smart');
		await expect(plain.getByTestId('approval-after')).toHaveText('off');
		const secret = cards.filter({ has: page.getByTestId('approval-secret') });
		await expect(secret).toHaveCount(1);
		await expect(secret.getByTestId('approval-before')).toHaveCount(0);
		await expect(secret.getByTestId('approval-after')).toHaveCount(0);
		await expect(secret).not.toContainText('(secret)');
		expect(await overflow(page)).toBeLessThanOrEqual(0);
		await page.screenshot({ path: `test-results/v2-settings-approval-screen-${size.width}.png`, animations: 'disabled' });
	});
}
