// The one route under the v2 shell: every path the console ever had still
// resolves (the phone's strip, bookmarks and notes carry them), each to the
// screen `lib/ds/shell/routes.ts` names, and anything else is a real 404. And
// the links come back on their own after a gateway restart.
//
// The tables are imported, not copied, so a path added to `hud/activities.ts`
// or a screen added to `routes.ts` is walked here without anybody remembering.
//
// Does not cover: what each screen shows (v2-screens, v2-chat, v2-ops, v2-car).
import { expect, test, type Page } from '@playwright/test';

import { ACTIVITIES, ALIASES } from '../src/lib/hud/activities';
import { screenOf } from '../src/lib/ds/shell/routes';
import { openShell, settledAt } from './v2';

/** The shell at `path` shows `screen`: the stage for the core, or the screen's own root. */
async function shows(page: Page, path: string): Promise<void> {
	const screen = screenOf(path);
	await openShell(page, path);
	await expect(page.getByTestId('shell')).toHaveAttribute('data-screen', screen);
	await expect(page.getByTestId(screen === 'core' ? 'stage' : `screen-${screen}`)).toBeVisible();
}

const paths = [...new Set([...Object.keys(ACTIVITIES), ...Object.keys(ALIASES), '/approvals', '/messages', '/ops', '/board'])];
for (const path of paths) {
	test(`${path} opens ${screenOf(path) === 'core' ? 'the core' : `the ${screenOf(path)} screen`}`, async ({ page }) => {
		const res = await page.goto(path);
		expect(res?.status(), 'a known path is a 200').toBe(200);
		await shows(page, path);
	});
}

test('a path the console has no screen for is a 404 with a way back', async ({ page }) => {
	const res = await page.goto('/nowhere/at/all');
	expect(res?.status()).toBe(404);
	await expect(page.getByTestId('route-error')).toBeVisible();
	await expect(page.getByTestId('error')).toContainText('There is no screen here');
	// The worn name (AURA unless the JARVIS theme is worn), so match its start only.
	await page.getByRole('link', { name: /^Back to / }).click();
	await expect(page.getByTestId('shell')).toBeVisible({ timeout: 15_000 });
	await expect(page).toHaveURL(/\/$/);
});

/**
 * A gateway restart, as an open console sees it: every automatic deploy takes
 * both sockets down and brings the servers back a few seconds later. The core
 * has to say it is down meanwhile, so nobody reads a stale reply as live, and
 * come back with no click (the links re-dial with backoff, capped at 8 s).
 */
test('the console comes back on its own after every socket is dropped', async ({ page }) => {
	await openShell(page);
	await settledAt(page, 'idle');
	const shell = page.getByTestId('shell');
	await expect(shell).toHaveAttribute('data-reactor', 'idle');
	await page.evaluate(() => ((window as unknown as { __samePage: boolean }).__samePage = true));

	const dropped = await page.request.post('/hermes/api/test/drop');
	expect(dropped.ok()).toBe(true);
	const { dropped: hermes, house } = (await dropped.json()) as { dropped: number; house: number };
	expect(hermes, 'the Hermes socket was open').toBeGreaterThan(0);
	expect(house, 'the house socket was open').toBeGreaterThan(0);

	await expect(shell).toHaveAttribute('data-reactor', 'offline', { timeout: 5_000 });
	await expect(shell).toHaveAttribute('data-reactor', 'idle', { timeout: 20_000 });
	await settledAt(page, 'idle');
	// A reconnect, not a reload: the page that lost the link is the one that has it back.
	expect(await page.evaluate(() => (window as unknown as { __samePage?: boolean }).__samePage)).toBe(true);
});
