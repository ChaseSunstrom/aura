import { test, expect, type Page } from '@playwright/test';
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { APPS } from '../src/lib/ds/shell/routes';
import { SNAPSHOT_APPROVAL, demo, emit, openShell, say, settledAt } from './v2';

/**
 * Contrast against the RENDERED backdrop, not the token.
 *
 * The scenes are the v2 shell where its light is brightest: the core in alert
 * with an approval card out and JARVIS's subtitles over it, the core speaking,
 * and every full screen. On each, every visible text block is located, then
 * the page is photographed with all text made transparent — the same layout,
 * the core's light and the gradients exactly as composited, minus the glyphs —
 * and the pixels under each block are sampled on a grid. The contrast of the
 * text colour over each sample is computed with WCAG 2.x relative luminance; a
 * block passes when the darkest sample (its worst pixel) clears 4.5:1, or 3:1
 * for large text (≥ 24 px, or ≥ 18.66 px at weight 700). The blocks and their
 * numbers go to `.verify/hud-contrast.json`; `scripts/verify/hud-contrast.py`
 * reads that file back and re-derives the verdict as the second opinion.
 *
 * Why the worst pixel and not the mean: a card can straddle the core's light,
 * and a mean over the block would let one edge of a label sit on light while
 * the other half made the average look fine.
 *
 * What it does not check: text in `<canvas>` (the core draws no text) and text
 * a screen reader receives but sighted users never see (`aria-label`); text in
 * disabled controls and placeholders is skipped, as WCAG exempts it.
 *
 * ## The baseline
 *
 * The `contrast` list in `e2e/a11y-baseline.json` holds blocks known to fail,
 * each with the ratio it was recorded at. A known block may not get worse; an
 * unknown failing block fails; a known block that now passes, or is gone,
 * fails too so the entry is removed — the same ratchet as the axe half.
 */

const ROOT = new URL('../../', import.meta.url).pathname;
const MIN_TEXT = 4.5;
const MIN_LARGE = 3;
type KnownBlock = { path: string; text: string; min: number };
const BASELINE = JSON.parse(readFileSync(new URL('./a11y-baseline.json', import.meta.url), 'utf8')) as { contrast: KnownBlock[] };
// Sampling jitter between runs (sub-pixel text placement) is well under this.
const WIGGLE = 0.1;
const VIEWPORT = { width: 1440, height: 900 };

type Block = { path: string; text: string; color: string; fontSize: number; fontWeight: number; rect: { x: number; y: number; w: number; h: number } };
type Result = Block & { large: boolean; required: number; min: number; median: number; samples: number; worst: string };

/** Every visible block of real text inside the viewport, with its rendered colour. */
const textBlocks = (page: Page): Promise<Block[]> =>
	page.evaluate(() => {
		const out: Block[] = [];
		const path = (el: Element) => {
			const parts: string[] = [];
			for (let e: Element | null = el; e && parts.length < 4; e = e.parentElement) {
				const id = (e as HTMLElement).dataset?.testid;
				parts.unshift(id ? `[${id}]` : e.tagName.toLowerCase() + (e.className && typeof e.className === 'string' ? '.' + e.className.split(' ')[0] : ''));
				if (id) break;
			}
			return parts.join('>');
		};
		const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
		let node: Node | null;
		while ((node = walker.nextNode())) {
			const text = (node.textContent ?? '').trim();
			if (!text) continue;
			const el = node.parentElement;
			if (!el || el.closest('script, style, noscript, [aria-hidden="true"], [disabled], option')) continue;
			const cs = getComputedStyle(el);
			if (cs.visibility === 'hidden' || cs.display === 'none' || Number(cs.opacity) === 0 || cs.color === 'rgba(0, 0, 0, 0)') continue;
			const range = document.createRange();
			range.selectNodeContents(node);
			const r = range.getBoundingClientRect();
			const x = Math.max(0, r.left), y = Math.max(0, r.top);
			const w = Math.min(innerWidth, r.right) - x, h = Math.min(innerHeight, r.bottom) - y;
			if (w < 2 || h < 2) continue;
			// Clipped away by an ancestor's overflow (a scrolled-out row) is not on screen.
			const probe = document.elementFromPoint(x + w / 2, y + h / 2);
			if (!probe || !(probe === el || el.contains(probe) || probe.contains(el))) continue;
			// SVG text is painted with `fill`, not `color` (the Ops board's labels):
			// reading `color` compared the inherited bright ink against the glyphs.
			const ink = el instanceof SVGElement && cs.fill.startsWith('rgb') ? cs.fill : cs.color;
			out.push({ path: path(el), text: text.slice(0, 40), color: ink, fontSize: parseFloat(cs.fontSize), fontWeight: Number(cs.fontWeight) || 400, rect: { x, y, w, h } });
		}
		return out;
	});

