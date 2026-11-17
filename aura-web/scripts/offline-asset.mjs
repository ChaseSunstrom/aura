// The offline page the phone and the car show while the console is out of reach
// (src/routes/offline, prerendered by `npm run build`), made one self-contained file for
// the APK: its stylesheets inlined, its fonts as data URIs, no script, no CSP meta (the
// WebView loads it from a string, with no server to send one).
//
//   node scripts/offline-asset.mjs           write android-app/.../assets/console-offline.html
//   node scripts/offline-asset.mjs --check   exit 1 when that file is stale (CI, like tokens)
//
// Needs a build first. The page's core is drawn in CSS (T-0611), so the output is the same
// on every build of the same source and --check can compare it byte for byte.
import { existsSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const web = join(dirname(fileURLToPath(import.meta.url)), '..');
const page = join(web, 'build/prerendered/offline.html');
const out = join(web, '../android-app/app/src/main/assets/console-offline.html');

if (!existsSync(page)) {
	console.error(`offline-asset: ${page} is missing; run npm run build first`);
	process.exit(2);
}
let html = readFileSync(page, 'utf8');

const font = (name) => `url(data:font/woff2;base64,${readFileSync(join(web, 'static/fonts', name)).toString('base64')})`;
const css = (href) => readFileSync(join(web, 'build/client', href.replace(/^\.?\//, '')), 'utf8').replace(/url\(['"]?(?:\.\.\/)*\/?fonts\/([^'")]+)['"]?\)/g, (_, name) => font(name));

html = html
	.replace(/<link\b[^>]*>/g, (tag) => {
		// SvelteKit writes href before rel; any order. Icons and preloads would only
		// reach for a network the page exists to do without.
		const href = /href="([^"]+)"/.exec(tag)?.[1];
		return /rel="stylesheet"/.test(tag) && href ? `<style>${css(href)}</style>` : '';
	})
	.replace(/<meta http-equiv="content-security-policy"[^>]*>/gi, '')
	.replace(/<script\b[\s\S]*?<\/script>/g, '');

// Two stylesheets declare the same faces; a second identical rule adds nothing but its bytes.
const faces = new Set();
html = html.replace(/@font-face\{[^}]*\}/g, (rule) => (faces.has(rule) ? '' : (faces.add(rule), rule)));

// The phone carries this file in every APK: each font once, and the whole under 400 kB
// (T-0911: two stylesheets each brought their own copy of five fonts, 220 kB of repeats).
const MAX_BYTES = 400_000;
const fonts = [...html.matchAll(/url\(data:font\/woff2;base64,([^)]+)\)/g)].map((m) => m[1]);
if (new Set(fonts).size !== fonts.length || html.length > MAX_BYTES) {
	console.error(`offline-asset: ${fonts.length - new Set(fonts).size} font(s) inlined twice, ${html.length} bytes (limit ${MAX_BYTES})`);
	process.exit(1);
}

if (process.argv.includes('--check')) {
	const now = existsSync(out) ? readFileSync(out, 'utf8') : '';
	if (now !== html) {
		console.error('offline-asset: android-app/app/src/main/assets/console-offline.html is stale; run node scripts/offline-asset.mjs');
		process.exit(1);
	}
	console.log('offline-asset: current');
} else {
	writeFileSync(out, html);
	console.log(`offline-asset: wrote ${out} (${Math.round(html.length / 1024)} KiB)`);
}
