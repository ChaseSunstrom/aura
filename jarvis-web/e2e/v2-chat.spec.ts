// Chat, full screen on the v2 shell (rewritten from hud-chatscreen.spec.ts, every
// assertion kept): reached from the launcher or /chat; a message sent here is
// answered here; the conversations list, open and delete; on a phone the list is
// a drawer; a private chat never reaches Hermes or storage; a photo goes with the
// words; the model select switches this conversation, and a refusal puts it back.
import { expect, test } from '@playwright/test';

import { layoutPx, openShell } from './v2';

test('the chat screen sends, answers, lists the conversation and opens a new one', async ({ page }) => {
	await page.setViewportSize({ width: 1440, height: 900 });
	await openShell(page, '/');
	await page.getByRole('button', { name: 'All apps' }).click();
	await page.getByRole('dialog').getByRole('link', { name: /Chat/ }).click();
	const screen = page.getByTestId('screen-chat');
	await expect(screen).toBeVisible();
	// Chat opens in the side panel beside the core (T-0725), narrower than its 720 px
	// break even at 1440: the conversations are a drawer, opened before each use (it
	// closes itself on New and on opening one).
	const conversations = async () => {
		const toggle = screen.getByTestId('chatscreen-rail-toggle');
		if ((await toggle.isVisible()) && (await toggle.getAttribute('aria-pressed')) !== 'true') await toggle.click();
	};
	// A new conversation offers somewhere to start.
	await expect(screen.getByTestId('chatscreen-suggestion').first()).toBeVisible();
	const input = screen.getByTestId('chatscreen-input');
	await input.fill('turn on the lab lights');
	await input.press('Enter');
	const thread = screen.getByTestId('chatscreen-thread');
	await expect(thread.locator('[data-role="user"]').last()).toContainText('turn on the lab lights');
	await expect(thread.locator('[data-role="assistant"]').last()).not.toBeEmpty({ timeout: 15_000 });
	await expect(screen.getByTestId('chatscreen-send')).toBeVisible({ timeout: 15_000 });
	// The conversation is in the list once it has something in it.
	await conversations();
	await screen.getByTestId('chatscreen-refresh').click();
	await expect(screen.locator('[data-testid^="chatscreen-session-"]').first()).toBeVisible();
	// A new chat is empty again; the old one is still listed and opens.
	await screen.getByTestId('chatscreen-new').click();
	await expect(screen.getByTestId('chatscreen-suggestion').first()).toBeVisible();
	await conversations();
	await screen.locator('[data-testid^="chatscreen-session-"] button.open').first().click();
	await expect(thread.locator('[data-role="user"]').first()).toContainText('turn on the lab lights');
	// Deleting asks first, and takes exactly that one away.
	await conversations();
	const rows = screen.locator('[data-testid^="chatscreen-session-"]');
	const before = await rows.count();
	const row = rows.first();
	await row.hover();
	await row.locator('[data-testid^="chatscreen-delete-"]').first().click();
	await row.getByRole('button', { name: 'Delete' }).click();
	await expect(rows).toHaveCount(before - 1);
	// Escape closes the open drawer first, then goes back to the core.
	await input.press('Escape');
	await expect(screen.getByTestId('chatscreen-rail')).toBeHidden();
	await input.press('Escape');
	await expect(screen).toHaveCount(0);
	await expect(page).toHaveURL(/\/$/);
});

