import { describe, expect, it, vi } from 'vitest';

import { PER_PAGE, reportErrors } from './errorReport';

// The console's uncaught errors reach the adapter's log (T-0475), except in a private chat.
function setup(privately: () => boolean) {
	const target = new EventTarget();
	const fetcher = vi.fn(async () => new Response(null, { status: 204 }));
	const stop = reportErrors(privately, fetcher, target);
	const fail = (message: string) =>
		target.dispatchEvent(Object.assign(new Event('error'), { message, filename: 'app.js', lineno: 7 }));
	return { target, fetcher, stop, fail };
}

describe('errorReport', () => {
	it('posts an uncaught error to the adapter with its source and line', () => {
		const { fetcher, fail } = setup(() => false);
		fail('boom');
		expect(fetcher).toHaveBeenCalledOnce();
		const [url, init] = fetcher.mock.calls[0] as unknown as [string, RequestInit];
		expect(url).toBe('/hermes/api/client-error');
		expect(JSON.parse(String(init.body))).toEqual({ kind: 'error', message: 'boom', source: 'app.js', line: 7 });
	});

	it('posts an unhandled rejection with its reason', () => {
		const { target, fetcher } = setup(() => false);
		target.dispatchEvent(Object.assign(new Event('unhandledrejection'), { reason: new Error('nope') }));
		const body = JSON.parse(String((fetcher.mock.calls[0] as unknown as [string, RequestInit])[1].body));
		expect(body).toMatchObject({ kind: 'rejection', message: 'nope' });
	});

	it('sends nothing at all while a private chat is on', () => {
		const { fetcher, fail, target } = setup(() => true);
		fail('private boom');
		target.dispatchEvent(Object.assign(new Event('unhandledrejection'), { reason: 'x' }));
		expect(fetcher).not.toHaveBeenCalled();
	});

	it('stops after the per-page cap and after stop()', () => {
		const { fetcher, fail } = setup(() => false);
		for (let i = 0; i < PER_PAGE + 3; i++) fail(`e${i}`);
		expect(fetcher).toHaveBeenCalledTimes(PER_PAGE);
		const again = setup(() => false);
		again.stop();
		again.fail('after stop');
		expect(again.fetcher).not.toHaveBeenCalled();
	});

	it('sends a repeated error once, so a noisy one cannot spend the cap', () => {
		const { fetcher, fail } = setup(() => false);
		for (let i = 0; i < PER_PAGE + 3; i++) fail('ResizeObserver loop');
		fail('the one that matters');
		expect(fetcher).toHaveBeenCalledTimes(2);
	});
});
