// The tab wears the worn theme's own icon at rest, in the built console (T-0883): both icon links
// carry the pack's favicon byte for byte, and they change when the theme does. The deployed
// console had asked for "undefined" here since its image could not see ../themes; favicon.test.ts
// pins that the copies live in the package, this that the build serves them.
import { readFileSync } from 'node:fs';
import { expect, test, type Page } from '@playwright/test';
import { openShell, resetHermes } from './v2';

test.afterEach(async ({ page }) => resetHermes(page));

const pack = (id: string) => readFileSync(new URL(`../../themes/${id}/assets/icons/favicon.ico`, import.meta.url));

/** The bytes behind each tab icon link: a data URL decoded (vite inlines small files), else fetched. */
async function tabIcons(page: Page): Promise<Buffer[]> {
	const hrefs = await page.evaluate(() =>
		[...document.querySelectorAll<HTMLLinkElement>('link[rel="icon"]')].map((l) => l.href)
	);
	return Promise.all(
		hrefs.map(async (href) => {
			if (href.startsWith('data:')) return Buffer.from(href.slice(href.indexOf(',') + 1), 'base64');
			const res = await page.request.get(href);
			expect(res.status(), href).toBe(200);
			return res.body();
		})
	);
}

const wears = async (page: Page, id: string) =>
	expect.poll(async () => (await tabIcons(page)).map((b) => b.equals(pack(id))), { timeout: 15_000 }).toEqual([true, true]);

test('the tab wears the worn theme’s icon at rest and changes with the theme', async ({ page }) => {
	await openShell(page, '/');
	// The mock's persona wears JARVIS.
	await expect.poll(() => page.evaluate(() => document.documentElement.dataset.theme)).toBe('jarvis');
	await wears(page, 'jarvis');

	await page.request.post('/hermes/api/test/theme', { data: { op: 'apply', id: 'aura' } });
	await wears(page, 'aura');
});