test('/chat opens it, and on a phone the conversations are a drawer', async ({ page }) => {
	await page.setViewportSize({ width: 390, height: 844 });
	await openShell(page, '/chat');
	const screen = page.getByTestId('screen-chat');
	await expect(screen).toBeVisible();
	const rail = screen.getByTestId('chatscreen-rail');
	await expect(rail).toBeHidden();
	await screen.getByTestId('chatscreen-rail-toggle').click();
	await expect(rail).toBeVisible();
	await expect(screen.getByTestId('chatscreen-new')).toBeVisible();
	// The toggle that opened the drawer is still there to close it.
	await screen.getByTestId('chatscreen-rail-toggle').click();
	await expect(rail).toBeHidden();
	const box = await screen.boundingBox();
	expect(box!.width).toBeLessThanOrEqual(390);
	const wide = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
	expect(wide, 'no sideways scroll').toBeLessThanOrEqual(0);
	for (const b of await screen.locator('button:visible').all()) expect(layoutPx((await b.boundingBox())!.height), await b.innerText()).toBeGreaterThanOrEqual(44);
});

test('a private conversation is answered by the private route and never reaches Hermes or storage', async ({ page }) => {
	// T-0462: the owner asked for chats "that won't be logged or stored absolutely anywhere".
	const canary = 'zebra-canary-7731';
	const asked: unknown[] = [];
	const frames: string[] = [];
	page.on('websocket', (ws) => ws.on('framesent', (f) => frames.push(String(f.payload))));
	await page.route('**/hermes/api/private/chat', async (route) => {
		asked.push(route.request().postDataJSON());
		await route.fulfill({
			status: 200,
			headers: { 'content-type': 'text/event-stream', 'cache-control': 'no-store' },
			body: 'data: {"content":"Kept between us."}\n\ndata: [DONE]\n\n'
		});
	});
	await page.setViewportSize({ width: 1440, height: 900 });
	await openShell(page, '/chat');
	const screen = page.getByTestId('screen-chat');
	await expect(screen.getByTestId('chatscreen-model')).toHaveValue('qwen3.8-27b', { timeout: 15_000 });
	await screen.getByTestId('chatscreen-private').click();
	await expect(screen.getByTestId('chatscreen-private-banner')).toBeVisible();
	await expect(screen.getByTestId('chatscreen-title')).toHaveText('Private conversation');
	// The suggestions ask for the house, which a private chat cannot reach.
	await expect(screen.getByTestId('chatscreen-suggestion')).toHaveCount(0);
	const input = screen.getByTestId('chatscreen-input');
	await expect(input).toHaveAttribute('spellcheck', 'false');
	await input.fill(`Remember ${canary}`);
	await input.press('Enter');
	const thread = screen.getByTestId('chatscreen-thread');
	await expect(thread.locator('[data-role="assistant"]').last()).toContainText('Kept between us.');
	// It starts on the model this chat is on (T-0504).
	expect(asked).toEqual([{ messages: [{ role: 'user', content: `Remember ${canary}` }], model: 'qwen3.8-27b' }]);
	expect(frames.filter((f) => f.includes(canary))).toEqual([]);
	const stored = await page.evaluate(() => JSON.stringify({ ...localStorage }) + JSON.stringify({ ...sessionStorage }));
	expect(stored).not.toContain(canary);
	// Switched off, it is gone, and the next line is Hermes' again.
	await screen.getByTestId('chatscreen-private').click();
	await expect(screen.getByTestId('chatscreen-private-banner')).toHaveCount(0);
	await expect(screen.getByText(canary)).toHaveCount(0);
	// Closed while private, it ends too: reopened, the chat is an ordinary one.
	await screen.getByTestId('chatscreen-private').click();
	await expect(screen.getByTestId('chatscreen-private')).toHaveAttribute('aria-pressed', 'true');
	await screen.getByTestId('chatscreen-close').click();
	await expect(screen).toBeHidden();
	await page.getByRole('button', { name: 'All apps' }).click();
	await page.getByRole('dialog').getByRole('link', { name: /Chat/ }).click();
	await expect(screen.getByTestId('chatscreen-private-banner')).toHaveCount(0);
	await expect(screen.getByTestId('chatscreen-private')).toHaveAttribute('aria-pressed', 'false');
});

