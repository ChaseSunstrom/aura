import { test, expect, type Page } from '@playwright/test';
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';

import { glRenderer, spans, stats, withCpu, type TraceEvent } from './frametime';

/**
 * The v2 core (`src/lib/ds/core/`) on `/gallery/core`, in a real browser.
 *
 * - each of the nine states draws differently from idle, on the GPU and in the
 *   Canvas2D fallback (a frozen frame each, so the comparison is exact);
 * - a change of state fires the shock ring;
 * - a hidden page stops the animation loop, and a visible one starts it again;
 * - with no WebGL at all the fallback still draws the core;
 * - the live page holds the frame budget the HUD's frame-time gate holds
 *   (`scripts/verify/hud-frametime-budget.json`, same method: `frametime.ts`).
 *
 * `CORE_SCREENS=1` also writes the screenshots in `docs/screens/v2/core/`
 * (each state, frozen, at 1440×900 and 390×844), which are committed rather than
 * rewritten on every run.
 *
 * Does not cover: how the states *look* against the Core2 board; that is the
 * screenshots, read by a person.
 */

const ROOT = new URL('../../', import.meta.url).pathname;
const STATES = ['idle', 'listening', 'speaking', 'thinking', 'embedding', 'acting', 'music', 'alert', 'working'] as const;
const SETTLED = 2500;
const BUDGET = JSON.parse(readFileSync(`${ROOT}scripts/verify/hud-frametime-budget.json`, 'utf8')) as {
	profiles: Record<string, { match: string; p95_ms: number; max_ms: number }>;
};

const core = (page: Page) => page.getByTestId('core');

/** The gallery at one frozen frame, the core drawn by `renderer`. */
async function frozen(page: Page, state: string, at: number, renderer: 'auto' | 'canvas2d', from = 'idle') {
	await page.goto(`/gallery/core?state=${state}&from=${from}&t=${at}&renderer=${renderer}&chrome=0`);
	await expect(core(page)).toHaveAttribute('data-renderer', /webgl|canvas2d/);
	// Two frames: the first draw is composited before it is photographed.
	await page.evaluate(() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r))));
	return (await core(page).screenshot()).toString('base64');
}

type Measure = { kind: 'diff'; other: string } | { kind: 'ring'; from: number; to: number } | { kind: 'lit' };

/**
 * One number about a PNG of the core, worked out in the page (the browser has
 * the decoder): the percent of pixels that differ from `other`; the mean
 * brightness of the annulus `from`..`to` (fractions of the half-width) about
 * the centre; or the percent of pixels lit at all.
 */
function measure(page: Page, png: string, how: Measure): Promise<number> {
	return page.evaluate(
		async ({ png, how }) => {
			const pixels = async (b64: string) => {
				// An <img>, not fetch(): the console's CSP has no data: in connect-src.
				const img = new Image();
				img.src = `data:image/png;base64,${b64}`;
				await img.decode();
				const g = new OffscreenCanvas(img.width, img.height).getContext('2d')!;
				g.drawImage(img, 0, 0);
				return g.getImageData(0, 0, img.width, img.height);
			};
			const a = await pixels(png);
			const luma = (d: Uint8ClampedArray, i: number) => 0.2126 * d[i] + 0.7152 * d[i + 1] + 0.0722 * d[i + 2];
			if (how.kind === 'diff') {
				const b = await pixels(how.other);
				let changed = 0;
				for (let i = 0; i < a.data.length; i += 4) {
					// A GPU frame is not bit-identical between loads; 12 levels is noise.
					const d = Math.abs(a.data[i] - b.data[i]) + Math.abs(a.data[i + 1] - b.data[i + 1]) + Math.abs(a.data[i + 2] - b.data[i + 2]);
					if (d > 12) changed++;
				}
				return (100 * changed) / (a.width * a.height);
			}
			const half = a.width / 2;
			let sum = 0;
			let n = 0;
			for (let y = 0; y < a.height; y++)
				for (let x = 0; x < a.width; x++) {
					const i = (y * a.width + x) * 4;
					const r = Math.hypot(x - half, y - a.height / 2) / half;
					if (how.kind === 'lit') {
						n++;
						if (luma(a.data, i) > 40) sum++;
					} else if (r >= how.from && r < how.to) {
						n++;
						sum += luma(a.data, i);
					}
				}
			return how.kind === 'lit' ? (100 * sum) / n : sum / Math.max(1, n);
		},
		{ png, how }
	);
}

