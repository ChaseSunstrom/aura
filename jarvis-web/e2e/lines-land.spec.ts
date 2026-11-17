// The stage's lines land on what they point at (T-0691). The working scene's connectors
// end at each projection's facing edge, the alert scene's leader at the card's left edge;
// both are read off the page (a connector's path end, the leader's end dot) and compared
// with the target's box, after the entrance, after a resize and after a rotation.
//
// The console is dark only, so the sweep has one theme. The 2 px numbers are the gate; the
// screenshots in docs/screens/v2/lines/ are the record (LINES_SCREENS=1 rewrites them: the
// core is animated all the time, so a pixel golden would be flaky).
import { expect, test, type Page } from '@playwright/test';
import { mkdirSync, writeFileSync } from 'node:fs';

import { SNAPSHOT_APPROVAL, demo, emit, openShell, settledAt } from './v2';

const ROOT = new URL('../../', import.meta.url).pathname;
const TOLERANCE = 2;
const SIZES = [
	[844, 390],
	[768, 1024],
	[1024, 768],
	[1440, 900],
	[1920, 1080]
] as const;

interface Pt {
	x: number;
	y: number;
}
interface Landing {
	what: string;
	line: Pt;
	target: Pt;
}

/** Where every line of the scene ends, and where its target's edge midpoint is. */
function landings(page: Page): Promise<{ items: Landing[]; slots: number; connectors: number; wide: boolean }> {
	return page.evaluate(() => {
		const st = document.querySelector('[data-testid="stage"]')!.getBoundingClientRect();
		const out: { what: string; line: { x: number; y: number }; target: { x: number; y: number } }[] = [];
		const slots = [...document.querySelectorAll<HTMLElement>('[data-shown]')];
		const connectors = [...document.querySelectorAll('[data-testid="stage-connector"]')];
		connectors.forEach((c) => {
			const nums = (c.querySelector('path')!.getAttribute('d') ?? '').match(/-?[\d.]+/g)!.map(Number);
			const slot = document.querySelector<HTMLElement>(`[data-shown="${c.getAttribute('data-for')}"]`)!;
			const b = slot.getBoundingClientRect();
			const edge = slot.dataset.edge;
			out.push({
				what: `connector to ${slot.dataset.shown}`,
				line: { x: st.left + (nums[nums.length - 2] / 100) * st.width, y: st.top + (nums[nums.length - 1] / 100) * st.height },
				target: edge === 'right' ? { x: b.right, y: b.top + b.height / 2 } : edge === 'left' ? { x: b.left, y: b.top + b.height / 2 } : { x: b.left + b.width / 2, y: b.top }
			});
		});
		const leader = document.querySelector('[data-testid="stage-leader"]');
		if (leader) {
			const d = leader.querySelectorAll('.dot')[1].getBoundingClientRect();
			const b = document.querySelector('.card')!.getBoundingClientRect();
			out.push({ what: 'leader to the card', line: { x: d.left + d.width / 2, y: d.top + d.height / 2 }, target: { x: b.left, y: b.top + b.height / 2 } });
			// And it leaves the core at the edge of its light (0.3 of the core's width from the centre).
			const s = leader.querySelectorAll('.dot')[0].getBoundingClientRect();
			const c = document.querySelector('[data-testid="core"]')!.getBoundingClientRect();
			out.push({ what: 'leader from the core', line: { x: s.left + s.width / 2, y: s.top + s.height / 2 }, target: { x: c.left + c.width / 2 + c.width * 0.3, y: c.top + c.height / 2 } });
		}
		return { items: out, slots: slots.length, connectors: connectors.length, wide: st.width > 720 };
	});
}

