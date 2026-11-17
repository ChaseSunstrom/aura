import { describe, expect, it } from 'vitest';
import { readdirSync, readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

// Every way the console changes something is a row in `tests/contracts/console-actions.json`:
// the Hermes tool that does the same thing when asked, or why only a person may, or a gap.
// `hermes/plugins/tests/test_action_parity.py` reads the same file and checks the tool side.
//
// This side reads the console's own sources, so a new changing call cannot be left out: it
// finds every call the console can make that is not a read by its name (the two clients'
// methods, the write fetches, the model manager's calls, the slash commands sent over the
// link, the literal house services) and fails on one the table does not name, and on a row
// that names a call no source makes any more.
//
// Not covered: a change that never leaves the browser (the tile layout, the per-device theme
// and voice are localStorage writes); a write fetch whose options sit in a variable
// (`errorReport.ts`, which files a crash report and nothing else); what a generic transport
// (`callTool`, `callService`, `command`, `dispatch`) is later given, beyond the literal
// `callService('domain', 'service'` and `dispatch('name'` forms; and whether a row's status
// is right, which is a judgement the table argues.

type Action = { id: string; what: string; status: 'tool' | 'human-only' | 'gap'; calls: string[]; tools?: string[]; reason?: string; note?: string };
type Registry = {
	pinned_gaps: number;
	actions: Action[];
	not_changes: { calls: string[]; why: string }[];
	unwired: Record<string, string[]>;
};

const REGISTRY = JSON.parse(
	readFileSync(fileURLToPath(new URL('../../../../tests/contracts/console-actions.json', import.meta.url)), 'utf8')
) as Registry;

const SRC = fileURLToPath(new URL('../../', import.meta.url));
const walk = (dir: string): string[] =>
	readdirSync(dir, { withFileTypes: true }).flatMap((e) => (e.isDirectory() ? walk(`${dir}${e.name}/`) : [`${dir}${e.name}`]));
// What runs in the browser: not the tests, and not the server routes that relay for it.
const SOURCES = walk(SRC)
	.filter((f) => /\.(ts|svelte)$/.test(f) && !/\.test\.ts$/.test(f) && !/\+server\.ts$/.test(f) && !f.includes('/lib/server/'))
	.map((path) => ({ path, text: readFileSync(path, 'utf8') }));
const rel = (p: string): string => p.slice(SRC.length);
const source = (name: string) => {
	const found = SOURCES.find((s) => s.path.endsWith(name));
	if (!found) throw new Error(`no source ${name}`);
	return found;
};

/** The methods of one class: a member one tab in that opens a parameter list. */
function membersOf(file: string, cls: string): string[] {
	const { text } = source(file);
	const from = text.indexOf(`export class ${cls}`);
	const body = text.slice(from, text.indexOf('\n}\n', from));
	return [...body.matchAll(/^\t(?:async )?([A-Za-z_]\w*)\s*(?:<[^>(]*>)?\(/gm)].map((m) => m[1]).filter((n) => n !== 'constructor');
}
const CLIENTS: [string, string][] = [
	['HermesLink', 'hermes/link.ts'],
	['JarvisClient', 'jarvisClient.ts']
];
/** A read by its name. Every other member has to be placed in the table: a change nobody listed is what this test is for. */
const READS_BY_NAME = /^(list|get|inspect)[A-Z]/;

const calledOutside = (name: string, file: string): string[] =>
	SOURCES.filter((s) => !s.path.endsWith(file) && new RegExp(`\\.${name}\\(`).test(s.text)).map((s) => rel(s.path));

/** The `/api/...` a write fetch goes to: template holes after a slash are `:x`, any other hole and the query are dropped. */
function urlKey(arg: string, text: string): string {
	let url = arg;
	if (/^[A-Za-z_]/.test(url)) url = new RegExp(`const ${url}\\s*=\\s*('[^']*'|\`[^\`]*\`)`).exec(text)?.[1] ?? url;
	return url.replace(/^['`]|['`]$/g, '').replace(/\/\$\{[^}]*\}/g, '/:x').replace(/\$\{[^}]*\}/g, '').replace(/\?.*$/, '');
}

function detected(): Set<string> {
	const found = new Set<string>();
	for (const [label, file] of CLIENTS) for (const m of membersOf(file, label)) if (!READS_BY_NAME.test(m)) found.add(`${label}.${m}`);
	for (const { path, text } of SOURCES) {
		// A write fetch, however the fetch is named: `fetch(url, { method: 'POST' ...`.
		for (const m of text.matchAll(/\w*[Ff]etch\w*\)?\(\s*(`[^`]*`|'[^']*'|[A-Za-z_]\w*)\s*,\s*\{[^{}]*?method:\s*'(POST|PUT|PATCH|DELETE)'/g)) found.add(`${m[2]} ${urlKey(m[1], text)}`);
		// A call the link is told by name, and a house service by its two literals.
		if (!path.endsWith('hermes/link.ts')) {
			for (const m of text.matchAll(/\.dispatch\(\s*'(\w+)'/g)) found.add(`dispatch:${m[1]}`);
			for (const m of text.matchAll(/\.dispatchPlugin\([^)]*?,\s*'(\w+)'\)/g)) found.add(`dispatchPlugin:${m[1]}`);
		}
		if (!path.endsWith('jarvisClient.ts')) for (const m of text.matchAll(/\.callService\(\s*'(\w+)',\s*'(\w+)'/g)) found.add(`service:${m[1]}.${m[2]}`);
	}
	// The model manager's calls are named exports around one `call('VERB', ...)`.
	const models = source('hud/models.svelte.ts').text;
	for (const m of models.matchAll(/call(?:<[^(]*>)?\('(POST|PUT|PATCH|DELETE)'/g)) {
		const owner = [...models.slice(0, m.index).matchAll(/^export const (\w+)/gm)].at(-1);
		if (owner) found.add(`models.${owner[1]}`);
	}
	return found;
}

const rows = REGISTRY.actions;
const notChanges = REGISTRY.not_changes.flatMap((n) => n.calls);
const unwired = Object.entries(REGISTRY.unwired).flatMap(([label, names]) => names.map((n) => `${label}.${n}`));
const listed = (): Set<string> => new Set([...rows.flatMap((a) => a.calls), ...notChanges, ...unwired]);

describe('the console actions registry', () => {
	it('names every changing call the console can make', () => {
		const missing = [...detected()].filter((c) => !listed().has(c)).sort();
		expect(
			missing,
			`calls with no row in tests/contracts/console-actions.json: ${missing.join(', ')}. Add an action (its tool, human-only with the reason, or gap), or put it in not_changes with why it changes nothing.`
		).toEqual([]);
	});

	it('names no call the console no longer makes', () => {
		const now = detected();
		const stale = [...listed()].filter((c) => !now.has(c)).sort();
		expect(stale, `rows naming calls no source makes: ${stale.join(', ')}`).toEqual([]);
	});

	it('keeps a method nothing calls in unwired, and calls every method an action names', () => {
		for (const [label, file] of CLIENTS) {
			for (const m of membersOf(file, label)) {
				const outside = calledOutside(m, file);
				if (REGISTRY.unwired[label]?.includes(m)) expect(outside, `${label}.${m} is called from ${outside.join(', ')}: give it an action row`).toEqual([]);
				else if (rows.some((a) => a.calls.includes(`${label}.${m}`))) expect(outside, `${label}.${m} is in an action but nothing calls it: move it to unwired`).not.toEqual([]);
			}
		}
	});

	it('gives each row an id, a call, and what its status needs', () => {
		const ids = new Set<string>();
		for (const a of rows) {
			expect(ids.has(a.id), `duplicate id ${a.id}`).toBe(false);
			ids.add(a.id);
			expect(a.calls.length, a.id).toBeGreaterThan(0);
			expect(a.what.length, a.id).toBeGreaterThan(8);
			if (a.status === 'tool') expect(a.tools?.length, `${a.id} is a tool row with no tool`).toBeGreaterThan(0);
			if (a.status === 'human-only') expect((a.reason ?? '').length, `${a.id} is human-only with no reason`).toBeGreaterThan(20);
			if (a.status === 'gap') expect((a.note ?? '').length, `${a.id} is a gap with no note on what the tool would be`).toBeGreaterThan(10);
		}
		const calls = [...rows.flatMap((a) => a.calls), ...notChanges, ...unwired];
		expect(new Set(calls).size, 'a call is in two places in the table').toBe(calls.length);
		for (const n of REGISTRY.not_changes) expect(n.why.length, n.calls.join(', ')).toBeGreaterThan(15);
	});

	it('lists the gaps, and a new one fails until pinned_gaps is raised with its row', () => {
		const gaps = rows.filter((a) => a.status === 'gap').map((a) => a.id);
		console.info(`console actions with no askable twin (${gaps.length}): ${gaps.join(', ')}`);
		expect(gaps.length, `gaps: ${gaps.join(', ')}`).toBe(REGISTRY.pinned_gaps);
	});
});
