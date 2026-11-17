#!/usr/bin/env node
// What the car's page costs on the wire, by kind, the way the phone loads it (T-0746).
//
//   CONSOLE_URL=http://<host>:8199 AURA_API_KEY=… node scripts/car_bytes.mjs \
//       --out bench/results/car-bytes-before.json [--cold 30] [--steady 300]
//
// The owner, 2026-11-08: "the web UI and the map data seem to stream from the server
// constantly". Loads `/car` in Chromium as CarHud.kt's ConsolePage does (the bearer key on the
// page load, " JarvisCar" on the user agent, a stand-in `window.JarvisCar` bridge), counts every
// response body and socket frame for a cold load, then for a steady state, and writes bytes by
// kind -- assets (the page, bundles, fonts, images), map (tile, route and place data), api (other
// fetches), socket (frames both ways) -- plus every asset or map URL fetched more than once.
// Steady figures are per minute.
//
// Does not cover: the native hologram map's own data (on the phone, not this page), the
// WebView's disk cache across app restarts (one browser context here, its memory cache), or the
// audio the car plays.
import { writeFileSync } from 'node:fs';
import { createRequire } from 'node:module';

const require = createRequire(new URL('../aura-web/package.json', import.meta.url));
const { chromium } = require('@playwright/test');

const arg = (name, dflt) => {
	const i = process.argv.indexOf(`--${name}`);
	return i > 0 ? process.argv[i + 1] : dflt;
};
const base = (process.env.CONSOLE_URL || '').replace(/\/$/, '');
const key = process.env.AURA_API_KEY || '';
if (!base || !key) {
	console.error('set CONSOLE_URL and AURA_API_KEY');
	process.exit(2);
}
const out = arg('out', '');
const coldS = Number(arg('cold', '30'));
const steadyS = Number(arg('steady', '300'));

/** The kind a response counts under. */
function kind(url, type) {
	const path = new URL(url).pathname;
	if (/tile|\.pmtiles|\/maps?\/|\/route|\/places|speed-?limit/i.test(path)) return 'map';
	if (['document', 'script', 'stylesheet', 'font', 'image', 'media', 'manifest'].includes(type)) return 'assets';
	if (path.startsWith('/_app/')) return 'assets';
	return 'api';
}

const browser = await chromium.launch();
const context = await browser.newContext({
	viewport: { width: 800, height: 480 },
	userAgent: (await browser.newPage().then(async (p) => {
		const ua = await p.evaluate(() => navigator.userAgent);
		await p.close();
		return ua;
	})) + ' JarvisCar'
});
// The bearer key only on the console's own origin, as loadUrl's header map is only on the page.
await context.route(`${base}/**`, (route) =>
	route.continue({ headers: { ...route.request().headers(), authorization: `Bearer ${key}` } })
);
await context.addInitScript(() => {
	window.JarvisCar = { state: () => '{}', act: () => {} };
});
const page = await context.newPage();

let phase = 'cold';
const bytes = { cold: {}, steady: {} };
/** Socket bytes by what the frame is: its event `type`, else its JSON-RPC `method`, else "other". */
const frames = { cold: {}, steady: {} };
const seen = { cold: new Map(), steady: new Map() };
const add = (k, n) => (bytes[phase][k] = (bytes[phase][k] || 0) + n);

page.on('response', async (res) => {
	const req = res.request();
	const k = kind(res.url(), req.resourceType());
	let n = 0;
	try {
		n = (await res.body()).length;
	} catch {
		n = Number(res.headers()['content-length'] || 0); // a redirect or a body already gone
	}
	add(k, n);
	if (k !== 'api') {
		const url = res.url().split('#')[0];
		seen[phase].set(url, (seen[phase].get(url) || 0) + 1);
	}
});
page.on('websocket', (ws) => {
	// A reply is counted under the request that asked for it ("<- jarvis/sensors/readings"), so a
	// poll is named by what it polls; both sockets number their own requests, hence one map each.
	const asked = new Map();
	const frame = (sent) => (f) => {
		const n = typeof f.payload === 'string' ? Buffer.byteLength(f.payload) : f.payload.length;
		add('socket', n);
		let what = 'other';
		try {
			const m = JSON.parse(f.payload);
			const name = m.method === 'command.dispatch' ? `command.dispatch ${m.params?.name ?? ''}`
				: m.type === 'call_service' ? `call_service ${m.domain}.${m.service}`
				: m.method || m.type;
			if (sent && m.id !== undefined) asked.set(m.id, name);
			const reply = !sent && m.id !== undefined && (m.result !== undefined || m.error !== undefined || m.type === 'result');
			what = reply ? `<- ${asked.get(m.id) ?? 'unknown'}` : (m.params?.type || name || 'other');
		} catch {
			what = 'binary';
		}
		frames[phase][what] = (frames[phase][what] || 0) + n;
	};
	ws.on('framereceived', frame(false));
	ws.on('framesent', frame(true));
});

await page.goto(`${base}/car`, { waitUntil: 'load' });
await page.waitForTimeout(coldS * 1000);
phase = 'steady';
await page.waitForTimeout(steadyS * 1000);

const perMin = (o) => Object.fromEntries(Object.entries(o).map(([k, v]) => [k, Math.round((v * 60) / steadyS)]));
const result = {
	measured_at: new Date().toISOString(),
	cold_seconds: coldS,
	steady_seconds: steadyS,
	cold: bytes.cold,
	steady: perMin(bytes.steady),
	// The socket's bytes by frame kind, so a chatty event is named, not guessed.
	socket_cold: frames.cold,
	socket_steady: perMin(frames.steady),
	// Asset or map URLs fetched in the steady state at all, or more than once cold.
	repeats: [
		...[...seen.steady].map(([url, n]) => ({ url: url.replace(base, ''), phase: 'steady', n })),
		...[...seen.cold].filter(([, n]) => n > 1).map(([url, n]) => ({ url: url.replace(base, ''), phase: 'cold', n }))
	]
};
const top = Object.entries(result.socket_steady).sort((a, b) => b[1] - a[1]).slice(0, 8);
console.log(JSON.stringify({ cold: result.cold, steady_per_min: result.steady, socket_steady_top: top, repeats: result.repeats.length }, null, 1));
if (out) writeFileSync(out, JSON.stringify(result, null, 2) + '\n');
await browser.close();
