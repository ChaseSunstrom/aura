// T-0725: a screen opens as a side panel beside the core instead of replacing
// it. The panel slides in from the inline end (from the bottom on a phone), the
// core stays on its stage and eases into the space left, and closing reverses
// it. The owner, 2026-11-08: "cleanly animate into the screen on the side ...
// and the reactor/other UI elements will smoothly move with them".
import { expect, test, type Page } from '@playwright/test';

import { emit, openShell, settledAt } from './v2';

const SIZES = [
	{ width: 390, height: 844, from: 'bottom' },
	{ width: 1024, height: 768, from: 'end' },
	{ width: 1440, height: 900, from: 'end' }
] as const;

const box = async (page: Page, testid: string) => (await page.getByTestId(testid).boundingBox())!;
const sameCore = (page: Page) => page.getByTestId('core').evaluate((el) => (window as unknown as { __core: Element }).__core === el);
const settled = (page: Page) => page.getByTestId('side-panel').evaluate((el) => Promise.all(el.getAnimations({ subtree: false }).map((a) => a.finished)).then(() => true));

/** Keep the core's element and, once the panel is in the page, where it was on its first frame. */
const watch = (page: Page) =>
	page.evaluate(() => {
		const w = window as unknown as { __core: Element; __first?: DOMRect };
		w.__core = document.querySelector('[data-testid="core"]')!;
		delete w.__first;
		const obs = new MutationObserver(() => {
			const el = document.querySelector('[data-testid="side-panel"]');
			if (!el) return;
			obs.disconnect();
			requestAnimationFrame(() => (w.__first = el.getBoundingClientRect()));
		});
		obs.observe(document.body, { childList: true, subtree: true });
	});
const first = (page: Page) => page.evaluate(() => (window as unknown as { __first?: DOMRect }).__first ?? null);

async function openSettings(page: Page): Promise<void> {
	await page.getByRole('button', { name: 'All apps' }).click();
	await page.getByTestId('launcher').getByRole('link', { name: /^Settings/ }).click();
	await expect(page.getByTestId('shell')).toHaveAttribute('data-screen', 'settings');
}

for (const size of SIZES) {
	test(`at ${size.width}: a screen slides in from the ${size.from} and the core stays beside it`, async ({ page }) => {
		await page.setViewportSize(size);
		await openShell(page);
		const whole = await box(page, 'stage');
		await watch(page);

		await openSettings(page);
		const panel = page.getByTestId('side-panel');
		await expect(panel).toBeVisible();
		await expect(page.getByTestId('screen-settings')).toBeVisible();
		expect(await settled(page)).toBe(true);
		expect(await sameCore(page), 'the core is never remounted').toBe(true);
		await expect(page.getByTestId('core')).toBeInViewport();

		const p = await box(page, 'side-panel');
		const s = await box(page, 'stage');
		const at = (await first(page))!;
		expect(at, 'the panel was sampled on its first frame').not.toBeNull();
		if (size.from === 'end') {
			expect(p.x + p.width).toBeCloseTo(size.width, 0);
			expect(s.x + s.width, 'the stage ends where the panel starts').toBeLessThanOrEqual(p.x + 1);
			expect(at.x, 'it came in from the end').toBeGreaterThan(p.x + p.width / 2);
		} else {
			expect(p.y + p.height).toBeCloseTo(size.height, 0);
			expect(s.y + s.height, 'the stage ends where the panel starts').toBeLessThanOrEqual(p.y + 1);
			expect(at.y, 'it came up from the bottom').toBeGreaterThan(p.y + p.height / 2);
		}

		// Closing reverses it: the panel leaves, the stage is whole again, the core the same.
		await page.keyboard.press('Escape');
		await expect(page.getByTestId('shell')).toHaveAttribute('data-screen', 'core');
		await expect(panel).toHaveCount(0);
		const back = await box(page, 'stage');
		expect(back.width).toBeCloseTo(whole.width, 0);
		expect(back.height).toBeCloseTo(whole.height, 0);
		expect(await sameCore(page), 'the core is never remounted').toBe(true);
	});
}

