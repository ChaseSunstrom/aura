// The lock, first run, people and enrolment on the v2 shell (rewritten from
// hud-lock.spec.ts and hud-setup.spec.ts, every assertion kept): "Lock now" from
// Settings, the wrong password refused and the right one opening; an enrolled
// voice opening it with voice unlock on; the idle timer; a browser with no
// session meeting the lock as its sign-in (T-0387); the setup sheet's refusals
// and its "later"; People naming, levelling, enrolling and forgetting; and the
// lock's custom minutes. Added: nothing behind the lock answers a shortcut.
import { expect, test, type Page } from '@playwright/test';

import { micAsShipped, openShell, spoken } from './v2';

const PASSWORD = 'e2e-console-password';

/** A household on the mock (or, with `speaker_reset`, the owner alone), through the house socket's test hook. */
const household = (page: Page, hook = 'speaker_household') =>
	page.evaluate(async (hook) => {
		const socket = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws`);
		await new Promise((resolve) => socket.addEventListener('open', resolve));
		socket.send(JSON.stringify({ id: 1, type: `jarvis/test/${hook}` }));
		await new Promise((resolve) => setTimeout(resolve, 300));
		socket.close();
	}, hook);

async function open(page: Page, path = '/settings'): Promise<void> {
	await page.setViewportSize({ width: 1440, height: 900 });
	// A console password exists: chosen if this machine has none yet, verified otherwise.
	const res = await page.request.post('/api/console', { data: { password: PASSWORD } });
	expect(res.ok(), 'the console password is set for the run').toBe(true);
	await openShell(page, path);
}

test('a console that reloads signed out answers no shortcut before its lock is up', async ({ page }) => {
	// CI, 2026-11-12 (pipeline 836): the palette stood open above the lock. "Lock now" reloads
	// the page, and until its first /api/console answer the reloaded shell did not know it was
	// locked. Held here, so the key lands in that gap every time.
	await open(page);
	await expect(page.getByTestId('lock-now')).toBeEnabled({ timeout: 10_000 });
	let release = (): void => undefined;
	const held = new Promise<void>((resolve) => (release = resolve));
	await page.route('**/api/console', async (route) => {
		if (route.request().method() === 'GET') await held;
		await route.continue();
	});
	const reloaded = page.waitForEvent('load');
	await page.getByTestId('lock-now').click();
	await reloaded;
	// The shell is drawn and listening, and has not been told yet.
	await expect(page.getByRole('button', { name: 'Search or command' })).toBeVisible({ timeout: 15_000 });
	await page.keyboard.press('Control+k');
	await expect(page.getByTestId('palette')).toBeHidden();
	release();
	await expect(page.getByTestId('lock-screen')).toBeVisible({ timeout: 10_000 });
	await expect(page.getByTestId('palette')).toBeHidden();
	await page.unroute('**/api/console');
	await page.getByTestId('lock-password').fill(PASSWORD);
	await page.getByTestId('lock-unlock').click();
	await expect(page.getByTestId('lock-screen')).toHaveCount(0, { timeout: 10_000 });
});

test('lock now, the wrong password refused, the right one opens', async ({ page }) => {
	await open(page);
	await expect(page.getByTestId('lock-now')).toBeEnabled({ timeout: 10_000 });
	await page.getByTestId('lock-now').click();
	const lock = page.getByTestId('lock-screen');
	await expect(lock).toBeVisible();
	await expect(page.getByTestId('lock-voice')).toHaveCount(0);
	// T-0946: the lock is the server's too. The locked tab's cookie opens nothing.
	await expect.poll(async () => (await page.request.get('/api/state')).status(), { timeout: 10_000 }).toBe(401);
	// Nothing behind it answers: not Escape, not ⌘K, not the launcher.
	await page.keyboard.press('Escape');
	await page.keyboard.press('Control+k');
	await expect(lock).toBeVisible();
	await expect(page.getByTestId('palette')).toBeHidden();
	// A modal dialog: the console behind it is inert, so the launcher's button takes no press.
	expect(await lock.evaluate((el) => el.matches(':modal'))).toBe(true);
	await expect(page.getByRole('button', { name: 'All apps' }).click({ timeout: 1_000 })).rejects.toThrow();
	await expect(page.getByTestId('launcher')).toBeHidden();
	await page.getByTestId('lock-password').fill('not-the-password');
	await page.getByTestId('lock-unlock').click();
	await expect(page.getByTestId('lock-said')).toContainText('not', { timeout: 10_000 });
	await expect(lock).toBeVisible();
	await page.getByTestId('lock-password').fill(PASSWORD);
	await page.getByTestId('lock-unlock').click();
	await expect(lock).toHaveCount(0, { timeout: 10_000 });
	await expect(page.getByRole('button', { name: 'All apps' })).toBeVisible();
});

// T-0946, in a real browser against Chromium's virtual authenticator (a platform
// authenticator that verifies its user): added in Settings, it opens the locked
// console with no password typed. On `localhost`, because a passkey belongs to a
// named site and 127.0.0.1 is not one. Does not cover a real fingerprint reader.
test('a passkey added in Settings opens the locked console, and can be removed', async ({ page, baseURL }) => {
	const origin = String(baseURL).replace('127.0.0.1', 'localhost');
	const cdp = await page.context().newCDPSession(page);
	await cdp.send('WebAuthn.enable');
	await cdp.send('WebAuthn.addVirtualAuthenticator', {
		options: {
			protocol: 'ctap2',
			transport: 'internal',
			hasResidentKey: true,
			hasUserVerification: true,
			isUserVerified: true,
			automaticPresenceSimulation: true
		}
	});
	await page.setViewportSize({ width: 1440, height: 900 });
	expect((await page.request.post(`${origin}/api/console`, { data: { password: PASSWORD } })).ok()).toBe(true);
	await openShell(page, `${origin}/settings`);

	await page.getByTestId('passkeys-open').click();
	await page.getByTestId('passkey-name').fill('Test bench');
	await page.getByTestId('passkey-add').click();
	await expect(page.getByTestId('passkey-said')).toContainText('Added', { timeout: 10_000 });
	await expect(page.getByTestId('passkeys')).toContainText('Test bench');

	await page.getByTestId('lock-now').click();
	const lock = page.getByTestId('lock-screen');
	await expect(page.getByTestId('lock-passkey')).toBeVisible({ timeout: 15_000 });
	expect((await page.request.get(`${origin}/api/state`)).status()).toBe(401);
	await page.getByTestId('lock-passkey').click();
	await expect(lock).toHaveCount(0, { timeout: 15_000 });
	expect((await page.request.get(`${origin}/api/state`)).status()).not.toBe(401);

	// Removed, so the next spec's lock asks for the password alone.
	await expect(page.getByTestId('shell')).toHaveAttribute('data-ready', 'true', { timeout: 15_000 });
	await page.getByTestId('passkey-remove').click();
	await expect(page.getByTestId('passkey-said')).toContainText('Removed', { timeout: 10_000 });
	await expect(page.getByTestId('passkey-remove')).toHaveCount(0);
});

test('with a voice enrolled and voice unlock on, the voice opens it', async ({ page }) => {
	await open(page);
	await household(page);
	await page.getByTestId('lock-voice-toggle').click();
	await expect(page.getByTestId('lock-voice-toggle')).toHaveAttribute('aria-checked', 'true');
	await page.getByTestId('lock-now').click();
	await expect(page.getByTestId('lock-screen')).toBeVisible();
	await expect(page.getByTestId('lock-voice')).toBeVisible({ timeout: 10_000 });
	await page.getByTestId('lock-voice').click();
	await expect(page.getByTestId('lock-screen')).toHaveCount(0, { timeout: 15_000 });
	// Off again for the next spec, and the owner alone: Ted's second "Voice enrolled" broke
	// v2-screens' Settings test whenever it ran after this file.
	await page.getByTestId('lock-voice-toggle').click();
	await expect(page.getByTestId('lock-voice-toggle')).toHaveAttribute('aria-checked', 'false');
	await household(page, 'speaker_reset');
});

test('a quiet console locks itself after the allowance, and is remembered', async ({ page }) => {
	await open(page);
	const after = page.getByTestId('lock-after');
	await after.getByRole('button', { name: '1 min' }).click();
	await expect(after.getByRole('button', { name: '1 min' })).toHaveAttribute('aria-pressed', 'true');
	await page.goto('/');
	await expect(page.getByTestId('shell')).toBeVisible();
	// The timer reads the clock: a minute is skipped by moving the clock, not by waiting.
	await page.evaluate(() => {
		const real = Date.now;
		Date.now = () => real() + 61_000;
	});
	await expect(page.getByTestId('lock-screen')).toBeVisible({ timeout: 15_000 });
	await page.getByTestId('lock-password').fill(PASSWORD);
	await page.getByTestId('lock-unlock').click();
	await expect(page.getByTestId('lock-screen')).toHaveCount(0, { timeout: 10_000 });
	await openShell(page, '/settings');
	await expect(page.getByTestId('lock-after').getByRole('button', { name: '1 min' })).toHaveAttribute('aria-pressed', 'true');
	await page.getByTestId('lock-after').getByRole('button', { name: 'Never' }).click();
});

// T-0387: the LAN reaches the console, and its relays act as the owner. A browser
// with no session is refused them, meets the lock (the password only: a voice
// opens no session), and is let in by the password, after a reload that dials
// the refused sockets again.
test('a browser that has not signed in is refused the relays and meets the lock', async ({ browser, baseURL }) => {
	const context = await browser.newContext({
		storageState: { cookies: [], origins: [{ origin: String(baseURL), localStorage: [{ name: 'jarvis:open-mic', value: '0' }] }] }
	});
	const page = await context.newPage();
	expect((await page.request.get('/hermes/api/status')).status()).toBe(401);
	expect((await page.request.get('/api/console')).status()).toBe(200);
	await page.setViewportSize({ width: 1440, height: 900 });
	await page.goto('/');
	const lock = page.getByTestId('lock-screen');
	await expect(lock).toBeVisible({ timeout: 15_000 });
	await expect(page.getByTestId('lock-voice')).toHaveCount(0);
	await page.getByTestId('lock-password').fill(PASSWORD);
	await page.getByTestId('lock-unlock').click();
	await expect(page.getByTestId('shell')).toBeVisible({ timeout: 15_000 });
	await expect(lock).toHaveCount(0, { timeout: 15_000 });
	expect((await page.request.get('/hermes/api/status')).status()).toBe(200);
	await context.close();
});

test('the setup sheet asks for a name and a password, refuses less, and can be left for later', async ({ page }) => {
	await open(page, '/?setup=1');
	const sheet = page.getByTestId('setup-screen');
	await expect(sheet).toBeVisible();
	await expect(page.getByTestId('setup-go')).toBeDisabled();
	await page.getByTestId('setup-name').fill('Vera');
	await page.getByTestId('setup-password').fill('short');
	await expect(page.getByTestId('setup-said')).toContainText('at least');
	await page.getByTestId('setup-password').fill('a-long-enough-password');
	await page.getByTestId('setup-again').fill('a-different-password');
	await expect(page.getByTestId('setup-said')).toContainText('differ');
	await page.getByTestId('setup-again').fill('a-long-enough-password');
	await expect(page.getByTestId('setup-go')).toBeEnabled();
	// Escape does not dismiss first run; "Later" does.
	await page.keyboard.press('Escape');
	await expect(sheet).toBeVisible();
	await page.getByTestId('setup-later').click();
	await expect(sheet).toHaveCount(0);
	// And the console is not first-run: the mark exists, the password exists.
	const state = await (await page.request.get('/api/console')).json();
	expect(state.firstRun).toBe(false);
});

test('Settings → People names a person, sets their level, opens their enrolment and forgets them', async ({ page }) => {
	await open(page);
	const people = page.getByTestId('settings-people');
	await expect(people).toBeVisible();
	await expect(page.getByTestId('person-owner')).toBeVisible({ timeout: 10_000 });
	await expect(page.getByTestId('level-owner').getByRole('button', { name: 'Owner' })).toHaveAttribute('aria-pressed', 'true');

	const name = `Ann ${Date.now() % 100000}`;
	await page.getByTestId('people-name').fill(name);
	await page.getByTestId('people-add').click();
	const row = page.getByTestId(`person-${name}`);
	await expect(row).toBeVisible({ timeout: 10_000 });
	await expect(row).toContainText('No voice yet');
	await expect(page.getByTestId(`level-${name}`).getByRole('button', { name: 'Member' })).toHaveAttribute('aria-pressed', 'true');

	await page.getByTestId(`level-${name}`).getByRole('button', { name: 'Guest' }).click();
	await expect(page.getByTestId(`level-${name}`).getByRole('button', { name: 'Guest' })).toHaveAttribute('aria-pressed', 'true', { timeout: 10_000 });

	// The enrolment sheet opens for that person and reads the house's phrases.
	await page.getByTestId(`enrol-${name}`).click();
	const enrol = page.getByTestId('enrol-screen');
	await expect(enrol).toBeVisible();
	await expect(enrol).toContainText(`Enrolling ${name}`);
	await expect(page.getByTestId('enrol-phrase')).toContainText('Jarvis', { timeout: 10_000 });
	await page.getByTestId('enrol-done').click();
	await expect(enrol).toBeHidden();

	await page.getByTestId(`forget-${name}`).click();
	await expect(row).toHaveCount(0, { timeout: 10_000 });
});

// The open mic went on listening behind the enrolment sheet, so every phrase read into it was
// also a turn: "set a timer for 55 minutes" set a real timer (the owner, 2026-11-09, T-0768).
test('enrolment holds the open mic: nothing read into the sheet is sent, and it listens again after', async ({ page }) => {
	await micAsShipped(page);
	await open(page);
	await expect(page.getByTestId('person-owner')).toBeVisible({ timeout: 10_000 });
	// The open mic hears the fake microphone's looping sentence.
	const before = (await spoken(page)).transcripts;
	await expect.poll(async () => (await spoken(page)).transcripts, { timeout: 15_000 }).toBeGreaterThan(before);

	await page.getByTestId('enrol-owner').click();
	await expect(page.getByTestId('enrol-screen')).toBeVisible();
	// A transcription already on its way when the sheet opened may land; then nothing more.
	await page.waitForTimeout(2_000);
	const held = (await spoken(page)).transcripts;
	await page.waitForTimeout(8_000);
	expect((await spoken(page)).transcripts, 'nothing is transcribed while enrolling').toBe(held);
	// Nor does the desktop app's push-to-talk key start a turn over the sheet.
	await page.evaluate(() => window.dispatchEvent(new CustomEvent('jarvis:push-to-talk', { detail: { down: true } })));
	await page.waitForTimeout(500);
	await expect(page.getByTestId('shell')).not.toHaveAttribute('data-reactor', 'listening');

	await page.getByTestId('enrol-done').click();
	await expect(page.getByTestId('enrol-screen')).toBeHidden();
	await expect.poll(async () => (await spoken(page)).transcripts, { timeout: 15_000 }).toBeGreaterThan(held);
});

test('the lock takes any number of minutes, not only the presets', async ({ page }) => {
	await open(page);
	const custom = page.getByTestId('lock-after-custom');
	const after = page.getByTestId('lock-after');
	await expect(custom).toBeEnabled({ timeout: 10_000 });
	await custom.fill('7');
	await custom.press('Enter');
	await expect(after.getByRole('button', { name: '5 min', exact: true })).toHaveAttribute('aria-pressed', 'false');
	await expect(custom).toHaveValue('7');
	expect(await page.evaluate(() => localStorage.getItem('jarvis:lock-after'))).toBe('7');
	// A preset lights up when it is typed, and a number past the cap is Never.
	await custom.fill('15');
	await custom.press('Enter');
	await expect(after.getByRole('button', { name: '15 min' })).toHaveAttribute('aria-pressed', 'true');
	await custom.fill('999');
	await custom.press('Enter');
	await expect(after.getByRole('button', { name: 'Never' })).toHaveAttribute('aria-pressed', 'true');
});