for (const renderer of ['auto', 'canvas2d'] as const) {
	test.describe(`the core, ${renderer === 'auto' ? 'on the GPU' : 'in Canvas2D'}`, () => {
		test('a frozen frame is the same frame every time', async ({ page }) => {
			const one = await frozen(page, 'idle', SETTLED, renderer);
			const two = await frozen(page, 'idle', SETTLED, renderer);
			expect(await measure(page, one, { kind: 'diff', other: two })).toBeLessThan(0.5);
		});

		test('each state draws differently from idle', async ({ page }) => {
			const idle = await frozen(page, 'idle', SETTLED, renderer);
			const seen: Record<string, number> = {};
			for (const state of STATES.slice(1)) {
				const png = await frozen(page, state, SETTLED, renderer);
				await expect(core(page)).toHaveAttribute('data-state', state);
				seen[state] = +(await measure(page, png, { kind: 'diff', other: idle })).toFixed(2);
			}
			console.log(`ds-core [${renderer}] percent of the core unlike idle: ${JSON.stringify(seen)}`);
			for (const state of STATES.slice(1)) expect(seen[state], `${state} against idle`).toBeGreaterThan(3);
		});

		test('a change of state fires the shock ring outward', async ({ page }) => {
			// The ring is at 0.2 + 1.1·f core radii, f = t / 900 ms: brightest in
			// the inner annulus early and in the outer one later. Everything else
			// in the frame (the tween to alert) moves in the same direction in
			// both annuli, so the ring is what makes them swap.
			const early = await frozen(page, 'alert', 300, renderer);
			const later = await frozen(page, 'alert', 600, renderer);
			const S = 0.36 * 2; // the shell's radius in half-widths (engine SPREAD)
			const inner = { kind: 'ring', from: 0.53 * S, to: 0.6 * S } as const;
			const outer = { kind: 'ring', from: 0.86 * S, to: 0.94 * S } as const;
			const e = [await measure(page, early, inner), await measure(page, early, outer)];
			const l = [await measure(page, later, inner), await measure(page, later, outer)];
			console.log(`ds-core [${renderer}] shock: inner ${e[0].toFixed(1)}→${l[0].toFixed(1)}, outer ${e[1].toFixed(1)}→${l[1].toFixed(1)}`);
			expect(e[0] - e[1], 'early: the ring is in the inner annulus').toBeGreaterThan(l[0] - l[1]);
		});
	});
}

test('a live change counts a shock, and the same state again does not', async ({ page }) => {
	await page.goto('/gallery/core');
	await expect(core(page)).toHaveAttribute('data-renderer', /webgl|canvas2d/);
	await expect(core(page)).toHaveAttribute('data-shocks', '0');
	await page.getByTestId('state-alert').click();
	await expect(core(page)).toHaveAttribute('data-state', 'alert');
	await expect(core(page)).toHaveAttribute('data-shocks', '1');
	await page.getByTestId('state-alert').click();
	await page.getByTestId('state-thinking').click();
	await expect(core(page)).toHaveAttribute('data-shocks', '2');
});

test('a hidden page stops the loop, and showing it starts it again', async ({ page }) => {
	await page.addInitScript(() => {
		const w = window as unknown as { __frames: number };
		w.__frames = 0;
		const raf = window.requestAnimationFrame.bind(window);
		window.requestAnimationFrame = (cb) => {
			w.__frames++;
			return raf(cb);
		};
	});
	await page.goto('/gallery/core?state=thinking');
	await expect(core(page)).toHaveAttribute('data-renderer', /webgl|canvas2d/);
	const frames = () => page.evaluate(() => (window as unknown as { __frames: number }).__frames);
	const hide = (hidden: boolean) =>
		page.evaluate((h) => {
			Object.defineProperty(document, 'hidden', { configurable: true, get: () => h });
			document.dispatchEvent(new Event('visibilitychange'));
		}, hidden);

	// "It runs" is growth within a generous window, not a frame rate: the shared CI runner drew
	// five frames in 300 ms (pipelines 713 and 715) and the old fixed wait failed a working loop.
	// The hidden check below stays exact; that is what this test is for.
	const runs = (from: number, what: string) =>
		expect.poll(frames, { message: what, timeout: 10_000 }).toBeGreaterThan(from + 5);
	await runs(await frames(), 'the loop runs while visible');

	await hide(true);
	await page.waitForTimeout(100);
	const stopped = await frames();
	await page.waitForTimeout(600);
	expect(await frames(), 'no frame is asked for while hidden').toBe(stopped);

	await hide(false);
	await runs(stopped, 'the loop resumes when shown');
});

