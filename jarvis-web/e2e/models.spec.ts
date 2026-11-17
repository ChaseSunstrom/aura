// The Models screen (T-1254; the owner: the v2 console lost the screen that sees, adds,
// downloads, loads and unloads the house's models). mock-hermes answers /api/models in the
// manager's shape (tests/contracts/models-api.json); `POST /api/test/models` resets it and its
// `mode` picks empty, error or gone. Each title says "models", so `-g models` runs the file.

import { readFileSync } from 'node:fs';
import { expect, test, type Page } from '@playwright/test';
import { openShell } from './v2';

const contract = JSON.parse(readFileSync(new URL('../../tests/contracts/models-api.json', import.meta.url), 'utf8'));
const [loaded, idle] = contract.endpoints.list.example.models.map((m: { id: string }) => m.id) as string[];
const models = (page: Page, mode = 'ok', missing_llama = false) => page.request.post('/hermes/api/test/models', { data: { mode, missing_llama } });
/** A refusal's sentence from the contract, its {slots} filled: what the manager and the mock both say. */
const refusal = (key: string, slots: Record<string, string>) =>
	Object.entries(slots).reduce((s, [k, v]) => s.replace(`{${k}}`, v), contract.refusals[key].message as string);

test.beforeEach(async ({ page }) => {
	await models(page);
});
test.afterEach(async ({ page }) => {
	await models(page);
});

test('models: the list says each state, a load says where it lands, an unload and a delete take a hold', async ({ page }) => {
	const writes: string[] = [];
	page.on('request', (r) => {
		if (r.method() !== 'GET' && r.url().includes('/hermes/api/models')) writes.push(`${r.method()} ${new URL(r.url()).pathname.slice('/hermes/api/models'.length)}${new URL(r.url()).search}`);
	});
	await openShell(page, '/models');
	await expect(page.getByTestId('shell')).toHaveAttribute('data-screen', 'models');
	await expect(page.getByTestId('models-state')).toHaveCount(0);
	await expect(page.getByTestId(`model-${loaded}`)).toHaveAttribute('data-state', 'loaded');
	await expect(page.getByTestId(`model-${loaded}-state`)).toHaveText('Loaded');
	await expect(page.getByTestId(`model-${idle}`)).toHaveAttribute('data-state', 'unloaded');
	// What an unload stops is said before anyone holds the button.
	await expect(page.getByTestId(`model-${loaded}-cost`)).toContainText('stops replies');
	await expect(page.getByTestId('card-0')).toContainText('Models');
	// Nothing here has lost its weights, so no row offers a second download.
	await expect(page.locator('[data-testid$="-refetch"]')).toHaveCount(0);

	await page.getByTestId(`model-${idle}-load`).click();
	await expect(page.getByTestId('models-said-list')).toContainText('on cards 0 and 1');
	await expect(page.getByTestId(`model-${idle}`)).toHaveAttribute('data-state', 'loaded');

	// A tap is not an unload; a full hold is.
	const unload = page.getByTestId(`model-${idle}-unload`);
	await unload.click();
	expect(writes).toEqual([`POST /${idle}/load`]);
	await unload.dispatchEvent('pointerdown');
	await expect(page.getByTestId(`model-${idle}`)).toHaveAttribute('data-state', 'unloaded', { timeout: 6_000 });

	// Edit sends only what changed, and refuses a value outside the limits before the round trip.
	await page.getByTestId(`model-${idle}-edit`).click();
	const context = page.getByTestId(`model-${idle}-context`);
	await context.fill('10');
	await page.getByTestId(`model-${idle}-save`).click();
	await expect(page.getByTestId('models-said-list')).toContainText('Context length');
	await context.fill('65536');
	const patch = page.waitForRequest((r) => r.method() === 'PATCH');
	await page.getByTestId(`model-${idle}-save`).click();
	expect((await patch).postDataJSON()).toEqual({ settings: { max_model_len: 65536 } });
	await expect(page.getByTestId('models-said-list')).toContainText('Saved');
	writes.length = 0;

	// Delete sits behind Edit and its own hold, and takes the weights only when asked.
	await page.getByTestId(`model-${idle}-edit`).click();
	await page.getByTestId(`model-${idle}-weights`).click();
	await page.getByTestId(`model-${idle}-delete`).dispatchEvent('pointerdown');
	await expect(page.getByTestId(`model-${idle}`)).toHaveCount(0, { timeout: 6_000 });
	await expect(page.getByTestId('models-said-list')).toContainText('freed');
	expect(writes).toEqual([`DELETE /${idle}?weights=1`]);

	const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
	expect(overflow).toBeLessThanOrEqual(0);
});

test('models: a house with none says so and still offers the search and the add form', async ({ page }) => {
	await models(page, 'empty');
	await openShell(page, '/models');
	await expect(page.getByTestId('models-state')).toHaveAttribute('data-state', 'empty');
	await expect(page.getByTestId('models-state')).toContainText('No models yet');
	await expect(page.getByTestId('models-query')).toBeVisible();
	await expect(page.getByTestId('models-add-submit')).toBeVisible();
});

