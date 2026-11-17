// A settings change waiting on a yes, on the Approvals screen (T-1316), against the mock:
// the proposal is a card with the value now and the value it becomes; Apply writes it and
// the card leaves; Discard leaves the setting alone; a secret's value is not on the page;
// nothing overflows sideways at 1440 or 390.
// Not covered: the real plugin, which proposes only for a security setting and only from an
// app's `jsettings set` (a change asked in words takes the approval card); a
// `settings.proposals` missed while the link was down (the reconnect read has it).
import { expect, test, type Page } from '@playwright/test';

import { openShell, resetHermes } from './v2';

/** `jsettings <words>` on a socket of its own, as another app (the phone) would send it. */
const jsettings = (page: Page, words: string) =>
	page.evaluate(async (w) => {
		const socket = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/hermes/ws`);
		await new Promise((resolve) => socket.addEventListener('open', resolve));
		let id = 20;
		const call = (method: string, params: Record<string, unknown>) =>
			new Promise<Record<string, unknown>>((resolve) => {
				const mine = ++id;
				const heard = (m: MessageEvent) => {
					const frame = JSON.parse(String(m.data));
					if (frame.id !== mine) return;
					socket.removeEventListener('message', heard);
					resolve(frame.result ?? {});
				};
				socket.addEventListener('message', heard);
				socket.send(JSON.stringify({ jsonrpc: '2.0', id: mine, method, params }));
			});
		// The command is refused without a live session, so this socket opens its own.
		const sid = String((await call('session.create', {})).session_id);
		const out = JSON.parse(String((await call('command.dispatch', { session_id: sid, name: 'jsettings', arg: `@${sid} ${w}` })).output ?? '{}'));
		socket.close();
		return out;
	}, words);

const overflow = (page: Page) => page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);

test.afterEach(async ({ page }) => {
	// The mock keeps proposals and what was applied.
	await resetHermes(page);
});

for (const size of [
	{ width: 1440, height: 900 },
	{ width: 390, height: 844 }
]) {
	test(`a proposal is a before and after card; Apply writes it, Discard does not (${size.width})`, async ({ page }) => {
		await page.setViewportSize(size);
		await openShell(page, '/approvals');
		await expect(page.getByTestId('approvals-proposals-state')).toHaveAttribute('data-state', 'empty');
		await jsettings(page, 'set approvals.mode "manual"');
		await jsettings(page, 'set approvals.always_ttl_days 7');
		await jsettings(page, 'set house.token "made-up-token-value"');

		// No reload: the plugin announces each proposal (T-1322).
		const cards = page.getByTestId('proposal-card');
		await expect(cards).toHaveCount(3);
		const mode = cards.filter({ hasText: 'approvals.mode' });
		await expect(mode.getByTestId('proposal-before')).toHaveText('smart');
		await expect(mode.getByTestId('proposal-after')).toHaveText('manual');
		await expect(mode).toContainText('applies now');
		// A secret says that it changes and draws neither value.
		const secret = cards.filter({ hasText: 'house.token' });
		await expect(secret.getByTestId('proposal-secret')).toBeVisible();
		await expect(secret.getByTestId('proposal-after')).toHaveCount(0);
		await expect(page.getByTestId('screen-approvals')).not.toContainText('made-up-token-value');
		expect(await overflow(page)).toBeLessThanOrEqual(0);
		// For a person to read: a tall window so all three cards are in it (the screen scrolls
		// inside the shell, which `fullPage` does not follow), with the shell's entry fade ended.
		await page.setViewportSize({ width: size.width, height: 1800 });
		await page.screenshot({ path: `test-results/v2-settings-proposals-${size.width}.png`, animations: 'disabled' });
		await page.setViewportSize(size);

		await mode.getByTestId('proposal-apply').click();
		await expect(cards).toHaveCount(2);
		expect((await jsettings(page, 'get approvals.mode')).value).toBe('manual');

		const days = cards.filter({ hasText: 'approvals.always_ttl_days' });
		const before = (await jsettings(page, 'get approvals.always_ttl_days')).value;
		await days.getByTestId('proposal-discard').click();
		await expect(cards).toHaveCount(1);
		expect((await jsettings(page, 'get approvals.always_ttl_days')).value).toBe(before);
		expect((await jsettings(page, 'proposals')).map((p: { key: string }) => p.key)).toEqual(['house.token']);
	});
}

test('a proposal made or answered somewhere else comes and goes on the open screen, no reload (T-1322)', async ({ page }) => {
	await page.setViewportSize({ width: 1440, height: 900 });
	await openShell(page, '/approvals');
	await expect(page.getByTestId('approvals-proposals-state')).toHaveAttribute('data-state', 'empty');
	const made = await jsettings(page, 'set approvals.mode "off"');
	await expect(page.getByTestId('proposal-card')).toHaveCount(1);
	await jsettings(page, `discard ${made.proposal.id}`);
	await expect(page.getByTestId('proposal-card')).toHaveCount(0);
	await expect(page.getByTestId('approvals-proposals-state')).toHaveAttribute('data-state', 'empty');
});
