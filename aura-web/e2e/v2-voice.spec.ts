// The v2 shell's voice: what the legacy dock's specs held, on the core.
//
// - Barge-in (JARVIS_V3_BRIEF PART 2: "cuts CosyVoice audio within 200 ms"): a
//   press while a reply is spoken stops the player synchronously before the
//   recorder opens, measured from the keydown to the shell's `data-reactor`
//   leaving `speaking`, by an observer in the page.
// - The streamed route (the owner, 2026-09-10: "I want it to be almost
//   instant"): a reply is spoken as PCM pieces, and by the whole-line route
//   when the voice cannot stream.
// - `hud.say` (a typed "can you say it" was answered with a file path): spoken
//   on any screen, every sentence, in the named voice.
// - The open mic (the owner, 2026-09-09 and 2026-09-18: "a microphone (that is
//   open) and can just be muted"): open from the first frame, an utterance sent
//   when it ends, quiet once muted, the mute remembered; and a microphone that
//   refuses leaves the page working.
// - The persona switch (ADR-0043): the name and the voice follow it, each way,
//   from Settings and from elsewhere, and a reload keeps it.
//
// - The core wears the persona's hue at rest and its own colour while it waits on a
//   person (T-0807, tests/contracts/persona.json calm_states).
//
// Does not cover: the phone's own voice path (android-app/tools/hermes_say_test.py).
import { expect, test, type Page } from '@playwright/test';

import { SNAPSHOT_APPROVAL, emit, micAsShipped, openShell, say, settledAt, spoken } from './v2';

const shell = (page: Page) => page.getByTestId('shell');
const speech = (page: Page, data: Record<string, unknown>) => page.request.post('/hermes/api/test/speech', { data });

test.afterEach(async ({ page }) => {
	await speech(page, { seconds: 0 });
});

test('a press while a reply is spoken cuts the audio within 200 ms', async ({ page }) => {
	await page.addInitScript(() => {
		const w = window as unknown as { __barge: { keyAt: number; changes: { at: number; to: string }[] } };
		w.__barge = { keyAt: 0, changes: [] };
		window.addEventListener(
			'keydown',
			(e) => {
				if (e.code === 'Space' && !w.__barge.keyAt) w.__barge.keyAt = performance.now();
			},
			true
		);
		new MutationObserver(() => {
			const to = document.querySelector('[data-testid="shell"]')?.getAttribute('data-reactor') ?? '';
			const last = w.__barge.changes[w.__barge.changes.length - 1];
			if (!last || last.to !== to) w.__barge.changes.push({ at: performance.now(), to });
		}).observe(document, { subtree: true, attributes: true, attributeFilter: ['data-reactor'] });
	});
	await openShell(page);
	await expect(shell(page)).toHaveAttribute('data-reactor', 'idle', { timeout: 15_000 });
	// The mock's clips are four seconds long, so the reply is still being spoken when the key goes down.
	await speech(page, { seconds: 4 });
	await say(page, 'turn on the lab lights');
	await expect(shell(page)).toHaveAttribute('data-reactor', 'speaking', { timeout: 20_000 });
	// Space is push-to-talk wherever it would not type: off the composer first, as a person does.
	await page.getByTestId('chat-input').blur();
	await page.keyboard.down('Space');
	await expect(shell(page)).toHaveAttribute('data-reactor', 'listening', { timeout: 5_000 });
	const seen = await page.evaluate(() => (window as unknown as { __barge: { keyAt: number; changes: { at: number; to: string }[] } }).__barge);
	await page.keyboard.up('Space');
	const cut = seen.changes.find((c) => c.at >= seen.keyAt && c.to !== 'speaking');
	expect(cut, 'the core left speaking after the press').toBeTruthy();
	const ms = cut!.at - seen.keyAt;
	test.info().annotations.push({ type: 'barge-in', description: `${ms.toFixed(1)} ms from keydown to the audio stopping` });
	expect(ms).toBeLessThan(200);
});

