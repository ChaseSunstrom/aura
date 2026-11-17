#!/usr/bin/env node
// The deployed console, checked the way a person meets it (T-0321; on the v2 shell, T-0838).
//
//   node scripts/verify/console-live.mjs <url> [--width 1440|390] [--check loaded|no-mic-toast|clipped|errors|shots|all] [--wait MS]
//
// Opens the console in headless Chromium with no microphone: the core, then every screen the
// launcher lists (APPS in aura-web/src/lib/ds/shell/routes.ts, read as text so a new app is
// checked without an edit here). Each must hydrate (the shell's data-ready) and show its screen.
// It fails on what the owner met: a screen still loading `--wait` ms after it opened (default
// 2000; ds/ui/State's data-state=loading, a data-status=loading list, a lone "Loading…"), an
// "open mic" alert on a device that has no mic, text clipped inside the screen, and (`errors`)
// an uncaught page error, a console error, a failed request or an HTTP error. `shots` writes one
// PNG per screen to .verify/console-live/ for a person to look at. A name this host maps to
// itself (/etc/hosts) can be pointed elsewhere with AURA_LIVE_IP.
//
// Not covered: whether the data shown is right (only that something real, or a clear empty,
// error or offline state, is there); the core's own drawing; the car's screen, which is its
// own device (`e2e/v2-car.spec.ts`).
import { mkdirSync, readFileSync } from 'node:fs';
import { createRequire } from 'node:module';

const require = createRequire(new URL('../../aura-web/package.json', import.meta.url));
const { chromium } = require('playwright');

const args = process.argv.slice(2);
const url = args.find((a) => /^https?:/.test(a));
const opt = (name, fallback) => {
	const i = args.indexOf(`--${name}`);
	return i >= 0 ? args[i + 1] : fallback;
};
const width = Number(opt('width', '1440'));
const check = opt('check', 'all');
const wait = Number(opt('wait', '2000'));
if (!url) {
	console.error('usage: console-live.mjs <url> [--width N] [--check loaded|no-mic-toast|clipped|errors|shots|all] [--wait MS]');
	process.exit(2);
}
const routes = readFileSync(new URL('../../aura-web/src/lib/ds/shell/routes.ts', import.meta.url), 'utf8');
const apps = [...(routes.split('export const APPS')[1] ?? '').matchAll(/screen: '(\w+)', path: '([^']+)'/g)].map((m) => ({ screen: m[1], path: m[2] }));
if (apps.length < 5) {
	console.error(`console-live: read ${apps.length} apps from routes.ts; its APPS shape changed`);
	process.exit(2);
}
const screens = [{ screen: 'core', path: '/' }, ...apps];
const height = width < 600 ? 844 : 900;
const host = new URL(url).hostname;
const ip = process.env.AURA_LIVE_IP ?? '';
const out = new URL('../../.verify/console-live/', import.meta.url).pathname;
mkdirSync(out, { recursive: true });

const browser = await chromium.launch({ args: ip ? [`--host-resolver-rules=MAP ${host} ${ip}`] : [] });
// No `permissions`: no microphone is granted, and the fake-device flags are not passed, so
// getUserMedia fails the way it does on a desktop with no mic.
const context = await browser.newContext({ viewport: { width, height } });
// The console gate (T-0387) answers an unsigned browser 401 on every relay and socket. Sign in
// the way the phone's web view does: one keyed page load, which the console turns into a session
// cookie in this context. Only that request carries the key, so map tiles and fonts never see it.
const key = (process.env.AURA_API_KEY ?? '').trim();
if (key) {
	const signIn = await context.request.get(url, { headers: { authorization: `Bearer ${key}`, accept: 'text/html' } });
	if (!(await context.cookies(url)).some((c) => c.name === 'jarvis_console'))
		console.error(`sign-in: HTTP ${signIn.status()} and no session cookie; is AURA_API_KEY the deployed key?`);
} else console.error('sign-in: AURA_API_KEY unset, so a console with a password answers 401');
const page = await context.newPage();
const problems = [];
const errors = [];
page.on('pageerror', (e) => errors.push(`page error: ${String(e).slice(0, 160)}`));
page.on('console', (m) => m.type() === 'error' && errors.push(`console error: ${m.text().slice(0, 160)}`));
page.on('requestfailed', (r) => {
	// A navigation away aborts what was in flight; that is not a fault.
	if (r.failure()?.errorText !== 'net::ERR_ABORTED') errors.push(`request failed: ${r.url().slice(0, 120)} ${r.failure()?.errorText}`);
});
page.on('response', (r) => r.status() >= 400 && errors.push(`HTTP ${r.status()}: ${r.url().slice(0, 120)}`));

