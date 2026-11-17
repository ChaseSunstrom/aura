// The wake word's runtime in the built console (T-0875): its wasm is one of the console's own
// assets, nothing in the build names a CDN, and the runtime's chunk is reached only by a dynamic
// import (the wake word loads it when it starts, not every page load). Run after `npm run build`.
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join } from 'node:path';

const ROOT = new URL('../build/', import.meta.url).pathname;
const files = (dir) =>
	readdirSync(dir).flatMap((n) => (statSync(join(dir, n)).isDirectory() ? files(join(dir, n)) : [join(dir, n)]));
const all = files(ROOT);
const text = all.filter((f) => /\.(js|html|css|json)$/.test(f));
const fail = [];

const wasm = all.filter((f) => /\/client\/.*ort-wasm[^/]*\.wasm$/.test(f));
if (wasm.length !== 1) fail.push(`expected one runtime wasm among the client assets, found ${wasm.length}`);

for (const f of text) {
	const m = readFileSync(f, 'utf8').match(/cdn\.jsdelivr\.net|unpkg\.com|cdnjs\.cloudflare\.com/);
	if (m) fail.push(`${f.slice(ROOT.length)} names ${m[0]}`);
}

// The runtime's chunk: the one that carries its glue's file name.
const runtime = text.filter((f) => f.includes('/client/') && readFileSync(f, 'utf8').includes('ort-wasm-simd-threaded.mjs'));
if (runtime.length !== 1) fail.push(`expected one runtime chunk, found ${runtime.length}`);
for (const r of runtime) {
	const name = r.split('/').pop();
	for (const f of text) {
		if (f === r) continue;
		const src = readFileSync(f, 'utf8');
		const uses = src.split(name).length - 1;
		const lazy = (src.match(new RegExp(`import\\("[^"]*${name.replace('.', '\\.')}"\\)`, 'g')) ?? []).length;
		if (uses > lazy) fail.push(`${f.slice(ROOT.length)} loads the runtime (${name}) up front`);
	}
}

if (fail.length) {
	console.error(fail.map((f) => `FAIL  ${f}`).join('\n'));
	process.exit(1);
}
console.log(`wake assets: ${wasm[0].slice(ROOT.length)} self-hosted, ${runtime[0].slice(ROOT.length)} loaded on demand, no CDN`);