const HIDE_TEXT = `*, *::before, *::after { color: transparent !important; -webkit-text-fill-color: transparent !important; text-shadow: none !important; caret-color: transparent !important; } svg text, svg tspan { fill: transparent !important; stroke: none !important; }`;

/** Every text block on the page as it stands, sampled against the frame photographed without glyphs. */
async function sample(page: Page, scene: string): Promise<Result[]> {
	// Every finite animation finished: a card mid-fade reads as failing text it is not. Looked
	// at again after each wait, until a look finds none: a scan-in that starts when the data
	// lands, after the first look, drew a chip's ink over a fill not yet revealed (pipeline 801).
	await page.evaluate(async () => {
		const finite = () => document.getAnimations().filter((a) => a.playState !== 'finished' && a.effect?.getComputedTiming().iterations !== Infinity);
		const until = performance.now() + 3_000;
		for (let running = finite(); running.length && performance.now() < until; running = finite()) {
			await Promise.race([Promise.all(running.map((a) => a.finished.catch(() => undefined))), new Promise((resolve) => setTimeout(resolve, until - performance.now()))]);
			// Two frames: what a finished animation's class change starts is running by then.
			await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
		}
	});
	const blocks = (await textBlocks(page)).map((b) => ({ ...b, path: `${scene} ${b.path}` }));
	// The scene as sampled, kept with the job's artifacts: a block that fails only
	// under CI's fonts cannot be reproduced here, so the picture has to travel.
	await page.screenshot({ path: `test-results/contrast-${scene.replace(/[^a-z0-9]+/gi, '-')}.png` });
	// The same frame without glyphs, sampled in the page: the screenshot is
	// decoded by the browser itself, so no image library is needed.
	const style = await page.addStyleTag({ content: HIDE_TEXT });
	await page.waitForTimeout(100);
	const png = (await page.screenshot({ fullPage: false })).toString('base64');
	await style.evaluate((el) => el.remove());
	return page.evaluate(
		async ({ png, blocks, MIN_TEXT, MIN_LARGE }) => {
			const img = new Image();
			img.src = `data:image/png;base64,${png}`;
			await img.decode();
			const canvas = document.createElement('canvas');
			canvas.width = img.naturalWidth;
			canvas.height = img.naturalHeight;
			const ctx = canvas.getContext('2d')!;
			ctx.drawImage(img, 0, 0);
			const scale = img.naturalWidth / innerWidth;
			const lum = (r: number, g: number, b: number) => {
				const f = (c: number) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4);
				return 0.2126 * f(r / 255) + 0.7152 * f(g / 255) + 0.0722 * f(b / 255);
			};
			const ratio = (l1: number, l2: number) => (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05);
			const parse = (c: string) => {
				const m = c.match(/[\d.]+/g)!.map(Number);
				return { r: m[0], g: m[1], b: m[2], a: m.length > 3 ? m[3] : 1 };
			};
			return blocks.map((b) => {
				const fg = parse(b.color);
				const large = b.fontSize >= 24 || (b.fontSize >= 18.66 && b.fontWeight >= 700);
				const cols = Math.min(24, Math.max(2, Math.floor(b.rect.w / 4)));
				const rows = Math.min(6, Math.max(2, Math.floor(b.rect.h / 4)));
				const ratios: number[] = [];
				let worst = '';
				let min = Infinity;
				for (let i = 0; i < cols; i++) {
					for (let j = 0; j < rows; j++) {
						const px = Math.floor((b.rect.x + ((i + 0.5) / cols) * b.rect.w) * scale);
						const py = Math.floor((b.rect.y + ((j + 0.5) / rows) * b.rect.h) * scale);
						const [r, g, bl] = ctx.getImageData(px, py, 1, 1).data;
						// A translucent text colour is composited on the sample before comparing.
						const tr = fg.r * fg.a + r * (1 - fg.a), tg = fg.g * fg.a + g * (1 - fg.a), tb = fg.b * fg.a + bl * (1 - fg.a);
						const c = ratio(lum(tr, tg, tb), lum(r, g, bl));
						ratios.push(c);
						if (c < min) {
							min = c;
							worst = `rgb(${r}, ${g}, ${bl})`;
						}
					}
				}
				ratios.sort((a, z) => a - z);
				return { ...b, large, required: large ? MIN_LARGE : MIN_TEXT, min: +min.toFixed(2), median: +ratios[Math.floor(ratios.length / 2)].toFixed(2), samples: ratios.length, worst } as Result;
			});
		},
		{ png, blocks, MIN_TEXT, MIN_LARGE }
	);
}