test('a reply is spoken from the streamed route, and the whole-line route when the voice cannot stream', async ({ page }) => {
	await openShell(page);
	await expect(shell(page)).toHaveAttribute('data-reactor', 'idle', { timeout: 15_000 });
	await speech(page, { seconds: 1, stream: true });
	const start = await spoken(page);
	await say(page, 'turn on the lab lights');
	await expect(shell(page)).toHaveAttribute('data-reactor', 'speaking', { timeout: 20_000 });
	await expect.poll(async () => (await spoken(page)).streamed.length, { timeout: 10_000 }).toBeGreaterThan(start.streamed.length);
	const first = await spoken(page);
	expect(first.spoken.length - start.spoken.length, 'every line spoken was streamed').toBe(first.streamed.length - start.streamed.length);

	// The voice that does not stream: the same lines, by the whole-line route.
	await speech(page, { seconds: 1, stream: false });
	await expect(shell(page)).not.toHaveAttribute('data-reactor', 'speaking', { timeout: 20_000 });
	await say(page, 'turn on the lab lights again');
	await expect(shell(page)).toHaveAttribute('data-reactor', 'speaking', { timeout: 20_000 });
	await expect.poll(async () => (await spoken(page)).spoken.length, { timeout: 10_000 }).toBeGreaterThan(first.spoken.length);
	expect((await spoken(page)).streamed.length, 'nothing more was streamed').toBe(first.streamed.length);
});

test('hud.say is spoken on a full screen too, every sentence, in the named voice', async ({ page }) => {
	// The chat screen does not read replies aloud unless asked: hud.say is the person asking.
	await openShell(page, '/chat');
	await expect(page.getByTestId('screen-chat')).toBeVisible();
	const before = (await spoken(page)).spoken.length;
	await emit(page, 'hud.say', { text: 'Bitcoin is down two percent. The bond yield spiked.', voice: 'aura' });
	await expect(shell(page)).toHaveAttribute('data-reactor', 'speaking', { timeout: 10_000 });
	await expect.poll(async () => (await spoken(page)).spoken.length, { timeout: 10_000 }).toBe(before + 2);
	const said = await spoken(page);
	expect(said.spoken.slice(before)).toEqual(['Bitcoin is down two percent.', 'The bond yield spiked.']);
	expect(said.voices.slice(before)).toEqual(['aura', 'aura']);
});

test('the mic is open from the first frame, sends an utterance when it ends, and is quiet once muted', async ({ page }) => {
	// The fake microphone plays a sentence and a pause on a loop (playwright.config.ts).
	await micAsShipped(page);
	await openShell(page);
	await expect(shell(page)).toHaveAttribute('data-reactor', /idle|listening/, { timeout: 15_000 });
	const before = (await spoken(page)).transcripts;

	// Open, and nothing was clicked: the button is a mute, not pressed.
	const mic = page.getByTestId('mic');
	await expect(mic).toHaveAccessibleName('Mute the microphone');
	await expect(mic).toHaveAttribute('aria-pressed', 'false');
	await expect(mic).not.toHaveAttribute('data-muted');
	// The core hears the sentence: it listens while it records, and sends it when the pause comes.
	await expect(shell(page)).toHaveAttribute('data-reactor', 'listening', { timeout: 10_000 });
	await expect.poll(async () => (await spoken(page)).transcripts, { timeout: 15_000 }).toBeGreaterThan(before);
	// Still open: the next sentence is heard too.
	const one = (await spoken(page)).transcripts;
	await expect.poll(async () => (await spoken(page)).transcripts, { timeout: 15_000 }).toBeGreaterThan(one);

	// Muted: whatever is in flight finishes, then nothing more is sent.
	await mic.click();
	await expect(mic).toHaveAttribute('aria-pressed', 'true');
	await expect(mic).toHaveAttribute('data-muted', 'true');
	await page.waitForTimeout(4_000);
	const closed = (await spoken(page)).transcripts;
	await page.waitForTimeout(4_000);
	expect((await spoken(page)).transcripts).toBe(closed);
	// Remembered per browser: the mute survives a reload.
	await page.reload();
	await expect(page.getByTestId('mic')).toHaveAttribute('aria-pressed', 'true', { timeout: 15_000 });
});