test('models: a manager that fails is an error with Retry, one out of reach is offline, and both recover', async ({ page }) => {
	await models(page, 'error');
	await openShell(page, '/settings/models');
	const state = page.getByTestId('models-state');
	await expect(state).toHaveAttribute('data-state', 'error');
	await expect(state).toContainText('The model manager broke.');
	await expect(page.getByTestId('models-add-submit')).toHaveCount(0);

	await models(page, 'gone');
	await state.getByRole('button', { name: 'Retry' }).click();
	await expect(state).toHaveAttribute('data-state', 'offline');

	await models(page, 'ok');
	await state.getByRole('button', { name: 'Retry' }).click();
	await expect(state).toHaveCount(0);
	await expect(page.getByTestId(`model-${loaded}`)).toBeVisible();
});

test('models: the add form says a bad GGUF file inline and sends nothing, then downloads the file and adds it on llama.cpp', async ({ page }) => {
	const posts: unknown[] = [];
	page.on('request', (r) => {
		if (r.method() === 'POST' && /\/hermes\/api\/models(\/downloads)?$/.test(new URL(r.url()).pathname)) posts.push(r.postDataJSON());
	});
	await openShell(page, '/models');
	const engine = page.getByTestId('models-add-engine');
	// vLLM serves the whole repo: no file box until llama.cpp is chosen.
	await expect(page.getByTestId('models-add-file')).toHaveCount(0);
	await engine.getByRole('button', { name: 'llama.cpp' }).click();
	await page.getByTestId('models-add-id').fill('pebble-7b');
	await page.getByTestId('models-add-repo').fill('made-up/Pebble-7B-GGUF');
	await page.getByTestId('models-add-file').fill('pebble-7b.safetensors');
	await expect(page.getByTestId('models-add-file-error')).toHaveText(contract.create_body.gguf_file.error);
	await expect(page.getByTestId('models-add-submit')).toBeDisabled();
	await expect(page.getByTestId('models-add-fetch')).toBeDisabled();
	expect(posts).toEqual([]);

	await page.getByTestId('models-add-file').fill('pebble-7b-q4.gguf');
	await expect(page.getByTestId('models-add-file-error')).toHaveCount(0);
	// Not on disk yet: the manager's own sentence, beside the form.
	await page.getByTestId('models-add-submit').click();
	await expect(page.getByTestId('models-said-add')).toContainText(refusal('not_downloaded', { what: 'pebble-7b-q4.gguf' }));

	await page.getByTestId('models-add-fetch').click();
	await expect(page.getByTestId('download-made-up/Pebble-7B-GGUF')).toHaveAttribute('data-state', 'done', { timeout: 15_000 });
	await page.getByTestId('models-add-submit').click();
	const row = page.getByTestId('model-pebble-7b');
	await expect(row).toHaveAttribute('data-state', 'unloaded');
	await expect(row).toContainText('llama.cpp');
	const body = { id: 'pebble-7b', repo: 'made-up/Pebble-7B-GGUF', engine: 'llama.cpp', file: 'pebble-7b-q4.gguf', settings: { max_model_len: 32768, keep_loaded: false, idle_unload_s: 600 } };
	expect(posts).toEqual([body, { repo: 'made-up/Pebble-7B-GGUF', file: 'pebble-7b-q4.gguf' }, body]);
});

test('models: a llama.cpp row whose file is gone offers Download again, sends only its repo, and ends not loaded', async ({ page }) => {
	await models(page, 'ok', true);
	const posts: unknown[] = [];
	page.on('request', (r) => {
		if (r.method() === 'POST' && new URL(r.url()).pathname.endsWith('/hermes/api/models/downloads')) posts.push(r.postDataJSON());
	});
	await openShell(page, '/models');
	const row = page.getByTestId('model-pebble-gguf');
	await expect(row).toHaveAttribute('data-state', 'missing');
	// Only the row that lost its file offers it: the rows that are on disk do not.
	await expect(page.getByTestId('model-pebble-gguf-refetch')).toBeEnabled();
	await expect(page.locator('[data-testid$="-refetch"]')).toHaveCount(1);
	await expect(page.getByTestId(`model-${idle}-refetch`)).toHaveCount(0);

	await page.getByTestId('model-pebble-gguf-refetch').click();
	// The row posts what the list carries (the repo); the manager finds the file.
	await expect(page.getByTestId('models-said-list')).toContainText('Downloading made-up/Pebble-7B-GGUF');
	expect(posts).toEqual([{ repo: 'made-up/Pebble-7B-GGUF' }]);
	await expect(row).toHaveAttribute('data-state', 'downloading');
	await expect(page.getByTestId('model-pebble-gguf-refetch')).toHaveCount(0);
	await expect(row).toHaveAttribute('data-state', 'unloaded', { timeout: 15_000 });
	await expect(page.getByTestId('model-pebble-gguf-load')).toBeVisible();
	expect(posts).toHaveLength(1);
});
