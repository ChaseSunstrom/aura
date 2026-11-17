// Builds the Pages site (T-0866): `node site/build.mjs [--out DIR]`, default `_site`.
//
// The site says nothing of its own. Its words are the README's: the opening paragraph, each
// feature's paragraph and picture caption, the install lines, what the install needs and what
// the films are. Its clips are the rows of tests/contracts/readme-clips.json, its colours and
// type the console's generated token files, its fonts the console's, and the core on its first
// screen the console's own galaxy renderer, bundled from jarvis-web/src/lib/ds/core. So a
// reworded README, a new clip or a changed token changes the site at its next build, and a
// paragraph this script cannot find stops the build instead of leaving a hole.
//
// No address is typed here or in the template: the install lines and the project's links are
// lifted from the README, and the repository's own address is derived from the install line.
//
// Needs node and jarvis-web's node_modules (for esbuild, which the console's build already
// uses). Not covered: making the films or their posters (`make readme-clips`), and anything
// about serving: the output is a plain folder.

import { cp, mkdir, readFile, rm, writeFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO = path.resolve(HERE, '..');
const WEB = path.join(REPO, 'jarvis-web');
const STYLES = path.join(WEB, 'src', 'lib', 'styles');
const FONTS = path.join(WEB, 'static', 'fonts');

/** The theme this page wears, and so the faces it ships (the other theme's stay behind). */
const THEME = 'aura';

const fail = (message) => {
	throw new Error(`site build: ${message}`);
};

const escape = (text) =>
	text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');

// ---- the README, as sections of blocks ---------------------------------------------------

/**
 * `{ heading: [block] }`, the text before the first `## ` under ''. A block is a fenced code
 * block (`{ code, lang }`) or a run of lines up to a blank one (`{ text }`, joined with spaces).
 */
export function readmeSections(markdown) {
	const sections = new Map([['', []]]);
	let blocks = sections.get('');
	let run = [];
	let fence = null;
	const close = () => {
		if (run.length) blocks.push({ text: run.join(' ') });
		run = [];
	};
	for (const line of markdown.split('\n')) {
		if (fence) {
			if (line.startsWith('```')) {
				blocks.push({ code: fence.lines.join('\n'), lang: fence.lang });
				fence = null;
			} else fence.lines.push(line);
			continue;
		}
		const heading = /^## (.+)$/.exec(line);
		if (heading) {
			close();
			blocks = [];
			sections.set(heading[1].trim(), blocks);
		} else if (line.startsWith('```')) {
			close();
			fence = { lang: line.slice(3).trim(), lines: [] };
		} else if (line.trim() === '') close();
		else run.push(line.trim());
	}
	close();
	return sections;
}

const PICTURE = /^\[!\[([^\]]+)\]\(([^)]+)\)\]\(([^)]+)\)$/;

/** The paragraph under `heading` that starts with `starts`; the build stops if it is gone. */
export function paragraph(sections, heading, starts) {
	const found = (sections.get(heading) ?? []).find((block) => block.text?.startsWith(starts));
	if (!found) fail(`the README has no paragraph under "${heading || 'its title'}" starting "${starts}"`);
	return found.text;
}

/** A feature's picture (its alt text and film link) and the first paragraph of prose after it. */
export function feature(sections, heading) {
	const blocks = sections.get(heading) ?? fail(`the README has no section "${heading}"`);
	const at = blocks.findIndex((block) => PICTURE.test(block.text ?? ''));
	if (at < 0) fail(`the README's "${heading}" shows no picture`);
	const [, alt, , film] = PICTURE.exec(blocks[at].text);
	// A line that only introduces a code block ("Linux and macOS:") is not the feature's prose.
	const prose = blocks.slice(at + 1).find((block) => block.text && !block.text.endsWith(':'));
	if (!prose) fail(`the README's "${heading}" has no paragraph after its picture`);
	return { alt, film, text: prose.text };
}

/** The project's addresses, from the lines the README already carries. */
export function addresses(sections) {
	const all = [...sections.values()].flat();
	const sh = all.find((block) => block.lang === 'sh' && block.code.startsWith('curl '))?.code;
	const ps = all.find((block) => block.lang === 'powershell')?.code;
	if (!sh || !ps) fail('the README has lost an install line');
	const raw = /https:\/\/raw\.githubusercontent\.com\/([^/\s]+)\/([^/\s]+)\/([^/\s]+)\/install\.sh/.exec(sh);
	if (!raw) fail('the install line is not a raw file of the repository: the source links cannot be derived');
	const [, account, name, branch] = raw;
	return { sh, ps, repo: `https://github.com/${account}/${name}`, branch };
}

/**
 * The README's inline Markdown as HTML: code spans and links, which is all its prose uses.
 * A link into the repository opens the file on the forge; `#install` stays on this page.
 */
