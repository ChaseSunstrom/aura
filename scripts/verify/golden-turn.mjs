#!/usr/bin/env node
// One golden turn per surface, reported to the last-verified board (T-0580).
//
//   AURA_API_KEY=… node scripts/verify/golden-turn.mjs <console-url> [--dry]
//
// console: signs in the way the phone's web view does, types one line into the core's
//          composer asking for a word that is in no prompt, and waits for it in the reply.
//          A real turn through the console, the adapter, Hermes and the model.
// car:     opens /car with a stand-in phone bridge and waits for the car's screen to draw
//          and say it is linked. The screen only: a head unit has no composer, and its
//          voice turn needs the phone.
// phone:   skipped unless adb sees one device with the app in front. No turn is typed on
//          the phone: a pass says only that the app is up.
// desktop: skipped: nothing here can drive a desktop session.
//
// Each surface is sent as passed, failed or skipped with its reason to
// `$AURA_BASE/api/verified` (default: the console's own relay is not used; the adapter on
// loopback 8643; `make golden-turn` passes the deployed one). Exit 1 when a surface failed
// (a skip is not a failure), 3 when the board did not take the result. `--dry` prints
// instead of sending. A name this host maps to itself can be pointed with AURA_LIVE_IP.
//
// Not covered: whether the asked word came in the reply itself or in the model's shown
// reasoning (either way the model answered through the console); speech in or out on any surface, the phone's and the desktop's own turns,
// and anything on a real head unit.
import { execFileSync } from 'node:child_process';
import { randomBytes } from 'node:crypto';
import { createRequire } from 'node:module';

const require = createRequire(new URL('../../aura-web/package.json', import.meta.url));
const { chromium } = require('playwright');

const args = process.argv.slice(2);
const url = args.find((a) => /^https?:/.test(a));
const key = (process.env.AURA_API_KEY ?? '').trim();
if (!url || !key) {
	console.error('usage: AURA_API_KEY=… golden-turn.mjs <console-url> [--dry]');
	process.exit(2);
}
const short = (e) => String(e?.message ?? e).split('\n')[0].slice(0, 180);
const surfaces = {};
/** Run one surface: its function returns a skip reason, or nothing when it passed. `proved` is what a pass means. */
const drive = async (name, proved, fn) => {
	surfaces[name] = await fn().then(
		(skip) => (skip ? { ok: null, why: skip } : { ok: true, why: proved }),
		(e) => ({ ok: false, why: short(e) })
	);
};

const ip = process.env.AURA_LIVE_IP ?? '';
// A browser that will not start is both browser surfaces failed, said on the board, not a crash
// that leaves the last pass standing.
let context = null;
let noBrowser = '';
const browser = await chromium.launch({ args: ip ? [`--host-resolver-rules=MAP ${new URL(url).hostname} ${ip}`] : [] }).catch((e) => {
	noBrowser = `no browser: ${short(e)}`;
	return null;
});
if (browser) {
	context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
	await context.request.get(url, { headers: { authorization: `Bearer ${key}`, accept: 'text/html' } }).catch(() => null);
}
// The turn this script types is a probe, not Sir asking: its outgoing prompt.submit says so as the
// surface, so the habit counter skips it (T-1201). The value is the contract's, tests/contracts/
// rig-session.json probe_surface, read against this text by deploy/tests/test_probe_mark.py. The
// console gains no test switch: the frame is rewritten on its way out, and only this one method.
const PROBE_SURFACE = 'probe';
const markProbe = (raw) => {
	try {
		const msg = JSON.parse(raw);
		if (msg?.method === 'prompt.submit' && msg.params && typeof msg.params === 'object') {
			msg.params.surface = { name: PROBE_SURFACE };
			return JSON.stringify(msg);
		}
	} catch {
		// not JSON: passes through untouched
	}
	return raw;
};
const newPage = async () => {
	if (!context) throw new Error(noBrowser);
	return context.newPage();
};

