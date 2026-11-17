import { test, expect } from '@playwright/test';
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { SCENES, SNAPSHOT_APPROVAL, blurLayers, demo, emit, openShell, settledAt } from './v2';
import { glRenderer, spans, stats, withCpu, type TraceEvent } from './frametime';

/**
 * The frame-time gate on the v2 shell: the console driven through what a
 * person meets — the core travelling through every scene, an approval card
 * coming out and going back, the launcher, the palette typed into, a full
 * screen and back, a resize — under a Chromium trace, and the frames read
 * back from it. It replaced the legacy 8-panel scene's gate with T-0611; the
 * budget file and the method are the same.
 *
 * ## What a frame is here
 *
 * One `AnimationFrame` event on the page's renderer main thread — the whole
 * rendering update, script through paint — which is the frame DevTools'
 * performance panel draws. It is independent of vsync, so an idle page counts
 * nothing and a page at 60 Hz is judged on the work it does per frame.
 *
 * The gated number is the frame's CPU time: the thread time (`tdur`) of the
 * main-thread tasks inside the frame's span. Wall time is reported beside it
 * but not gated: on a 4-core host beside two gradle builds a frame's wall time
 * doubles while the UI did nothing different (the thread was descheduled).
 *
 * ## The profile
 *
 * The runner's headless Chromium has no GPU: WebGL comes from ANGLE over
 * SwiftShader. The budget file (`scripts/verify/hud-frametime-budget.json`)
 * carries a profile per renderer, picked here from the renderer string, and the
 * report names which one applied.
 *
 * ## Best of three
 *
 * The interactions run three times under one trace and the gate reads the best
 * pass: a software rasteriser on a shared runner loses frames to the scheduler,
 * not to the UI, while a real regression raises every pass. Every pass is in
 * the report (`.verify/hud-frametime.json`).
 *
 * ## Layout shift
 *
 * The core travels on `transform` alone (`ds/shell/Stage.svelte`), so the
 * scenes phase is held to the budget's CLS of 0: a scene change that moved
 * layout would be the core pushing the page around.
 *
 * ## What it does not cover
 *
 * Android (the macrobenchmark module, on a device) and the desktop app's own
 * webview, which renders the same bundle. The core alone, on its gallery, is
 * `ds-core.spec.ts`'s budget. Warm-up is excluded: the first frames compile the
 * core's shader, and a gate that failed on that would catch no regression.
 */

const ROOT = new URL('../../', import.meta.url).pathname;
const PASSES = 3;
const PHASES = ['scenes', 'card', 'launcher', 'palette', 'screen', 'resize', 'end'] as const;
const BUDGET = JSON.parse(readFileSync(`${ROOT}scripts/verify/hud-frametime-budget.json`, 'utf8')) as {
	profiles: Record<string, { match: string; p95_ms: number; max_ms: number }>;
	blur_layers_max: number;
	cls_during_travel_max: number;
};
const VIEWPORT = { width: 1440, height: 900 };