test('every text block on the core and every screen clears AA against its rendered backdrop', async ({ page }) => {
	test.setTimeout(180_000);
	await page.setViewportSize(VIEWPORT);
	const results: Result[] = [];

	// The core at its brightest: alert, a card out, JARVIS's words over the light.
	await openShell(page);
	await say(page, 'turn on the lab lights');
	await expect(page.getByTestId('conversation-jarvis')).not.toBeEmpty({ timeout: 15_000 });
	await expect(page.getByTestId('shell')).toHaveAttribute('data-reactor', 'idle', { timeout: 20_000 });
	await emit(page, 'approval.request', SNAPSHOT_APPROVAL);
	await settledAt(page, 'alert');
	await expect(page.getByTestId('conversation-jarvis')).toBeVisible();
	results.push(...(await sample(page, 'core alert')));
	await page.getByTestId('approval-card').getByRole('button', { name: 'Deny' }).click();
	await settledAt(page, 'idle');
	await demo(page, 'speaking');
	await page.waitForTimeout(800);
	results.push(...(await sample(page, 'core speaking')));
	await demo(page, '');

	for (const app of APPS) {
		await openShell(page, app.path);
		await page.waitForLoadState('networkidle').catch(() => {});
		results.push(...(await sample(page, app.path)));
	}
	expect(results.length, 'text blocks found').toBeGreaterThan(60);

	mkdirSync(`${ROOT}.verify`, { recursive: true });
	writeFileSync(`${ROOT}.verify/hud-contrast.json`, JSON.stringify({ recorded_at: new Date().toISOString(), scene: `the core (alert with a card, speaking) and ${APPS.length} screens`, viewport: VIEWPORT, min_text: MIN_TEXT, min_large: MIN_LARGE, blocks: results }, null, 2));
	const describe = (r: Result) => `${r.path} "${r.text}" ${r.color} ${r.fontSize}px/${r.fontWeight} over ${r.worst}: ${r.min}:1 (needs ${r.required})`;
	const known = new Map(BASELINE.contrast.map((k) => [`${k.path} "${k.text}"`, k]));
	const failing = results.filter((r) => r.min < r.required);
	const fresh = failing.filter((r) => !known.has(`${r.path} "${r.text}"`)).map(describe);
	const worse = failing.filter((r) => known.has(`${r.path} "${r.text}"`) && r.min < known.get(`${r.path} "${r.text}"`)!.min - WIGGLE).map((r) => `${describe(r)} — baseline ${known.get(`${r.path} "${r.text}"`)!.min}:1`);
	const stillFailing = new Set(failing.map((r) => `${r.path} "${r.text}"`));
	const stale = [...known.keys()].filter((k) => !stillFailing.has(k));
	const worstOverall = [...results].sort((a, b) => a.min - b.min)[0];
	console.log(`hud-contrast: ${results.length} text blocks, worst ${worstOverall.min}:1 (${worstOverall.path} "${worstOverall.text}" ${worstOverall.color} over ${worstOverall.worst}), ${failing.length} below AA (${failing.length - fresh.length} in the baseline)`);
	expect(fresh, 'text blocks below AA against their rendered backdrop, not in a11y-baseline.json').toEqual([]);
	expect(worse, 'baseline blocks whose contrast dropped').toEqual([]);
	expect(stale, 'baseline blocks that now pass or are gone: remove them from a11y-baseline.json').toEqual([]);
});
