import { test, expect, type Page, type Request } from '@playwright/test';
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';

/**
 * Generative UI (`src/lib/ds/gen/`) on `/gallery/projections`, in a real browser,
 * fed the way the model feeds it: `hud.projection` frames on the HUD socket,
 * sent by the mock adapter (`POST /api/test/projection`, the frame `hud_project` emits).
 *
 * - a projection is painted within 300 ms (p95 over 20, every type), with the
 *   appear motion, and without it under reduced motion;
 * - each malformed payload in the contract draws the error state, in the
 *   validator's words;
 * - the sandbox: model-written HTML cannot fetch, load an image from the
 *   network, read the console's document or its cookies, is capped in size,
 *   and its frame is gone the moment it goes back into the core and after the
 *   time cap.
 *
 * `PROJECTION_SCREENS=1` also writes `docs/screens/v2/projections/` (the whole
 * gallery at 1440 and 390 wide), committed rather than rewritten on every run.
 *
 * Does not cover: how a type *looks*; that is the screenshots, read by a person.
 * The latency clock starts in the page before the mock is asked to send, so it
 * includes the request, the relay and the socket; it is stricter than "socket
 * frame to paint", never looser.
 */

const ROOT = new URL('../../', import.meta.url).pathname;
const C = JSON.parse(readFileSync(`${ROOT}tests/contracts/projections.json`, 'utf8')) as {
	limits: { custom_html_seconds: number };
	types: Record<string, unknown>;
	examples: Record<string, unknown>;
	malformed: { type: string; data: unknown; error: string }[];
};
const TYPES = Object.keys(C.types);

interface Frame {
	id: string;
	type: string;
	data: unknown;
}

async function live(page: Page) {
	await page.goto('/gallery/projections?live=1');
	await expect(page.getByTestId('projections-gallery')).toHaveAttribute('data-link', 'connected');
}

/**
 * Sends one frame through the mock and resolves with the milliseconds from just
 * before the request to the animation frame after the projection mounted (the
 * gallery stamps `window.__projected[id]` then).
 */
function project(page: Page, frame: Frame): Promise<number> {
	return page.evaluate(async (frame) => {
		const seen = (window as unknown as { __projected: Record<string, number> }).__projected;
		const t0 = performance.now();
		const r = await fetch('/hermes/api/test/projection', {
			method: 'POST',
			headers: { 'content-type': 'application/json' },
			body: JSON.stringify(frame)
		});
		if (!r.ok) throw new Error(`the mock answered ${r.status}`);
		return new Promise<number>((resolve, reject) => {
			const poll = () => {
				const at = seen[frame.id];
				if (at !== undefined && at >= t0) return resolve(at - t0);
				if (performance.now() - t0 > 5000) return reject(new Error(`${frame.id} never painted`));
				requestAnimationFrame(poll);
			};
			poll();
		});
	}, frame);
}

/** The CSS animations running on a projection's body, by keyframe name (Svelte prefixes it). */
const bodyAnimations = (page: Page, id: string) =>
	page
		.getByTestId(`projected-${id}`)
		.locator('.body')
		.evaluate((el) =>
			el.getAnimations().map((a) => ({
				name: (a as CSSAnimation).animationName ?? '',
				ms: Number(a.effect?.getComputedTiming().duration ?? 0)
			}))
		);

test('a projection is painted within 300 ms (p95 over 20) with the appear motion', async ({ page }) => {
	await live(page);
	const times: number[] = [];
	for (let i = 0; i < 20; i++) {
		const type = TYPES[i % TYPES.length];
		times.push(await project(page, { id: `lat-${i}`, type, data: C.examples[type] }));
		if (i === 0) {
			const motion = await bodyAnimations(page, 'lat-0');
			const appear = motion.find((a) => a.name.endsWith('project-in'));
			expect(appear, `the appear motion runs (${JSON.stringify(motion)})`).toBeTruthy();
			expect(appear!.ms).toBeGreaterThanOrEqual(400);
			expect(appear!.ms).toBeLessThanOrEqual(600);
		}
	}
	const sorted = [...times].sort((a, b) => a - b);
	const p95 = sorted[Math.ceil(0.95 * sorted.length) - 1];
	console.log(`projection paint: n=${times.length} p50=${sorted[9].toFixed(1)} p95=${p95.toFixed(1)} max=${sorted[19].toFixed(1)} ms`);
	expect(p95).toBeLessThanOrEqual(300);
	// Every one drew its type, not an error.
	await expect(page.locator('[data-state="ready"]')).toHaveCount(20);
});

