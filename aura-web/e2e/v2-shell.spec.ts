// The v2 shell (T-0611): one stage, no permanent panels. The core travels
// between seven scenes; what a scene projects comes out of the core and goes
// back into it; a long job lifts the core into the presence strip. The left
// rail is gone: "All apps", "Search or command" (⌘K) and "Devices" replace it.
//
// Every ordered pair of scenes is driven through `jarvis:orb-demo`, each
// transition is also interrupted (turned round mid-exit), and the strip, the
// launcher, the palette and the devices sheet are read off the page.
import { expect, test } from '@playwright/test';

import { SCENES, SNAPSHOT_APPROVAL, demo, emit, layoutPx, openShell, record, say, seen, settledAt } from './v2';

test.beforeEach(async ({ page }) => {
	await page.setViewportSize({ width: 1440, height: 900 });
	await openShell(page);
});

test('the console opens on the core, with no panels, tabs or rail', async ({ page }) => {
	const shell = page.getByTestId('shell');
	await expect(shell).toHaveAttribute('data-screen', 'core');
	await expect(page.getByTestId('core')).toBeVisible();
	for (const legacy of ['hud', 'rail', 'dock', 'panel-drawer', 'tab-strip']) await expect(page.getByTestId(legacy)).toHaveCount(0);
	await expect(page.locator('[data-panel]')).toHaveCount(0);
	for (const name of ['All apps', 'Search or command', 'Devices']) await expect(page.getByRole('button', { name, exact: true })).toBeVisible();
	await expect(page.getByTestId('chat-input')).toBeVisible();
});

// data-ready is "mounted and gate known" and data-settled is layout; neither says Hermes has
// created or resumed the HUD session, which is what a live rig must wait on (T-0525).
test('session-ready: the shell says true only while a session is live on an open socket', async ({ page }) => {
	let hold = true;
	const held: Array<() => void> = [];
	let dropped: { close(): Promise<void> } | undefined;
	await page.routeWebSocket(/\/hermes\/ws$/, (ws) => {
		dropped = ws;
		const server = ws.connectToServer();
		ws.onMessage((m) => {
			const f = JSON.parse(String(m));
			const opens = f.method === 'session.resume' || f.method === 'session.create';
			if (opens && hold) held.push(() => server.send(m));
			else server.send(m);
		});
	});
	await page.reload();
	const shell = page.getByTestId('shell');
	// Connected, mounted, gate known, but no session yet: layout is ready, the session is not.
	await expect(shell).toHaveAttribute('data-ready', 'true', { timeout: 15_000 });
	await expect.poll(() => held.length, { timeout: 15_000 }).toBeGreaterThan(0);
	await expect(shell).toHaveAttribute('data-session-ready', 'false');
	hold = false;
	held.splice(0).forEach((send) => send());
	await expect(shell).toHaveAttribute('data-session-ready', 'true', { timeout: 15_000 });
	// A drop: false again, and it stays false until the redialled socket has its session back.
	hold = true;
	await dropped!.close();
	await expect(shell).toHaveAttribute('data-session-ready', 'false');
	await expect.poll(() => held.length, { timeout: 15_000 }).toBeGreaterThan(0);
	await expect(shell).toHaveAttribute('data-session-ready', 'false');
	hold = false;
	held.splice(0).forEach((send) => send());
	await expect(shell).toHaveAttribute('data-session-ready', 'true', { timeout: 15_000 });
});