test('the name alone wakes the core: it listens for the command and sends nothing until it comes (T-0769)', async ({ page }, info) => {
	// The owner, 2026-11-09: "I say the wake word on the console and it doesn't go into a listening
	// state". The fake microphone loops a sentence; the transcriber is answered here, as Whisper
	// writes the name, then as a command.
	const frames: string[] = [];
	page.on('websocket', (ws) => ws.on('framesent', (f) => frames.push(String(f.payload))));
	let said = 'Hey, Jarvis.';
	let asked = 0;
	await page.route('**/hermes/api/audio/transcribe', async (route) => {
		asked += 1;
		await route.fulfill({ json: { ok: true, transcript: said, speaker: null } });
	});
	await micAsShipped(page);
	await openShell(page);
	const submits = () => frames.filter((f) => f.includes('"prompt.submit"')).length;
	await expect.poll(() => asked, { timeout: 20_000 }).toBeGreaterThan(1);
	expect(submits()).toBe(0);
	// Awake: the core listens through the pauses between sentences, not only while one is heard
	// (it thinks while the next one is transcribed); asleep, it would rest in idle there.
	const seen = new Set<string>();
	for (let i = 0; i < 30; i++) {
		seen.add(String(await shell(page).getAttribute('data-reactor')));
		await page.waitForTimeout(100);
	}
	expect([...seen]).not.toContain('idle');
	await page.screenshot({ path: info.outputPath('awake.png') });

	said = 'Turn on the den lamp.';
	await expect.poll(submits, { timeout: 20_000 }).toBeGreaterThan(0);
	expect(frames.find((f) => f.includes('"prompt.submit"'))).toContain('den lamp');
});

// A device with no microphone, or a denied permission: the effect that
// restored the open mic re-ran on its own failure until Svelte stopped the
// page, and Settings would not open (production, 2026-09-25).
test('a microphone that refuses leaves the page working: no effect loop, Settings still opens', async ({ page }) => {
	const errors: string[] = [];
	page.on('pageerror', (e) => errors.push(String(e)));
	await page.addInitScript(() => {
		localStorage.setItem('jarvis:open-mic', '1');
		navigator.mediaDevices.getUserMedia = () => Promise.reject(new DOMException('Permission denied', 'NotAllowedError'));
	});
	await openShell(page);
	await settledAt(page, 'idle');
	await page.waitForTimeout(1500);
	// Drawn muted, with the reason as its title.
	await expect(page.getByTestId('mic')).toHaveAttribute('aria-pressed', 'true');
	await expect(page.getByTestId('mic')).toHaveAttribute('title', /.+/);
	await page.getByRole('button', { name: 'All apps' }).click();
	await page.getByTestId('launcher').getByRole('link', { name: /^Settings/ }).click();
	await expect(page.getByTestId('screen-settings')).toBeVisible();
	expect(errors, 'no page error').toEqual([]);
});

/** The persona Settings has pressed, and the name the top bar wears. */
const pick = (page: Page, name: string) => page.getByTestId('settings-persona-pick').getByRole('button', { name, exact: true });
const mark = (page: Page) => page.getByRole('link', { name: /, the core$/ });