// T-0974: on a phone the core jumped to its small size above the panel and only slid there
// (the owner, 2026-11-10: "make the core above the settings/opened menus transition/animate
// smoother"). It now shrinks while it glides, and grows back as the panel goes.
test('on a phone the core shrinks into its place above a screen and grows back', async ({ page }) => {
	await page.setViewportSize({ width: 390, height: 844 });
	await openShell(page);
	// The core's drawn width on every frame from the tap until it settles: no frame may jump
	// most of the way (the old core did all of it in one). A read can land between a layout
	// change and the resize observer that starts the glide, seeing one unpainted frame of the
	// new size; a median over three drops that blip, while a real jump stays.
	const glide = async (act: () => Promise<void>) => {
		await page.evaluate(() => {
			const w = window as unknown as { __w: number[]; __on: boolean };
			w.__w = [];
			w.__on = true;
			const core = document.querySelector('[data-testid="stage"] .core')!;
			const tick = () => {
				w.__w.push(core.getBoundingClientRect().width);
				if (w.__on) requestAnimationFrame(tick);
			};
			tick();
		});
		await act();
		await page.waitForTimeout(100);
		await page.waitForFunction(() => document.querySelector('[data-testid="stage"] .core')!.getAnimations().length === 0);
		const widths = await page.evaluate(() => {
			const w = window as unknown as { __w: number[]; __on: boolean };
			w.__on = false;
			return w.__w;
		});
		const drawn = widths.map((_, i) => [...widths.slice(Math.max(0, i - 1), i + 2)].sort((a, b) => a - b)[i ? 1 : 0]);
		const moved = Math.abs(drawn.at(-1)! - drawn[0]);
		const step = Math.max(...drawn.slice(1).map((x, i) => Math.abs(x - drawn[i])));
		return { moved, step };
	};
	// Through the apps sheet: the sheet moves it aside too, and the panel takes over mid-glide.
	const opened = await glide(() => openSettings(page));
	expect(await settled(page)).toBe(true);
	expect(opened.moved, 'it moves aside for the panel').toBeGreaterThan(10);
	expect(opened.step, `it glides there: ${JSON.stringify(opened)}`).toBeLessThan(opened.moved * 0.6);
	const closed = await glide(async () => {
		await page.keyboard.press('Escape');
		await expect(page.getByTestId('shell')).toHaveAttribute('data-screen', 'core');
	});
	expect(closed.moved, 'and comes back').toBeGreaterThan(10);
	expect(closed.step, `gliding: ${JSON.stringify(closed)}`).toBeLessThan(closed.moved * 0.6);
});

test('a sheet slides in the same way and the core moves aside for it', async ({ page }) => {
	await page.setViewportSize({ width: 1440, height: 900 });
	await openShell(page);
	const whole = await box(page, 'stage');
	for (const [button, testid] of [
		['All apps', 'launcher'],
		['Search or command', 'palette'],
		['Devices', 'devices-sheet']
	] as const) {
		await page.getByRole('button', { name: button, exact: true }).click();
		const sheet = page.getByTestId(testid);
		await expect(sheet).toBeVisible();
		await sheet.evaluate((el) => Promise.all(el.getAnimations({ subtree: false }).map((a) => a.finished)));
		const b = (await sheet.boundingBox())!;
		expect(b.x + b.width, `${testid} sits at the end`).toBeCloseTo(1440, 0);
		const s = await box(page, 'stage');
		expect(s.x + s.width, `the stage makes room for ${testid}`).toBeLessThanOrEqual(b.x + 1);
		await page.keyboard.press('Escape');
		await expect(sheet).toBeHidden();
		await expect.poll(async () => Math.round((await box(page, 'stage')).width)).toBe(Math.round(whole.width));
	}
});