for (const from of SCENES) {
	test(`from ${from}: every other scene is reached whole, and a turn back mid-exit never shows it`, async ({ page }) => {
		test.setTimeout(90_000);
		await demo(page, from);
		await settledAt(page, from);
		for (const to of SCENES) {
			if (to === from) continue;
			// Turned round: the exit starts, then the stage is sent back. The
			// target is never drawn; the scene comes back from where it got to.
			// Sent back the moment the shell says it is leaving: a fixed 120 ms
			// outlasted the whole exit on a loaded runner (pipeline 767).
			await record(page);
			await page.evaluate(
				async ([a, b]) => {
					const say = (s: string) => window.dispatchEvent(new CustomEvent('jarvis:orb-demo', { detail: s }));
					const el = document.querySelector('[data-testid="shell"]')!;
					const leaving = new Promise<void>((resolve) => {
						const obs = new MutationObserver(() => {
							if (el.getAttribute('data-phase') !== 'leaving') return;
							obs.disconnect();
							say(a);
							resolve();
						});
						obs.observe(el, { attributes: true, attributeFilter: ['data-phase'] });
					});
					say(b);
					await leaving;
				},
				[from, to]
			);
			await settledAt(page, from);
			const back = await seen(page);
			expect(back.some((s) => s.startsWith(`${to}:`)), `${from}→${to} turned round showed ${to}: ${back}`).toBe(false);
			expect(back).toContain(`${from}:leaving`);

			// Whole: the outgoing scene leaves before the incoming enters.
			await record(page);
			await demo(page, to);
			await settledAt(page, to);
			const there = await seen(page);
			const left = there.indexOf(`${from}:leaving`);
			const entered = there.indexOf(`${to}:entering`);
			expect(left, `${from}→${to}: ${there}`).toBeGreaterThanOrEqual(0);
			expect(entered, `${from}→${to}: ${there}`).toBeGreaterThan(left);
			await demo(page, from);
			await settledAt(page, from);
		}
	});
}

test('with reduced motion a scene changes at once', async ({ page }) => {
	await page.emulateMedia({ reducedMotion: 'reduce' });
	await page.reload();
	await expect(page.getByTestId('shell')).toHaveAttribute('data-ready', 'true', { timeout: 15_000 });
	await record(page);
	await demo(page, 'thinking');
	await settledAt(page, 'thinking');
	expect((await seen(page)).some((s) => s.endsWith(':leaving'))).toBe(false);
});

test('the working scene lifts the core into the presence strip, with pulsing connectors to its work', async ({ page }) => {
	await emit(page, 'status.update', { kind: 'goal', text: '⊙ Goal set: Book a table for two (20-turn budget)' });
	await emit(page, 'tool.start', { tool_id: 'w1', name: 'web_search', context: 'restaurants near me' });
	await settledAt(page, 'working');
	const strip = page.getByTestId('presence-strip');
	await expect(strip).toBeVisible();
	await expect(strip).toContainText('Book a table for two');
	await expect(page.getByTestId('stage-projection').first()).toBeVisible();
	await expect(page.getByTestId('stage-connector').first()).toBeAttached();
	// The core sits in the strip: above the stage's middle and smaller than at rest.
	const core = await page.getByTestId('core').boundingBox();
	expect(core!.y + core!.height / 2).toBeLessThan(900 * 0.3);
	// Esc takes over: the goal is paused, and the strip says so.
	await page.keyboard.press('Escape');
	await expect(page.getByTestId('shell-status')).toContainText(/paus|took over/i);
});

test('an approval comes out as a card: what, why, impact, hold to confirm, deny, show me first, and the rule', async ({ page }) => {
	await emit(page, 'approval.request', SNAPSHOT_APPROVAL);
	await settledAt(page, 'alert');
	const card = page.getByRole('alertdialog', { name: 'Delete 3 old snapshots?' });
	await expect(card).toBeVisible();
	await expect(card).toContainText('older than 30 days');
	await expect(card).toContainText('Frees 61 GB');
	await expect(card.getByRole('button', { name: /Hold to/ })).toBeVisible();
	await expect(card.getByRole('button', { name: 'Deny' })).toBeVisible();
	const rule = card.getByRole('group', { name: /Next time/ });
	for (const name of ['Ask every time', 'Allow for this task', 'Allow when I’m home']) await expect(rule.getByRole('button', { name })).toBeVisible();
	await expect(rule.getByRole('button', { name: 'Ask every time' })).toHaveAttribute('aria-pressed', 'true');
	await card.getByRole('button', { name: /Show me first/ }).click();
	await expect(card).toContainText('proxmox_snapshot_delete');
	await card.getByRole('button', { name: 'Deny' }).click();
	await expect(card).toHaveCount(0);
	await expect(page.getByTestId('shell-status')).toContainText('Denied');
	await settledAt(page, 'idle');
});

