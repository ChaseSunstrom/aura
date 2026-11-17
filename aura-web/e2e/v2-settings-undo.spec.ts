// A settings change made by asking (T-0700), against the mock: the shell offers Undo for the
// token's five seconds, Settings lists it under Recent changes with who, when and both values,
// Put back is on the newest change of a key only and never on a secret, a security setting put
// back becomes a proposal and is said to be waiting, and nothing overflows at 1440 or 390.
// Not covered: the real plugin (its suite pins `history` and `undo`, and which keys it holds
// for a yes: the mock holds the Approvals group, the contract carrying no such flag); a change
// made while this console was not connected, which only the list shows.
import { expect, test, type Page } from '@playwright/test';

import { openShell, resetHermes } from './v2';

/** One call to the mock on a socket of its own; `words` is sent as `jsettings <words>`, as another app would. */
const mock = (page: Page, method: string, params: Record<string, unknown>) =>
	page.evaluate(
		async ([m, p]) => {
			const socket = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/hermes/ws`);
			await new Promise((resolve) => socket.addEventListener('open', resolve));
			let id = 40;
			const call = (method: string, params: Record<string, unknown>) =>
				new Promise<Record<string, unknown>>((resolve) => {
					const mine = ++id;
					const heard = (frame: MessageEvent) => {
						const got = JSON.parse(String(frame.data));
						if (got.id !== mine) return;
						socket.removeEventListener('message', heard);
						resolve(got.result ?? {});
					};
					socket.addEventListener('message', heard);
					socket.send(JSON.stringify({ jsonrpc: '2.0', id: mine, method, params }));
				});
			let out: unknown;
			if (m === 'jsettings') {
				// The command is refused without a live session, so this socket opens its own.
				const sid = String((await call('session.create', {})).session_id);
				out = JSON.parse(String((await call('command.dispatch', { session_id: sid, name: 'jsettings', arg: `@${sid} ${p.words}` })).output ?? '{}'));
			} else out = await call(m, p);
			socket.close();
			return out as never;
		},
		[method, params] as [string, Record<string, unknown>]
	);
const jsettings = (page: Page, words: string) => mock(page, 'jsettings', { words });
/** A setting written the way the model's `settings_set` writes it. */
const asked = (page: Page, key: string, value: unknown) => mock(page, 'test.setting', { key, value });
const valueOf = async (page: Page, key: string) => ((await jsettings(page, `get ${key}`)) as { value: unknown }).value;

const overflow = (page: Page) => page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);

test.describe('against the mock', () => {
	test.afterEach(async ({ page }) => {
		// The mock keeps what was written and its change log.
		await resetHermes(page);
	});

	for (const size of [
		{ width: 1440, height: 900 },
		{ width: 390, height: 844 }
	]) {
		test(`a change by asking offers Undo, and Recent changes lists it with Put back (${size.width})`, async ({ page }) => {
			await page.setViewportSize(size);
			await openShell(page, '/settings');
			const offer = page.getByTestId('settings-undo');
			const rows = page.getByTestId('change-row');
			await expect(page.getByTestId('settings-changes-state')).toHaveAttribute('data-state', 'empty');

			// A secret is listed and never offered: the plugin keeps no old value of one.
			await asked(page, 'house.token', 'made-up-token-value');
			await expect(rows).toHaveCount(1);
			await expect(offer).toHaveCount(0);

			await asked(page, 'goals.max_turns', 30);
			await asked(page, 'goals.max_turns', 40);
			await expect(offer).toBeVisible();
			await expect(page.getByTestId('settings-undo-key')).toHaveText('goals.max_turns');
			await expect(page.getByRole('button', { name: 'Undo the change to goals.max_turns' })).toBeVisible();
			// It arrives moving (the reduced-motion test below says when it must not), and the key
			// is on one line: centred at 50% it had half the width and broke over four at 390.
			expect(await offer.evaluate((el) => getComputedStyle(el).animationName)).not.toBe('none');
			const box = (await offer.boundingBox())!;
			expect(box.height).toBeLessThan(70);
			expect(box.x + box.width).toBeLessThanOrEqual(size.width);

			// Newest first: when, who, the key and both values.
			await expect(rows).toHaveCount(3);
			await expect(rows.nth(0)).toHaveAttribute('data-key', 'goals.max_turns');
			await expect(rows.nth(0).getByTestId('change-before')).toHaveText('30');
			await expect(rows.nth(0).getByTestId('change-after')).toHaveText('40');
			await expect(rows.nth(0).getByTestId('change-who')).toContainText('just now');
			await expect(rows.nth(0).getByTestId('change-who')).toContainText(/JARVIS|AURA/i);
			// Put back on the newest change of a key only, and never on a secret.
			await expect(rows.nth(0).getByTestId('change-undo')).toBeVisible();
			await expect(rows.nth(1).getByTestId('change-before')).toHaveText('20');
			await expect(rows.nth(1).getByTestId('change-undo')).toHaveCount(0);
			await expect(rows.nth(2)).toHaveAttribute('data-key', 'house.token');
			await expect(rows.nth(2).getByTestId('change-secret')).toBeVisible();
			await expect(rows.nth(2).getByTestId('change-after')).toHaveCount(0);
			await expect(rows.nth(2).getByTestId('change-undo')).toHaveCount(0);
			await expect(page.getByTestId('screen-settings')).not.toContainText('made-up-token-value');
			expect(await overflow(page)).toBeLessThanOrEqual(0);

			// A pointer on the offer holds it, so the picture has it; the list is scrolled into it.
			await offer.hover();
			await page.getByTestId('settings-changes').scrollIntoViewIfNeeded();
			await page.screenshot({ path: `test-results/v2-settings-undo-${size.width}.png`, animations: 'disabled' });
			await page.waitForTimeout(5_500);
			await expect(offer, 'held while the pointer is on it').toBeVisible();
			// Off it, it goes after the token's five seconds and not before.
			const left = Date.now();
			await page.mouse.move(1, 1);
			await expect(offer).toHaveCount(0, { timeout: 9_000 });
			expect(Date.now() - left).toBeGreaterThanOrEqual(4_000);

			// Undo puts the value back, says so, and the write it made is not offered an Undo.
			await asked(page, 'goals.max_turns', 50);
			await page.getByTestId('settings-undo-button').click();
			await expect(page.getByTestId('shell-status')).toHaveText('Put goals.max_turns back.');
			expect(await valueOf(page, 'goals.max_turns')).toBe(40);
			await expect(rows.nth(0).getByTestId('change-who')).toContainText('An app');
			await expect(offer).toHaveCount(0);

			// Put back on the row does the same thing, with no timer: here it is the redo.
			await rows.nth(0).getByRole('button', { name: 'Put back goals.max_turns' }).click();
			await expect(rows.nth(0).getByTestId('change-after')).toHaveText('50');
			expect(await valueOf(page, 'goals.max_turns')).toBe(50);
			await expect(offer).toHaveCount(0);

			// Six changes by now: three are drawn, the rest are one press away.
			await expect(rows).toHaveCount(3);
			await page.getByTestId('changes-all').click();
			await expect(rows).toHaveCount(6);
			expect(await overflow(page)).toBeLessThanOrEqual(0);
		});

		test(`a security setting put back waits under Approvals and is not said to be back (${size.width})`, async ({ page }) => {
			await page.setViewportSize(size);
			await openShell(page, '/settings');
			await asked(page, 'approvals.mode', 'manual');
			// By the keyboard: the offer's one control takes focus and Enter presses it.
			await page.getByTestId('settings-undo-button').focus();
			await page.keyboard.press('Enter');
			await expect(page.getByTestId('shell-status')).toContainText('waiting for your OK under Approvals');
			await expect(page.getByTestId('shell-status')).not.toContainText('Put approvals.mode back');
			expect(await valueOf(page, 'approvals.mode')).toBe('manual');
			expect(((await jsettings(page, 'proposals')) as { key: string; after: unknown }[]).map((p) => [p.key, p.after])).toEqual([['approvals.mode', 'smart']]);
		});
	}

	test('the change log that cannot be read says why, and Retry reads it', async ({ page }) => {
		await page.setViewportSize({ width: 1440, height: 900 });
		await openShell(page, '/settings');
		await expect(page.getByTestId('settings-changes-state')).toHaveAttribute('data-state', 'empty');
		await mock(page, 'test.setting', { history: false });
		await page.reload();
		const state = page.getByTestId('settings-changes-state');
		await expect(state).toHaveAttribute('data-state', 'error');
		await expect(state).toContainText('the change log could not be read');
		await mock(page, 'test.setting', { history: true });
		await state.getByRole('button', { name: 'Retry' }).click();
		await expect(state).toHaveAttribute('data-state', 'empty');
	});

	test('with reduced motion the offer arrives without moving', async ({ page }) => {
		await page.emulateMedia({ reducedMotion: 'reduce' });
		await page.setViewportSize({ width: 1440, height: 900 });
		await openShell(page, '/settings');
		await asked(page, 'goals.max_turns', 30);
		const offer = page.getByTestId('settings-undo');
		await expect(offer).toBeVisible();
		expect(await offer.evaluate((el) => getComputedStyle(el).animationName)).toBe('none');
	});
});

test('Recent changes says it is offline when the link is down', async ({ page }) => {
	await page.routeWebSocket(/\/hermes\/ws/, (ws) => ws.close());
	await page.setViewportSize({ width: 1440, height: 900 });
	await openShell(page, '/settings');
	await expect(page.getByTestId('settings-changes-state')).toHaveAttribute('data-state', 'offline');
});
