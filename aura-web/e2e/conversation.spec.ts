// JARVIS's words sit in a conversation panel, not a centred wall under the core (T-0728). The
// owner, 2026-11-08: "make it smoother, animated, and in a different spot to match the other UI
// stuff". The panel is drawn like the stage's projections, docked bottom-left on a desktop and
// across the bottom on a phone; each turn is labelled, the history scrolls and follows, a turn
// with no words yet types, and streamed words fade in without what came before moving.
//
// CONVERSATION_SCREENS=before|after writes docs/screens/v2/conversation/<phase>-<width>.png;
// =after also records the stream (stream.webm). The core is animated all the time, so the
// pictures are the record and the boxes are the gate.
import { expect, test, type Page } from '@playwright/test';
import { mkdirSync } from 'node:fs';

import { openShell, say } from './v2';

const OUT = new URL('../../docs/screens/v2/conversation/', import.meta.url).pathname;
const PHASE = process.env.CONVERSATION_SCREENS;
const SIZES = [
	[390, 844],
	[1024, 768],
	[1440, 900]
] as const;
const DONE = 'which of these should I connect?';

/** Two turns: a short one, then the owner's NUFI reply streamed a few characters at a time. */
async function converse(page: Page): Promise<void> {
	await openShell(page);
	await say(page, 'turn on the lab lights');
	await expect(page.getByText('Turning on the lab lights.').first()).toBeVisible({ timeout: 15_000 });
	await expect(page.getByTestId('shell')).toHaveAttribute('data-reactor', 'idle', { timeout: 20_000 });
	await say(page, 'What is on the NUFI page?');
}

const box = async (page: Page, sel: string) => (await page.locator(sel).first().boundingBox())!;

if (PHASE) {
	for (const [w, h] of SIZES) {
		test(`records the conversation at ${w}px (${PHASE})`, async ({ page }) => {
			await page.setViewportSize({ width: w, height: h });
			await converse(page);
			await expect(page.getByText(DONE).first()).toBeVisible({ timeout: 15_000 });
			// Let the last words finish their fade.
			await page.waitForTimeout(800);
			mkdirSync(OUT, { recursive: true });
			await page.screenshot({ path: `${OUT}${PHASE}-${w}.png` });
		});
	}
}

if (PHASE !== 'before') {
	// A portrait tablet too: across the bottom, as a phone, but with the core's full box above it.
	for (const [w, h] of [...SIZES, [768, 1024] as const]) {
		test(`the conversation is a labelled panel in its place at ${w}px`, async ({ page }) => {
			await page.setViewportSize({ width: w, height: h });
			await converse(page);
			const panel = page.getByTestId('conversation');
			// Mid-stream: the words arrive revealed, and no literal ** ever shows.
			const seen: string[] = [];
			let revealed = 0;
			for (let i = 0; i < 80; i++) {
				const text = await panel.innerText();
				seen.push(text);
				revealed = Math.max(revealed, await panel.locator('[data-reveal]').count());
				if (text.includes(DONE)) break;
				await page.waitForTimeout(20);
			}
			expect(seen.at(-1)).toContain(DONE);
			expect(seen.filter((t) => t.includes('**'))).toEqual([]);
			expect(revealed).toBeGreaterThan(0);

			await expect(panel.locator('[data-turn="you"]')).toHaveCount(2);
			await expect(panel.locator('[data-turn="jarvis"]')).toHaveCount(2);
			await expect(panel.locator('[data-turn="you"] [data-who]').first()).toHaveText(/you/i);
			await expect(panel.locator('[data-turn="jarvis"] [data-who]').first()).toHaveText(/jarvis/i);
			await expect(panel.locator('[data-turn="jarvis"]').last().locator('li')).toHaveCount(3);

			// Its place: bottom-left beside the core on a desktop or a landscape screen, across the
			// bottom on a phone; either way clear of the core's sphere (0.4 of its box), which the
			// panel covered at 1024x768 when it ran across the bottom there. Measured once the
			// stage has settled: a projection coming back after the last word shrinks the history
			// under a phone's core, and the newest line must still be what shows.
			await page.waitForTimeout(1500);
			const p = await box(page, '[data-testid="conversation"]');
			const stage = await box(page, '[data-testid="stage"]');
			const core = await box(page, '[data-testid="stage"] .core');
			const docked = w >= 1100 || (w > 720 && w > h);
			if (docked) expect(p.x + p.width).toBeLessThanOrEqual(core.x + core.width / 2 - core.width * 0.4);
			else {
				expect(Math.abs(p.x + p.width / 2 - (stage.x + stage.width / 2))).toBeLessThanOrEqual(2);
				expect(p.y).toBeGreaterThanOrEqual(core.y + core.height / 2 + core.height * 0.4);
			}
			expect(p.y + p.height).toBeLessThanOrEqual(stage.y + stage.height + 1);
			const last = await box(page, '[data-testid="conversation"] [data-turn="jarvis"]:last-of-type');
			expect(last.y + last.height).toBeLessThanOrEqual(p.y + p.height + 1);

			// Nothing the stage projects lands on it.
			for (const slot of await page.locator('[data-testid="stage"] .slot').all()) {
				const s = await slot.boundingBox();
				if (!s || !s.width) continue;
				const apart = s.x >= p.x + p.width || s.x + s.width <= p.x || s.y >= p.y + p.height || s.y + s.height <= p.y;
				expect(apart, `a projection at ${JSON.stringify(s)} over the panel at ${JSON.stringify(p)}`).toBe(true);
			}
		});
	}

	test('a turn with no words yet types', async ({ page }) => {
		await openShell(page);
		await say(page, 'answer slowly please');
		await expect(page.getByTestId('conversation-typing')).toBeVisible({ timeout: 5_000 });
		await expect(page.getByTestId('conversation-typing')).toBeHidden({ timeout: 15_000 });
	});

	test('reduced motion reveals words without moving them', async ({ page }) => {
		await page.emulateMedia({ reducedMotion: 'reduce' });
		await converse(page);
		await expect(page.getByText(DONE).first()).toBeVisible({ timeout: 15_000 });
		const moving = await page
			.getByTestId('conversation')
			.evaluate((el) => [...el.querySelectorAll('[data-reveal]')].filter((w) => getComputedStyle(w).animationName !== 'none').length);
		expect(moving).toBe(0);
	});
}

if (PHASE === 'after') {
	test('records a streamed reply', async ({ browser }) => {
		mkdirSync(OUT, { recursive: true });
		const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, recordVideo: { dir: OUT, size: { width: 1440, height: 900 } } });
		const page = await context.newPage();
		await converse(page);
		await expect(page.getByText(DONE).first()).toBeVisible({ timeout: 15_000 });
		await page.waitForTimeout(1500);
		const video = page.video()!;
		await context.close();
		await video.saveAs(`${OUT}stream.webm`);
		await video.delete();
	});
}