test('an approval a turn is held on is the card, and holding to confirm lets the turn finish', async ({ page }) => {
	await say(page, 'please delete the scratch folder, with approval');
	const card = page.getByTestId('approval-card');
	await expect(card).toBeVisible({ timeout: 15_000 });
	await expect(page.getByTestId('shell')).toHaveAttribute('data-reactor', 'attention');
	// Not undoable, so a hold, not a click: down long enough for the fill, then up.
	await page.getByTestId('approval-hold').hover();
	await page.mouse.down();
	await expect(card).toHaveCount(0, { timeout: 5_000 });
	await page.mouse.up();
	await expect(page.getByTestId('conversation-jarvis')).toContainText('approved once', { timeout: 15_000 });
});

test('a question JARVIS asks mid-turn is answered in words on the card', async ({ page }) => {
	await say(page, 'lock the door, ask me which door');
	const card = page.getByTestId('approval-card');
	await expect(card).toContainText('Front door or garage door?', { timeout: 15_000 });
	const typed = card.getByRole('textbox', { name: 'Your answer' });
	await typed.fill('garage');
	// The turn keeps streaming under the card: what the owner typed stays (T-0823).
	await expect(page.getByTestId('conversation-jarvis')).toContainText('Which door', { timeout: 15_000 });
	await expect(typed).toHaveValue('garage');
	await card.getByRole('button', { name: 'Answer' }).click();
	await expect(card).toHaveCount(0, { timeout: 10_000 });
	await expect(page.getByTestId('conversation-jarvis')).toContainText('garage door', { timeout: 15_000 });
});

test('All apps opens the launcher; each app is a link to its screen', async ({ page }) => {
	await page.getByRole('button', { name: 'All apps', exact: true }).click();
	const sheet = page.getByRole('dialog', { name: 'All apps' });
	await expect(sheet).toBeVisible();
	for (const name of ['Chat', 'Approvals', 'Texts', 'Devices', 'Integrations', 'Settings', 'Ops']) await expect(sheet.getByRole('link', { name: new RegExp(`^${name}`) })).toBeVisible();
	await sheet.getByRole('link', { name: /^Settings/ }).click();
	await expect(page).toHaveURL(/\/settings$/);
	await expect(page.getByTestId('shell')).toHaveAttribute('data-screen', 'settings');
	await expect(sheet).toHaveCount(0);
	// Back to the core from the screen's own way home.
	await page.getByRole('link', { name: 'Back to the core' }).click();
	await expect(page.getByTestId('shell')).toHaveAttribute('data-screen', 'core');
});

test('⌘K opens Search or command; typing ranks, Enter runs the first, Escape closes', async ({ page }) => {
	await page.keyboard.press('ControlOrMeta+k');
	const sheet = page.getByRole('dialog', { name: 'Search or command' });
	await expect(sheet).toBeVisible();
	const input = sheet.getByRole('combobox', { name: 'Search or command' });
	await expect(input).toBeFocused();
	await input.fill('appro');
	await expect(sheet.getByRole('option').first()).toContainText('Approvals');
	await page.keyboard.press('Enter');
	await expect(page).toHaveURL(/\/approvals$/);
	await expect(sheet).toHaveCount(0);
	await page.getByRole('button', { name: 'Search or command', exact: true }).click();
	await expect(sheet).toBeVisible();
	await page.keyboard.press('Escape');
	await expect(sheet).toHaveCount(0);
});

test('⌘K hands a query that matches nothing to JARVIS', async ({ page }) => {
	await page.keyboard.press('ControlOrMeta+k');
	const sheet = page.getByRole('dialog', { name: 'Search or command' });
	await sheet.getByRole('combobox').fill('what is the weather tomorrow');
	await expect(sheet.getByRole('option').first()).toContainText('Ask JARVIS');
	await page.keyboard.press('Enter');
	await expect(sheet).toHaveCount(0);
	await expect(page.getByTestId('conversation-you')).toContainText('what is the weather tomorrow');
});

