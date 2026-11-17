// The installer's setup page (T-0864), driven the way a person meets it: the address the
// installer printed, six questions, Install, then the install's own progress.
//
// The page is not part of the console: deploy/setup/server.py serves it for the length of one
// install. This spec starts that server itself (python3, standard library only) on a free port
// with a scratch state folder, plays the installer's side by writing progress.json, and reads
// what the page left in answers.env. A model endpoint is a few lines of Node below: no real
// provider is ever asked.
//
// Not covered: the installer acting on the answers (deploy/tests/test_install.py), and the
// whole line on a clean machine (docs/verification/install/).
import { existsSync, readFileSync } from 'node:fs';
import { join } from 'node:path';

import { expect, test, type Page } from '@playwright/test';

import { startModelStub, startSetupServer, type ModelStub, type SetupStage } from './setup-stage';

const ADDRESS = '192.0.2.10';
const PASSWORD = ['correct', 'horse', 'battery'].join(' ');
const MODEL = 'stub-model';

let stage: SetupStage | null = null;
let state = '';
let token = '';
let origin = '';
let endpoint: ModelStub | null = null;
let endpointUrl = '';

function progress(doc: Record<string, unknown>) {
	stage!.progress(doc);
}

function answersEnv(): Record<string, string> {
	const rows: Record<string, string> = {};
	for (const line of readFileSync(join(state, 'answers.env'), 'utf8').split('\n')) {
		const at = line.indexOf('=');
		if (at > 0) rows[line.slice(0, at)] = line.slice(at + 1);
	}
	return rows;
}

async function startSetup(facts: Record<string, unknown>) {
	stage = await startSetupServer(facts);
	({ state, token, origin } = stage);
}

function stopSetup() {
	stage?.stop();
	stage = null;
	state = '';
}

test.beforeAll(async () => {
	endpoint = await startModelStub([MODEL, 'another-model']);
	endpointUrl = endpoint.url;
});

test.afterAll(async () => {
	await endpoint?.close();
});

test.beforeEach(async () => {
	await startSetup({ addresses: [ADDRESS], gpu: false, timezone: 'Etc/UTC' });
});

test.afterEach(() => stopSetup());

async function open(page: Page) {
	await page.goto(`${origin}/?t=${token}`);
	await expect(page.locator('#panel')).toHaveAttribute('data-view', 'steps');
}

async function next(page: Page) {
	await page.locator('#next').click();
}

/** Step 01 with everything it needs, left on step 02. */
async function answerServer(page: Page) {
	await page.locator('#password').fill(PASSWORD);
	await page.locator('#password2').fill(PASSWORD);
	await page.locator('#timezone').fill('Europe/Berlin');
	await next(page);
	await expect(page.locator('#t-models')).toBeFocused();
}

test('the address without its token opens nothing', async ({ page }) => {
	await page.goto(`${origin}/`);
	await expect(page.locator('#gate')).toBeVisible();
	await expect(page.locator('#gate')).toContainText('one-time token');
	await expect(page.locator('#steps')).toBeHidden();
});

test('the token leaves the address bar and survives a reload', async ({ page }) => {
	await open(page);
	expect(new URL(page.url()).search).toBe('');
	await page.reload();
	await expect(page.locator('#panel')).toHaveAttribute('data-view', 'steps');
	await expect(page.locator('#t-server')).toBeVisible();
});

