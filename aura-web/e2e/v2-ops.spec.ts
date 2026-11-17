// The Ops board as a v2 screen (Ops.dc.html; replaces hud.spec.ts's /ops row):
// its nine panes under the shell's top bar, the Activity pane asking JARVIS for
// the audit digest, the way back to the core, and a phone's width.
import { expect, test } from '@playwright/test';

import { layoutPx, openShell } from './v2';

test('/ops is the board: nine panes under the top bar, and the Activity pane asks JARVIS', async ({ page }) => {
	await page.setViewportSize({ width: 1440, height: 900 });
	await openShell(page, '/ops');
	const ops = page.getByTestId('screen-ops');
	await expect(ops).toBeVisible();
	await expect(ops.getByRole('heading', { level: 1, name: 'Ops' })).toBeVisible();
	await expect(ops.locator('.pane')).toHaveCount(9);
	// The header prints the adapter's own word (T-0583), not one the page worked out.
	await expect(ops.getByTestId('ops-health')).toHaveText(/^ready$/i);
	// The board sits under the shell's top bar, never over it.
	await expect(page.getByRole('button', { name: 'All apps' })).toBeVisible();
	const bar = (await page.locator('header.bar').boundingBox())!;
	expect((await ops.boundingBox())!.y).toBeGreaterThanOrEqual(bar.y + bar.height - 1);
	await ops.getByTestId('ops-ask').first().click();
	await expect(ops.getByTestId('ops-digest')).not.toBeEmpty({ timeout: 15_000 });
	// Sent today asks for the audit log's outbound rows by name (T-0573).
	await ops.getByRole('button', { name: 'Sent today' }).click();
	await expect(ops.getByText('What went out of the house today?')).toBeVisible();
	await ops.getByTestId('ops-close').click();
	await expect(page).toHaveURL(/\/$/);
	await expect(ops).toHaveCount(0);
});

test('/ops on a phone fits the width and keeps its targets large', async ({ page }) => {
	await page.setViewportSize({ width: 390, height: 844 });
	await openShell(page, '/ops');
	const ops = page.getByTestId('screen-ops');
	await expect(ops.locator('.pane').first()).toBeVisible();
	// The panes swing up staggered; a box is measured once they have landed.
	await page.waitForFunction(() => document.getAnimations().every((a) => a.effect?.getTiming().iterations === Infinity || a.playState === 'finished'));
	const wide = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
	expect(wide, 'no sideways scroll').toBeLessThanOrEqual(0);
	for (const b of await ops.locator('button:visible').all()) expect(layoutPx((await b.boundingBox())!.height), await b.innerText()).toBeGreaterThanOrEqual(44);
});