test('Devices lists the paired devices, each a large target', async ({ page }) => {
	await page.getByRole('button', { name: 'Devices', exact: true }).click();
	const sheet = page.getByRole('dialog', { name: 'Your devices' });
	await expect(sheet).toBeVisible();
	const nav = sheet.getByRole('navigation', { name: 'Your devices' });
	await expect(nav.getByRole('link').first()).toBeVisible();
	for (const link of await nav.getByRole('link').all()) {
		const box = (await link.boundingBox())!;
		expect(layoutPx(box.height)).toBeGreaterThanOrEqual(44);
	}
	await expect(sheet.getByRole('link', { name: /Pair a device|All devices/ })).toBeVisible();
});

// T-0831: openShell once returned on the server-rendered shell, before its listeners were up, and
// a scene forced then was lost on a loaded runner (pipeline 715). Late scripts make that certain.
test('a scene forced right after opening lands even when the app arrives late', async ({ page }) => {
	await page.route('**/_app/immutable/**/*.js', async (route) => {
		await new Promise((r) => setTimeout(r, 2000));
		await route.continue();
	});
	await openShell(page);
	await demo(page, 'answer');
	await settledAt(page, 'answer');
});

// T-0694: the common approval (undoable, a server card) is decided on one phone screen, with
// no scrolling, its risk named, Ask why a no, and Approve all N only for a queue of undoable ones.
const UNDOABLE = (id: string, what: string) => ({
	...SNAPSHOT_APPROVAL,
	request_id: id,
	reason: SNAPSHOT_APPROVAL.reason.replace('What: Delete 3 old snapshots?', `What: ${what}`).replace('Undoable: no', 'Undoable: yes')
});

test('an approval card fits a 390x844 phone screen without scrolling, risk chip and Ask why included', async ({ page }) => {
	await page.setViewportSize({ width: 390, height: 844 });
	await emit(page, 'approval.request', UNDOABLE('e2e-fit', 'Move 3 old snapshots?'));
	await settledAt(page, 'alert');
	const card = page.getByRole('alertdialog', { name: 'Move 3 old snapshots?' });
	await expect(card).toBeVisible();
	await expect(card.getByTestId('approval-risk')).toHaveText('Low risk');
	await expect(card.getByRole('button', { name: 'Approve', exact: true })).toBeVisible();
	await expect(card.getByRole('button', { name: 'Ask why' })).toBeVisible();
	const box = (await card.boundingBox())!;
	expect(box.y).toBeGreaterThanOrEqual(0);
	expect(box.y + box.height).toBeLessThanOrEqual(844);
	const scrolls = await page.evaluate(() => document.documentElement.scrollHeight > window.innerHeight);
	expect(scrolls).toBe(false);
	// Ask why is a no: the request closes as denied, and nothing was approved.
	await card.getByRole('button', { name: 'Ask why' }).click();
	await expect(card).toHaveCount(0);
	await expect(page.getByTestId('shell-status')).toContainText('Denied. Asked why.');
});

test('approval: Approve all N is drawn only for a queue of undoable approvals, and answers each once', async ({ page }) => {
	await page.setViewportSize({ width: 390, height: 844 });
	await emit(page, 'approval.request', UNDOABLE('e2e-b1', 'Move the first?'));
	await emit(page, 'approval.request', SNAPSHOT_APPROVAL);
	await settledAt(page, 'alert');
	// One of the two cannot be undone: no batch, and the held one still needs its hold.
	await expect(page.getByRole('button', { name: /^Approve all/ })).toHaveCount(0);
	await emit(page, 'approval.cancelled', { request_id: SNAPSHOT_APPROVAL.request_id });
	await emit(page, 'approval.request', UNDOABLE('e2e-b2', 'Move the second?'));
	const all = page.getByRole('button', { name: 'Approve all 2' });
	await expect(all).toBeVisible();
	await all.click();
	await expect(page.getByRole('alertdialog')).toHaveCount(0);
	await expect(page.getByTestId('shell-status')).toContainText('Approved');
});