await drive('console', 'a typed turn answered', async () => {
	const page = await newPage();
	try {
		await page.routeWebSocket(/\/hermes\/ws(\?|$)/, (ws) => {
			const server = ws.connectToServer();
			ws.onMessage((m) => server.send(typeof m === 'string' ? markProbe(m) : m));
			server.onMessage((m) => ws.send(m));
			ws.onClose((code, reason) => server.close({ code, reason }));
			server.onClose((code, reason) => ws.close({ code, reason }));
		});
		await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 30_000 });
		await page.locator('[data-testid=shell][data-ready=true]').waitFor({ timeout: 30_000 });
		// The word asked for appears in no prompt, so the composer's echo cannot pass for a reply.
		const nonce = randomBytes(3).toString('hex').toUpperCase();
		await page.getByTestId('chat-input').fill(`Golden turn check. Reply with only the single word made by joining GOLD and ${nonce} with nothing between them.`);
		await page.getByTestId('chat-input').press('Enter');
		await page.getByText(`GOLD${nonce}`).first().waitFor({ timeout: 180_000 }).catch(() => {
			throw new Error('no reply carrying the asked word in 180 s');
		});
	} finally {
		await page.close();
	}
});

await drive('car', 'the screen drew, linked to a stand-in phone', async () => {
	const page = await newPage();
	try {
		await page.addInitScript(() => {
			window.JarvisCar = { state: () => JSON.stringify({ messages: [], navApps: [] }), act: () => {} };
		});
		await page.setViewportSize({ width: 800, height: 480 });
		await page.goto(new URL('/car', url).href, { waitUntil: 'domcontentloaded', timeout: 30_000 });
		await page.locator('[data-testid=screen-car][data-linked=true]').waitFor({ timeout: 30_000 });
	} finally {
		await page.close();
	}
});
await browser?.close().catch(() => {});

await drive('phone', 'the app is in front', async () => {
	const adb = process.env.ADB ?? `${process.env.ANDROID_HOME ?? `${process.env.HOME}/Android/Sdk`}/platform-tools/adb`;
	const run = (...a) => execFileSync(adb, a, { encoding: 'utf8', timeout: 15_000, stdio: ['ignore', 'pipe', 'ignore'] });
	let devices;
	try {
		devices = run('devices');
	} catch {
		return 'adb is not here';
	}
	const n = (devices.match(/^\S+\tdevice$/gm) ?? []).length;
	if (n !== 1) return n ? 'more than one phone attached (adb)' : 'no phone attached (adb)';
	// A phone on a desk with another app open is not a fault of the deploy.
	try {
		if (!/ResumedActivity.*ai\.jarvis\.app/i.test(run('shell', 'dumpsys', 'activity', 'activities'))) return 'a phone is attached but the app is not in front';
	} catch {
		return 'the attached phone did not answer adb';
	}
});
await drive('desktop', '', async () => 'nothing here can drive a desktop session');

for (const [name, r] of Object.entries(surfaces)) console.log(`${r.ok === true ? 'PASS' : r.ok === false ? 'FAIL' : 'SKIP'}: ${name}${r.why ? ` (${r.why})` : ''}`);
if (!args.includes('--dry')) {
	const base = process.env.AURA_BASE ?? 'http://127.0.0.1:8643';
	const res = await fetch(`${base}/api/verified`, {
		method: 'PUT',
		headers: { authorization: `Bearer ${key}`, 'content-type': 'application/json' },
		body: JSON.stringify({ at: Date.now() / 1000, surfaces })
	}).catch((e) => short(e));
	if (typeof res === 'string' || res.status !== 200) {
		console.error(`golden-turn: the board did not take it (${typeof res === 'string' ? res : res.status}) at ${base}`);
		process.exit(3);
	}
	// The board as the house now answers it, so a run shows what /api/status will say.
	const board = await res.json();
	const on = board.checks.filter((c) => c.current).length;
	console.log(`board: ${board.run ? `${on} of ${board.checks.length} checks passed on ${board.run.tag}${board.run.ok ? '' : `, failed at ${board.run.failed_check ?? '?'}`}` : 'no smoke run reported'}; surfaces: ${board.surfaces.map((x) => `${x.id}=${x.ok === true ? 'passed' : x.ok === false ? 'failed' : 'skipped'}`).join(' ')}`);
}
process.exit(Object.values(surfaces).some((r) => r.ok === false) ? 1 : 0);