test('under reduced motion a projection appears without moving', async ({ page }) => {
	await page.emulateMedia({ reducedMotion: 'reduce' });
	await live(page);
	await project(page, { id: 'still', type: 'bar', data: C.examples.bar });
	await expect(page.getByTestId('projected-still').locator('[data-state="ready"]')).toBeVisible();
	expect(await bodyAnimations(page, 'still')).toEqual([]);
});

test('each malformed payload draws the error state in the validator words', async ({ page }) => {
	await live(page);
	for (const [i, m] of C.malformed.entries()) {
		await project(page, { id: `bad-${i}`, type: m.type, data: m.data });
		const gen = page.getByTestId(`projected-bad-${i}`).locator('.gen');
		await expect(gen, `${m.type} ${JSON.stringify(m.data).slice(0, 60)}`).toHaveAttribute('data-state', 'error');
		await expect(gen).toContainText(m.error);
	}
	// A frame with the same id replaces the projection: the fixed one draws.
	await project(page, { id: 'bad-0', type: 'bar', data: C.examples.bar });
	await expect(page.getByTestId('projected-bad-0').locator('.gen')).toHaveAttribute('data-state', 'ready');
});

/**
 * What the model-written HTML tries: each probe writes its outcome on the
 * frame's own body, where the spec reads it (Playwright reaches into the frame
 * from outside; the frame can reach nothing).
 */
const HOSTILE = `<body><script>
const out = document.body.dataset;
const violations = [];
document.addEventListener('securitypolicyviolation', (e) => {
	violations.push(e.effectiveDirective);
	out.violations = violations.sort().join(' ');
});
try { parent.document.title; out.parent = 'read'; } catch (e) { out.parent = 'blocked'; }
try { out.cookie = document.cookie ? 'read' : 'empty'; } catch (e) { out.cookie = 'blocked'; }
try { localStorage.length; out.storage = 'read'; } catch (e) { out.storage = 'blocked'; }
fetch('/api/status').then(() => (out.fetch = 'read'), () => (out.fetch = 'blocked'));
fetch('/hermes/api/test/spoken').then(() => (out.relay = 'read'), () => (out.relay = 'blocked'));
const img = new Image();
img.onload = () => (out.image = 'read');
img.onerror = () => (out.image = 'blocked');
img.src = '/favicon.png';
try {
	const ws = new WebSocket('ws://' + location.host + '/hermes/ws');
	ws.onopen = () => (out.socket = 'read');
	ws.onerror = () => (out.socket = 'blocked');
} catch (e) { out.socket = 'blocked'; }
out.ran = 'yes';
</script></body>`;

