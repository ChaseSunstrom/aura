// The desktop popup as the island (T-0695): a card while the person is here, a pill while a turn
// runs out of the way, an approval as its own card, at the app's window size (shell.rs
// POPUP_SIZE). Each state is photographed, the morph's frames are timed, and the round trip is
// recorded. The states themselves are tests/contracts/island.json's (island.test.ts).
//
// Does not cover the app's window following the island (shell.rs `popup_fit`, its cargo test)
// or the phone's island (android-app IslandScreenshotTest).
import { expect, test, type Page } from '@playwright/test';

import { SNAPSHOT_APPROVAL, emit, openShell } from './v2';

test.use({ viewport: { width: 616, height: 416 }, video: { mode: 'on', size: { width: 616, height: 416 } } });

const island = (page: Page) => page.getByTestId('popup');

async function open(page: Page): Promise<void> {
	await openShell(page, '/?popup=1');
	await expect(island(page)).toHaveAttribute('data-reactor', 'idle', { timeout: 15_000 });
}

const talk = (page: Page, detail: Record<string, unknown>) =>
	page.evaluate((d) => window.dispatchEvent(new CustomEvent('jarvis:push-to-talk', { detail: d })), detail);

/** The morph and the float-in have finished (the core's own loop runs for ever). */
const settled = (page: Page) =>
	page.waitForFunction(() => document.getAnimations().every((a) => a.effect?.getTiming().iterations === Infinity || a.playState === 'finished'));

async function shot(page: Page, name: string): Promise<void> {
	await settled(page);
	await page.screenshot({ path: test.info().outputPath(`island-${name}-616.png`) });
}

test('the island is a card at the top while the person is here, and a pill while a turn runs out of the way', async ({ page }) => {
	await open(page);
	await expect(island(page)).toHaveAttribute('data-island', 'card');
	// Measured once its entrance has finished: read mid-slide it was 22 px, settled 28, and the
	// pill's top was then "6 px lower" in three runs of four.
	// Finite ones only: a looping glow inside would never finish.
	await island(page).evaluate((el) =>
		Promise.all(el.getAnimations({ subtree: true }).filter((a) => a.effect?.getComputedTiming().endTime !== Infinity).map((a) => a.finished))
	);
	const card = (await island(page).boundingBox())!;
	// Hangs from the top, centred, no wider than the card token.
	expect(Math.abs(card.x + card.width / 2 - 308)).toBeLessThanOrEqual(1);
	expect(card.y).toBeLessThan(40);
	expect(card.width).toBeLessThanOrEqual(402);
	await shot(page, 'card');

	await talk(page, { down: true });
	await expect(island(page)).toHaveAttribute('data-reactor', 'listening', { timeout: 10_000 });
	await expect(page.getByTestId('popup-listening')).toBeVisible();
	await shot(page, 'listening');

	await page.keyboard.press('Escape');
	await expect(island(page)).toHaveAttribute('data-island', 'pill');
	await expect(page).toHaveTitle('JARVIS · working · pill');
	await expect.poll(async () => (await island(page).boundingBox())!.width).toBeLessThan(216);
	const pill = (await island(page).boundingBox())!;
	expect(pill.height).toBeLessThan(48);
	// The pill keeps the island's top centre: the window shrinks round it there.
	expect(Math.abs(pill.x + pill.width / 2 - 308)).toBeLessThanOrEqual(1);
	expect(Math.abs(pill.y - card.y)).toBeLessThanOrEqual(1);
	await expect(page.getByRole('button', { name: 'Open JARVIS' })).toBeVisible();
	await shot(page, 'pill');

	// The pill opens the card again; the core is the one core, moved and scaled, not a second.
	await page.getByRole('button', { name: 'Open JARVIS' }).click();
	await expect(island(page)).toHaveAttribute('data-island', 'card');
	await expect(page).toHaveTitle('JARVIS · working');
	await expect(island(page).getByTestId('core')).toHaveCount(1);
	await talk(page, { down: false });
});

test('an approval is its own card, cannot be put away unanswered, and dims round itself', async ({ page }) => {
	await open(page);
	await emit(page, 'approval.request', SNAPSHOT_APPROVAL);
	await expect(island(page)).toHaveAttribute('data-island', 'approval', { timeout: 10_000 });
	const ask = island(page).getByTestId('approval-card');
	await expect(ask).toBeVisible();
	await page.keyboard.press('Escape');
	await expect(island(page)).toHaveAttribute('data-island', 'approval');
	// The answer, not a composer, is what the card asks for.
	await expect(page.getByTestId('chat-input')).toHaveCount(0);
	const veil = await page.locator('.veil').evaluate((el) => Number(getComputedStyle(el).opacity));
	expect(veil).toBe(1);
	const box = (await island(page).boundingBox())!;
	expect(box.y + box.height).toBeLessThanOrEqual(416);
	await shot(page, 'approval');
	await ask.getByRole('button', { name: 'Deny', exact: true }).click();
	await expect(island(page)).toHaveAttribute('data-island', 'card', { timeout: 10_000 });
});

test('the morph holds the frame rate, recording the round trip', async ({ page }) => {
	await open(page);
	await talk(page, { down: true });
	await expect(island(page)).toHaveAttribute('data-reactor', 'listening', { timeout: 10_000 });
	await settled(page);
	await page.evaluate(() => {
		const w = window as unknown as { __gaps: number[] };
		w.__gaps = [];
		let last = performance.now();
		const step = (now: number) => {
			w.__gaps.push(now - last);
			last = now;
			if (w.__gaps.length < 600) requestAnimationFrame(step);
		};
		requestAnimationFrame(step);
	});
	// Card → pill → card → pill → card, each morph left to settle.
	for (let i = 0; i < 2; i++) {
		await page.keyboard.press('Escape');
		await expect(island(page)).toHaveAttribute('data-island', 'pill');
		await settled(page);
		await page.getByRole('button', { name: 'Open JARVIS' }).click();
		await expect(island(page)).toHaveAttribute('data-island', 'card');
		await settled(page);
	}
	const gaps = await page.evaluate(() => (window as unknown as { __gaps: number[] }).__gaps.slice(1));
	gaps.sort((a, b) => a - b);
	const median = gaps[Math.floor(gaps.length / 2)];
	test.info().annotations.push({ type: 'morph frames', description: `${gaps.length} frames, median ${median.toFixed(1)} ms` });
	expect(gaps.length).toBeGreaterThan(30);
	expect(median).toBeLessThan(17.5);
	await talk(page, { down: false });
});
