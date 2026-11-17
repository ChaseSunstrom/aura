// The wake word starts in a real browser (T-0875): the runtime's chunk and wasm come from the
// console itself, the three models from the house (the mock answers 404, so here a route serves
// the real files), and Settings says it listens. The chain's scores are wake.test.ts's; this pins
// what node cannot: the wasm's path in a built console and the browser's own audio graph.
import { expect, test } from '@playwright/test';
import { head, model } from '../src/lib/audio/fixtures/models';
import { micAsShipped, openShell, resetHermes } from './v2';

test.afterEach(async ({ page }) => resetHermes(page));

test('the open mic listens for “Aura” with the runtime from the console and the models from the house', async ({ page }) => {
	const served: string[] = [];
	await page.route('**/hermes/api/models/*', async (route) => {
		const name = decodeURIComponent(new URL(route.request().url()).pathname.split('/').pop() ?? '');
		served.push(name);
		if (name !== 'aura.onnx' && name !== 'melspectrogram.onnx' && name !== 'embedding_model.onnx')
			return route.fulfill({ status: 404, body: '' });
		const body = name === 'aura.onnx' ? head() : await model(name);
		await route.fulfill({ body: Buffer.from(body), contentType: 'application/octet-stream' });
	});
	await micAsShipped(page); // the open mic as shipped: on, where every other spec starts muted
	const wasm = page.waitForResponse((r) => /ort-wasm-simd-threaded\.[^/]*\.wasm$/.test(r.url()), { timeout: 30_000 });
	await openShell(page, '/settings');
	await page.request.post('/hermes/api/test/theme', { data: { op: 'apply', id: 'aura' } });

	const runtime = await wasm;
	expect(runtime.status()).toBe(200);
	expect(new URL(runtime.url()).origin).toBe(new URL(page.url()).origin);
	await expect(page.getByTestId('settings-wake-state')).toHaveText('Listening for “Aura” on this device.', { timeout: 30_000 });
	expect(served).toEqual(expect.arrayContaining(['melspectrogram.onnx', 'embedding_model.onnx', 'aura.onnx']));

	// Every word: the wake word stops, and the open mic sends what it hears as before.
	await page.getByTestId('settings-wake').getByRole('button', { name: 'Every word' }).click();
	await expect(page.getByTestId('settings-wake-state')).toHaveCount(0);
});