test('on a phone the stage stacks and the top bar still fits', async ({ page }) => {
	await page.setViewportSize({ width: 390, height: 844 });
	await demo(page, 'answer');
	await settledAt(page, 'answer');
	const width = await page.evaluate(() => document.documentElement.scrollWidth);
	expect(width).toBeLessThanOrEqual(390);
	for (const name of ['All apps', 'Search or command', 'Devices']) {
		const box = (await page.getByRole('button', { name, exact: true }).boundingBox())!;
		expect(layoutPx(box.height)).toBeGreaterThanOrEqual(44);
		expect(box.x + box.width).toBeLessThanOrEqual(390);
	}
});

// The owner's desktop, 2026-11-08: in a short window "I don't recognise that voice." was drawn
// over the reply line, the shell's status pinned a dock's height up and the stage's words at the
// stage's foot (T-0703). Every visible box that shows the status is checked against the reply.
for (const size of [{ width: 720, height: 300 }, { width: 1440, height: 900 }]) {
	test(`a status line never lands on the words, at ${size.width}x${size.height}`, async ({ page }) => {
		await page.setViewportSize(size);
		await say(page, 'hello');
		const words = page.getByTestId('conversation-jarvis');
		await expect(words).not.toBeEmpty({ timeout: 15_000 });
		await emit(page, 'status.update', { kind: 'goal', text: '⊙ Goal set: Book a table for two (20-turn budget)' });
		await emit(page, 'tool.start', { tool_id: 'w1', name: 'web_search', context: 'restaurants near me' });
		await page.keyboard.press('Escape');
		await expect(page.getByTestId('shell-status')).toContainText(/paus|took over/i);
		const reply = (await words.boundingBox())!;
		const shown = page.locator('p:visible', { hasText: /paus|took over/i });
		expect(await shown.count()).toBeGreaterThan(0);
		for (const box of await Promise.all((await shown.all()).map((p) => p.boundingBox()))) {
			if (!box || box.width < 2 || box.height < 2) continue; // a screen reader's copy
			const overlapX = Math.min(box.x + box.width, reply.x + reply.width) - Math.max(box.x, reply.x);
			const overlapY = Math.min(box.y + box.height, reply.y + reply.height) - Math.max(box.y, reply.y);
			expect(overlapX > 1 && overlapY > 1, `status ${JSON.stringify(box)} on the reply ${JSON.stringify(reply)}`).toBe(false);
		}
	});
}

// The owner's temperature question, 2026-11-08: four calls flung to the stage's corners, each
// titled by its tool name and showing raw arguments (T-0707). The calls are one ordered list of
// plain steps, beside the core and clear of its light.
for (const size of [{ width: 1440, height: 900 }, { width: 1024, height: 700 }]) {
	test(`tool steps stand as one list beside the core, at ${size.width}x${size.height}`, async ({ page }) => {
		await page.setViewportSize(size);
		await settledAt(page, 'idle');
		await emit(page, 'tool.start', { tool_id: 't1', name: 'ha_list_entities', context: 'sensor', args: { domain: 'sensor' } });
		await emit(page, 'tool.complete', { tool_id: 't1', duration: 0.4 });
		await emit(page, 'tool.start', { tool_id: 't2', name: 'web_search', context: 'weather tomorrow', args: { query: 'weather tomorrow' } });
		await emit(page, 'tool.complete', { tool_id: 't2', duration: 1.1 });
		const urls = ['https://www.bbc.co.uk/weather/2643743', 'https://weather.com/today'];
		await emit(page, 'tool.start', { tool_id: 't3', name: 'web_extract', context: urls[0], args: { urls, char_limit: 4000 } });
		await settledAt(page, 'thinking');
		const steps = page.getByTestId('stage-steps');
		await expect(steps).toHaveCount(1);
		await expect(steps.locator('.what')).toHaveText(["Checked the house's devices · sensor", 'Searched the web · weather tomorrow', 'Reading bbc.co.uk, weather.com']);
		await expect(page.getByTestId('stage-projection')).toHaveCount(1);
		await expect(page.getByTestId('stage')).not.toContainText(/char_limit|urls:|HA LIST/i);
		// Clear of the core's light (its particles reach about 0.3 of its box from the centre).
		const core = (await page.getByTestId('core').boundingBox())!;
		const box = (await page.getByTestId('stage-projection').boundingBox())!;
		expect(box.x).toBeGreaterThan(core.x + core.width / 2 + core.width * 0.3);
		expect(box.x + box.width).toBeLessThanOrEqual(size.width);
		expect(box.y).toBeGreaterThanOrEqual(0);
	});
}