test('a private chat runs on the model picked in its select, and never tells Hermes', async ({ page }) => {
	// T-0504: the owner asked to set the model in a private chat ("the uncensored model for example").
	const asked: { model?: string }[] = [];
	const frames: string[] = [];
	page.on('websocket', (ws) => ws.on('framesent', (f) => frames.push(String(f.payload))));
	await page.route('**/hermes/api/private/chat', async (route) => {
		asked.push(route.request().postDataJSON());
		await route.fulfill({ status: 200, headers: { 'content-type': 'text/event-stream' }, body: 'data: {"content":"Done."}\n\ndata: [DONE]\n\n' });
	});
	await page.setViewportSize({ width: 1440, height: 900 });
	await openShell(page, '/chat');
	const screen = page.getByTestId('screen-chat');
	const select = screen.getByTestId('chatscreen-model');
	await expect(select).toHaveValue('qwen3.8-27b', { timeout: 15_000 });
	await screen.getByTestId('chatscreen-private').click();
	await expect(select).toHaveValue('qwen3.8-27b');
	await select.selectOption('qwen3.6-35b');
	await expect(select).toHaveValue('qwen3.6-35b');
	await screen.getByTestId('chatscreen-input').fill('Hello');
	await screen.getByTestId('chatscreen-input').press('Enter');
	await expect(screen.getByTestId('chatscreen-thread').locator('[data-role="assistant"]').last()).toContainText('Done.');
	expect(asked.map((b) => b.model)).toEqual(['qwen3.6-35b']);
	expect(frames.filter((f) => f.includes('config.set'))).toEqual([]);
});

// A 1×1 PNG: enough for the browser to decode and redraw as the JPEG that is sent.
const PNG = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==';

test('a photo goes with the words, shows until it is sent, and a private chat offers none', async ({ page }) => {
	// T-0497: "what is this plant" with the plant.
	const frames: string[] = [];
	page.on('websocket', (ws) => ws.on('framesent', (f) => frames.push(String(f.payload))));
	await page.setViewportSize({ width: 390, height: 844 });
	await openShell(page, '/chat');
	const screen = page.getByTestId('screen-chat');
	await expect(screen).toBeVisible();
	const photo = { name: 'plant.png', mimeType: 'image/png', buffer: Buffer.from(PNG, 'base64') };
	const picker = screen.getByTestId('chatscreen-picker');
	await expect(screen.getByTestId('chatscreen-attach')).toBeVisible();
	await picker.setInputFiles(photo);
	await expect(screen.getByTestId('chatscreen-photo')).toBeVisible();
	await screen.getByTestId('chatscreen-photo-remove').click();
	await expect(screen.getByTestId('chatscreen-photo')).toHaveCount(0);
	// The photo alone sends: no words needed, it asks what is in it.
	await picker.setInputFiles(photo);
	await expect(screen.getByTestId('chatscreen-send')).toBeEnabled();
	await screen.getByTestId('chatscreen-send').click();
	const thread = screen.getByTestId('chatscreen-thread');
	await expect(thread.locator('[data-role="user"]').last()).toContainText("What's in this photo?");
	await expect(thread.locator('[data-role="assistant"]').last()).toContainText('photo you attached', { timeout: 15_000 });
	await expect(screen.getByTestId('chatscreen-photo')).toHaveCount(0);
	const methods = frames.map((f) => (JSON.parse(f) as { method?: string }).method ?? '');
	expect(methods.indexOf('image.attach_bytes')).toBeGreaterThanOrEqual(0);
	expect(methods.indexOf('image.attach_bytes')).toBeLessThan(methods.lastIndexOf('prompt.submit'));
	// Turned private, a pending photo is dropped and there is nothing to attach with.
	await expect(screen.getByTestId('chatscreen-send')).toBeVisible({ timeout: 15_000 });
	await picker.setInputFiles(photo);
	await expect(screen.getByTestId('chatscreen-photo')).toBeVisible();
	await screen.getByTestId('chatscreen-private').click();
	await expect(screen.getByTestId('chatscreen-photo')).toHaveCount(0);
	await expect(screen.getByTestId('chatscreen-attach')).toHaveCount(0);
	// A photo still decoding when Private goes on is dropped too, not shown once it ends
	// (T-0497 review: a large photo takes a moment to shrink).
	await screen.getByTestId('chatscreen-private').click();
	await page.evaluate(() => {
		const real = window.createImageBitmap.bind(window) as (...a: unknown[]) => Promise<ImageBitmap>;
		const slow = (...a: unknown[]) =>
			new Promise<ImageBitmap>((done) => setTimeout(() => done(real(...a)), 600)).finally(() => Object.assign(window, { __decoded: true }));
		Object.assign(window, { createImageBitmap: slow });
	});
	await picker.setInputFiles(photo);
	await screen.getByTestId('chatscreen-private').click();
	await page.waitForFunction(() => (window as unknown as { __decoded?: boolean }).__decoded === true);
	await screen.getByTestId('chatscreen-private').click();
	await expect(screen.getByTestId('chatscreen-attach')).toBeVisible();
	await expect(screen.getByTestId('chatscreen-photo')).toHaveCount(0);
});

