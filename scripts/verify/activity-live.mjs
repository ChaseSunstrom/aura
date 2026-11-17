// Record the deployed console while JARVIS browses (T-0732): the stage's steps projection with
// the page it has open, from a real turn on the real stack. One width per run:
//
//   AURA_API_KEY=… AURA_LIVE_IP=… node scripts/verify/activity-live.mjs https://<console>/ \
//       --width 390 --out docs/screens/v2/activity/browse-390.webm
//
// It signs in as console-live.mjs does, types a browse request into the core's composer, and
// passes when the steps projection showed a browse step with a picture of the page and the
// reply arrived. It also reports the frame intervals while the turn ran (the phone's frame cost).
// Not covered: what a phone's own GPU makes of it; this is headless Chromium on the host.

import { mkdirSync, renameSync, rmSync } from 'node:fs';
import { createRequire } from 'node:module';
import { dirname } from 'node:path';

const require = createRequire(new URL('../../jarvis-web/package.json', import.meta.url));
const { chromium } = require('playwright');

const args = process.argv.slice(2);
const url = args.find((a) => /^https?:/.test(a));
const opt = (name, fallback) => {
	const i = args.indexOf(`--${name}`);
	return i >= 0 ? args[i + 1] : fallback;
};
if (!url) {
	console.error('usage: activity-live.mjs <console url> [--width 1440] [--out file.webm]');
	process.exit(2);
}
const width = Number(opt('width', '1440'));
const height = width < 600 ? 844 : 900;
const out = opt('out', `.verify/activity-live/browse-${width}.webm`);
const prompt = opt('prompt', 'Use the browser to open https://example.com and tell me the page title in one sentence.');
const ip = process.env.AURA_LIVE_IP ?? '';
const host = new URL(url).hostname;

const browser = await chromium.launch({ args: ip ? [`--host-resolver-rules=MAP ${host} ${ip}`] : [] });
const videoDir = `${dirname(out)}/.video-${process.pid}`;
mkdirSync(videoDir, { recursive: true });
const context = await browser.newContext({ viewport: { width, height }, recordVideo: { dir: videoDir, size: { width, height } } });
const key = (process.env.AURA_API_KEY ?? '').trim();
if (key) await context.request.get(url, { headers: { authorization: `Bearer ${key}`, accept: 'text/html' } });
else console.error('sign-in: AURA_API_KEY unset, so a console with a password answers 401');

const page = await context.newPage();
const problems = [];
let ok = false;
try {
	await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 30_000 });
	await page.locator('[data-testid=shell][data-ready=true]').waitFor({ timeout: 30_000 });
	await page.waitForTimeout(1500);
	// Frame intervals from here to the reply: the turn's cost on this width.
	await page.evaluate(() => {
		const w = window;
		w.__frames = [];
		let last = performance.now();
		const tick = (t) => {
			w.__frames.push(t - last);
			last = t;
			if (!w.__stopFrames) requestAnimationFrame(tick);
		};
		requestAnimationFrame(tick);
	});
	await page.getByTestId('chat-input').fill(prompt);
	await page.getByTestId('chat-input').press('Enter');
	const browse = page.locator('[data-testid=stage-steps] li.step[data-kind=browse]');
	await browse.first().waitFor({ timeout: 120_000 });
	const picture = page.getByTestId('step-page');
	await picture.waitFor({ timeout: 60_000 }).catch(() => problems.push('no picture of the page on the browse step'));
	const steps = await page.locator('[data-testid=stage-steps] li.step .what').allTextContents();
	console.log(`steps: ${JSON.stringify(steps)}`);
	// The reply: the stage leaves the thinking scene.
	await page.locator('[data-testid=shell]:not([data-scene=thinking]):not([data-scene=working])').waitFor({ timeout: 120_000 });
	await page.waitForTimeout(2500);
	const frames = await page.evaluate(() => {
		window.__stopFrames = true;
		const f = window.__frames.slice(1).sort((a, b) => a - b);
		const at = (q) => Math.round(f[Math.min(f.length - 1, Math.floor(q * f.length))] ?? 0);
		return { n: f.length, p50: at(0.5), p95: at(0.95), max: Math.round(f[f.length - 1] ?? 0) };
	});
	console.log(`frames while the turn ran: ${JSON.stringify(frames)} (ms between frames)`);
	ok = problems.length === 0;
} catch (e) {
	problems.push(String(e).split('\n')[0]);
}
for (const p of problems) console.error(`problem: ${p}`);
const video = page.video();
await context.close();
await browser.close();
// A page that failed before its first paint has no video; the problem above says why.
const recorded = video ? await video.path().catch(() => null) : null;
if (recorded) {
	mkdirSync(dirname(out), { recursive: true });
	renameSync(recorded, out);
	console.log(`recording: ${out}`);
} else ok = false;
rmSync(videoDir, { recursive: true, force: true });
process.exit(ok ? 0 : 1);
