// The slight visual bugs, found by the page rather than by eye (T-0692): on every v2 screen and
// the core's scenes, at a phone, a tablet and a desktop width, in both shipped themes, no text is
// cut off by a box that hides its overflow, none runs off the screen, the page never scrolls
// sideways, and no two lines of words lie on top of each other. Does not judge spacing, radii or
// motion: those are the sweep's pictures (`make sweep`) and the eye.
import { expect, test, type Page } from '@playwright/test';
import { APPS } from '../src/lib/ds/shell/routes';
import { GITLAB } from '../src/lib/hud/data/apps';
import { demo, openShell, releases, settledAt } from './v2';

const WIDTHS = [
	[390, 844],
	[768, 1024],
	[1440, 900]
] as const;
const THEMES = ['jarvis', 'aura'] as const;

const SCREENS: Record<string, (page: Page) => Promise<void>> = {
	'core-idle': async (page) => {
		await openShell(page);
		await settledAt(page, 'idle');
	},
	'core-answer': async (page) => {
		await openShell(page);
		await settledAt(page, 'idle');
		await demo(page, 'answer');
		await settledAt(page, 'answer');
	},
	...Object.fromEntries(
		APPS.map((app) => [
			app.screen,
			async (page: Page) => {
				if (app.screen === 'devices') await releases(page);
				await openShell(page, app.path);
				await expect(page.getByTestId(`screen-${app.screen}`)).toBeVisible();
			}
		])
	)
};

/** Every word on the page that is cut, off the screen or under another, as one line each. */
function audit(): string[] {
	const W = innerWidth;
	/** The share of a line's box above and below its ink. */
	const AIR = 0.16;
	const out: string[] = [];
	const root = document.documentElement;
	if (root.scrollWidth > W + 1) out.push(`the page scrolls sideways: ${root.scrollWidth} > ${W}`);
	const name = (el: Element) => {
		const id = (el as HTMLElement).dataset?.testid;
		return `${el.tagName.toLowerCase()}${id ? `[${id}]` : ''} "${(el.textContent ?? '').trim().slice(0, 40)}"`;
	};
	const words: [Element, DOMRect][] = [];
	for (const el of document.querySelectorAll('body *')) {
		if (!el.checkVisibility({ opacityProperty: true, visibilityProperty: true })) continue;
		if (el.closest('[aria-hidden="true"]')) continue;
		const own = [...el.childNodes].some((n) => n.nodeType === Node.TEXT_NODE && n.textContent?.trim());
		if (!own) continue;
		const r = el.getBoundingClientRect();
		if (r.width < 1 || r.height < 1) continue;
		const s = getComputedStyle(el);
		if (r.right > W + 1 || r.left < -1) out.push(`off the screen: ${name(el)} at ${Math.round(r.left)}..${Math.round(r.right)}`);
		const hides = (v: string) => v === 'hidden' || v === 'clip';
		if (el.scrollWidth > el.clientWidth + 1 && hides(s.overflowX) && s.textOverflow !== 'ellipsis')
			out.push(`cut off: ${name(el)} (${el.scrollWidth} in ${el.clientWidth})`);
		// The nearest box above it that clips: one that hides what it does not hold, and does
		// not hold all of it. A scroller's rows past its edge are a scroll away, not cut.
		for (let p = el.parentElement; p && p !== document.body; p = p.parentElement) {
			const ps = getComputedStyle(p);
			if (ps.overflowX === 'visible' && ps.overflowY === 'visible') continue;
			if (!hides(ps.overflowX) && !hides(ps.overflowY)) break;
			const pr = p.getBoundingClientRect();
			// A line's box is taller than its ink by the font's ascent and descent: a few pixels
			// past a box's top or foot cut nothing.
			const air = Math.max(1, r.height * AIR);
			if (r.left < pr.left - 1 || r.right > pr.right + 1 || r.top < pr.top - air || r.bottom > pr.bottom + air)
				out.push(`cut off by ${name(p).split(' ')[0]}: ${name(el)}`);
			break;
		}
		words.push([el, r]);
	}
	for (let i = 0; i < words.length; i++)
		for (let j = i + 1; j < words.length; j++) {
			const [a, ra] = words[i];
			const [b, rb] = words[j];
			if (a.contains(b) || b.contains(a)) continue;
			const w = Math.min(ra.right, rb.right) - Math.max(ra.left, rb.left);
			const h = Math.min(ra.bottom, rb.bottom) - Math.max(ra.top, rb.top);
			// Each line brings its own air: the ink overlaps only past the two together.
			if (w > 3 && h > Math.max(3, (ra.height + rb.height) * AIR)) out.push(`on top of each other: ${name(a)} and ${name(b)}`);
		}
	return [...new Set(out)];
}

test.beforeEach(async ({ page }) => {
	// GitLab is off this network: Devices says so at once rather than after its timeout.
	await page.route(`${GITLAB}/**`, (route) => route.abort());
});

test.afterEach(async ({ page }) => {
	await page.request.post('/hermes/api/test/theme', { data: { op: 'apply', id: 'jarvis' } });
});

for (const themeId of THEMES)
	for (const [width, height] of WIDTHS)
		for (const [screen, go] of Object.entries(SCREENS))
			test(`${screen} at ${width} in ${themeId}: nothing cut, off the screen or overlapping`, async ({ page }) => {
				await page.request.post('/hermes/api/test/theme', { data: { op: 'apply', id: themeId } });
				await page.setViewportSize({ width, height });
				await go(page);
				await expect.poll(() => page.evaluate(() => document.documentElement.dataset.theme)).toBe(themeId);
				// The projections and lines finish coming out of the core.
				await page.waitForTimeout(1200);
				expect(await page.evaluate(audit)).toEqual([]);
			});