/** One connector per slot in the working scene, the leader's two ends in the alert scene; none on a phone. */
async function expectLanded(page: Page, scene: string, when: string, quick = false): Promise<void> {
	// ResizeObserver, then a frame, then the Svelte patch. `quick` allows two frames only: the
	// shell re-derives its slots as feeds tick, which would hide a line that is never re-measured.
	if (quick) await page.evaluate(() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r))));
	else await page.waitForTimeout(400);
	const { items, slots, connectors, wide } = await landings(page);
	const expected = !wide ? 0 : scene === 'working' ? slots : 2;
	if (scene === 'working') expect(slots, `${scene} ${when}: slots shown`).toBeGreaterThan(0);
	if (scene === 'working') expect(connectors, `${scene} ${when}: a connector per slot`).toBe(expected);
	expect(items.length, `${scene} ${when}: lines drawn`).toBe(expected);
	for (const l of items) {
		expect(Math.abs(l.line.x - l.target.x), `${scene} ${when} ${l.what} x`).toBeLessThanOrEqual(TOLERANCE);
		expect(Math.abs(l.line.y - l.target.y), `${scene} ${when} ${l.what} y`).toBeLessThanOrEqual(TOLERANCE);
	}
}

async function shoot(page: Page, scene: string, w: number): Promise<void> {
	if (!process.env.LINES_SCREENS) return;
	const out = `${ROOT}docs/screens/v2/lines`;
	mkdirSync(out, { recursive: true });
	writeFileSync(`${out}/${scene}-${w}.png`, await page.screenshot());
}

/**
 * A slot grows taller while the stage keeps its size (a chart's axis, a long row, a font that
 * arrives late); the slot is centred on its point, so its top edge moves. No stage resize
 * fires, so only an observer on the slot itself re-measures.
 */
const growSlots = (page: Page) =>
	page.evaluate(() => {
		for (const el of document.querySelectorAll<HTMLElement>('[data-shown]')) el.style.minBlockSize = '40vh';
	});

for (const scene of ['working', 'alert'] as const) {
	test(`${scene}: every line ends on its element after the entrance, a resize and a rotation`, async ({ page }) => {
		test.setTimeout(90_000);
		const [w0, h0] = SIZES[3];
		await page.setViewportSize({ width: w0, height: h0 });
		await openShell(page);
		if (scene === 'working') {
			await emit(page, 'status.update', { kind: 'goal', text: '⊙ Goal set: Book a table for two (20-turn budget)' });
			await emit(page, 'tool.start', { tool_id: 'w1', name: 'web_search', context: 'restaurants near me' });
			await emit(page, 'tool.start', { tool_id: 'w2', name: 'read_file', context: 'notes.md' });
		} else {
			await emit(page, 'approval.request', SNAPSHOT_APPROVAL);
		}
		await settledAt(page, scene);
		await expectLanded(page, scene, `at ${w0} after the entrance`);
		if (scene === 'working') {
			await growSlots(page);
			await expectLanded(page, scene, `at ${w0} after the slots grew`, true);
		}

		for (const [w, h] of SIZES) {
			await page.setViewportSize({ width: w, height: h });
			await expectLanded(page, scene, `at ${w}x${h} after a resize`);
			await shoot(page, scene, w);
		}
		// A rotation, as the phone reports it: upright (no lines, the slots stack under the core),
		// then on its side (the lines are back).
		const turned = () => page.evaluate(() => window.dispatchEvent(new Event('orientationchange')));
		await page.setViewportSize({ width: 390, height: 844 });
		await turned();
		await expectLanded(page, scene, 'at 390x844 upright');
		await page.setViewportSize({ width: 844, height: 390 });
		await turned();
		await expectLanded(page, scene, 'at 844x390 after a rotation');
	});
}

test('an alert with nothing to answer projects no card and draws no line to one', async ({ page }) => {
	// A fault puts the core in its alert state with no approval waiting. The stage drew the
	// card's backing and its amber line all the same: an empty box pinned beside the core
	// (seen on a fresh install's first answer, T-1480).
	await page.setViewportSize({ width: 1440, height: 900 });
	await openShell(page);
	await demo(page, 'alert');
	await settledAt(page, 'alert');
	await page.waitForTimeout(400); // the measure that would draw the line has run
	await expect(page.locator('[data-testid="stage"] .below > .card')).toHaveCount(0);
	await expect(page.getByTestId('stage-leader')).toHaveCount(0);

	// An approval still brings both.
	await demo(page, '');
	await emit(page, 'approval.request', SNAPSHOT_APPROVAL);
	await settledAt(page, 'alert');
	await expect(page.locator('[data-testid="stage"] .below > .card')).toHaveCount(1);
	await expect(page.getByTestId('stage-leader')).toHaveCount(1);
});
