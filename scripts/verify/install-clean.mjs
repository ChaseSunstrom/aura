#!/usr/bin/env node
// The browser's half of scripts/verify/install-clean.sh (T-0864): answer the installer's setup
// page as a person would, press Install, wait for the install the page reports, then open the
// console it links to, sign in with the password chosen on the page, and ask it a question.
//
// It is given the page's address by the shell script, which read it from the installer's own
// output. Everything it needs comes from the environment:
//
//   SETUP_URL        the address the installer printed, token and all
//   SETUP_BIND       the address to choose on the first step (one the page offers)
//   SETUP_PASSWORD   the console password to choose (made up by the shell script for this run)
//   SETUP_LLM_URL    an OpenAI-compatible endpoint the page can test
//   SETUP_THEME      aura or jarvis (default aura)
//   SETUP_OUT        the folder the pictures go to
//   SETUP_WAIT_S     how long the install may take (default 5400)
//
// It prints one line per step and ends with `RESULT: installed` (exit 0) or `RESULT: failed: …`
// (exit 1). Not covered: a voice turn, pairing a phone, and what a real model would say (the
// endpoint the shell script gives it answers every question with one fixed sentence, so the
// answer proves the path from the console to the endpoint and nothing about a model).
import { createRequire } from 'node:module';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';

const require = createRequire(new URL('../../jarvis-web/package.json', import.meta.url));
const { chromium } = require('@playwright/test');

const need = (name) => {
	const value = process.env[name];
	if (!value) {
		console.log(`RESULT: failed: ${name} is not set`);
		process.exit(1);
	}
	return value;
};
const url = need('SETUP_URL');
const bind = need('SETUP_BIND');
const password = need('SETUP_PASSWORD');
const endpoint = need('SETUP_LLM_URL');
const out = need('SETUP_OUT');
const theme = process.env.SETUP_THEME || 'aura';
const waitMs = Number(process.env.SETUP_WAIT_S || 5400) * 1000;
mkdirSync(out, { recursive: true });

const say = (line) => console.log(`[page] ${line}`);
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
const shot = async (name) => {
	// A step arrives with a short move; the picture is of the step at rest.
	await page.waitForTimeout(700);
	await page.screenshot({ path: join(out, `${name}.png`) });
};
const next = () => page.locator('#next').click();
// What the core is wearing: the shell writes the reactor's state on its root. Read, not guessed
// from a picture: the first run's pictures showed the core at alert on a console nobody had
// asked anything, and a picture could not say which of three states drew it (T-1483).
const reactor = () => page.getByTestId('shell').getAttribute('data-reactor', { timeout: 5000 }).catch(() => 'unread');
const at = (id) => page.locator(id).waitFor({ state: 'visible', timeout: 15_000 });