test('switching to AURA and back: the name and the voice follow, nothing stale', async ({ page }) => {
	// The switch goes through the plugin (`/persona`, the mock's copy) and comes
	// back as `persona.switch`: nothing is assumed locally.
	await openShell(page, '/settings');
	await expect(mark(page)).toHaveText('JARVIS');
	await pick(page, 'AURA').click();
	await expect(mark(page)).toHaveText('AURA', { timeout: 10_000 });
	await expect(pick(page, 'AURA')).toHaveAttribute('aria-pressed', 'true');

	// A reply on the core is spoken in AURA's voice now.
	await mark(page).click();
	await expect(page.getByTestId('stage')).toBeVisible();
	const before = (await spoken(page)).voices.length;
	await say(page, 'who are you?');
	await expect.poll(async () => (await spoken(page)).voices.length, { timeout: 15_000 }).toBeGreaterThan(before);
	expect((await spoken(page)).voices.slice(before).every((v) => v === 'aura'), "every line in AURA's voice").toBe(true);

	// And back, the same way.
	await expect(shell(page)).not.toHaveAttribute('data-reactor', 'speaking', { timeout: 20_000 });
	await page.goto('/settings');
	await pick(page, 'JARVIS').click();
	await expect(mark(page)).toHaveText('JARVIS', { timeout: 10_000 });
	await mark(page).click();
	const back = (await spoken(page)).voices.length;
	await say(page, 'who are you now?');
	await expect.poll(async () => (await spoken(page)).voices.length, { timeout: 15_000 }).toBeGreaterThan(back);
	expect((await spoken(page)).voices.slice(back).every((v) => v === 'jarvis'), "every line in JARVIS's voice").toBe(true);
});

test('a switch made elsewhere reaches this console too, and a reload keeps it', async ({ page }) => {
	await openShell(page, '/settings');
	// The phone said "switch to AURA": the plugin broadcasts, this screen follows.
	await emit(page, 'persona.switch', { active: 'aura', display: 'AURA', voice: 'aura', wake_word: 'aura', pool: ['jarvis'], previous: 'jarvis', changed: true, at: '' });
	await expect(mark(page)).toHaveText('AURA', { timeout: 10_000 });
	await expect(pick(page, 'AURA')).toHaveAttribute('aria-pressed', 'true');
	// And back, from the phone again. The mock's /api/status still says JARVIS (the
	// events bypassed its /persona), so a reload is the honest test of the status
	// path: switch through it.
	await emit(page, 'persona.switch', { active: 'jarvis', display: 'JARVIS', voice: 'jarvis', wake_word: 'jarvis', pool: ['aura'], previous: 'aura', changed: true, at: '' });
	await expect(mark(page)).toHaveText('JARVIS', { timeout: 10_000 });
	await pick(page, 'AURA').click();
	await expect(mark(page)).toHaveText('AURA', { timeout: 10_000 });
	await page.reload();
	await expect(mark(page)).toHaveText('AURA', { timeout: 15_000 });
	// Put the mock back for the next spec.
	await pick(page, 'JARVIS').click();
	await expect(mark(page)).toHaveText('JARVIS', { timeout: 10_000 });
});

test('the core wears the persona’s hue at rest, and its own colour while it waits on a person', async ({ page }) => {
	// T-0807: under another persona the phone's reactor was pink and the console's core cyan.
	await openShell(page, '/');
	const tone = () => page.getByTestId('core').first().evaluate((el) => (el as HTMLElement).style.getPropertyValue('--tone'));
	await expect.poll(tone).toBe('var(--jv-accent)');
	await emit(page, 'persona.switch', { active: 'aura', display: 'AURA', voice: 'aura', wake_word: 'aura', pool: ['jarvis'], previous: 'jarvis', changed: true, at: '' });
	await expect.poll(tone, { timeout: 10_000 }).toBe('var(--jv-persona-aura)');
	// And the composer asks AURA, not JARVIS.
	await expect(page.getByTestId('chat-input')).toHaveAttribute('placeholder', 'Ask AURA anything');
	await page.waitForTimeout(1500); // the colour tweens over about a second
	await page.screenshot({ path: test.info().outputPath('core-aura.png') });
	// An approval is information: its colour, not AURA's (an approval in a persona's hue is one nobody notices).
	await emit(page, 'approval.request', SNAPSHOT_APPROVAL);
	await expect.poll(tone, { timeout: 10_000 }).not.toBe('var(--jv-persona-aura)');
	await emit(page, 'persona.switch', { active: 'jarvis', display: 'JARVIS', voice: 'jarvis', wake_word: 'jarvis', pool: ['aura'], previous: 'aura', changed: true, at: '' });
});
