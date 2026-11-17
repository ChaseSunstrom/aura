// The two shipped themes, each worn whole (docs/screens/<theme>/), and a drafted theme
// shown, then worn, then fetched again by id after a reload (T-0870).
//
// The theme is this device's override (`jv.theme`, Settings' "Keep on this device"), and the
// mock's primary persona is the theme's own, so AURA's shots carry her name as the house does
// (the mock's JARVIS wrote "JARVIS" on AURA's first ones). Each theme: the core, an answer
// card, an approval and Settings at two widths, and the car's screen (T-0861 §7).
import { expect, test, type Page } from '@playwright/test';
import { mkdirSync } from 'node:fs';

import { THEMES, THEME_IDS } from '../src/lib/themes';
import { SNAPSHOT_APPROVAL, demo, emit, openShell, resetHermes, settledAt } from './v2';

const OUT = (id: string) => new URL(`../../docs/screens/${id}/`, import.meta.url).pathname;
const WIDTHS = [
	{ width: 1440, height: 900 },
	{ width: 390, height: 844 }
];

const theme = (page: Page, data: Record<string, unknown>) => page.request.post('/hermes/api/test/theme', { data });
const worn = (page: Page) => page.evaluate(() => document.documentElement.dataset.theme);
/** The mock persona's (jarvis) report landed and its tween finished: a draft applied before then is tweened over. */
const settled = async (page: Page) => {
	await expect.poll(() => worn(page)).toBe('jarvis');
	await expect.poll(() => page.evaluate(() => document.documentElement.style.getPropertyValue('--jv-bg'))).toBe('');
};

// The persona and any drafted pack go back for the next spec.
test.afterEach(async ({ page }) => resetHermes(page));

for (const id of THEME_IDS) {
	test(`${id}: the core, a card, an approval, Settings and the car, worn whole`, async ({ page }) => {
		await page.emulateMedia({ reducedMotion: 'reduce' });
		await page.addInitScript((t) => localStorage.setItem('jv.theme', t), id);
		await page.request.post('/hermes/api/test/status', { data: { persona: id } });
		mkdirSync(OUT(id), { recursive: true });
		for (const size of WIDTHS) {
			await page.setViewportSize(size);
			await openShell(page, '/');
			await expect.poll(() => worn(page)).toBe(id);
			await expect(page.locator('a.mark')).toHaveText(THEMES[id].name);
			await page.screenshot({ path: `${OUT(id)}core-${size.width}.png` });
			await demo(page, 'answer');
			await settledAt(page, 'answer');
			await page.screenshot({ path: `${OUT(id)}card-${size.width}.png` });
			await demo(page, '');
			await emit(page, 'approval.request', SNAPSHOT_APPROVAL);
			await expect(page.getByTestId('approval-card').first()).toBeVisible({ timeout: 10_000 });
			await page.screenshot({ path: `${OUT(id)}approval-${size.width}.png` });
			await openShell(page, '/settings');
			await expect(page.getByTestId(`theme-keep-${id}`)).toHaveAttribute('aria-pressed', 'true');
			// The shell scrolls inside itself, so a full-page shot stops at Account.
			await page.getByTestId('settings-theme').scrollIntoViewIfNeeded();
			await page.screenshot({ path: `${OUT(id)}settings-${size.width}.png` });
		}
		await page.setViewportSize({ width: 800, height: 480 });
		await page.goto('/car');
		await expect(page.getByTestId('screen-car')).toBeVisible({ timeout: 15_000 });
		await page.screenshot({ path: `${OUT(id)}car-800.png` });
	});
}

test.describe('a drafted theme', () => {
	// One shipped family with the other's colours: a draft unlike either shipped theme.
	const [family, colours] = THEME_IDS;
	const pack = { ...THEMES[family], id: 'dusk', name: 'Dusk', color: THEMES[colours].color };
	// The same draft with an accent no shipped theme has, so a tween toward one never matches it.
	const tinted = (accent: string) => ({ ...pack, color: { ...pack.color, accent } });


	test('a preview is shown, not worn; apply wears it; a reload fetches it by id', async ({ page }) => {
		await openShell(page, '/');
		// The default theme is worn until the mock persona's (jarvis) report lands, then tweens
		// to it inline: read once that has settled.
		const bg = () => page.evaluate(() => document.documentElement.style.getPropertyValue('--jv-bg'));
		await expect.poll(() => worn(page)).toBe('jarvis');
		await expect.poll(bg).toBe('');
		const before = await worn(page);

		await theme(page, { op: 'preview', pack });
		await expect(page.getByTestId('theme-preview')).toBeVisible();
		expect(await worn(page)).toBe(before);
		expect(await bg()).toBe('');

		await theme(page, { op: 'apply', id: 'dusk', pack });
		await expect.poll(() => worn(page)).toBe(family);
		await expect.poll(bg).not.toBe('');

		// A fresh page has no theme.apply event, only the persona's theme id: it fetches the pack.
		// Once: the fetch once ran on every HUD event, five times on this reload.
		let fetches = 0;
		page.on('request', (r) => {
			if (r.url().endsWith('/hermes/api/themes/dusk')) fetches += 1;
		});
		await page.reload();
		await expect.poll(() => fetches).toBe(1);
		await expect.poll(bg).not.toBe('');
		await page.waitForLoadState('networkidle');
		expect(fetches).toBe(1);
	});

	test('a reload that meets a house that cannot answer asks again and still wears the draft', async ({ page }) => {
		await openShell(page, '/');
		await settled(page);
		await theme(page, { op: 'apply', id: 'dusk', pack: tinted('#ff8a3d') });
		const accent = () =>
			page.evaluate(() => document.documentElement.style.getPropertyValue('--jv-accent'));
		await expect.poll(accent).toBe('#ff8a3d');

		// The house restarting under the reload: its first answer is a 502.
		let failed = 0;
		await page.route('**/hermes/api/themes/dusk', (route) =>
			failed++ ? route.continue() : route.fulfill({ status: 502, body: '' })
		);
		await page.reload();
		await expect.poll(() => failed).toBeGreaterThanOrEqual(1);
		await expect.poll(accent, { timeout: 15_000 }).toBe('#ff8a3d');
		expect(failed).toBe(2);
	});

	test('a pack re-drafted under the same id is the one worn when it comes back by id alone', async ({ page }) => {
		await openShell(page, '/');
		await settled(page);
		await theme(page, { op: 'apply', id: 'dusk', pack: tinted('#ff8a3d') });
		const accent = () =>
			page.evaluate(() => document.documentElement.style.getPropertyValue('--jv-accent'));
		await page.reload();
		await expect.poll(accent).toBe('#ff8a3d');

		// Re-drafted (theme.apply carries the new pack), then off and back on by id alone.
		await theme(page, { op: 'apply', id: 'dusk', pack: tinted('#7ae582') });
		await expect.poll(accent).toBe('#7ae582');
		await theme(page, { op: 'apply', id: family });
		await expect.poll(accent).toBe('');
		await theme(page, { op: 'apply', id: 'dusk' });
		await expect.poll(accent).toBe('#7ae582');
	});
});