test('lines land beside an open panel', async ({ page }) => {
	await page.setViewportSize({ width: 1440, height: 900 });
	await openShell(page);
	await emit(page, 'status.update', { kind: 'goal', text: '⊙ Goal set: Book a table for two (20-turn budget)' });
	await emit(page, 'tool.start', { tool_id: 'w1', name: 'web_search', context: 'restaurants near me' });
	await emit(page, 'tool.start', { tool_id: 'w2', name: 'read_file', context: 'notes.md' });
	await settledAt(page, 'working');
	await openSettings(page);
	expect(await settled(page)).toBe(true);
	await page.waitForTimeout(400);
	// Each connector's end against its projection's facing edge, as lines-land.spec reads them.
	const off = await page.evaluate(() => {
		const st = document.querySelector('[data-testid="stage"]')!.getBoundingClientRect();
		return [...document.querySelectorAll('[data-testid="stage-connector"]')].map((c) => {
			const nums = (c.querySelector('path')!.getAttribute('d') ?? '').match(/-?[\d.]+/g)!.map(Number);
			const slot = document.querySelector<HTMLElement>(`[data-shown="${c.getAttribute('data-for')}"]`)!;
			const b = slot.getBoundingClientRect();
			const edge = slot.dataset.edge;
			const x = st.left + (nums[nums.length - 2] / 100) * st.width;
			const y = st.top + (nums[nums.length - 1] / 100) * st.height;
			const t = edge === 'right' ? [b.right, b.top + b.height / 2] : edge === 'left' ? [b.left, b.top + b.height / 2] : [b.left + b.width / 2, b.top];
			return Math.max(Math.abs(x - t[0]), Math.abs(y - t[1]));
		});
	});
	expect(off.length, 'a connector per projection beside the panel').toBeGreaterThan(0);
	for (const d of off) expect(d).toBeLessThanOrEqual(2);
});

test("beside Chat the core's own composer steps aside", async ({ page }) => {
	await page.setViewportSize({ width: 768, height: 1024 });
	await openShell(page);
	await expect(page.getByTestId('chat-input')).toBeVisible();
	await page.getByRole('button', { name: 'All apps' }).click();
	await page.getByTestId('launcher').getByRole('link', { name: /^Chat/ }).click();
	await expect(page.getByTestId('chatscreen-input')).toBeVisible();
	await expect(page.getByTestId('chat-input')).toHaveCount(0);
	await page.goBack();
	await expect(page.getByTestId('chat-input')).toBeVisible();
});

test('a slide out cut short still lets the panel go', async ({ page }) => {
	// Without the animationcancel handler the closed screen stayed mounted off the
	// edge, and on a phone data-panel kept the composer hidden.
	await page.setViewportSize({ width: 390, height: 844 });
	await openShell(page);
	await openSettings(page);
	const panel = page.getByTestId('side-panel');
	expect(await settled(page)).toBe(true);
	// A minute-long slide, so it is surely still running when it is cut.
	await page.addStyleTag({ content: ".room[data-panel='out'] .panel { animation-duration: 60s !important; }" });
	await page.keyboard.press('Escape');
	await expect(page.getByTestId('shell')).toHaveAttribute('data-screen', 'core');
	await expect(panel).toHaveCount(1);
	await panel.evaluate((el) => ((el as HTMLElement).style.display = 'none'));
	await expect(panel).toHaveCount(0, { timeout: 1_000 });
	await expect(page.locator('main.room')).not.toHaveAttribute('data-panel', /.+/);
});

test('under reduced motion the panel and the sheets are in and out at once', async ({ page }) => {
	await page.emulateMedia({ reducedMotion: 'reduce' });
	await page.setViewportSize({ width: 1440, height: 900 });
	await openShell(page);
	await page.getByRole('button', { name: 'All apps' }).click();
	expect(await page.getByTestId('launcher').evaluate((el) => el.getAnimations({ subtree: false }).length), 'the launcher too').toBe(0);
	await page.keyboard.press('Escape');
	await openSettings(page);
	const panel = page.getByTestId('side-panel');
	await expect(panel).toBeVisible();
	expect(await panel.evaluate((el) => el.getAnimations({ subtree: false }).length)).toBe(0);
	await page.keyboard.press('Escape');
	await expect(panel).toHaveCount(0, { timeout: 1_000 });
});
