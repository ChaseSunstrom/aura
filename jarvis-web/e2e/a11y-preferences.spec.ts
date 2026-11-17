import { test, expect, type Page } from '@playwright/test';
import { APPS } from '../src/lib/ds/shell/routes';
import { SCENES, SNAPSHOT_APPROVAL, blurLayers, demo, emit, openShell, settledAt } from './v2';

/**
 * The OS preferences on the v2 shell.
 *
 * - `prefers-reduced-motion: reduce`: only opacity moves. Every running
 *   animation and transition is read back through `document.getAnimations()`
 *   while the core changes scene, a card comes out and the launcher opens, and
 *   any keyframe that touches a property other than opacity fails. The core
 *   itself is a canvas, which no animation API sees, so it is photographed
 *   twice half a second apart and must not have changed.
 * - `prefers-reduced-transparency: reduce`: no `backdrop-filter` anywhere, on
 *   the core, a sheet or any screen. This media feature has no Playwright
 *   emulation; it is set through CDP's `Emulation.setEmulatedMedia`. Since
 *   Reactor II, and DECISIONS 60 for the popup, nothing carries one at all, so
 *   the count is also asserted zero without the preference.
 *
 * What it does not check: `forced-colors`, which has no design yet.
 */

const OPACITY_ONLY = new Set(['opacity', 'offset', 'computedOffset', 'easing', 'composite']);

/** Every running animation whose keyframes move something other than opacity. */
const movingAnimations = (page: Page) =>
	page.evaluate(() =>
		document
			.getAnimations()
			.filter((a) => a.playState === 'running' || a.playState === 'pending')
			.map((a) => {
				const effect = a.effect as KeyframeEffect | null;
				const props = new Set<string>();
				for (const kf of effect?.getKeyframes() ?? []) for (const k of Object.keys(kf)) props.add(k);
				const target = (effect?.target as HTMLElement | null)?.dataset?.testid ?? (effect?.target as Element | null)?.tagName ?? '?';
				return { target, kind: a.constructor.name, props: [...props] };
			})
	);

test('reduced motion: only opacity animates while scenes change, a card comes out and a sheet opens', async ({ page }) => {
	await page.emulateMedia({ reducedMotion: 'reduce' });
	await openShell(page);
	await settledAt(page, 'idle');
	const offenders: string[] = [];
	const sample = async (moment: string) => {
		for (const a of await movingAnimations(page)) {
			if (a.props.some((p) => !OPACITY_ONLY.has(p))) offenders.push(`${moment}: ${a.kind} on ${a.target} moves ${a.props.join(',')}`);
		}
	};
	// Motion happens on the way in and out: sample right after each change and once more mid-way.
	for (const scene of SCENES) {
		await demo(page, scene);
		await sample(`scene ${scene}`);
		await page.waitForTimeout(50);
		await sample(`scene ${scene}+50ms`);
	}
	await demo(page, '');
	await emit(page, 'approval.request', SNAPSHOT_APPROVAL);
	await sample('card');
	await page.waitForTimeout(50);
	await sample('card+50ms');
	await page.getByTestId('approval-card').getByRole('button', { name: 'Deny' }).click();
	await page.getByRole('button', { name: 'All apps' }).click();
	await sample('launcher');
	await page.waitForTimeout(50);
	await sample('launcher+50ms');
	await page.keyboard.press('Escape');
	expect(offenders, 'animations that move more than opacity under reduced motion').toEqual([]);

	// The core holds still: two photographs, half a second apart, identical.
	await demo(page, 'speaking');
	await page.waitForTimeout(300);
	const core = page.getByTestId('core').first();
	const before = await core.screenshot();
	await page.waitForTimeout(500);
	const after = await core.screenshot();
	expect(before.equals(after), 'the core kept moving under reduced motion').toBe(true);
});

test('reduced transparency: no backdrop-filter on the core, a sheet or any screen', async ({ page, context }) => {
	const cdp = await context.newCDPSession(page);
	await cdp.send('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-reduced-transparency', value: 'reduce' }] });
	await openShell(page);
	expect(await page.evaluate(() => matchMedia('(prefers-reduced-transparency: reduce)').matches), 'the emulation took').toBe(true);
	await emit(page, 'approval.request', SNAPSHOT_APPROVAL);
	await settledAt(page, 'alert');
	expect(await blurLayers(page), 'on the core with a card out').toBe(0);
	await page.getByTestId('approval-card').getByRole('button', { name: 'Deny' }).click();
	await page.getByRole('button', { name: 'All apps' }).click();
	expect(await blurLayers(page), 'with the launcher open').toBe(0);
	await page.keyboard.press('Escape');
	for (const app of APPS) {
		await page.goto(app.path);
		await expect(page.getByTestId(`screen-${app.screen}`)).toBeVisible({ timeout: 15_000 });
		expect(await blurLayers(page), app.path).toBe(0);
	}
	await page.goto('/?popup=1');
	await expect(page.getByTestId('popup')).toBeVisible({ timeout: 15_000 });
	expect(await blurLayers(page), 'the popup (DECISIONS 60)').toBe(0);
});
