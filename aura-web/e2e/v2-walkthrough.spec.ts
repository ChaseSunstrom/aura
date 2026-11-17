// The first-run walkthrough on the v2 shell (T-0629), against the mock: the six steps in
// order with what the house already shows marked done, a step's request to JARVIS showing
// what was done and what waits on a yes, the owner's marks surviving a reload, and an
// open walkthrough offered once more on the core and then never by itself.
// Not covered: what a real model proposes; the reply shown once a turn ends; the hand-off from the first-run password sheet;
// a console with no row at all (the model's own test pins that it is not shown).
import { expect, test, type Page } from '@playwright/test';

import { openShell, resetHermes } from './v2';

const row = (page: Page, marks: Record<string, string>, closed: boolean) =>
	page.request.put('/api/console/walkthrough', { data: { progress: JSON.stringify({ schema: 1, marks, closed }) } });

test.beforeEach(async ({ page }) => {
	await page.setViewportSize({ width: 1440, height: 900 });
	expect((await row(page, {}, false)).ok()).toBe(true);
});
// Left closed: a row with open steps sends every later spec's core to /setup.
test.afterEach(async ({ page }) => {
	await row(page, {}, true);
});

test('six steps in order; the house decides three, the owner marks the rest, and a reload resumes', async ({ page }) => {
	await openShell(page, '/setup');
	const steps = page.getByTestId('setup-rail').getByRole('button');
	await expect(steps).toHaveCount(6);
	await expect(steps).toContainText(['Account', 'Voice and people', 'Persona', 'Privacy and approvals', 'Devices', 'Integrations']);
	// The mock house has a password, an enrolled owner and paired devices.
	for (const s of ['account', 'people', 'devices']) await expect(page.getByTestId(`setup-step-${s}`)).toHaveAttribute('data-state', 'done');
	await expect(page.getByTestId('setup-step-persona')).toHaveAttribute('aria-current', 'step');

	// A step the house decides has no "Mark done".
	await page.getByTestId('setup-step-account').click();
	await expect(page.getByTestId('setup-done')).toHaveCount(0);
	await page.getByTestId('setup-step-persona').click();

	await page.getByTestId('setup-done').click();
	await expect(page.getByTestId('setup-step-persona')).toHaveAttribute('data-state', 'done');
	await expect(page.getByTestId('setup-step-privacy')).toHaveAttribute('aria-current', 'step');
	await page.getByTestId('setup-skip').click();
	await expect(page.getByTestId('setup-step-privacy')).toHaveAttribute('data-state', 'skipped');
	await expect(page.getByTestId('setup-step-integrations')).toHaveAttribute('aria-current', 'step');

	await page.reload();
	await expect(page.getByTestId('setup-step-integrations')).toHaveAttribute('aria-current', 'step');
	await expect(page.getByTestId('setup-step-persona')).toHaveAttribute('data-state', 'done');
	await expect(page.getByTestId('setup-step-privacy')).toHaveAttribute('data-state', 'skipped');

	await page.getByTestId('setup-done').click();
	await expect(page.getByTestId('setup-finished')).toBeVisible();
});

test('asking JARVIS for a step shows what was done and what waits on a yes', async ({ page }) => {
	await openShell(page, '/setup');
	await expect(page.getByTestId('setup-proposal')).toHaveCount(0);
	await page.getByTestId('setup-want').fill('a calm voice');
	await page.getByTestId('setup-propose').click();
	await expect(page.getByTestId('setup-proposal')).toBeVisible();
	// The mock's turn stops on a request for the owner: the step says one waits and where to answer.
	await expect(page.getByTestId('setup-waiting')).toHaveText('1 request. Answer on the card.', { timeout: 15_000 });
	await expect(page.getByTestId('setup-proposal')).toContainText('Working');
	await resetHermes(page);
});

test('an open walkthrough is offered once more on the core, and then never by itself', async ({ page }) => {
	await openShell(page, '/');
	await expect(page).toHaveURL(/\/setup$/);
	await expect(page.getByTestId('screen-setup')).toBeVisible();
	await openShell(page, '/');
	await expect(page.getByTestId('screen-setup')).toHaveCount(0);
	await expect(page).not.toHaveURL(/\/setup$/);
});

test('Settings opens it, and a row that is not one is refused', async ({ page }) => {
	await row(page, {}, true);
	await openShell(page, '/settings');
	await page.getByTestId('settings-setup').click();
	await expect(page.getByTestId('screen-setup')).toBeVisible();
	const bad = await page.request.put('/api/console/walkthrough', { data: { progress: '{"schema":9}' } });
	expect(bad.status()).toBe(400);
});