test.describe('the sandbox', () => {
	test('model-written HTML runs, but reaches no network and not the console, and goes on Back', async ({ page }) => {
		await live(page);
		// A probe that says "blocked" could have been stopped by the server or by CORS
		// (an opaque origin's fetch fails either way, net::ERR_FAILED, after it went
		// out). So each request the frame makes is filed with how it ended: only the
		// bootstrap may be answered, and everything else must die of the policy.
		const fromFrame: Record<string, string> = {};
		const file = (r: Request, how: string) => {
			if (r.frame() !== page.mainFrame()) fromFrame[new URL(r.url()).pathname] = how;
		};
		page.on('request', (r) => file(r, 'sent'));
		page.on('requestfinished', (r) => file(r, 'answered'));
		page.on('requestfailed', (r) => file(r, r.failure()?.errorText ?? 'failed'));
		await project(page, { id: 'hostile', type: 'custom_html', data: { title: 'Probe', html: HOSTILE, height: 120 } });
		// Nothing the model wrote runs until the owner presses Run (a busy loop would hold
		// the console's thread, and the time cap cannot fire then).
		await expect(page.locator('iframe')).toHaveCount(0);
		expect(Object.keys(fromFrame)).toEqual([]);
		await page.getByTestId('projected-hostile').getByTestId('projection-run').click();
		const frameEl = page.getByTestId('projected-hostile').getByTestId('projection-frame');
		await expect(frameEl).toHaveAttribute('sandbox', 'allow-scripts');
		const body = page.frameLocator('[data-testid="projected-hostile"] [data-testid="projection-frame"]').locator('body');
		await expect(body).toHaveAttribute('data-ran', 'yes');
		for (const probe of ['parent', 'cookie', 'storage', 'fetch', 'relay', 'image', 'socket']) {
			await expect(body, probe).toHaveAttribute(`data-${probe}`, 'blocked');
		}
		// The policy stopped them, in the frame's own words (a socket is not a request
		// Playwright can tie to a frame): the two fetches and the socket, and the image.
		await expect(body).toHaveAttribute('data-violations', 'connect-src connect-src connect-src img-src');
		await expect.poll(() => Object.values(fromFrame).filter((how) => how === 'sent')).toEqual([]);
		const { '/projection-frame': boot, ...rest } = fromFrame;
		expect(boot, 'the bootstrap').toBe('answered');
		for (const [path, how] of Object.entries(rest)) expect(how, path).toBe('csp');

		// Going back into the core kills it at once, not after the exit motion.
		await page.getByTestId('cell-hostile').getByRole('button', { name: 'Back' }).click();
		await expect(page.locator('iframe')).toHaveCount(0, { timeout: 100 });
	});

	test('its bootstrap carries the no-network policy even when opened on its own', async ({ page }) => {
		const r = await page.request.get('/projection-frame');
		const csp = r.headers()['content-security-policy'] ?? '';
		expect(csp).toContain("default-src 'none'");
		expect(csp).toContain('sandbox allow-scripts');
		expect(csp).not.toMatch(/connect-src|allow-same-origin/);
	});

	test('a frame too tall or too long is refused, and one left up is stopped at the time cap', async ({ page }) => {
		await live(page);
		await project(page, { id: 'tall', type: 'custom_html', data: { html: '<p>x</p>', height: 5000 } });
		await expect(page.getByTestId('projected-tall').locator('.gen')).toHaveAttribute('data-state', 'error');
		await project(page, { id: 'long', type: 'custom_html', data: { html: 'x'.repeat(32769) } });
		await expect(page.getByTestId('projected-long').locator('.gen')).toContainText('longer than 32768');
		await expect(page.locator('iframe')).toHaveCount(0);

		// The time cap, on a fake clock: the static gallery's custom visual. fastForward,
		// not runFor: five minutes of animation frames (the 3D model) would each be run.
		await page.clock.install();
		await page.goto('/gallery/projections');
		const cell = page.getByTestId('cell-custom_html');
		// A visual nobody started uses none of the cap.
		await page.clock.fastForward(C.limits.custom_html_seconds * 1000 + 1000);
		await expect(cell.getByTestId('projection-run')).toBeVisible();
		await cell.getByTestId('projection-run').click();
		await expect(cell.locator('iframe')).toHaveCount(1);
		await page.clock.fastForward(C.limits.custom_html_seconds * 1000 + 1000);
		await expect(cell.locator('iframe')).toHaveCount(0);
		await expect(cell).toContainText(`Stopped after ${C.limits.custom_html_seconds} s`);
	});
});

test.describe('the screenshots', () => {
	test.skip(!process.env.PROJECTION_SCREENS, 'PROJECTION_SCREENS=1 rewrites docs/screens/v2/projections/');
	for (const [width, height] of [
		[1440, 900],
		[390, 844]
	] as const) {
		test(`the gallery at ${width}`, async ({ page }) => {
			const out = `${ROOT}docs/screens/v2/projections`;
			mkdirSync(out, { recursive: true });
			await page.setViewportSize({ width, height });
			await page.goto('/gallery/projections');
			await expect(page.locator('[data-state="ready"]').first()).toBeVisible();
			// Every projection finishes coming out of the core, its rows and bars in.
			await page.waitForTimeout(1500);
			// A shot taller than the viewport leaves out a frame below the fold (custom_html
			// was blank), so the viewport takes the whole page, the width unchanged.
			const tall = await page.evaluate(() => document.documentElement.scrollHeight);
			await page.setViewportSize({ width, height: tall });
			await page.waitForTimeout(300);
			// A section each: the whole page at 390 is nine thousand pixels tall.
			for (const [name, label] of [
				['types', 'Every type'],
				['light', 'Light: required fields only'],
				['states', 'States']
			] as const) {
				const shot = await page.getByRole('region', { name: label }).screenshot();
				writeFileSync(`${out}/${name}-${width}.png`, shot);
			}
		});
	}
});