// The owner, 2026-11-08: "it didn't popup charts ... for example with a weather chart". The
// stage drew no `hud.projection` at all: Projected was wired only into the gallery (T-0698).
// A tool's chart comes out of the core beside it, clear of its light, inside the screen.
for (const size of [{ width: 1440, height: 900 }, { width: 1024, height: 700 }, { width: 390, height: 844 }]) {
	test(`a projected chart stands beside the core, at ${size.width}x${size.height}`, async ({ page }) => {
		await page.setViewportSize(size);
		await settledAt(page, 'idle');
		await emit(page, 'tool.start', { tool_id: 'w1', name: 'weather_forecast', args: {} });
		const chart = {
			title: 'Next 24 hours · home',
			unit: '°F',
			x: Array.from({ length: 24 }, (_, h) => `${(h + 15) % 12 || 12} ${(h + 15) % 24 < 12 ? 'AM' : 'PM'}`),
			series: [{ name: 'Temperature', points: Array.from({ length: 24 }, (_, h) => 46 - Math.abs(h - 10) / 2), tone: 'accent' }]
		};
		await emit(page, 'hud.projection', { id: 'weather', type: 'line', data: chart, origin: 'agent' });
		await settledAt(page, 'thinking');
		const visual = page.getByTestId('stage-visual');
		await expect(visual).toHaveCount(1);
		await expect(visual.locator('[data-state="ready"][data-type="line"]')).toHaveCount(1);
		await expect(visual).toContainText('Next 24 hours · home');
		// The same id replaces it: still one chart.
		await emit(page, 'hud.projection', { id: 'weather', type: 'line', data: { ...chart, title: 'Next 24 hours · Oslo' }, origin: 'agent' });
		await expect(visual).toHaveCount(1);
		await expect(visual).toContainText('Oslo');
		await expect(page.getByTestId('stage-steps')).toHaveCount(1);
		await page.waitForTimeout(800);
		const box = (await visual.boundingBox())!;
		expect(box.x).toBeGreaterThanOrEqual(0);
		expect(box.x + box.width).toBeLessThanOrEqual(size.width);
		expect(box.y + box.height).toBeLessThanOrEqual(size.height);
		const core = (await page.getByTestId('core').boundingBox())!;
		if (size.width > 720) {
			// Clear of the core's light (its particles reach about 0.3 of its box from the centre).
			const centre = core.x + core.width / 2;
			const clear = core.width * 0.3;
			expect(box.x + box.width < centre - clear || box.x > centre + clear).toBe(true);
			// Wide enough to read as a chart, not a chip.
			expect(box.width).toBeGreaterThan(260);
		} else {
			// Under the core on a phone, the whole chart on screen: not clipped by the stack's scroll box.
			expect(box.y).toBeGreaterThan(core.y + core.height / 2);
			const stack = (await page.locator('.slots').boundingBox())!;
			expect(box.y + box.height).toBeLessThanOrEqual(stack.y + stack.height + 1);
		}
		await page.screenshot({ path: test.info().outputPath(`projected-chart-${size.width}x${size.height}.png`) });
	});
}