test('six questions, a review, then the install as the installer reports it', async ({ page }) => {
	await open(page);

	// 01: the machine's own address is offered first and chosen; this computer only is the other.
	await expect(page.locator(`input[name="bind"][value="${ADDRESS}"]`)).toBeChecked();
	await expect(page.locator('input[name="bind"][value="127.0.0.1"]')).toHaveCount(1);
	await answerServer(page);

	// 02: no graphics card, so the local model is not offered and the endpoint is.
	await expect(page.locator('input[name="model-mode"][value="local"]')).toBeDisabled();
	await expect(page.locator('input[name="model-mode"][value="endpoint"]')).toBeChecked();
	await page.locator('#llm-url').fill(endpointUrl);
	await page.locator('#llm-key').fill(['key', 'for', 'the', 'stub'].join('-'));
	await page.locator('#test-model').click();
	await expect(page.locator('#test-result')).toHaveAttribute('data-ok', 'true');
	await expect(page.locator('#test-result')).toContainText('2 models');
	// The first model it found is filled in, and the key went to the endpoint as a bearer.
	await expect(page.locator('#llm-model')).toHaveValue(MODEL);
	expect(endpoint!.auth()).toBe('Bearer key-for-the-stub');
	await next(page);

	// 03: the voice that needs a graphics card is off; the light one is chosen.
	await expect(page.locator('#t-voice')).toBeFocused();
	await expect(page.locator('input[name="tts"][value="kokoro"]')).toBeDisabled();
	await expect(page.locator('input[name="tts"][value="piper"]')).toBeChecked();
	await page.locator('#stt-choices label', { hasText: 'base' }).click();
	await expect(page.locator('#stt-hint')).toContainText('Base');
	await next(page);

	// 04: on or off; the word is the theme's, said and not asked.
	await expect(page.locator('#t-wake')).toBeFocused();
	await expect(page.locator('#wake-on')).toBeChecked();
	await expect(page.locator('#wake-word-now')).toHaveText('Hey Jarvis');
	await page.locator('.toggle').click();
	await expect(page.locator('#wake-on')).not.toBeChecked();
	await expect(page.locator('.said')).toBeHidden();
	await page.locator('.toggle').click();
	await next(page);

	// 05: the car comes through the phone, so choosing it chooses both.
	await expect(page.locator('#t-devices')).toBeFocused();
	await page.locator('input[name="device"][value="car"]').check();
	await expect(page.locator('input[name="device"][value="phone"]')).toBeChecked();
	await expect(page.locator('#reach-warning')).toBeHidden();
	await next(page);

	// 06: the page wears the theme the moment it is picked.
	await expect(page.locator('#t-theme')).toBeFocused();
	await expect(page.locator('html')).toHaveAttribute('data-theme', 'aura');
	// Each choice is drawn in its own theme's light, whichever theme the page is wearing.
	const drawn = await page.evaluate(() =>
		Array.from(document.querySelectorAll<HTMLElement>('.look .swatch')).map((node) => getComputedStyle(node).backgroundImage)
	);
	expect(drawn).toHaveLength(2);
	for (const image of drawn) expect(image).toContain('radial-gradient');
	expect(drawn[0]).not.toBe(drawn[1]);
	const violet = await page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue('--jv-accent').trim());
	await page.locator('.look[data-look="jarvis"]').click();
	await expect(page.locator('html')).toHaveAttribute('data-theme', 'jarvis');
	await expect(page.locator('.wordmark')).toHaveText('JARVIS');
	const cyan = await page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue('--jv-accent').trim());
	expect(cyan).not.toBe(violet);
	await next(page);

	// The review says what was chosen, and never the password or the key.
	await expect(page.locator('#t-review')).toBeFocused();
	const summary = page.locator('#summary');
	await expect(summary).toContainText(ADDRESS);
	await expect(summary).toContainText('Europe/Berlin');
	await expect(summary).toContainText(MODEL);
	await expect(summary).toContainText('On: Hey Jarvis');
	await expect(summary).toContainText('Phone, Car');
	await expect(summary).toContainText('JARVIS');
	await expect(summary).not.toContainText(PASSWORD);
	await expect(summary).not.toContainText('key-for-the-stub');
	await expect(page.locator('#next')).toHaveText('Install');
	await next(page);

	// What the installer reads: values only it understands, the password as a hash.
	await expect(page.locator('#panel')).toHaveAttribute('data-view', 'install');
	const env = answersEnv();
	expect(env.SETUP_BIND).toBe(ADDRESS);
	expect(env.SETUP_TZ).toBe('Europe/Berlin');
	expect(env.SETUP_MODEL).toBe('endpoint');
	expect(env.SETUP_LLM_URL).toBe(endpointUrl);
	expect(env.SETUP_LLM_MODEL).toBe(MODEL);
	expect(env.SETUP_TTS).toBe('piper');
	expect(env.SETUP_STT).toBe('base');
	expect(env.SETUP_WAKE).toBe('on');
	expect(env.SETUP_DEVICES.split(',').sort()).toEqual(['car', 'phone']);
	expect(env.SETUP_THEME).toBe('jarvis');
	expect(env.SETUP_CONSOLE_HASH).toMatch(/^scrypt\$16384\$8\$1\$/);
	expect(readFileSync(join(state, 'answers.env'), 'utf8')).not.toContain(PASSWORD);
	// Nothing secret stays behind in the browser.
	const kept = await page.evaluate(() => JSON.stringify(Object.entries(sessionStorage)));
	expect(kept).not.toContain('correct');
	expect(kept).not.toContain('key-for-the-stub');

	// The installer's side: each phase it reports shows on the page.
	progress({ phase: 'images', state: 'running', done: ['preflight', 'setup', 'secrets', 'n8n'], detail: 'building the console' });
	await expect(page.locator('#phases li[data-phase="images"]')).toHaveAttribute('data-state', 'running');
	await expect(page.locator('#phases li[data-phase="secrets"]')).toHaveAttribute('data-state', 'done');
	await expect(page.locator('#phases li[data-phase="verify"]')).toHaveAttribute('data-state', 'waiting');
	await expect(page.locator('#install-count')).toHaveText('4/8');
	await expect(page.locator('#phase-detail')).toHaveText('building the console');
	await expect(page.locator('#done')).toBeHidden();

	const consoleUrl = `http://${ADDRESS}:8199`;
	progress({ phase: 'done', state: 'done', done: ['preflight', 'setup', 'secrets', 'n8n', 'images', 'start', 'verify', 'pair'], console: consoleUrl });
	await expect(page.locator('#done')).toBeVisible();
	await expect(page.locator('#install-count')).toHaveText('8/8');
	await expect(page.locator('#install-word')).toHaveText('Installed');
	// One child: a flex button drops the space between two, and read "OPENJARVIS".
	await expect(page.locator('#open-console > *')).toHaveCount(1);
	await expect(page.locator('#open-console')).toHaveText('Open JARVIS');
	await expect(page.locator('#t-install')).toHaveText('JARVIS is up.');
	await expect(page.locator('#open-console')).toHaveAttribute('href', consoleUrl);
	await expect(page.locator('#next-steps li')).toHaveCount(3);
	await expect(page.locator('#next-steps')).toContainText('sign in with the password you chose');
});

