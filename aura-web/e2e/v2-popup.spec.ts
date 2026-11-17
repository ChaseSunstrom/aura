// What the desktop app (Tauri, ADR-0051) asks of the console, on the v2 shell.
// The app loads `/?popup=1` into its assist popup, dispatches
// `jarvis:push-to-talk` with `detail.down` from its held hotkey, sets
// `html[data-float]` to play the card in and out, and reads the page's title
// for its mood (`shell.rs` `mood_of`). This is that contract, against the mock
// backend with Chromium's fake microphone.
//
// The island's states, sizes and morph are v2-island.spec.ts's.
//
// Retired with hud-desktop.spec.ts: the blurred card (the v2 brief has no
// glass, DECISIONS.md 2026-11-08), the compact-HUD rung (the popup is one
// view now), and Settings → Reactor 2D (the v2 core has one look).
import { expect, test, type Page } from '@playwright/test';

import { SNAPSHOT_APPROVAL, emit, layoutPx, openShell } from './v2';

async function open(page: Page): Promise<void> {
	await openShell(page, '/?popup=1');
	await expect(page.getByTestId('popup')).toHaveAttribute('data-reactor', 'idle', { timeout: 15_000 });
}

const talk = (page: Page, detail: Record<string, unknown>) =>
	page.evaluate((d) => window.dispatchEvent(new CustomEvent('jarvis:push-to-talk', { detail: d })), detail);

const transcripts = (page: Page) =>
	page.evaluate(() => fetch('/hermes/api/test/spoken').then((r) => r.json())).then((j: { transcripts: number }) => j.transcripts);

test('the popup is the core, a line and the input, and nothing of the console', async ({ page }) => {
	await open(page);
	const card = page.getByTestId('popup');
	await expect(card.getByTestId('core')).toBeVisible();
	await expect(card.getByTestId('popup-line')).not.toBeEmpty();
	await expect(card.getByTestId('chat-input')).toBeVisible();
	for (const name of ['All apps', 'Search or command', 'Devices']) await expect(page.getByRole('button', { name, exact: true })).toHaveCount(0);
	// ⌘K opens nothing over it.
	await page.keyboard.press('Control+k');
	await expect(page.getByRole('dialog')).toHaveCount(0);
	// Every control on it can be hit, once the card has arrived (it floats in from 97 %).
	await page.waitForFunction(() => document.getAnimations().every((a) => a.effect?.getTiming().iterations === Infinity || a.playState === 'finished'));
	for (const b of await card.getByRole('button').all()) {
		const box = await b.boundingBox();
		expect(layoutPx(box!.width)).toBeGreaterThanOrEqual(44);
		expect(layoutPx(box!.height)).toBeGreaterThanOrEqual(44);
	}
});

// The app's window is transparent, so the page's ground must be too; the card
// is the island's opaque fill with a hairline edge (T-0695); the `out` cue
// plays an exit to nothing and `in` brings the card back (the owner,
// 2026-10-07: "the animation is rough").
test('the popup is the island on a clear ground, and leaves and returns on cue', async ({ page }) => {
	await open(page);
	// The console paints the default pack first and the mock persona's JARVIS a moment later:
	// read before it lands and a slow runner sees AURA's fill (T-0926, hud-behaviour job 23720).
	await expect.poll(() => page.evaluate(() => document.documentElement.dataset.theme)).toBe('jarvis');
	for (const el of ['html', 'body']) expect(await page.evaluate((s) => getComputedStyle(document.querySelector(s)!).backgroundColor, el)).toBe('rgba(0, 0, 0, 0)');
	expect(await page.getByTestId('shell').evaluate((el) => getComputedStyle(el).backgroundColor)).toBe('rgba(0, 0, 0, 0)');
	const card = page.getByTestId('popup');
	const look = await card.evaluate((el) => {
		const css = getComputedStyle(el);
		return { radius: parseFloat(css.borderTopLeftRadius), blur: css.backdropFilter, ground: css.backgroundColor };
	});
	expect(look.radius).toBe(22);
	expect(look.blur).toBe('none');
	// Near opaque, so its words read over any desktop, white pages included (overlay_scrim_test.py).
	expect(look.ground).toBe('rgba(4, 11, 19, 0.94)');
	const opacity = () => card.evaluate((el) => Number(getComputedStyle(el).opacity));
	await page.evaluate(() => (document.documentElement.dataset.float = 'out'));
	await expect.poll(opacity, { timeout: 3_000 }).toBe(0);
	await page.evaluate(() => (document.documentElement.dataset.float = 'in'));
	await expect.poll(opacity, { timeout: 3_000 }).toBe(1);
});