// The owner, 2026-10-30: "how can we have a select for models in jarvis? instead of having to
// type it". The chat's select lists what the model manager serves and switches this conversation
// through Hermes' session-scoped config.set model, the path /model takes.
test('the model select lists the served models and switches this conversation', async ({ page }) => {
	const sent: { method?: string; params?: Record<string, unknown> }[] = [];
	page.on('websocket', (ws) =>
		ws.on('framesent', (f) => {
			try {
				sent.push(JSON.parse(String(f.payload)));
			} catch {
				/* not a JSON-RPC frame */
			}
		})
	);
	await page.setViewportSize({ width: 1440, height: 900 });
	// The options are the models feed, loaded when the chat opens: wait on that fetch, not a
	// timer (T-0830: on a loaded CI host the list took longer than the default 5 s). Registered
	// before the shell opens, so a fast answer is not missed.
	const served = page.waitForResponse((r) => new URL(r.url()).pathname === '/hermes/api/models');
	await openShell(page, '/chat');
	const select = page.getByTestId('screen-chat').getByTestId('chatscreen-model');
	await expect(select).toHaveValue('qwen3.8-27b', { timeout: 15_000 });
	await served;
	await expect(select.locator('option')).toHaveText(['Qwen3.8 27B (AWQ int4)', 'Qwen3.6 35B-A3B (GPTQ int4), not loaded']);
	await select.selectOption('qwen3.6-35b');
	await expect
		.poll(() => sent.find((m) => m.method === 'config.set' && m.params?.key === 'model')?.params)
		.toMatchObject({ value: 'qwen3.6-35b', session_id: expect.any(String) });
	await expect(select).toHaveValue('qwen3.6-35b');
});

test('a model switch Hermes refuses puts the select back on the model this chat is on', async ({ page }) => {
	// Answer the switch with an error in Hermes' shape; every other frame goes to the mock.
	await page.routeWebSocket(/\/hermes\/ws$/, (ws) => {
		const server = ws.connectToServer();
		ws.onMessage((m) => {
			const f = JSON.parse(String(m));
			if (f.method === 'config.set' && f.params?.key === 'model')
				return ws.send(JSON.stringify({ jsonrpc: '2.0', id: f.id, error: { code: 5032, message: 'agent initialization failed' } }));
			server.send(m);
		});
	});
	await page.setViewportSize({ width: 1440, height: 900 });
	await openShell(page, '/chat');
	const select = page.getByTestId('screen-chat').getByTestId('chatscreen-model');
	await expect(select).toHaveValue('qwen3.8-27b', { timeout: 15_000 });
	await select.selectOption('qwen3.6-35b');
	await expect(page.getByText(/switch the model/i)).toBeVisible();
	await expect(select).toHaveValue('qwen3.8-27b');
});