test('a mistake is named beside its field and nothing is written', async ({ page }) => {
	await open(page);
	await page.locator('#password').fill(PASSWORD);
	await page.locator('#password2').fill(PASSWORD + ' not');
	await next(page);
	await expect(page.locator('[data-error="server.password"]')).toHaveText('The two do not match.');
	await expect(page.locator('#password')).toHaveAttribute('aria-invalid', 'true');
	await expect(page.locator('#t-server')).toBeVisible();

	await page.locator('#password2').fill(PASSWORD);
	await page.locator('#timezone').fill('Europe/Berlin');
	await next(page);
	await next(page); // the endpoint with no address
	await expect(page.locator('[data-error="models.url"]')).not.toBeEmpty();
	await expect(page.locator('#t-models')).toBeVisible();
	expect(existsSync(join(state, 'answers.env'))).toBe(false);
});

test('what the server refuses lands on the step it belongs to', async ({ page }) => {
	await open(page);
	await answerServer(page);
	await page.locator('#llm-url').fill(endpointUrl);
	await page.locator('#llm-model').fill(MODEL);
	for (let i = 0; i < 5; i += 1) await next(page);
	await expect(page.locator('#t-review')).toBeFocused();
	// Only the server knows a time zone that does not exist; the page lets it through.
	await page.locator('#rail button[data-rail="server"]').click();
	await page.locator('#timezone').fill('Nowhere/Atlantis');
	await page.locator('#password').fill(PASSWORD);
	await page.locator('#password2').fill(PASSWORD);
	await page.locator('#rail button[data-rail="review"]').click();
	await next(page);
	await expect(page.locator('#t-server')).toBeVisible();
	await expect(page.locator('[data-error="server.timezone"]')).not.toBeEmpty();
	expect(existsSync(join(state, 'answers.env'))).toBe(false);
});

