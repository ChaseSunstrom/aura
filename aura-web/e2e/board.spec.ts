// The Board (T-0733; the owner, 2026-11-08: "graphs with the differnent models/tasks … inspired
// from the panel UI on the sampo live site"). mock-hermes answers GET /api/board in the adapter's
// shape; `POST /api/test/board` picks what it says. Each panel draws its own source's state: a
// stale board says its age, a missing one its reason, the models server down takes out only LOAD,
// and the screen's own states are an error with Retry and offline over the last board.

import { expect, test, type Page } from '@playwright/test';
import { openShell } from './v2';

const board = (page: Page, mode: string) => page.request.post('/hermes/api/test/board', { data: { mode } });
const NAMES = ['01 MODELS', '02 TURNS', '03 TASKS', '04 TOOLS', '05 LOAD', '06 SUITES', '07 VERIFIED'];

test.afterEach(async ({ page }) => {
	await board(page, 'ok');
});

for (const size of [{ width: 1440, height: 900 }, { width: 768, height: 1024 }, { width: 360, height: 780 }]) {
	test(`six numbered panels on the mock's data, none overflowing, at ${size.width}`, async ({ page }) => {
		await page.setViewportSize(size);
		await board(page, 'ok');
		await openShell(page, '/board');
		const panels = page.locator('[data-testid^="board-panel-"]');
		await expect(panels).toHaveCount(7);
		for (const [i, name] of NAMES.entries()) {
			const p = panels.nth(i);
			await expect(p).toHaveAttribute('data-state', 'ok');
			await expect(p.locator('h2')).toHaveText(name.slice(3));
			await expect(p.locator('svg')).toBeVisible();
		}
		await expect(page.getByTestId('board-label-01')).toHaveText('47 TURNS / 24 H');
		await expect(page.getByTestId('board-label-03')).toHaveText('41 OF 47 ANSWERED');
		await expect(page.getByTestId('board-label-05')).toHaveText('1 READY');
		await expect(panels.nth(1).locator('footer')).toContainText('no cost recorded');
		const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
		expect(overflow).toBeLessThanOrEqual(0);
	});
}

test('a stale board says its age; a missing one says why, and the live load still stands', async ({ page }) => {
	await board(page, 'stale');
	await openShell(page, '/board');
	await expect(page.getByTestId('board-panel-01')).toHaveAttribute('data-state', 'stale');
	await expect(page.getByTestId('board-panel-01').locator('footer')).toContainText('as of 1 h ago');
	await board(page, 'missing');
	await page.reload();
	for (const n of ['01', '02', '03', '04']) {
		await expect(page.getByTestId(`board-panel-${n}`)).toHaveAttribute('data-state', 'unavailable');
		await expect(page.getByTestId(`board-panel-${n}`)).toContainText('not written yet');
	}
	await expect(page.getByTestId('board-panel-05')).toHaveAttribute('data-state', 'ok');
	await expect(page.getByTestId('board-panel-06')).toHaveAttribute('data-state', 'ok');
});

test('the models server down takes out only LOAD', async ({ page }) => {
	await board(page, 'models-down');
	await openShell(page, '/board');
	await expect(page.getByTestId('board-panel-05')).toHaveAttribute('data-state', 'unavailable');
	await expect(page.getByTestId('board-panel-05')).toContainText('ConnectError');
	await expect(page.getByTestId('board-panel-01')).toHaveAttribute('data-state', 'ok');
});

test('an adapter error is the error state with a Retry that recovers', async ({ page }) => {
	await board(page, 'error');
	await openShell(page, '/board');
	const state = page.getByTestId('board-state');
	await expect(state).toHaveAttribute('data-state', 'error');
	await expect(state).toContainText('the adapter answered 500');
	await board(page, 'ok');
	await state.getByRole('button', { name: 'Retry' }).click();
	await expect(page.locator('[data-testid^="board-panel-"]')).toHaveCount(7);
});

test('the adapter out of reach is offline over the last board, then live again', async ({ page }) => {
	await board(page, 'ok');
	await openShell(page, '/board');
	await expect(page.locator('[data-testid^="board-panel-"]')).toHaveCount(7);
	await board(page, 'gone');
	await page.evaluate(() => document.dispatchEvent(new Event('visibilitychange')));
	await expect(page.getByTestId('board-state')).toHaveAttribute('data-state', 'offline');
	await expect(page.locator('[data-testid^="board-panel-"]')).toHaveCount(7);
	await board(page, 'ok');
	await page.getByTestId('board-state').getByRole('button', { name: 'Retry' }).click();
	await expect(page.getByTestId('board-state')).toHaveCount(0);
});

test('the launcher lists the Board', async ({ page }) => {
	await openShell(page, '/board');
	await expect(page).toHaveTitle(/Board/);
});
