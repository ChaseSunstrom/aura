// The stage's panels tile (T-0951), as they moved freely before (T-0887). The owner, 2026-11-10:
// "With the dragging of panels I want it to lock and tile like hyprland does instead of just
// being free floating, with nice animations". A panel follows the finger while held, a ghost
// marks the tile it will take, and on release it locks into that tile: a panel already there
// takes the dropped one's old tile, both glide on the motion tokens (at once under reduced
// motion), no two panels ever overlap, and the arrangement is kept per scene on the device.
// Does not cover the keyboard (the panels keep their reading order) or resizing a tile.
import { expect, test, type Page } from '@playwright/test';

import { openShell } from './v2';

type Box = { x: number; y: number; width: number; height: number };

const panel = (page: Page, id?: string) => (id ? page.locator(`[data-shown="${id}"]`) : page.locator('[data-shown]').first());
const box = async (page: Page, id: string) => (await panel(page, id).boundingBox())!;
const centre = (b: Box): [number, number] => [b.x + b.width / 2, b.y + b.height / 2];
const near = (a: [number, number], b: [number, number], by = 3) => Math.hypot(a[0] - b[0], a[1] - b[1]) <= by;
const ids = (page: Page) => page.locator('[data-shown]').evaluateAll((n) => n.map((e) => e.getAttribute('data-shown')!));
const token = (page: Page, name: string) => page.evaluate((n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim(), name);

/** Press on [id]'s middle and travel to [to]; [hold] runs before the release, while it is held. */
async function drag(page: Page, id: string, to: [number, number], hold?: () => Promise<void>) {
	const [x, y] = centre(await box(page, id));
	await page.mouse.move(x, y);
	await page.mouse.down();
	await page.mouse.move((x + to[0]) / 2, (y + to[1]) / 2, { steps: 4 });
	await page.mouse.move(to[0], to[1], { steps: 4 });
	await hold?.();
	await page.mouse.up();
}

/** Every panel's box once nothing on the stage is moving. */
async function settled(page: Page): Promise<Record<string, Box>> {
	await expect.poll(() => page.evaluate(() => document.querySelectorAll('[data-shown]').length && document.getAnimations().filter((a) => (a.effect as KeyframeEffect)?.target?.matches?.('[data-shown]')).length)).toBe(0);
	const out: Record<string, Box> = {};
	for (const id of await ids(page)) out[id] = await box(page, id);
	return out;
}

const overlap = (a: Box, b: Box) => a.x < b.x + b.width - 1 && b.x < a.x + a.width - 1 && a.y < b.y + b.height - 1 && b.y < a.y + a.height - 1;

// The home's panels follow the feeds as they load (a job can stand in a slot a reading takes
// a moment later), so the set is read once two reads a second apart agree.
async function home(page: Page, count = 2): Promise<string[]> {
	await openShell(page, '/');
	await expect(panel(page)).toBeVisible();
	let last = '';
	await expect
		.poll(
			async () => {
				const seen = await ids(page);
				const now = seen.join();
				// Settled is the same panels twice AND enough of them: on a loaded runner the home
				// was still empty a second after it opened, and "empty twice" passed as settled
				// (pipelines 806 and 809: "the home shows enough panels to tile", received 0).
				const same = now === last && seen.length >= count;
				last = now;
				return same;
			},
			{ intervals: [1000], timeout: 20_000 }
		)
		.toBe(true);
	const found = await ids(page);
	expect(found.length, 'the home shows enough panels to tile').toBeGreaterThanOrEqual(count);
	return found;
}

test.describe('on a wide stage', () => {
	test.use({ viewport: { width: 1440, height: 900 } });

	test('a panel dropped on another’s tile takes it, the other takes its old one, and it is kept across a reload', async ({ page }) => {
		const [a, b] = await home(page);
		const was = await settled(page);
		await drag(page, a, centre(was[b]));
		const now = await settled(page);
		expect(near(centre(now[a]), centre(was[b]))).toBe(true);
		expect(near(centre(now[b]), centre(was[a]))).toBe(true);
		expect(overlap(now[a], now[b])).toBe(false);
		await page.reload();
		await expect(panel(page, a)).toBeVisible();
		const kept = await settled(page);
		expect(near(centre(kept[a]), centre(was[b]))).toBe(true);
	});

	test('a drop between tiles locks into the nearest one, never floating where it was let go', async ({ page }) => {
		const [a] = await home(page);
		const was = await settled(page);
		const [x, y] = centre(was[a]);
		await drag(page, a, [x + 60, y + 50]);
		const now = await settled(page);
		expect(near(centre(now[a]), [x, y])).toBe(true);
		for (const [i, p] of Object.entries(now)) for (const [j, q] of Object.entries(now)) if (i < j) expect(overlap(p, q), `${i} on ${j}`).toBe(false);
	});

	test('while held, a ghost marks the tile it will take, and it goes on release', async ({ page }) => {
		const [a, b] = await home(page);
		const was = await settled(page);
		const ghost = page.getByTestId('tile-ghost');
		await expect(ghost).toHaveCount(0);
		await drag(page, a, centre(was[b]), async () => {
			await expect(ghost).toBeVisible();
			await expect.poll(async () => near(centre((await ghost.boundingBox())!), centre(was[b]), 4)).toBe(true);
			await expect(panel(page, a)).toHaveAttribute('data-dragging', '');
		});
		await expect(ghost).toHaveCount(0);
	});

	test('the panels glide into their tiles on the motion tokens', async ({ page }) => {
		const [a, b] = await home(page);
		const was = await settled(page);
		await drag(page, a, centre(was[b]));
		const timing = await panel(page, b).evaluate((el) => el.getAnimations().map((x) => x.effect!.getTiming()));
		expect(timing, 'the panel pushed aside glides, it does not jump').toHaveLength(1);
		const dur = await token(page, '--jv-dur-row-in');
		expect(timing[0].duration).toBe(dur.endsWith('ms') ? parseFloat(dur) : parseFloat(dur) * 1000);
		const curve = (c: string) => c.match(/[\d.]+/g)!.map(Number);
		expect(curve(timing[0].easing)).toEqual(curve(await token(page, '--jv-ease-quick')));
	});

	test('under reduced motion the panels are in their tiles at once', async ({ page }) => {
		await page.emulateMedia({ reducedMotion: 'reduce' });
		const [a, b] = await home(page);
		const was = await settled(page);
		await drag(page, a, centre(was[b]));
		expect(await page.evaluate(() => document.getAnimations().filter((x) => (x.effect as KeyframeEffect)?.target?.matches?.('[data-shown]')).length)).toBe(0);
		expect(near(centre(await box(page, b)), centre(was[a]))).toBe(true);
	});
});

test.describe('on a phone', () => {
	test.use({ viewport: { width: 412, height: 915 } });

	test('the column reorders: a panel dropped on the one above takes its place', async ({ page }) => {
		const [a, b] = await home(page);
		const was = await settled(page);
		expect(was[a].y).toBeLessThan(was[b].y);
		await drag(page, b, centre(was[a]));
		await settled(page);
		expect(await ids(page)).toEqual(expect.arrayContaining([a, b]));
		const order = await ids(page);
		expect(order.indexOf(b)).toBeLessThan(order.indexOf(a));
		expect((await box(page, b)).y).toBeLessThan((await box(page, a)).y);
	});

	test('a press that does not travel is not a drag', async ({ page }) => {
		const [a] = await home(page, 1);
		const was = await settled(page);
		const [x, y] = centre(was[a]);
		await drag(page, a, [x + 2, y + 2]);
		expect(await box(page, a)).toEqual(was[a]);
	});

	// The column a phone stacks the panels in scrolls when they outgrow it: there a finger's vertical
	// swipe must scroll it (touch-action pan-y), and a panel's glow past the column is not outgrowing.
	test('panels that outgrow their column leave its vertical swipe to the scroll', async ({ page }) => {
		const [a] = await home(page, 1);
		await expect(panel(page, a)).toHaveCSS('touch-action', 'none');
		await page.locator('.slots').evaluate((l) => ((l as HTMLElement).style.maxBlockSize = '60px'));
		await expect(panel(page, a)).toHaveCSS('touch-action', 'pan-y');
	});
});
