// T-0898: under a pack whose ground is a starfield (AURA) the surfaces over the stage are
// the projections' glass, and under JARVIS they are what they were. The owner, 2026-11-09:
// "have it be the nice glass effect with the space stuff from the core screen and make all
// UI for the AURA theme that way too". A sheet and a side panel are see-through (the
// pack's panel fill over its translucent panel surface, the stars behind); the island and
// the gate stay opaque (the desktop is behind one, the locked console behind the other)
// and carry the starfield inside them instead.
import { expect, test, type Locator, type Page } from '@playwright/test';

import { openShell, resetHermes } from './v2';

const ground = (loc: Locator) =>
	loc.evaluate((el) => {
		const s = getComputedStyle(el);
		return { color: s.backgroundColor, image: s.backgroundImage };
	});
const alpha = (c: string) => {
	const parts = c.match(/\(([^)]+)\)/)![1].split(/[,\s/]+/).filter(Boolean).map(Number);
	return parts.length === 4 ? parts[3] : 1;
};
/** What a token resolves to as a background colour on this page. */
const resolved = (page: Page, token: string) =>
	page.evaluate((t) => {
		const probe = document.createElement('div');
		probe.style.background = `var(${t})`;
		document.body.append(probe);
		const c = getComputedStyle(probe).backgroundColor;
		probe.remove();
		return c;
	}, token);

async function wear(page: Page, id: 'aura' | 'jarvis', path = '/'): Promise<void> {
	await page.emulateMedia({ reducedMotion: 'reduce' });
	await page.addInitScript((t) => localStorage.setItem('jv.theme', t), id);
	await page.request.post('/hermes/api/test/status', { data: { persona: id } });
	await openShell(page, path);
	await expect.poll(() => page.evaluate(() => document.documentElement.dataset.theme)).toBe(id);
}

test.afterEach(async ({ page }) => resetHermes(page));

test('AURA: a sheet and a side panel are glass over the starfield', async ({ page }) => {
	await page.setViewportSize({ width: 1440, height: 900 });
	await wear(page, 'aura');
	await expect(page.getByTestId('starfield')).toHaveCount(1);

	await page.getByRole('button', { name: 'All apps' }).click();
	const sheet = await ground(page.getByTestId('launcher'));
	expect(alpha(sheet.color), 'the sheet lets the stars through').toBeLessThan(1);
	expect(sheet.image, "the sheet wears the pack's panel fill").toContain('gradient');
	await page.keyboard.press('Escape');

	await page.getByRole('button', { name: 'All apps' }).click();
	await page.getByTestId('launcher').getByRole('link', { name: /^Settings/ }).click();
	const panel = await ground(page.getByTestId('side-panel'));
	expect(alpha(panel.color), 'the panel lets the stars through').toBeLessThan(1);
	expect(alpha(panel.color), 'but it is a panel, not bare stage').toBeGreaterThan(0);
	expect(panel.image).toContain('gradient');
	// T-0974: and it is one of her projections, their gradient rim and star in its corner (the
	// owner, 2026-11-10: "match those settings/options views with the matching glass panel look").
	const marks = await page.getByTestId('side-panel').evaluate((el) => {
		const rim = getComputedStyle(el, '::before');
		return { star: getComputedStyle(el, '::after').clipPath, rim: rim.maskImage || rim.webkitMaskImage, edge: rim.backgroundImage };
	});
	expect(marks.star, "the projections' star").toContain('polygon');
	expect(marks.rim, 'a rim, masked to its edge').toContain('gradient');
	expect(marks.edge, "in the pack's accent gradient").toContain('gradient');
});

test('JARVIS: the sheet and the side panel are as they were', async ({ page }) => {
	await page.setViewportSize({ width: 1440, height: 900 });
	await wear(page, 'jarvis');
	await expect(page.getByTestId('starfield')).toHaveCount(0);

	await page.getByRole('button', { name: 'All apps' }).click();
	const sheet = await ground(page.getByTestId('launcher'));
	expect(sheet.color).toBe(await resolved(page, '--jv-panel-solid'));
	expect(sheet.image).toBe('none');
	await page.keyboard.press('Escape');

	await page.getByRole('button', { name: 'All apps' }).click();
	await page.getByTestId('launcher').getByRole('link', { name: /^Settings/ }).click();
	const panel = await ground(page.getByTestId('side-panel'));
	expect(alpha(panel.color), 'the stage shows through, as before').toBe(0);
	expect(panel.image).toBe('none');
	// T-0974: with the bracket JARVIS's projections carry in their corner.
	const bracket = await page.getByTestId('side-panel').evaluate((el) => getComputedStyle(el, '::after').borderTopWidth);
	expect(bracket).toBe('1px');
});

test('a locked console under AURA shows one sky, the gate’s, not a second one twinkling unseen behind it', async ({ page }) => {
	await page.setViewportSize({ width: 1440, height: 900 });
	// The password v2-gate.spec sets: chosen if this machine has none yet, verified otherwise.
	const password = 'e2e-console-password'; // pragma: allowlist secret
	expect((await page.request.post('/api/console', { data: { password } })).ok()).toBe(true);
	await wear(page, 'aura', '/settings');
	await expect(page.getByTestId('lock-now')).toBeEnabled({ timeout: 10_000 });
	await page.getByTestId('lock-now').click();
	await expect(page.getByTestId('lock-screen')).toBeVisible();
	await expect(page.getByTestId('starfield')).toHaveCount(1);
	await expect(page.getByTestId('lock-screen').getByTestId('starfield')).toHaveCount(1);
	// T-0946: the lock ends the session and reloads the page. Wait that reload out and look
	// again: the signed-out pane is the one the owner sits in front of, and the cleanup's
	// page.evaluate died in the navigation whenever it started before the reload had.
	await expect.poll(async () => (await page.request.get('/api/state')).status(), { timeout: 10_000 }).toBe(401);
	await page.waitForFunction(() => performance.getEntriesByType('navigation')[0]?.type === 'reload');
	await expect(page.getByTestId('lock-screen').getByTestId('starfield')).toHaveCount(1);
	await expect(page.getByTestId('starfield')).toHaveCount(1);
	// Signed in again for the cleanup: a signed-out page is refused the Hermes socket the
	// reset opens, and the reset then waited the whole test timeout for it.
	expect((await page.request.post('/api/console', { data: { password } })).ok()).toBe(true);
	await page.reload();
	await expect(page.getByTestId('lock-screen')).toHaveCount(0, { timeout: 10_000 });
});

test('the island carries the starfield inside it under AURA, and not under JARVIS', async ({ page }) => {
	await page.setViewportSize({ width: 1024, height: 768 });
	await wear(page, 'aura', '/?popup=1');
	await expect(page.getByTestId('popup').getByTestId('starfield')).toHaveCount(1);
	await resetHermes(page);

	await wear(page, 'jarvis', '/?popup=1');
	await expect(page.getByTestId('popup')).toBeVisible();
	await expect(page.getByTestId('starfield')).toHaveCount(0);
});
