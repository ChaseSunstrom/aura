// The Pages site (T-0866), built as the workflow builds it and served the way Pages serves it:
// from a folder under a sub-path, so an address that only works at a site's root fails here.
//
// What it holds: the build fills the page from the README and the clip table; nothing on the
// page comes from another origin; the console's own core is drawn live and takes a state; each
// clip's film and still are there and answer; a film plays in view and stops when told; the
// install line copies as the README has it; nothing overflows a phone; and the page still
// reads with reduced motion and with no script at all.
//
// SITE_SHOTS=<folder> also writes the first screen and the whole page at both sizes there
// (docs/screens/site holds the committed pair).
//
// Not covered: GitHub's own deploy (the workflow's two jobs run only there); how the page
// looks, which is judged by eye from those screenshots; and a film's frames actually moving.
// Playwright's Chromium is built without an H.264 decoder, so here a film is asked to play and
// asked to stop, and each file is asked for and answers, but nothing is decoded. That the
// films are ones a browser plays is held from the files (deploy/tests/test_site.py reads
// each one's codec, length and where its index sits).

import { expect, test, type Page } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { createReadStream, existsSync, mkdtempSync, readFileSync, rmSync, statSync } from 'node:fs';
import http from 'node:http';
import type { AddressInfo } from 'node:net';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const REPO = fileURLToPath(new URL('../../', import.meta.url));
const TABLE = JSON.parse(readFileSync(path.join(REPO, 'tests', 'contracts', 'readme-clips.json'), 'utf8')) as {
	clips: { name: string; heading: string; width: number; height: number }[];
};
const README = readFileSync(path.join(REPO, 'README.md'), 'utf8');
/** The install line as the README has it: what the page's first Copy button must hand over. */
const INSTALL = /^curl -fsSL \S+ \| sh$/m.exec(README)![0];
const SHOTS = process.env.SITE_SHOTS;
/** The sub-path Pages serves a project site under. */
const UNDER = '/aura/';
const SIZES = [
	{ name: 'desktop', width: 1440, height: 900 },
	{ name: 'phone', width: 390, height: 844 }
] as const;
const TYPES: Record<string, string> = {
	'.html': 'text/html; charset=utf-8',
	'.css': 'text/css',
	'.js': 'text/javascript',
	'.mp4': 'video/mp4',
	'.jpg': 'image/jpeg',
	'.png': 'image/png',
	'.svg': 'image/svg+xml',
	'.ico': 'image/x-icon',
	'.woff2': 'font/woff2',
	'.txt': 'text/plain'
};

let out = '';
let base = '';
let server: http.Server;

/** A plain file server with byte ranges (a browser asks a film for ranges), and nothing above UNDER. */
function serve(folder: string): http.Server {
	return http.createServer((req, res) => {
		const { pathname } = new URL(req.url ?? '/', 'http://site.test');
		if (!pathname.startsWith(UNDER)) return void res.writeHead(404).end();
		let file = path.join(folder, decodeURIComponent(pathname.slice(UNDER.length)));
		if (!path.extname(file)) file = path.join(file, 'index.html');
		if (!file.startsWith(folder) || !existsSync(file)) return void res.writeHead(404).end();
		const size = statSync(file).size;
		const type = TYPES[path.extname(file)] ?? 'application/octet-stream';
		const range = /bytes=(\d+)-(\d*)/.exec(req.headers.range ?? '');
		if (!range) {
			res.writeHead(200, { 'content-type': type, 'content-length': size, 'accept-ranges': 'bytes' });
			return void createReadStream(file).pipe(res);
		}
		const from = Number(range[1]);
		const to = range[2] ? Number(range[2]) : size - 1;
		res.writeHead(206, { 'content-type': type, 'content-range': `bytes ${from}-${to}/${size}`, 'content-length': to - from + 1 });
		createReadStream(file, { start: from, end: to }).pipe(res);
	});
}

test.beforeAll(async () => {
	out = mkdtempSync(path.join(os.tmpdir(), 'aura-site-'));
	execFileSync(process.execPath, [path.join(REPO, 'site', 'build.mjs'), '--out', out], { stdio: 'pipe' });
	server = serve(out);
	await new Promise<void>((done) => server.listen(0, '127.0.0.1', done));
	base = `http://127.0.0.1:${(server.address() as AddressInfo).port}${UNDER}`;
});

test.afterAll(async () => {
	await new Promise((done) => server.close(done));
	rmSync(out, { recursive: true, force: true });
});