export function inline(text, { repo, branch }) {
	const span = (plain) =>
		plain
			.split(/(`[^`]+`)/)
			.map((part) => (part.startsWith('`') && part.length > 2 ? `<code>${escape(part.slice(1, -1))}</code>` : escape(part)))
			.join('');
	const link = /\[((?:`[^`]*`|[^\]`])+)\]\(([^)\s]+)\)/g;
	let out = '';
	let from = 0;
	for (const match of text.matchAll(link)) {
		const [whole, label, target] = match;
		out += span(text.slice(from, match.index));
		let href = target;
		let outside = /^https?:\/\//.test(target);
		if (!outside && !target.startsWith('#')) {
			href = `${repo}/${target.endsWith('/') ? 'tree' : 'blob'}/${branch}/${target.replace(/\/$/, '')}`;
			outside = true;
		}
		out += `<a href="${escape(href)}"${outside ? ' rel="noopener"' : ''}>${span(label)}</a>`;
		from = match.index + whole.length;
	}
	out += span(text.slice(from));
	if (/\*\*|!\[/.test(out)) fail(`a README paragraph uses Markdown this build does not render: "${text.slice(0, 60)}"`);
	return out;
}

// ---- the page ---------------------------------------------------------------------------

function filmHtml(clip, alt) {
	const shape = clip.height > clip.width ? 'tall' : 'wide';
	return [
		`<figure class="film" data-shape="${shape}">`,
		// `controls` is for a browser without script; the page's script takes it off where it plays the films itself.
		`\t<video muted loop playsinline controls preload="none" width="${clip.width}" height="${clip.height}" poster="posters/${clip.name}.jpg" aria-label="${escape(alt)}">`,
		`\t\t<source src="media/${clip.name}.mp4" type="video/mp4">`,
		'\t</video>',
		'</figure>'
	].join('\n');
}

function featureHtml(clip, index, sections, where) {
	const { alt, text } = feature(sections, clip.heading);
	return [
		`<section class="feature" id="${clip.name}" aria-labelledby="h-${clip.name}">`,
		'\t<div class="copy">',
		`\t\t<p class="label"><span class="num">${String(index + 1).padStart(2, '0')}</span> ${escape(clip.name)}</p>`,
		`\t\t<h2 id="h-${clip.name}">${escape(clip.heading)}</h2>`,
		`\t\t<p>${inline(text, where)}</p>`,
		'\t</div>',
		filmHtml(clip, alt).replace(/^/gm, '\t'),
		'</section>'
	].join('\n');
}

/**
 * An install line as the page shows it: the same text, with a place to break after each slash.
 * A browser breaks an address that does not fit wherever it runs out of room, mid-word; with
 * these it breaks between the address's parts. `<wbr>` holds no text, so the line still copies
 * and reads as the README has it.
 */
const line = (text) => escape(text).replace(/\/(?=[^/ ])/g, '/<wbr>');