const MIC = /open mic|microphone is unavailable|could not listen/i;
let n = 0;
for (const { screen, path } of screens) {
	const went = await page.goto(new URL(path, url).href, { waitUntil: 'domcontentloaded', timeout: 30_000 }).then(() => '', (e) => String(e).split('\n')[0]);
	if (went) {
		problems.push(`${screen}: ${went.slice(0, 160)}`);
		continue;
	}
	// The server-rendered shell shows before it hydrates; until data-ready nothing has loaded (T-0831).
	const ready = await page.waitForSelector('[data-testid=shell][data-ready=true]', { timeout: 20_000 }).then(() => true, () => false);
	if (!ready) {
		problems.push(`${screen}: the shell never became ready`);
		continue;
	}
	const opened = Date.now();
	await page.waitForTimeout(wait);
	const state = await page.evaluate(
		({ screen, mic }) => {
			const shown = (el) => {
				if (!(el instanceof HTMLElement)) return false;
				const r = el.getBoundingClientRect();
				return r.width > 0 && r.height > 0 && getComputedStyle(el).visibility !== 'hidden';
			};
			const shell = document.querySelector('[data-testid=shell]');
			const root = screen === 'core' ? shell : document.querySelector(`[data-testid=screen-${screen}]`);
			if (shell?.getAttribute('data-screen') !== screen || !shown(root)) return { missing: shell?.getAttribute('data-screen') ?? 'none' };
			const loading = [...root.querySelectorAll('[data-state=loading], [data-status=loading], [role=status]')]
				.filter((el) => shown(el) && (el.getAttribute('role') !== 'status' || /^Loading…?$/.test(el.textContent?.trim() ?? '')))
				.map((el) => el.getAttribute('data-testid') ?? el.textContent?.trim().slice(0, 40));
			const re = new RegExp(mic, 'i');
			const micToast = [...document.querySelectorAll('[role=alert]')].filter((el) => shown(el) && re.test(el.innerText)).map((el) => el.innerText.trim().slice(0, 80));
			const clipped = [];
			for (const el of root.querySelectorAll('p, span, h1, h2, h3, h4, li, button, label')) {
				if (!(el instanceof HTMLElement) || !el.offsetParent || !el.textContent?.trim()) continue;
				const cs = getComputedStyle(el);
				// A 1 px box is a screen-reader copy (the pairing code spelled out), hidden on purpose.
				if (cs.textOverflow === 'ellipsis' || el.clientWidth <= 1) continue;
				if (el.scrollWidth > el.clientWidth + 2 && !['visible', 'auto', 'scroll'].includes(cs.overflowX))
					// The report lands in CI's log: a secret (the pairing code) is named, never quoted.
					clipped.push(`"${el.closest('[data-secret]') ? '(secret)' : el.textContent.trim().slice(0, 40)}"`);
			}
			return { loading, micToast, clipped: clipped.slice(0, 5) };
		},
		{ screen, mic: MIC.source }
	);
	if (state.missing) {
		problems.push(`${path} opened the ${state.missing} screen, not ${screen}`);
		continue;
	}
	if ((check === 'loaded' || check === 'all') && state.loading.length)
		problems.push(`${screen}: still loading ${Date.now() - opened} ms after opening: ${state.loading.join(', ')}`);
	if ((check === 'no-mic-toast' || check === 'all') && state.micToast.length) problems.push(`${screen}: mic alert with no microphone: ${JSON.stringify(state.micToast)}`);
	if ((check === 'clipped' || check === 'all') && state.clipped.length) problems.push(`${screen}: clipped text ${state.clipped.join('; ')}`);
	// The shots are a CI artifact kept two weeks: a live pairing code or QR is boxed out of them.
	if (check === 'shots' || check === 'all') await page.screenshot({ path: `${out}${width}-${n}-${screen}.png`, mask: [page.locator('[data-secret]')] });
	n++;
}
await browser.close();
if (check === 'errors' || check === 'all') for (const e of new Set(errors)) problems.push(e);
for (const p of [...new Set(problems)]) console.log(`FAIL ${p}`);
console.log(`console-live ${width}px ${check}: ${n} of ${screens.length} screens, ${problems.length ? `${new Set(problems).size} problems` : 'ok'}`);
process.exit(problems.length || n < screens.length ? 1 : 0);