/** Open the page and keep what went wrong: errors it logged, and requests that left its folder. */
async function open(page: Page) {
	const errors: string[] = [];
	const outside: string[] = [];
	page.on('console', (message) => {
		if (message.type() === 'error') errors.push(message.text());
	});
	page.on('pageerror', (error) => errors.push(String(error)));
	page.on('request', (request) => {
		if (!request.url().startsWith(base)) outside.push(new URL(request.url()).host);
	});
	await page.goto(base);
	return { errors, outside };
}

test('the build fills the page from the README and the table, and leaves no marker', () => {
	const html = readFileSync(path.join(out, 'index.html'), 'utf8');
	expect(html).not.toMatch(/@@/);
	// The line as text: the page gives it places to break (<wbr>), which hold none.
	expect(html.replaceAll('<wbr>', '')).toContain(INSTALL);
	for (const clip of TABLE.clips) {
		expect(html, clip.name).toContain(`media/${clip.name}.mp4`);
		expect(html, clip.name).toContain(`>${clip.heading}</h2>`);
		expect(existsSync(path.join(out, 'media', `${clip.name}.mp4`)), clip.name).toBe(true);
		expect(existsSync(path.join(out, 'posters', `${clip.name}.jpg`)), clip.name).toBe(true);
	}
	// What the page loads is its own: no script, style, font or picture from another origin.
	expect(html).not.toMatch(/<(script|link|img|source|video)\b[^>]*\b(src|href|poster)="https?:/);
	for (const file of ['site.css', 'themes.css', 'tokens.css']) {
		expect(readFileSync(path.join(out, file), 'utf8'), file).not.toMatch(/url\(\s*['"]?(https?:)?\/\//);
	}
	// The console's reactor needs three.js; this page wears the galaxy only and must not carry it.
	expect(statSync(path.join(out, 'site.js')).size).toBeLessThan(150_000);
});

test('the build stops, and says why, when the page would be wrong', async () => {
	// The page is filled by a pure function, so each refusal is asked for here without a build.
	const { page: fill } = (await import(pathToFileURL(path.join(REPO, 'site', 'build.mjs')).href)) as {
		page: (template: string, readme: string, table: typeof TABLE) => string;
	};
	const template = readFileSync(path.join(REPO, 'site', 'index.html'), 'utf8');
	expect(() => fill(template, README, TABLE)).not.toThrow();

	// A placeholder nothing fills would be printed on the page as it is.
	expect(() => fill(template.replace('@@features@@', '@@features@@ @@nothing@@'), README, TABLE)).toThrow(
		/placeholder this build does not fill: @@nothing@@/
	);
	// A paragraph the README no longer has: the page must not keep the old words.
	expect(() => fill(template, README.replace('AURA is a voice-first', 'AURA was a voice-first'), TABLE)).toThrow(
		/no paragraph under "its title" starting "AURA is a voice-first"/
	);
	// A clip whose section the README does not have.
	const lost = { clips: [...TABLE.clips, { ...TABLE.clips[0], name: 'extra', heading: 'No such section' }] };
	expect(() => fill(template, README, lost)).toThrow(/no section "No such section"/);
	// A clip named like something the template already names: two of one id.
	const clash = { clips: TABLE.clips.map((clip) => (clip.name === 'core' ? { ...clip, name: 'install' } : clip)) };
	expect(() => fill(template, README, clash)).toThrow(/the id "install" twice/);
});

for (const size of SIZES) {
	test.describe(`at ${size.name} width`, () => {
		test.use({ viewport: { width: size.width, height: size.height }, hasTouch: size.name === 'phone' });

		test('it loads from its own folder, fits the screen, and shows every film', async ({ page, request }) => {
			const { errors, outside } = await open(page);
			await expect(page.locator('h1')).toHaveText('AURA');
			await expect(page.locator('.lede')).toContainText('AURA is a voice-first assistant');
			await expect(page.locator('#stage-core[data-core="live"] canvas')).toBeVisible();

			const films = page.locator('.film video');
			await expect(films).toHaveCount(TABLE.clips.length);
			for (const clip of TABLE.clips) {
				const film = page.locator(`.film video:has(source[src="media/${clip.name}.mp4"])`);
				await expect(film, clip.name).toHaveAttribute('poster', `posters/${clip.name}.jpg`);
				await expect(film, clip.name).toHaveAttribute('aria-label', /\S{3}/);
				for (const file of [`media/${clip.name}.mp4`, `posters/${clip.name}.jpg`]) {
					expect((await request.head(base + file)).status(), file).toBe(200);
				}
			}

			expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(0);
			expect(errors).toEqual([]);
			expect(outside).toEqual([]);

			if (SHOTS) {
				await page.waitForTimeout(1500); // the core has settled and the fonts are in
				await page.screenshot({ path: path.join(SHOTS, `${size.name}.png`) });
				const tall = await page.evaluate(() => document.documentElement.scrollHeight);
				for (let y = 0; y < tall; y += size.height / 2) {
					await page.evaluate((to) => window.scrollTo(0, to), y);
					await page.waitForTimeout(200);
				}
				await page.evaluate(() => window.scrollTo(0, 0));
				await page.screenshot({ path: path.join(SHOTS, `${size.name}-whole.png`), fullPage: true });
			}
		});

		test('the core takes a state from its chips, and the one chosen stays', async ({ page }) => {
			await open(page);
			const chips = page.locator('#states .chip');
			await expect(chips).toHaveCount(9);
			await expect(page.locator('#states .chip[aria-pressed="true"]')).toHaveText('idle');
			await chips.filter({ hasText: 'thinking' }).click();
			await expect(page.locator('#stage-core')).toHaveAttribute('data-state', 'thinking');
			await expect(page.locator('#states .chip[aria-pressed="true"]')).toHaveText('thinking');
			await expect(page.locator('#said')).toHaveText('The core is thinking');
		});

		test('a film plays when it is in view, stops when told, and stays stopped', async ({ page }) => {
			await open(page);
			const film = page.locator('.film').first();
			const video = film.locator('video');
			await film.scrollIntoViewIfNeeded();
			// Started, not seen moving: this browser has no H.264 decoder (see the head of this file).
			await expect.poll(() => video.evaluate((v: HTMLVideoElement) => !v.paused)).toBe(true);
			await expect(video).not.toHaveAttribute('controls', '');

			await film.hover();
			await film.getByRole('button', { name: 'Pause' }).click();
			await expect.poll(() => video.evaluate((v: HTMLVideoElement) => v.paused)).toBe(true);
			// Out of view and back: a film stopped by hand does not start itself again.
			await page.evaluate(() => window.scrollTo(0, 0));
			await film.scrollIntoViewIfNeeded();
			await page.waitForTimeout(400);
			expect(await video.evaluate((v: HTMLVideoElement) => v.paused)).toBe(true);
			await expect(film.getByRole('button', { name: 'Play' })).toBeVisible();
		});
	});
}

test('the install line copies as the README has it', async ({ page, context }) => {
	await context.grantPermissions(['clipboard-read', 'clipboard-write']);
	await open(page);
	const copy = page.locator('button[data-copy="line-sh"]');
	await copy.click();
	await expect(copy).toHaveText('Copied');
	expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(INSTALL);
});

test.describe('with reduced motion', () => {
	test('no film starts itself, the films keep the browser controls, and nothing waits to arrive', async ({ page }) => {
		await page.emulateMedia({ reducedMotion: 'reduce' });
		const { errors } = await open(page);
		await expect(page.locator('#stage-core[data-core="live"] canvas')).toBeVisible();
		const film = page.locator('.film').nth(1);
		await film.scrollIntoViewIfNeeded();
		await page.waitForTimeout(500);
		const video = film.locator('video');
		expect(await video.evaluate((v: HTMLVideoElement) => v.paused)).toBe(true);
		await expect(video).toHaveAttribute('controls', '');
		await expect(film.locator('.toggle')).toBeHidden();
		expect(await page.evaluate(() => 'arrivals' in document.documentElement.dataset)).toBe(false);
		await expect(page.locator('.feature').last()).toHaveCSS('opacity', '1');
		expect(errors).toEqual([]);
	});
});

test.describe('with no script', () => {
	test.use({ javaScriptEnabled: false });

	test('the page still reads: the still core, the install line, every film with controls', async ({ page }) => {
		await page.goto(base);
		await expect(page.locator('#stage-core img')).toBeVisible();
		await expect(page.locator('#states')).toBeHidden();
		await expect(page.locator('#line-sh')).toHaveText(INSTALL);
		await expect(page.locator('.film video[controls]')).toHaveCount(TABLE.clips.length);
		await expect(page.locator('.feature').last()).toHaveCSS('opacity', '1');
	});
});