// The owner, 2026-10-08: the popup "looks weird and cut off". Send ran off the
// card's edge, and a reply was laid out below the dock, off the card's foot.
test('the popup keeps Send and a long reply inside the card', async ({ page }) => {
	await page.setViewportSize({ width: 616, height: 416 });
	await open(page);
	// The card is as tall as what it shows, so it is measured again once it has settled.
	const settled = () =>
		page.waitForFunction(() => document.getAnimations().every((a) => a.effect?.getTiming().iterations === Infinity || a.playState === 'finished'));
	const inside = async (testid: string) => {
		await settled();
		const card = (await page.getByTestId('popup').boundingBox())!;
		const box = (await page.getByTestId(testid).boundingBox())!;
		expect(box.x).toBeGreaterThanOrEqual(card.x);
		expect(box.x + box.width).toBeLessThanOrEqual(card.x + card.width);
		expect(box.y).toBeGreaterThanOrEqual(card.y);
		expect(box.y + box.height).toBeLessThanOrEqual(card.y + card.height + 1);
	};
	await inside('chat-send');
	await page.getByTestId('chat-input').fill('research bitcoin for me');
	await page.getByTestId('chat-input').press('Enter');
	// At rest the line is the state's word; with a reply, the reply.
	await expect(page.getByTestId('popup-line')).not.toHaveText(/^\s*Ready\s*$/, { timeout: 15_000 });
	await expect(page.getByTestId('popup')).toHaveAttribute('data-reactor', 'idle', { timeout: 30_000 });
	await expect(page.getByTestId('popup-line')).not.toHaveText(/^\s*Ready\s*$/);
	await inside('chat-send');
	// The reply is there to read, above the input, not under it.
	const card = (await page.getByTestId('popup').boundingBox())!;
	const line = (await page.getByTestId('popup-line').boundingBox())!;
	const input = (await page.getByTestId('chat-input').boundingBox())!;
	expect(line.y).toBeGreaterThanOrEqual(card.y);
	expect(line.y + line.height).toBeLessThanOrEqual(input.y + 1);
});

test('the desktop hotkey is held to talk: down listens, up sends the recording', async ({ page }) => {
	await open(page);
	const before = await transcripts(page);
	await talk(page, { down: true });
	await expect(page.getByTestId('popup')).toHaveAttribute('data-reactor', 'listening', { timeout: 10_000 });
	await expect(page.getByTestId('popup-listening')).toBeVisible();
	await expect(page.getByRole('button', { name: 'Stop listening' })).toHaveAttribute('aria-pressed', 'true');
	await page.waitForTimeout(1200);
	await talk(page, { down: false });
	await expect.poll(() => transcripts(page), { message: 'the recording reached the transcriber', timeout: 15_000 }).toBeGreaterThan(before);
});

test("the tray's push-to-talk, which has no release, toggles", async ({ page }) => {
	await open(page);
	await talk(page, {});
	await expect(page.getByTestId('popup')).toHaveAttribute('data-reactor', 'listening', { timeout: 10_000 });
	await page.waitForTimeout(800);
	await talk(page, {});
	await expect(page.getByTestId('popup')).not.toHaveAttribute('data-reactor', 'listening', { timeout: 10_000 });
});

// The owner, 2026-09-25: "if it asks for approval ... I can't click it without
// closing Jarvis". The card is on the popup, and the title says it waits.
test('a request waits on the popup itself, and its title says so', async ({ page }) => {
	await open(page);
	await emit(page, 'approval.request', SNAPSHOT_APPROVAL);
	const card = page.getByTestId('popup').getByTestId('approval-card');
	await expect(card).toBeVisible({ timeout: 10_000 });
	await expect(page).toHaveTitle('JARVIS · needs you');
	await card.getByRole('button', { name: 'Deny', exact: true }).click();
	await expect(card).toHaveCount(0, { timeout: 10_000 });
	await expect(page).not.toHaveTitle('JARVIS · needs you');
});

// The popup keeps itself up while it matters; the app reads the page's title
// (`shell.rs` `mood_of`). This pins what the page says.
test('the popup titles itself by what it is doing, and pulls a fresh notice up', async ({ page }) => {
	await open(page);
	await expect(page).toHaveTitle('JARVIS');
	// The moment is a broadcast, not a queue: one sent before the popup's own socket has
	// subscribed is gone, so it is sent again until the popup shows it (a loaded host lost it).
	await expect(async () => {
		await page.evaluate(async () => {
			const socket = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws`);
			await new Promise((resolve) => socket.addEventListener('open', resolve));
			socket.send(JSON.stringify({ id: 1, type: 'jarvis/test/moment', title: 'The laundry is done' }));
			await new Promise((resolve) => setTimeout(resolve, 300));
			socket.close();
		});
		await expect(page.getByTestId('popup-notice')).toContainText('The laundry is done', { timeout: 2_000 });
	}).toPass({ timeout: 15_000 });
	await expect(page).toHaveTitle('JARVIS · notice');
	await talk(page, { down: true });
	await expect(page.getByTestId('popup')).toHaveAttribute('data-reactor', 'listening', { timeout: 10_000 });
	// The notice still outranks the turn while it shows; once it goes, the turn.
	await expect(page).toHaveTitle('JARVIS · working', { timeout: 12_000 });
	await talk(page, { down: false });
});