// T-1472: the stage drew a visual only while a turn ran or its answer was spoken. With speech
// off a list left the screen as the model finished, and a frame another session shared with
// a resting console (T-0785: the voice client's turn, the car's) was filed and never drawn.
const SHOPPING = { title: 'Shopping', items: [{ text: 'milk' }, { text: 'eggs' }] };

test('a projection sent to a console at rest is drawn, redrawn in place, and Escape sends it back', async ({ page }) => {
	await settledAt(page, 'idle');
	// No turn runs here: the frame is another session's.
	await emit(page, 'hud.projection', { id: 'list.a', type: 'list', data: SHOPPING, origin: 'agent' });
	await settledAt(page, 'answer');
	const visual = page.getByTestId('stage-visual');
	await expect(visual).toHaveCount(1);
	await expect(visual).toContainText('eggs');
	// The core is at rest behind it, not speaking.
	await expect(page.getByTestId('core')).toHaveAttribute('data-state', 'idle');
	// "Add bread", said elsewhere: the same id, the same place.
	await emit(page, 'hud.projection', { id: 'list.a', type: 'list', data: { ...SHOPPING, items: [...SHOPPING.items, { text: 'bread' }] }, origin: 'agent' });
	await expect(visual).toHaveCount(1);
	await expect(visual).toContainText('bread');
	await page.screenshot({ path: test.info().outputPath('held-at-rest.png') });

	await page.keyboard.press('Escape');
	await settledAt(page, 'idle');
	await expect(visual).toHaveCount(0);
	// Sent back is not gone for good: the next change to the list is needed again.
	await emit(page, 'hud.projection', { id: 'list.a', type: 'list', data: { ...SHOPPING, items: [{ text: 'jam' }] }, origin: 'agent' });
	await settledAt(page, 'answer');
	await expect(visual).toContainText('jam');
});

test('what a turn drew is still out at rest once the turn is over, and the next turn takes it back', async ({ page }) => {
	await settledAt(page, 'idle');
	await emit(page, 'message.start', {});
	await emit(page, 'tool.start', { tool_id: 'l1', name: 'lists', args: {} });
	await emit(page, 'hud.projection', { id: 'list.a', type: 'list', data: SHOPPING, origin: 'agent' });
	await settledAt(page, 'thinking');
	await expect(page.getByTestId('stage-visual')).toHaveCount(1);
	// The turn ends: the tool, then the reply. Once nothing is going on the core is at rest,
	// and the list is still out.
	await emit(page, 'tool.complete', { tool_id: 'l1', duration: 0.2 });
	await emit(page, 'message.complete', { text: 'Here it is.' });
	await expect(page.getByTestId('shell')).toHaveAttribute('data-reactor', 'idle', { timeout: 10_000 });
	await settledAt(page, 'answer');
	await expect(page.getByTestId('core')).toHaveAttribute('data-state', 'idle');
	await expect(page.getByTestId('stage-visual')).toContainText('milk');
	await expect(page.getByTestId('stage-steps')).toHaveCount(0);
	// The person asks for something else: the stage is theirs again.
	await emit(page, 'message.start', {});
	await settledAt(page, 'thinking');
	await expect(page.getByTestId('stage-visual')).toHaveCount(0);
});

/** A finger dragged from the middle of `selector` down by `dy` px, in four moves. */
async function dragDown(page: import('@playwright/test').Page, selector: string, dy: number): Promise<void> {
	await page.evaluate(
		async ([sel, by]) => {
			const el = document.querySelector(sel as string)!;
			const r = el.getBoundingClientRect();
			const x = r.x + r.width / 2;
			const y = r.y + r.height / 2;
			const fire = (target: EventTarget, type: string, at: number) =>
				target.dispatchEvent(new PointerEvent(type, { bubbles: true, pointerType: 'touch', pointerId: 7, clientX: x, clientY: at }));
			fire(el, 'pointerdown', y);
			for (let i = 1; i <= 4; i++) {
				// Slow, so only the distance decides: a flick is the unit test's.
				await new Promise((done) => setTimeout(done, 120));
				fire(window, 'pointermove', y + ((by as number) * i) / 4);
			}
			await new Promise((done) => setTimeout(done, 120));
			fire(window, 'pointerup', y + (by as number));
		},
		[selector, dy]
	);
}

