// Screenshot the deployed console's Board (T-0733) at a phone, a tablet and a desktop width, and
// fail unless every panel draws real data: each of the six panels present, none unavailable, and
// the adapter's three sources answering. Also reports the frame intervals while the Board animates.
//
//   AURA_API_KEY=… node scripts/verify/board_shots.mjs https://<console>/ [--out docs/verification]
//
// Writes t0733-board-{1440,768,360}.png. It signs in as console-live.mjs does.
// Not covered: whether the numbers are right (the plugin's and the adapter's suites pin those);
// a panel may honestly be stale on a quiet day, which this reports and does not fail on.
import { mkdirSync } from 'node:fs';
import { createRequire } from 'node:module';

const require = createRequire(new URL('../../aura-web/package.json', import.meta.url));
const { chromium } = require('playwright');

const args = process.argv.slice(2);
const url = args.find((a) => /^https?:/.test(a));
const i = args.indexOf('--out');
const out = i >= 0 ? args[i + 1] : 'docs/verification';
if (!url) {
	console.error('usage: board_shots.mjs <console url> [--out dir]');
	process.exit(2);
}
mkdirSync(out, { recursive: true });
const key = (process.env.AURA_API_KEY ?? '').trim();
const browser = await chromium.launch();
let ok = true;
for (const width of [1440, 768, 360]) {
	const context = await browser.newContext({ viewport: { width, height: width < 600 ? 800 : 900 } });
	if (key) await context.request.get(url, { headers: { authorization: `Bearer ${key}`, accept: 'text/html' } });
	const page = await context.newPage();
	const sources = await page.request.get(new URL('/hermes/api/board', url).href).then((r) => r.json()).catch((e) => ({ error: String(e) }));
	await page.goto(new URL('/board', url).href, { waitUntil: 'domcontentloaded', timeout: 30_000 });
	await page.locator('[data-testid=shell][data-ready=true]').waitFor({ timeout: 30_000 });
	const panels = page.locator('[data-testid^="board-panel-"]');
	await panels.nth(5).waitFor({ timeout: 20_000 }).catch(() => {});
	const states = await panels.evaluateAll((els) => els.map((e) => `${e.dataset.testid?.slice(-2)}:${e.dataset.state}`));
	const labels = await page.locator('[data-testid^="board-label-"]').allInnerTexts();
	const frames = await page.evaluate(
		() =>
			new Promise((resolve) => {
				const gaps = [];
				let last = performance.now();
				const tick = (t) => {
					gaps.push(t - last);
					last = t;
					if (gaps.length < 120) requestAnimationFrame(tick);
					else resolve(gaps.sort((a, b) => a - b));
				};
				requestAnimationFrame(tick);
			})
	);
	const p50 = Math.round(frames[Math.floor(frames.length / 2)]);
	const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
	await page.screenshot({ path: `${out}/t0733-board-${width}.png`, fullPage: true });
	const why = [];
	if (states.length !== 6) why.push(`${states.length} panels`);
	if (states.some((s) => s.endsWith('unavailable'))) why.push('a panel is unavailable');
	if (overflow > 0) why.push(`${overflow} px of sideways scroll`);
	for (const [name, s] of Object.entries(sources.sources ?? {})) if (s.status === 'unavailable') why.push(`${name}: ${s.why}`);
	if (sources.error) why.push(sources.error);
	console.log(`${width}: ${states.join(' ')} | ${labels.join(' · ')} | frame p50 ${p50} ms${why.length ? ` | FAIL ${why.join('; ')}` : ''}`);
	if (why.length) ok = false;
	await context.close();
}
await browser.close();
process.exit(ok ? 0 : 1);
