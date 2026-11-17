import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { inflateSync } from 'node:zlib';
import { fileURLToPath } from 'node:url';
import { DEFAULT_THEME, THEMES } from './themes';

/**
 * The shipped tab icons, checked as a browser sees them.
 *
 * They are no longer this tree's to draw: `scripts/gen-icons.py` renders every
 * icon Jarvis has — favicon, apple-touch, the PWA rasters, the phone's adaptive
 * layers, the desktop set — from the v2 core's own geometry
 * (`tests/contracts/core_frames.json`, the numbers `engine.ts` draws), so the
 * mark in the tab strip is the core on the stage (T-0808; it was the Reactor II
 * instrument before). There were four hand-drawn reactors before that and none of them
 * agreed on how many rings there were; the web's own `scripts/icons.mjs` was
 * one of them, and it is gone.
 *
 * That leaves two different questions, and they are checked in two places.
 * Whether the committed files ARE the geometry's is
 * `python3 scripts/gen-icons.py --check` (`make icons && git diff --exit-code`),
 * which compares byte for byte and needs the contract. Whether they are icons a
 * browser can use — a real PNG, a lit core on a dark plate at 16 px, linked
 * from the document — is this file, and it needs no generator at all.
 */

const file = (rel: string) => readFileSync(fileURLToPath(new URL(rel, import.meta.url)));
const text = (rel: string) => file(rel).toString('utf8');

const appHtml = text('../app.html');
const svg = text('../../static/favicon.svg');

/** Every raster the tree ships, by the name a browser asks for it under. */
const RASTERS = {
	'apple-touch-icon.png': 180,
	'icon-192.png': 192,
	'icon-512.png': 512
} as const;

/**
 * The RGBA pixels of one of the generator's PNGs.
 *
 * A whole PNG decoder is not needed and would be its own liability: the
 * generator writes 8-bit RGBA, filter type 0 on every row, no interlace and no
 * ancillary chunks, and `readPng` asserts each of those rather than assuming
 * them — a file that stopped matching that shape would come back as garbage
 * pixels and quietly pass the brightness checks below.
 */
function readPng(buffer: Buffer): { width: number; height: number; rgba: Uint8Array } {
	expect([...buffer.subarray(0, 8)], 'PNG magic').toEqual([137, 80, 78, 71, 13, 10, 26, 10]);
	let at = 8;
	let width = 0;
	let height = 0;
	const idat: Buffer[] = [];
	while (at < buffer.length) {
		const length = buffer.readUInt32BE(at);
		const kind = buffer.subarray(at + 4, at + 8).toString('latin1');
		const data = buffer.subarray(at + 8, at + 8 + length);
		if (kind === 'IHDR') {
			width = data.readUInt32BE(0);
			height = data.readUInt32BE(4);
			expect(data[8], 'bit depth').toBe(8);
			expect(data[9], 'colour type (6 = RGBA)').toBe(6);
			expect(data[12], 'interlace').toBe(0);
		} else if (kind === 'IDAT') idat.push(Buffer.from(data));
		at += 12 + length;
	}
	const raw = inflateSync(Buffer.concat(idat));
	const stride = width * 4;
	const rgba = new Uint8Array(width * height * 4);
	for (let y = 0; y < height; y++) {
		const filter = raw[y * (stride + 1)];
		expect(filter, `row ${y} filter`).toBe(0);
		rgba.set(raw.subarray(y * (stride + 1) + 1, y * (stride + 1) + 1 + stride), y * stride);
	}
	return { width, height, rgba };
}