test('a phone and an address that is this computer only is said before the install', async ({ page }) => {
	await open(page);
	await page.locator('input[name="bind"][value="127.0.0.1"]').check();
	await answerServer(page);
	await page.locator('#llm-url').fill(endpointUrl);
	await page.locator('#llm-model').fill(MODEL);
	await next(page);
	await next(page);
	await next(page);
	await page.locator('input[name="device"][value="phone"]').check();
	await expect(page.locator('#reach-warning')).toBeVisible();
	await page.locator('#reach-warning [data-goto="server"]').click();
	await expect(page.locator('#t-server')).toBeFocused();
});

test('a failed install says where it stopped and that the line can be run again', async ({ page }) => {
	await open(page);
	await answerServer(page);
	await page.locator('#llm-url').fill(endpointUrl);
	await page.locator('#llm-model').fill(MODEL);
	for (let i = 0; i < 6; i += 1) await next(page);
	await expect(page.locator('#panel')).toHaveAttribute('data-view', 'install');
	progress({ phase: 'start', state: 'failed', done: ['preflight', 'setup', 'secrets', 'n8n', 'images'] });
	await expect(page.locator('#phases li[data-phase="start"]')).toHaveAttribute('data-state', 'failed');
	await expect(page.locator('#install-error')).toContainText('run the same line again');
	await expect(page.locator('#done')).toBeHidden();
});

test('a page opened after the answers were given shows the install, not the questions', async ({ page }) => {
	await open(page);
	await answerServer(page);
	await page.locator('#llm-url').fill(endpointUrl);
	await page.locator('#llm-model').fill(MODEL);
	for (let i = 0; i < 6; i += 1) await next(page);
	await expect(page.locator('#panel')).toHaveAttribute('data-view', 'install');
	await page.reload();
	await expect(page.locator('#panel')).toHaveAttribute('data-view', 'install');
	await expect(page.locator('#steps')).toBeHidden();
});

for (const width of [360, 768, 1440]) {
	test(`every step fits ${width}px with no sideways scroll`, async ({ page }) => {
		await page.setViewportSize({ width, height: width < 700 ? 760 : 900 });
		await open(page);
		const fits = () => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth);
		expect(await fits()).toBe(true);
		await answerServer(page);
		await page.locator('#llm-url').fill(endpointUrl);
		await page.locator('#llm-model').fill(MODEL);
		for (let i = 0; i < 5; i += 1) {
			expect(await fits()).toBe(true);
			// Every control a finger meets is at least the touch size (its laid-out size: a step
			// is still arriving, a little scaled, when this looks).
			const small = await page.evaluate(() =>
				Array.from(document.querySelectorAll<HTMLElement>('.button, .chip, .choice, .segment, .toggle'))
					.filter((node) => node.offsetParent !== null && node.offsetHeight < 44)
					.map((node) => node.className + ':' + node.offsetHeight)
			);
			expect(small).toEqual([]);
			await next(page);
		}
		await expect(page.locator('#t-review')).toBeFocused();
		expect(await fits()).toBe(true);
	});
}

test('reduced motion draws the core once and moves nothing', async ({ page }) => {
	await page.emulateMedia({ reducedMotion: 'reduce' });
	await open(page);
	const painted = () =>
		page.evaluate(() => {
			const canvas = document.querySelector<HTMLCanvasElement>('#core')!;
			return canvas.toDataURL().length;
		});
	const first = await painted();
	expect(first).toBeGreaterThan(5000); // a blank canvas is a few hundred characters
	const frame = () => page.evaluate(() => document.querySelector<HTMLCanvasElement>('#core')!.toDataURL());
	const a = await frame();
	await page.waitForTimeout(400);
	expect(await frame()).toBe(a);
});

test('the questions can be answered from the keyboard alone', async ({ page }) => {
	await open(page);
	await expect(page.locator('#t-server')).toBeFocused();
	await page.locator('#password').focus();
	await page.keyboard.type(PASSWORD);
	await page.keyboard.press('Tab');
	await page.keyboard.type(PASSWORD);
	// Enter in a field is Continue.
	await page.keyboard.press('Enter');
	await expect(page.locator('#t-models')).toBeFocused();
	// A focused control shows a ring.
	await page.keyboard.press('Tab');
	const ring = await page.evaluate(() => {
		const style = getComputedStyle(document.activeElement as Element);
		return style.outlineStyle !== 'none' || style.boxShadow !== 'none';
	});
	expect(ring).toBe(true);
});