test('a phone menu has one frame, and dragging it down closes it', async ({ page }) => {
	await page.setViewportSize({ width: 390, height: 844 });
	await openShell(page);
	const frame = (testid: string) =>
		page.getByTestId(testid).evaluate((el) => {
			const s = getComputedStyle(el);
			return [s.backgroundColor, s.borderTopLeftRadius, s.borderTopColor, getComputedStyle(el, '::after').inlineSize];
		});

	await page.getByRole('button', { name: 'All apps' }).click();
	const launcher = page.getByTestId('launcher');
	await expect(launcher).toBeVisible();
	// Let go early and it stays; past a quarter of its height it closes.
	await dragDown(page, '[data-testid="launcher"] [data-drag]', 60);
	await expect(launcher).toBeVisible();
	await dragDown(page, '[data-testid="launcher"] [data-drag]', 220);
	await expect(launcher).toBeHidden();

	await page.goto('/settings');
	const panel = page.getByTestId('side-panel');
	await expect(panel).toBeVisible();
	await page.waitForFunction(() => document.querySelector('[data-testid="side-panel"]')?.getAnimations().length === 0);
	await page.getByRole('button', { name: 'All apps' }).click();
	await expect(launcher).toBeVisible();
	// Both read in each poll: a sample of the panel kept from before was taken while its
	// ground was still fading in on a loaded runner, and nothing could ever equal it.
	await expect
		.poll(async () => JSON.stringify(await frame('launcher')) === JSON.stringify(await frame('side-panel')))
		.toBe(true);
	expect((await frame('launcher'))[3]).not.toBe('auto');
	await page.keyboard.press('Escape');
	await expect(launcher).toBeHidden();
	await dragDown(page, '[data-testid="side-panel"] [data-drag]', 220);
	await expect(panel).toBeHidden();
	await expect(page).toHaveURL(/\/$/);
});

test('the phone core at the top is smaller than in the middle, and is drawn at its own size there', async ({ page }) => {
	await page.setViewportSize({ width: 390, height: 844 });
	await openShell(page);
	const canvas = () =>
		// The one that is laid out: a second renderer's canvas can sit beside it with no box.
		page.locator('.room .core canvas').evaluateAll((all: HTMLCanvasElement[]) => {
			const c = all.reduce((a, b) => (b.offsetWidth > a.offsetWidth ? b : a));
			return { px: c.width, css: c.offsetWidth, dpr: Math.min(3, devicePixelRatio) };
		});
	// The middle core, once laid out: read too early it has no size to shrink from.
	// The size the poll accepted is the one kept: a second read can land while the renderer
	// swaps its canvas and see width 0, and "smaller than 0" then fails (pipeline 834).
	let middle = await canvas();
	await expect.poll(async () => (middle = await canvas()).css).toBeGreaterThan(200);
	await page.getByRole('button', { name: 'All apps' }).click();
	await page.getByTestId('launcher').getByRole('link', { name: /Settings/ }).click();
	await expect(page.getByTestId('side-panel')).toBeVisible();
	await page.waitForFunction(() => document.querySelector('[data-testid="side-panel"]')?.getAnimations().length === 0);
	// That it glides there without a jump is side-panel.spec's (T-0974); here, what it is at the top.
	await expect.poll(async () => (await canvas()).css).toBeLessThan(middle.css);
	// Not a handful of dots: most of the box's height, and a backing store of its own size
	// (polled: the renderer resizes a frame after the box does).
	await expect
		.poll(async () => {
			const top = await canvas();
			return top.css >= 140 && Math.abs(top.px - Math.round(top.css * top.dpr)) <= 1;
		})
		.toBe(true);
});