describe('the shipped rasters', () => {
	it('are the PNGs they claim to be, at the size they are named for', () => {
		for (const [name, size] of Object.entries(RASTERS)) {
			const png = readPng(file(`../../static/${name}`));
			expect(png.width, name).toBe(size);
			expect(png.height, name).toBe(size);
		}
	});

	it('shows a lit core on a dark plate at every size, down to the 16 px tab strip', () => {
		// The failure this guards against is an icon that is technically valid and
		// visually a dark square: every ring drawn sub-pixel, nothing left.
		const ico = file('../../static/favicon.ico');
		const sizes = readIco(ico);
		expect(sizes.map((s) => s.size).sort((a, b) => a - b)).toEqual([16, 32]);
		const all = [
			...sizes.map((s) => ({ what: `favicon ${s.size}`, png: readPng(s.png) })),
			...Object.keys(RASTERS).map((name) => ({ what: name, png: readPng(file(`../../static/${name}`)) }))
		];
		for (const { what, png } of all) {
			const { width, rgba } = png;
			const at = (x: number, y: number) => {
				const i = (Math.floor(y) * width + Math.floor(x)) * 4;
				return [rgba[i], rgba[i + 1], rgba[i + 2], rgba[i + 3]];
			};
			const centre = at(width / 2, width / 2);
			expect(centre[3], `${what}: the core is opaque`).toBeGreaterThan(200);
			expect(Math.max(centre[0], centre[1], centre[2]), `${what}: the core is lit`).toBeGreaterThan(
				150
			);
			// Blue dominates: this is a cyan reactor, not a grey disc.
			let blue = 0;
			let red = 0;
			for (let i = 0; i < rgba.length; i += 4) {
				red += rgba[i] * rgba[i + 3];
				blue += rgba[i + 2] * rgba[i + 3];
			}
			expect(blue, `${what}: blue-dominant`).toBeGreaterThan(red * 1.5);
		}
	});
});

/** The `.ico` is a container of whole PNGs; pull each one back out. */
function readIco(buffer: Buffer): { size: number; png: Buffer }[] {
	expect(buffer.readUInt16LE(0), 'ICO reserved word').toBe(0);
	expect(buffer.readUInt16LE(2), 'ICO type (1 = icon)').toBe(1);
	const count = buffer.readUInt16LE(4);
	const out: { size: number; png: Buffer }[] = [];
	for (let i = 0; i < count; i++) {
		const entry = 6 + i * 16;
		const size = buffer[entry] === 0 ? 256 : buffer[entry];
		const length = buffer.readUInt32LE(entry + 8);
		const offset = buffer.readUInt32LE(entry + 12);
		out.push({ size, png: buffer.subarray(offset, offset + length) });
	}
	return out;
}

describe('the vector favicon', () => {
	it('says it is generated from the core, so nobody hand-edits it back out of step', () => {
		expect(svg).toContain('@generated');
		expect(svg).toContain('scripts/gen-icons.py');
		expect(svg).toContain('tests/contracts/core_frames.json');
	});

	it("is the default pack's own icon, the same picture as the rasters", () => {
		// AURA's icon (T-0861) is a painting the generator cannot draw from geometry, so
		// the vector favicon carries her 64 px PNG; the drawn core's 30 segments are JARVIS's.
		expect(DEFAULT_THEME).toBe('aura');
		const data = svg.match(/href="data:image\/png;base64,([^"]+)"/);
		expect(data, 'an embedded PNG').not.toBeNull();
		const png = readPng(Buffer.from(data![1], 'base64'));
		expect([png.width, png.height]).toEqual([64, 64]);
		expect(svg).not.toContain('class="seg"');
	});

	it('is a square viewBox with a title, so a reader announces it', () => {
		expect(svg).toMatch(/viewBox="0 0 (\d+) \1"/);
		expect(svg).toContain(`<title>${THEMES[DEFAULT_THEME].name}</title>`);
		expect(svg).toContain('role="img"');
	});
});

describe('app.html', () => {
	it('links every generated icon', () => {
		expect(appHtml).toContain(
			'<link rel="icon" href="%sveltekit.assets%/favicon.ico" sizes="32x32" />'
		);
		expect(appHtml).toContain(
			'<link rel="icon" href="%sveltekit.assets%/favicon.svg" type="image/svg+xml" />'
		);
		expect(appHtml).toContain(
			'<link rel="apple-touch-icon" href="%sveltekit.assets%/apple-touch-icon.png" />'
		);

		// The blank placeholder that used to be here suppressed the request
		// entirely; leaving it in alongside the real ones would win by being first.
		expect(appHtml).not.toContain('href="data:,"');
	});

	it('offers the vector after the raster, so browsers that can take it do', () => {
		expect(appHtml.indexOf('favicon.ico')).toBeLessThan(appHtml.indexOf('favicon.svg'));
	});
});
