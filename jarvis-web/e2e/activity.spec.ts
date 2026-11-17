// What JARVIS is doing, while it does it (T-0732; the owner, 2026-11-08: "theres no visual
// representation of it doing different tasks, like browsing the website"). mock-hermes's browse
// turn sends Hermes's real shapes (a search with its sources, a navigate with the
// jarvis-activity plugin's frame and JPEG, `duration_s`); the stage's steps projection draws
// each step with its detail, the page JARVIS has open, and a Stop that ends the run.

import { expect, test } from '@playwright/test';
import { openShell, resetHermes, say, settledAt } from './v2';

for (const size of [{ width: 1440, height: 900 }, { width: 360, height: 780 }]) {
	test.describe(`at ${size.width}x${size.height}`, () => {
		test.beforeEach(async ({ page }) => {
			await page.setViewportSize(size);
			await openShell(page);
			await resetHermes(page);
			await settledAt(page, 'idle');
		});

		test('a browse turn shows its steps, the sources and the page it has open, inside the screen', async ({ page }) => {
			await say(page, 'browse the tide times');
			const steps = page.getByTestId('stage-steps');
			await expect(steps.locator('li.step')).toHaveCount(2);
			const [search, browse] = [steps.locator('li.step').nth(0), steps.locator('li.step').nth(1)];
			await expect(search).toHaveAttribute('data-state', 'done');
			await expect(search).toHaveAttribute('data-kind', 'search');
			await expect(search.locator('.what')).toHaveText('Searched the web · tide times');
			await expect(search.locator('.detail')).toHaveText('2 sources');
			await expect(search).toContainText('1.2 s');
			await expect(browse).toHaveAttribute('data-state', 'acting');
			await expect(browse).toHaveAttribute('data-current', 'true');
			// The host is in the step's words, so the detail line carries only the page's title.
			await expect(browse.locator('.what')).toContainText('tides.example');
			await expect(browse.locator('.line')).toHaveText('Tide tables');
			const picture = page.getByTestId('step-page');
			await expect(picture).toBeVisible();
			await expect(picture).toHaveAttribute('src', /^data:image\/jpeg;base64,/);
			await expect(picture).toHaveAttribute('alt', 'The page JARVIS has open: Tide tables');
			// Inside the screen at either width: no step, picture or Stop runs off it.
			for (const el of [steps, picture, page.getByTestId('stage-stop')]) {
				const box = (await el.boundingBox())!;
				expect(box.x).toBeGreaterThanOrEqual(0);
				expect(box.x + box.width).toBeLessThanOrEqual(size.width);
			}
			// The run finishes on its own: the reply is the answer scene's, the steps and Stop go.
			await expect(page.getByTestId('stage')).toContainText('High tide is at 14:20.', { timeout: 8000 });
			await expect(page.getByTestId('stage-stop')).toHaveCount(0);
		});

		test('Stop ends the run while the page is open', async ({ page }) => {
			await say(page, 'browse the tide times');
			const browse = page.getByTestId('stage-steps').locator('li.step[data-kind="browse"]');
			await expect(browse).toHaveAttribute('data-state', 'acting');
			await page.getByTestId('stage-stop').click();
			// The interrupt ends the turn: nothing is acting, and the mock sends no reply after it.
			await expect(page.getByTestId('shell')).not.toHaveAttribute('data-scene', 'thinking', { timeout: 5000 });
			// Past the mock's longest hold (browseTurn waits up to 4 s for the interrupt), after which
			// an un-stopped turn would have sent its reply.
			await page.waitForTimeout(4500);
			await expect(page.getByTestId('stage')).not.toContainText('High tide is at 14:20.');
		});
	});
}
