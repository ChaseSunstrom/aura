#!/usr/bin/env node
// The size budget (GLASS_HUD brief, "ACCESSIBILITY AND PERFORMANCE GATES"):
// the console's client bundle and the Android APK, measured and held against
// `scripts/verify/hud-size-budget.json`. Exit 1 on growth past a budget.
//
// What is measured
//   web_client_gzip   every .js and .css under jarvis-web/build/client, each
//                     gzipped on its own at level 9 and summed — what a browser
//                     actually transfers for the whole app, all routes. Fonts,
//                     icons and images are reported but not budgeted: a font
//                     subset is a design decision made in design/, not a
//                     regression a bundle gate should catch.
//   web_client_largest_gzip
//                     the single largest chunk, gzipped — a route that swallows
//                     a library shows here before it shows in the total.
//   android_apk_debug the debug APK as assembleDebug leaves it. The release
//                     APK is the same unminified build plus signing (R8 is off,
//                     app/build.gradle.kts), so one budget covers both; a
//                     release build is compared with the same number.
//
// `--record` rewrites the budget file's `measured` values (never the budgets)
// so the commit that changes a size carries the new number beside the old.
// Budgets are the recorded size plus `tolerance_pct`: a content hash or a
// comment moves a gzip by bytes, and a gate that failed on that would be
// muted within a week. Anything inside the tolerance is reported, not failed.
//
// A missing artefact is a SKIP with its reason, never a pass: the web build is
// always present in CI (the job builds it); the APK only when apk:build ran.
import { existsSync, readFileSync, readdirSync, statSync, writeFileSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { gzipSync } from 'node:zlib';

const ROOT = resolve(new URL('../../', import.meta.url).pathname);
const BUDGET_FILE = join(ROOT, 'scripts', 'verify', 'hud-size-budget.json');
const record = process.argv.includes('--record');
const apkArg = process.argv.find((a) => a.startsWith('--apk='))?.slice(6);

const budget = JSON.parse(readFileSync(BUDGET_FILE, 'utf8'));
const kb = (n) => `${(n / 1024).toFixed(1)} KB`;
const mb = (n) => `${(n / (1024 * 1024)).toFixed(2)} MB`;

function walk(dir) {
	const out = [];
	for (const entry of readdirSync(dir, { withFileTypes: true })) {
		const p = join(dir, entry.name);
		if (entry.isDirectory()) out.push(...walk(p));
		else out.push(p);
	}
	return out;
}

const results = [];
let failed = false;

function check(key, value, note) {
	const entry = budget.sizes[key];
	if (!entry) throw new Error(`no budget entry for ${key}`);
	const limit = Math.ceil(entry.measured * (1 + budget.tolerance_pct / 100));
	const fmt = key.startsWith('android') ? mb : kb;
	const over = value > limit;
	const delta = value - entry.measured;
	const sign = delta >= 0 ? '+' : '';
	results.push({ key, value, measured: entry.measured, limit, over });
	console.log(`${over ? 'FAIL' : ' ok '} ${key.padEnd(24)} ${fmt(value).padStart(11)}  recorded ${fmt(entry.measured)} (${sign}${fmt(delta)}), budget ${fmt(limit)}${note ? `  ${note}` : ''}`);
	if (over) failed = true;
	if (record) entry.measured = value;
}

// --- the console's client bundle ------------------------------------------
const client = join(ROOT, 'jarvis-web', 'build', 'client');
if (!existsSync(client)) {
	console.log('FAIL web_client_gzip: jarvis-web/build/client is missing — run `npm run build` in jarvis-web first');
	failed = true;
} else {
	const files = walk(client);
	let total = 0;
	let largest = { file: '', size: 0 };
	let assets = 0;
	for (const f of files) {
		if (/\.(js|css)$/.test(f)) {
			const size = gzipSync(readFileSync(f), { level: 9 }).length;
			total += size;
			if (size > largest.size) largest = { file: f.slice(client.length + 1), size };
		} else {
			assets += statSync(f).size;
		}
	}
	check('web_client_gzip', total, `${files.filter((f) => /\.(js|css)$/.test(f)).length} files`);
	check('web_client_largest_gzip', largest.size, largest.file);
	console.log(`     other client assets (fonts, icons, images; not budgeted): ${kb(assets)}`);
}

// --- the Android APK -----------------------------------------------------------
const apk = apkArg ? resolve(apkArg) : join(ROOT, 'android-app', 'app', 'build', 'outputs', 'apk', 'debug', 'app-debug.apk');
if (!existsSync(apk)) {
	console.log(`SKIP android_apk_debug: ${apk} is not present (assembleDebug has not run here; apk:build measures it in CI)`);
} else {
	check('android_apk_debug', statSync(apk).size, apk.slice(ROOT.length + 1));
}

if (record) {
	budget.recorded_at = new Date().toISOString().slice(0, 10);
	writeFileSync(BUDGET_FILE, JSON.stringify(budget, null, 2) + '\n');
	console.log(`recorded the measured sizes in ${BUDGET_FILE.slice(ROOT.length + 1)}`);
}
process.exit(failed ? 1 : 0);