test('the shell stays inside the frame budget through every scene, a card, the sheets, a screen and a resize', async ({ page, context }) => {
	test.setTimeout(240_000);
	await page.setViewportSize(VIEWPORT);
	await openShell(page);
	await settledAt(page, 'idle');
	const renderer = await glRenderer(page);
	const profileName = Object.keys(BUDGET.profiles).find((k) => BUDGET.profiles[k].match && renderer.includes(BUDGET.profiles[k].match)) ?? 'gpu';
	const budget = BUDGET.profiles[profileName];

	const blur = await blurLayers(page);
	expect(blur, 'backdrop-filter layers on screen').toBeLessThanOrEqual(BUDGET.blur_layers_max);

	// Layout shifts are collected in the page for the scenes phase only; the
	// observer is armed before tracing so the first frame of travel is not missed.
	await page.evaluate(() => {
		const w = window as unknown as { __shifts: number[]; __observe: boolean };
		w.__shifts = [];
		w.__observe = false;
		new PerformanceObserver((list) => {
			for (const entry of list.getEntries()) if (w.__observe) w.__shifts.push((entry as unknown as { value: number }).value);
		}).observe({ type: 'layout-shift', buffered: false });
	});
	const observe = (on: boolean) => page.evaluate((v) => ((window as unknown as { __observe: boolean }).__observe = v), on);

	// Shader compile and the first layout are warm-up, not the shell's cost.
	await page.waitForTimeout(1000);

	const cdp = await context.newCDPSession(page);
	const events: TraceEvent[] = [];
	cdp.on('Tracing.dataCollected', (d: { value: TraceEvent[] }) => events.push(...d.value));
	await cdp.send('Tracing.start', {
		categories: 'disabled-by-default-devtools.timeline,disabled-by-default-devtools.timeline.frame,devtools.timeline,blink.user_timing',
		transferMode: 'ReportEvents'
	});
	const mark = (name: string) => page.evaluate((n) => performance.mark(n), name);

	for (let pass = 1; pass <= PASSES; pass++) {
		// scenes: the core travels to each scene and back to idle.
		await mark(`hud:scenes#${pass}`);
		await observe(true);
		for (const scene of [...SCENES.slice(1), 'idle']) {
			await demo(page, scene);
			await settledAt(page, scene);
		}
		await demo(page, '');
		await settledAt(page, 'idle');
		await observe(false);

		// card: an approval comes out of the core and goes back on Deny.
		await mark(`hud:card#${pass}`);
		await emit(page, 'approval.request', { ...SNAPSHOT_APPROVAL, request_id: `frametime-${pass}` });
		await settledAt(page, 'alert');
		await page.getByTestId('approval-card').getByRole('button', { name: 'Deny' }).click();
		await settledAt(page, 'idle');

		// launcher: All apps, open and closed.
		await mark(`hud:launcher#${pass}`);
		await page.getByRole('button', { name: 'All apps' }).click();
		await expect(page.getByTestId('launcher')).toBeVisible();
		await page.waitForTimeout(300);
		await page.keyboard.press('Escape');
		await expect(page.locator('dialog[open]')).toHaveCount(0);

		// palette: ⌘K, a query typed a key at a time, closed.
		await mark(`hud:palette#${pass}`);
		await page.keyboard.press('Control+k');
		await expect(page.getByTestId('palette')).toBeVisible();
		await page.keyboard.type('settings', { delay: 40 });
		await page.keyboard.press('Escape');
		await expect(page.locator('dialog[open]')).toHaveCount(0);

		// screen: Chat from the launcher, and back to the core by the mark.
		await mark(`hud:screen#${pass}`);
		await page.getByRole('button', { name: 'All apps' }).click();
		await page.getByTestId('launcher').getByRole('link', { name: /^Chat/ }).click();
		await expect(page.getByTestId('screen-chat')).toBeVisible();
		await page.waitForTimeout(300);
		await page.getByRole('link', { name: /, the core$/ }).click();
		await settledAt(page, 'idle');

		// resize: a smaller window and back, the stage laid out again each way.
		await mark(`hud:resize#${pass}`);
		await page.setViewportSize({ width: 1024, height: 768 });
		await page.waitForTimeout(300);
		await page.setViewportSize(VIEWPORT);
		await page.waitForTimeout(300);
		await mark(`hud:end#${pass}`);
	}

	await new Promise<void>((resolve) => {
		cdp.once('Tracing.tracingComplete', () => resolve());
		void cdp.send('Tracing.end');
	});

	// The marks in the trace slice the frames into passes and phases.
	const marks = events
		.filter((e) => e.name?.startsWith('hud:') && (e.ph === 'R' || e.ph === 'I' || e.ph === 'n'))
		.map((e) => ({ name: e.name!, ts: e.ts }))
		.sort((a, b) => a.ts - b.ts);
	const expectedMarks: string[] = [];
	for (let pass = 1; pass <= PASSES; pass++) for (const ph of PHASES) expectedMarks.push(`hud:${ph}#${pass}`);
	expect(marks.map((m) => m.name), 'phase marks reached the trace').toEqual(expectedMarks);
	const allFrames = withCpu(events, spans(events, 'AnimationFrame'));
	const pipeline = spans(events, 'PipelineReporter');
	const between = (a: number, b: number) => (f: { start: number }) => f.start >= a && f.start < b;

	const passes = [];
	for (let pass = 1; pass <= PASSES; pass++) {
		const at = (ph: string) => marks.find((m) => m.name === `hud:${ph}#${pass}`)!.ts;
		const phases: Record<string, ReturnType<typeof stats>> = {};
		for (let i = 0; i + 1 < PHASES.length; i++) {
			phases[PHASES[i]] = stats(allFrames.filter(between(at(PHASES[i]), at(PHASES[i + 1]))).map((f) => f.cpu));
		}
		const inPass = allFrames.filter(between(at('scenes'), at('end')));
		const frames = stats(inPass.map((f) => f.cpu));
		expect(frames.n, `frames traced in pass ${pass}`).toBeGreaterThan(60);
		passes.push({ pass, frames, wall: stats(inPass.map((f) => f.ms)), phases, pipeline_reporter: stats(pipeline.filter(between(at('scenes'), at('end'))).map((f) => f.ms)) });
	}
	// The gate reads the best pass; a regression in the UI raises all of them.
	const best = passes.reduce((a, b) => (b.frames.p95 < a.frames.p95 ? b : a));
	const all = best.frames;
	const shifts = await page.evaluate(() => (window as unknown as { __shifts: number[] }).__shifts);
	const report = {
		recorded_at: new Date().toISOString(),
		surface: 'the v2 shell (T-0611): scenes, an approval card, the launcher, the palette, the chat screen, a resize',
		renderer,
		profile: profileName,
		budget,
		best_pass: best.pass,
		metric: 'frame CPU ms (thread time of main-thread tasks inside each AnimationFrame)',
		frames: all,
		wall: best.wall,
		phases: best.phases,
		pipeline_reporter: best.pipeline_reporter,
		passes,
		blur_layers: blur,
		cls_during_travel: +shifts.reduce((a, b) => a + b, 0).toFixed(4)
	};
	mkdirSync(`${ROOT}.verify`, { recursive: true });
	writeFileSync(`${ROOT}.verify/hud-frametime.json`, JSON.stringify(report, null, 2));
	console.log(
		`hud-frametime [${profileName}] best of ${PASSES} (pass ${best.pass}): frames=${all.n} cpu p50=${all.p50} p95=${all.p95} max=${all.max} ms (budget p95≤${budget.p95_ms} max≤${budget.max_ms}); ` +
			`wall p95=${best.wall.p95} max=${best.wall.max}; passes cpu p95=${passes.map((p) => p.frames.p95).join('/')} max=${passes.map((p) => p.frames.max).join('/')}; blur=${blur}; CLS(travel)=${report.cls_during_travel}`
	);

	expect(report.cls_during_travel, 'cumulative layout shift while the core travels').toBeLessThanOrEqual(BUDGET.cls_during_travel_max);
	expect(all.p95, `p95 frame CPU on the ${profileName} profile`).toBeLessThanOrEqual(budget.p95_ms);
	expect(all.max, `longest frame CPU on the ${profileName} profile`).toBeLessThanOrEqual(budget.max_ms);
});
