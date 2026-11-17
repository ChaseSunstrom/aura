// The repository's own records, read for the Pages site (T-1516): what the model can reach, what
// it may do unasked, what the benchmark asks of it, and the two personas it can wear.
//
// Every figure the site shows comes from a file the repository already keeps true: the reference
// pages `make docs` generates (docs/reference/*.md, CAPABILITIES.md; CI's docs-check fails a
// stale one) and the theme packs (themes/<id>/theme.json, the source the console's themes are
// generated from). So a new plugin, a new matrix row or a reworded persona changes the site at its
// next build, and a page whose shape this reader no longer knows stops the build and names it.
//
// Not covered: whether those files are current (docs-check's job), and anything a reference
// page does not say (a plugin's tools are counted, not described one by one).

import { readdir, readFile } from 'node:fs/promises';
import path from 'node:path';

const fail = (message) => {
	throw new Error(`site build: ${message}`);
};

/** A Markdown table row's cells, with `\|` (a pipe inside a cell) kept as a pipe. */
const cells = (line) =>
	line
		.trim()
		.replace(/^\||\|$/g, '')
		.split(/(?<!\\)\|/)
		.map((cell) => cell.trim().replace(/\\\|/g, '|'));

/** A cell without its code ticks. */
export const bare = (cell) => cell.replace(/`/g, '').trim();

/**
 * The body rows of the first table after the line `after` ('' for the first in the file), and
 * the paragraph between that line and the table (a section's own introduction).
 */
export function table(markdown, after, file) {
	const lines = markdown.split('\n');
	let at = after ? lines.findIndex((line) => line.trim() === after) : 0;
	if (at < 0) fail(`${file} has no "${after}": the site reads its table`);
	const intro = [];
	for (at += after ? 1 : 0; at < lines.length && !lines[at].startsWith('|'); at++) {
		if (after && /^#{2,3} /.test(lines[at])) fail(`${file} has no table under "${after}" before the next heading`);
		if (lines[at].trim() && !intro.length) intro.push(lines[at].trim());
		else if (lines[at].trim() && intro.length && lines[at - 1].trim()) intro.push(lines[at].trim());
	}
	const rows = [];
	for (; at < lines.length && lines[at].startsWith('|'); at++) rows.push(cells(lines[at]));
	if (rows.length < 3) fail(`${file}'s table under "${after || 'its title'}" has no rows`);
	return { rows: rows.slice(2), intro: intro.join(' ') };
}

/** The plugins this repository adds: each enabled one with tools, its one line and its tools. */
export function plugins(markdown) {
	const file = 'docs/reference/plugins.md';
	const { rows } = table(markdown, '', file);
	const found = rows
		.map(([name, toolset, enabled, tools, slash]) => ({
			name: bare(name),
			toolset: bare(toolset),
			enabled: enabled === 'yes',
			count: Number(tools),
			slash: slash === '—' ? '' : bare(slash)
		}))
		.filter((plugin) => plugin.enabled && plugin.count > 0);
	if (!found.length) fail(`${file} lists no enabled plugin with tools`);
	for (const plugin of found) {
		const section = markdown.split(`\n## \`${plugin.name}\`\n`)[1]?.split('\n## ')[0];
		if (!section) fail(`${file} has no section for ${plugin.name}`);
		plugin.about = section.split('\n').find((line) => line.trim() && !line.startsWith('- ')) ?? '';
		const tools = /\*\*Tools \(\d+\):\*\* (.+)/.exec(section)?.[1] ?? '';
		plugin.tools = tools.split(',').map(bare).filter(Boolean);
	}
	return found;
}

/** Hermes's own toolsets that are on here, by their display names (the leading emoji dropped). */
export function toolsets(markdown) {
	const { rows } = table(markdown, '## Built-in toolsets', 'docs/reference/tools.md');
	const on = rows
		.filter(([, , state]) => state.startsWith('enabled'))
		.map(([id, name]) => ({ id: bare(id), name: name.replace(/^[^\p{L}\p{N}]+/u, '').trim() }));
	if (!on.length) fail('docs/reference/tools.md lists no enabled built-in toolset');
	return on;
}

/** The approval matrix: its size, and each verdict's introduction and reasons. */
export function matrix(markdown) {
	const file = 'docs/reference/approval-matrix.md';
	const size = /^(\d+) rows over (\d+) tools\.$/m.exec(markdown);
	if (!size) fail(`${file} no longer says how many rows over how many tools`);
	const verdict = (heading, key) => {
		const { rows, intro } = table(markdown, heading, file);
		const reasons = [...new Set(rows.map((row) => row[3]).filter(Boolean))];
		return { key, heading: heading.replace(/^## /, ''), intro, rows: rows.length, reasons };
	};
	return {
		rows: Number(size[1]),
		tools: Number(size[2]),
		verdicts: [verdict('## Refused outright', 'deny'), verdict('## Needs approval', 'ask'), verdict('## Free', 'free')]
	};
}

/** How many skills it follows. */
export const skills = (markdown) => table(markdown, '', 'docs/reference/skills.md').rows.length;

/** The benchmark: tasks per category, from CAPABILITIES.md's first table. */
export function bench(markdown) {
	const { rows } = table(markdown, '', 'CAPABILITIES.md');
	const categories = rows.map(([name, tasks]) => ({ name: bare(name), tasks: Number(tasks) }));
	if (categories.some((c) => !Number.isFinite(c.tasks))) fail('CAPABILITIES.md: a category without a task count');
	return { categories, tasks: categories.reduce((sum, c) => sum + c.tasks, 0) };
}

/** The personas, from each theme pack: what the page says about each face. */
export function faces(packs) {
	return packs.map((pack) => ({
		id: pack.id,
		name: pack.persona?.name ?? fail(`themes/${pack.id}: no persona name`),
		about: pack.description,
		voice: pack.persona.voiceDescription,
		style: pack.persona.style,
		wake: pack.wake?.phrases ?? [],
		core: pack.core?.renderer ?? fail(`themes/${pack.id}: no core renderer`),
		ready: pack.persona.strings?.ready ?? ''
	}));
}

/** Everything above, read from the repository at `repo`. */
export async function records(repo) {
	const read = (...parts) => readFile(path.join(repo, ...parts), 'utf8');
	// Each folder under themes/ is a pack; the console lists them the same way (design/build.py).
	const ids = (await readdir(path.join(repo, 'themes'), { withFileTypes: true })).filter((d) => d.isDirectory()).map((d) => d.name).sort();
	const packs = await Promise.all(ids.map(async (id) => JSON.parse(await read('themes', id, 'theme.json'))));
	// The console's default first: the page opens wearing it.
	packs.sort((a, b) => (a.id === 'aura' ? -1 : b.id === 'aura' ? 1 : 0));
	return {
		plugins: plugins(await read('docs', 'reference', 'plugins.md')),
		toolsets: toolsets(await read('docs', 'reference', 'tools.md')),
		matrix: matrix(await read('docs', 'reference', 'approval-matrix.md')),
		skills: skills(await read('docs', 'reference', 'skills.md')),
		bench: bench(await read('CAPABILITIES.md')),
		faces: faces(packs)
	};
}