/** The template with every `@@…@@` filled; one left over stops the build. */
export function page(template, markdown, contract) {
	const sections = readmeSections(markdown);
	const where = addresses(sections);
	const placed = new Set([...template.matchAll(/@@film:([a-z-]+)@@/g)].map((m) => m[1]));
	const clip = (name) => contract.clips.find((c) => c.name === name) ?? fail(`the template places a film the table does not name: ${name}`);
	const pages = /^(https:\/\/[^/\s]+\/[^/\s]+)\/media\//.exec(feature(sections, contract.clips[0].heading).film)?.[1];
	if (!pages) fail("the README's first film link is not under the site's media folder");
	const html = template
		.replace(/@@features@@/g, () =>
			contract.clips
				.map((c, index) => (placed.has(c.name) ? '' : featureHtml(c, index, sections, where)))
				.filter(Boolean)
				.join('\n\n')
		)
		.replace(/@@film:([a-z-]+)@@/g, (_, name) => filmHtml(clip(name), feature(sections, clip(name).heading).alt))
		.replace(/@@number:([a-z-]+)@@/g, (_, name) => String(contract.clips.indexOf(clip(name)) + 1).padStart(2, '0'))
		.replace(/@@para:([^:@]*):([^@]+)@@/g, (_, heading, starts) => inline(paragraph(sections, heading, starts), where))
		.replace(/@@plain:([^:@]*):([^@]+)@@/g, (_, heading, starts) =>
			// For an attribute: the paragraph's first sentence, without its Markdown.
			escape(paragraph(sections, heading, starts).replace(/`|\[|\]\([^)]*\)/g, '').split(/(?<=[.:]) /)[0].replace(/:$/, '.'))
		)
		.replace(/@@install-sh@@/g, line(where.sh))
		.replace(/@@install-ps@@/g, line(where.ps))
		.replace(/@@repo@@/g, escape(where.repo))
		.replace(/@@pages@@/g, escape(pages));
	const left = /@@[^@\n]+@@/.exec(html);
	if (left) fail(`the template has a placeholder this build does not fill: ${left[0]}`);
	// A feature's section takes its clip's name as its id, so a clip named like something the
	// template already names would give the page two of one id: the script and a link to it
	// would then find whichever comes first.
	const ids = [...html.matchAll(/\sid="([^"]+)"/g)].map((m) => m[1]);
	const twice = ids.find((id, at) => ids.indexOf(id) !== at);
	if (twice) fail(`the page would carry the id "${twice}" twice: rename the clip or the template's element`);
	return html;
}

/**
 * themes.css for this page: its font faces at addresses that hold under a sub-path (the console
 * serves them from its root), and only the faces the page's theme wears.
 */
export function themesCss(themes) {
	const rule = new RegExp(`\\[data-theme=['"]${THEME}['"]\\][^{]*\\{([^}]*)\\}`).exec(themes)?.[1] ?? '';
	const worn = new Set([...rule.matchAll(/--jv-font-[a-z]+:\s*'?([A-Za-z ]+?)'?\s*[,;]/g)].map((m) => m[1]));
	if (!worn.size) fail(`themes.css names no font for the ${THEME} theme`);
	const files = [];
	const lines = themes.split('\n').filter((line) => {
		if (!line.startsWith('@font-face')) return true;
		if (!worn.has(/font-family:\s*'([^']+)'/.exec(line)?.[1])) return false;
		files.push(/url\('\/fonts\/([^']+)'\)/.exec(line)?.[1] ?? fail(`a font face without a file: ${line}`));
		return true;
	});
	if (!files.length) fail(`themes.css declares no face for ${[...worn].join(', ')}`);
	return { css: lines.join('\n').replace(/url\('\/fonts\//g, "url('fonts/"), files, families: [...worn] };
}

async function bundle(out) {
	const esbuild = createRequire(path.join(WEB, 'package.json'))('esbuild');
	await esbuild.build({
		entryPoints: [path.join(HERE, 'src', 'site.ts')],
		bundle: true,
		format: 'esm',
		target: 'es2022',
		minify: true,
		legalComments: 'none',
		outfile: path.join(out, 'site.js'),
		alias: { $lib: path.join(WEB, 'src', 'lib') },
		logLevel: 'warning',
		plugins: [
			{
				// The reactor's GPU drawer is the only thing that needs three.js, and this page
				// never asks for the reactor: bundled, it would be most of the download.
				name: 'no-reactor',
				setup(build) {
					build.onResolve({ filter: /^\.\/webgl$/ }, (args) =>
						args.importer.endsWith(path.join('core', 'renderer.ts')) ? { path: 'reactor-gl', namespace: 'absent' } : null
					);
					build.onLoad({ filter: /.*/, namespace: 'absent' }, () => ({ contents: 'export const createWebgl = () => null;', loader: 'js' }));
				}
			}
		]
	});
}

export async function build(out) {
	const read = (...parts) => readFile(path.join(...parts), 'utf8');
	const contract = JSON.parse(await read(REPO, 'tests', 'contracts', 'readme-clips.json'));
	const html = page(await read(HERE, 'index.html'), await read(REPO, 'README.md'), contract);
	const themes = themesCss(await read(STYLES, 'themes.css'));

	await rm(out, { recursive: true, force: true });
	await mkdir(path.join(out, 'fonts'), { recursive: true });
	await writeFile(path.join(out, 'index.html'), html);
	await writeFile(path.join(out, 'themes.css'), themes.css);
	await cp(path.join(STYLES, 'tokens.css'), path.join(out, 'tokens.css'));
	await cp(path.join(HERE, 'site.css'), path.join(out, 'site.css'));
	for (const file of themes.files) await cp(path.join(FONTS, file), path.join(out, 'fonts', file));
	// Each face ships beside its licence, as the licence asks.
	for (const family of themes.families) await cp(path.join(FONTS, `OFL-${family.replace(/ /g, '')}.txt`), path.join(out, 'fonts', `OFL-${family.replace(/ /g, '')}.txt`));
	for (const name of ['favicon.svg', 'favicon.ico', 'apple-touch-icon.png']) await cp(path.join(WEB, 'static', name), path.join(out, name));
	for (const clip of contract.clips) {
		await cp(path.join(HERE, 'media', `${clip.name}.mp4`), path.join(out, 'media', `${clip.name}.mp4`));
		await cp(path.join(HERE, 'posters', `${clip.name}.jpg`), path.join(out, 'posters', `${clip.name}.jpg`));
	}
	await bundle(out);
	return out;
}

if (import.meta.url === pathToFileURL(process.argv[1] ?? '').href) {
	const at = process.argv.indexOf('--out');
	const out = path.resolve(at > 0 ? process.argv[at + 1] : path.join(REPO, '_site'));
	try {
		await build(out);
		console.log(`site: built into ${out}`);
	} catch (error) {
		console.error(String(error.message ?? error));
		process.exit(1);
	}
}