try {
	await page.goto(url);
	await page.locator('#panel[data-view="steps"]').waitFor({ timeout: 20_000 });
	say('opened; the token left the address bar: ' + String(!page.url().includes('t=')));

	await page.locator(`input[name="bind"][value="${bind}"]`).check({ force: true });
	await page.locator('#password').fill(password);
	await page.locator('#password2').fill(password);
	await page.locator('#timezone').fill('Etc/UTC');
	await shot('setup-1-server');
	await next();

	await at('#t-models');
	if (await page.locator('input[name="model-mode"][value="local"]').isDisabled()) say('no graphics card here: the local model is not offered');
	await page.locator('input[name="model-mode"][value="endpoint"]').check({ force: true });
	await page.locator('#llm-url').fill(endpoint);
	await page.locator('#test-model').click();
	await page.locator('#test-result[data-ok="true"]').waitFor({ timeout: 20_000 });
	say('the endpoint answered: ' + (await page.locator('#test-result').innerText()).trim());
	await shot('setup-2-models');
	await next();

	await at('#t-voice');
	say('voice: ' + (await page.locator('input[name="tts"]:checked').inputValue()));
	await shot('setup-3-voice');
	await next();

	await at('#t-wake');
	await shot('setup-4-wake');
	await next();

	await at('#t-devices');
	await page.locator('input[name="device"][value="phone"]').check({ force: true });
	await shot('setup-5-devices');
	await next();

	await at('#t-theme');
	await page.locator(`.look[data-look="${theme}"]`).click();
	await shot('setup-6-theme');
	await next();

	await at('#t-review');
	say('review: ' + (await page.locator('#summary').innerText()).replace(/\s+/g, ' ').trim());
	await shot('setup-7-review');
	await next();

	await page.locator('#panel[data-view="install"]').waitFor({ timeout: 20_000 });
	say('Install pressed');
	const started = Date.now();
	let seen = '';
	let pictured = false;
	for (;;) {
		if (await page.locator('#done').isVisible()) break;
		const error = (await page.locator('#install-error').innerText()).trim();
		if (error) throw new Error(error);
		if (Date.now() - started > waitMs) throw new Error(`the install was not done after ${waitMs / 1000} s`);
		const count = (await page.locator('#install-count').innerText()).trim();
		if (count !== seen) {
			seen = count;
			say(`${count} ${(await page.locator('#phases li[data-state="running"]').allInnerTexts()).join(' ').replace(/\s+/g, ' ').trim()}`);
			if (!pictured && count.startsWith('4/')) {
				pictured = true;
				await shot('setup-8-installing');
			}
		}
		await page.waitForTimeout(3000);
	}
	say((await page.locator('#t-install').innerText()).trim() + ` after ${Math.round((Date.now() - started) / 1000)} s`);
	await shot('setup-9-done');

	const consoleUrl = await page.locator('#open-console').getAttribute('href');
	say('console: ' + consoleUrl);
	await page.goto(consoleUrl, { waitUntil: 'domcontentloaded', timeout: 60_000 });
	await page.getByTestId('lock-screen').waitFor({ timeout: 60_000 });
	await shot('console-1-locked');
	await page.getByTestId('lock-password').fill(password);
	await page.getByTestId('lock-unlock').click();
	await page.getByTestId('lock-screen').waitFor({ state: 'detached', timeout: 30_000 });
	say('signed in to the console with the password chosen on the page');
	await page.waitForTimeout(4000);
	await page.screenshot({ path: join(out, 'console-2-signed-in.png') });
	// Said, not required: Hermes is the last service to be ready, so offline or warming here is
	// the truth about a stack still starting.
	say(`the core signed in at: ${await reactor()}`);

	// A console that opens is not AURA answering. A question typed here goes console, adapter,
	// Hermes, then the endpoint named on the page, and Hermes is the last of them to be ready:
	// the first picture of a signed-in console said "link closed" under the core. What the
	// endpoint says is asked of it directly first, so the sentence is typed in one place.
	const direct = await fetch(`${endpoint}/chat/completions`, {
		method: 'POST',
		headers: { 'content-type': 'application/json' },
		body: JSON.stringify({ model: 'proof-model', messages: [{ role: 'user', content: 'Say hello.' }] })
	}).then((r) => r.json());
	const sentence = String(direct?.choices?.[0]?.message?.content || '').trim();
	if (!sentence) throw new Error('the model endpoint gave no sentence to look for');
	const shown = () =>
		page.evaluate((want) => document.body.innerText.replace(/\s+/g, ' ').includes(want), sentence);
	const asked = Date.now();
	let tries = 0;
	let heard = false;
	while (!heard && tries < 8) {
		tries += 1;
		await page.getByTestId('chat-input').fill('Say hello.');
		await page.getByTestId('chat-input').press('Enter');
		const until = Date.now() + 45_000;
		while (!heard && Date.now() < until) {
			heard = await shown();
			if (!heard) await page.waitForTimeout(1000);
		}
	}
	if (!heard) throw new Error(`the console showed no answer from the model endpoint in ${tries} tries`);
	say(`asked "Say hello." in the console; the endpoint's sentence came back after ${Math.round((Date.now() - asked) / 1000)} s (try ${tries})`);
	// Let the conversation and the core come to rest before the picture, and require the rest:
	// an answered question with nothing waiting is `idle`. A new install that sits at alert,
	// attention or kill after its first answer is telling a new owner something is wrong.
	const restBy = Date.now() + 20_000;
	let wearing = await reactor();
	while (wearing !== 'idle' && Date.now() < restBy) {
		await page.waitForTimeout(1000);
		wearing = await reactor();
	}
	await page.waitForTimeout(1500);
	await page.screenshot({ path: join(out, 'console-3-answered.png') });
	say(`the core after the answer: ${wearing}`);
	if (wearing !== 'idle') throw new Error(`the core is at "${wearing}" after the answer, not at rest`);
	console.log('RESULT: installed');
	await browser.close();
} catch (error) {
	await page.screenshot({ path: join(out, 'failed.png') }).catch(() => {});
	console.log(`RESULT: failed: ${String(error && error.message ? error.message : error).split('\n')[0]}`);
	await browser.close();
	process.exit(1);
}
