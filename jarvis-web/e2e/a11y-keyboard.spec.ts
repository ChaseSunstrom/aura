import { test, expect, type Page } from '@playwright/test';
import { APPS } from '../src/lib/ds/shell/routes';
import { SNAPSHOT_APPROVAL, emit, openShell, settledAt } from './v2';

/**
 * Keyboard reach on the v2 shell (the brief's accessibility gate): on the core
 * and on every screen, Tab reaches every control a pointer can, and whatever
 * holds focus shows a ring that is drawn — an outline or a shadow with real
 * width — not a token that resolved to nothing. The approval card's verbs
 * answer from the keyboard, and a sheet closed with Escape hands focus back
 * to the button that opened it.
 *
 * What it does not check: reading order beyond "Tab reaches it" (axe's
 * `tabindex` rule covers positive tabindexes), and screen-reader announcements.
 */

type Focused = { key: string; tag: string; ring: boolean; ringStyle: string };

/**
 * The active element, its key (the number `controls` gave it, and its tag and
 * name for a failure), and whether its focus ring has visible width.
 */
const focused = (page: Page): Promise<Focused> =>
	page.evaluate(() => {
		const el = document.activeElement as HTMLElement | null;
		if (!el || el === document.body) return { key: 'body', tag: 'body', ring: false, ringStyle: '' };
		const cs = getComputedStyle(el);
		const outline = cs.outlineStyle !== 'none' && parseFloat(cs.outlineWidth) > 0;
		const shadow = cs.boxShadow !== 'none' && cs.boxShadow !== '';
		const name = `${el.tagName.toLowerCase()} "${el.getAttribute('aria-label') || el.textContent?.trim().slice(0, 30) || ''}"`;
		return { key: `${el.dataset.kbd ?? '?'} ${name}`, tag: el.tagName.toLowerCase(), ring: outline || shadow, ringStyle: `${cs.outlineStyle} ${cs.outlineWidth} / ${cs.boxShadow}` };
	});

/**
 * Every enabled, rendered control a pointer could press, numbered in the page
 * (`data-kbd`) so a control is the same key wherever a scroll has moved it.
 */
const controls = (page: Page): Promise<string[]> =>
	page.evaluate(() =>
		[...document.querySelectorAll<HTMLElement>('a[href], button, input, select, textarea, [tabindex]:not([tabindex="-1"])')]
			.filter((el) => {
				if ((el as HTMLButtonElement).disabled || el.closest('dialog:not([open]), [inert], [aria-hidden="true"]')) return false;
				const r = el.getBoundingClientRect();
				return r.width > 0 && r.height > 0 && getComputedStyle(el).visibility !== 'hidden';
			})
			.map((el, i) => {
				el.dataset.kbd = String(i);
				return `${i} ${el.tagName.toLowerCase()} "${el.getAttribute('aria-label') || el.textContent?.trim().slice(0, 30) || ''}"`;
			})
	);

/** Tab from the top until focus comes round again; what was reached and what showed no ring. */
async function walk(page: Page): Promise<{ reached: Set<string>; noRing: string[] }> {
	await page.locator('body').focus();
	const reached = new Set<string>();
	const noRing: string[] = [];
	for (let i = 0; i < 120; i++) {
		await page.keyboard.press('Tab');
		const f = await focused(page);
		if (f.tag === 'body') continue;
		if (reached.has(f.key)) break;
		reached.add(f.key);
		if (!f.ring) noRing.push(`${f.key}: ${f.ringStyle}`);
	}
	return { reached, noRing };
}

for (const path of ['/', ...APPS.map((a) => a.path)]) {
	test(`${path}: Tab reaches every control and each shows a focus ring`, async ({ page }) => {
		await page.setViewportSize({ width: 1440, height: 900 });
		await openShell(page, path);
		await page.waitForLoadState('networkidle').catch(() => {});
		if (path === '/') await settledAt(page, 'idle');
		const want = await controls(page);
		expect(want.length, 'controls on the page').toBeGreaterThan(2);
		const { reached, noRing } = await walk(page);
		expect(want.filter((c) => !reached.has(c)), 'controls Tab never reached').toEqual([]);
		expect(noRing, 'a control focused without a visible ring').toEqual([]);
	});
}

test('the approval card answers from the keyboard, and focus is not dropped on the page', async ({ page }) => {
	await page.setViewportSize({ width: 1440, height: 900 });
	await openShell(page);
	await emit(page, 'approval.request', SNAPSHOT_APPROVAL);
	await settledAt(page, 'alert');
	const card = page.getByTestId('approval-card');
	const deny = card.getByRole('button', { name: 'Deny' });
	// Reached by Tab, like every other control, with a ring on it.
	await controls(page);
	const { reached, noRing } = await walk(page);
	expect([...reached].some((k) => / button "Deny"$/.test(k)), 'Deny reached by Tab').toBe(true);
	expect(noRing).toEqual([]);
	await deny.focus();
	await page.keyboard.press('Enter');
	await expect(card).toHaveCount(0);
	await settledAt(page, 'idle');
	// The card took the focused control with it: the next Tab lands on the page, not nowhere.
	await page.keyboard.press('Tab');
	expect((await focused(page)).tag).not.toBe('body');
});

test('Escape closes a sheet and hands focus back to the button that opened it', async ({ page }) => {
	await page.setViewportSize({ width: 1440, height: 900 });
	await openShell(page);
	for (const name of ['All apps', 'Search or command', 'Devices']) {
		const opener = page.getByRole('button', { name, exact: true });
		await opener.focus();
		await page.keyboard.press('Enter');
		await expect(page.locator('dialog[open]')).toHaveCount(1);
		await page.keyboard.press('Escape');
		await expect(page.locator('dialog[open]')).toHaveCount(0);
		await expect(opener, `${name} has the focus back`).toBeFocused();
	}
});