test('with no WebGL the Canvas2D fallback draws the core', async ({ page }) => {
	await page.addInitScript(() => {
		const get = HTMLCanvasElement.prototype.getContext;
		HTMLCanvasElement.prototype.getContext = function (this: HTMLCanvasElement, kind: string, ...rest: unknown[]) {
			return /webgl/i.test(kind) ? null : (get as (...a: unknown[]) => unknown).call(this, kind, ...rest);
		} as typeof get;
	});
	await page.goto(`/gallery/core?state=speaking&t=${SETTLED}`);
	await expect(core(page)).toHaveAttribute('data-renderer', 'canvas2d');
	const png = (await core(page).screenshot()).toString('base64');
	expect(await measure(page, png, { kind: 'lit' }), 'percent of the core lit').toBeGreaterThan(2);
});

test('the live core holds the frame budget through every state', async ({ page, context }) => {
	await page.goto('/gallery/core');
	await expect(core(page)).toHaveAttribute('data-renderer', /webgl|canvas2d/);
	const renderer = await glRenderer(page);
	const profile = Object.keys(BUDGET.profiles).find((k) => BUDGET.profiles[k].match && renderer.includes(BUDGET.profiles[k].match)) ?? 'gpu';
	const budget = BUDGET.profiles[profile];
	// Shader compile and the first layout are warm-up, not the core's cost.
	await page.waitForTimeout(1000);

	const cdp = await context.newCDPSession(page);
	const events: TraceEvent[] = [];
	cdp.on('Tracing.dataCollected', (d: { value: TraceEvent[] }) => events.push(...d.value));
	await cdp.send('Tracing.start', {
		categories: 'disabled-by-default-devtools.timeline,disabled-by-default-devtools.timeline.frame,devtools.timeline',
		transferMode: 'ReportEvents'
	});
	for (const state of [...STATES.slice(1), 'idle']) {
		await page.getByTestId(`state-${state}`).click();
		await page.waitForTimeout(400);
	}
	await new Promise<void>((resolve) => {
		cdp.once('Tracing.tracingComplete', () => resolve());
		void cdp.send('Tracing.end');
	});
	const frames = withCpu(events, spans(events, 'AnimationFrame'));
	const cpu = stats(frames.map((f) => f.cpu));
	const kind = await core(page).getAttribute('data-renderer');
	console.log(`ds-core frametime [${profile}, ${kind}]: frames=${cpu.n} cpu p50=${cpu.p50} p95=${cpu.p95} max=${cpu.max} ms (wall p95=${stats(frames.map((f) => f.ms)).p95})`);
	expect(cpu.n, 'frames traced').toBeGreaterThan(60);
	expect(cpu.p95, `p95 frame CPU on the ${profile} profile`).toBeLessThanOrEqual(budget.p95_ms);
	expect(cpu.max, `longest frame CPU on the ${profile} profile`).toBeLessThanOrEqual(budget.max_ms);
});

// A change to the core's look is judged by a person from a pair of pictures (T-0748: the rings).
// `CORE_PAIR=rings-before` (or `-after`) writes the JARVIS theme's reactor, idle, at three widths under that name.
test('a before or after picture of the core at 390, 1024 and 1440', async ({ page }) => {
	test.skip(!process.env.CORE_PAIR, 'CORE_PAIR=<name> writes docs/screens/v2/core/<name>-<width>.png');
	const out = `${ROOT}docs/screens/v2/core`;
	mkdirSync(out, { recursive: true });
	for (const [width, height] of [[390, 844], [1024, 768], [1440, 900]] as const) {
		await page.setViewportSize({ width, height });
		await page.goto(`/gallery/core?state=idle&t=${SETTLED}&chrome=0&theme=jarvis`);
		await expect(core(page)).toHaveAttribute('data-renderer', /webgl|canvas2d/);
		await page.waitForTimeout(1200);
		writeFileSync(`${out}/${process.env.CORE_PAIR}-${width}.png`, await page.screenshot());
	}
});

test.describe('the screenshots', () => {
	test.skip(!process.env.CORE_SCREENS, 'CORE_SCREENS=1 rewrites docs/screens/v2/core/');
	for (const [width, height] of [
		[1440, 900],
		[390, 844]
	] as const) {
		test(`every state at ${width}×${height}`, async ({ page }) => {
			const out = `${ROOT}docs/screens/v2/core`;
			mkdirSync(out, { recursive: true });
			await page.setViewportSize({ width, height });
			// Every state on the GPU, and idle on the fallback to judge the port by.
			const shots = [...STATES.map((s) => [s, '', s] as const), ['idle', '&renderer=canvas2d', 'idle-canvas2d'] as const];
			for (const [state, extra, name] of shots) {
				await page.goto(`/gallery/core?state=${state}&t=${SETTLED}&chrome=0${extra}`);
				await expect(core(page)).toHaveAttribute('data-renderer', /webgl|canvas2d/);
				// The projections and lines finish coming out of the core.
				await page.waitForTimeout(1200);
				writeFileSync(`${out}/${name}-${width}.png`, await page.screenshot());
			}
		});
	}
});
