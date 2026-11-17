import { afterEach, describe, expect, it, vi } from 'vitest';
import { PHOTO_EDGE, fitWithin, photoForChat } from './photo';
import { token } from '$lib/tokens';

describe('a photo for the chat', () => {
	it('fits the long edge within the cap and keeps the shape', () => {
		// A phone's 12 MP photo is 4000x3000: ~4 MB of base64 over the tunnel for a
		// model that reads it at a fraction of that.
		expect(fitWithin(4000, 3000)).toEqual({ width: PHOTO_EDGE, height: 1200 });
		expect(fitWithin(3000, 4000)).toEqual({ width: 1200, height: PHOTO_EDGE });
	});

	it('never enlarges a small one', () => {
		expect(fitWithin(640, 480)).toEqual({ width: 640, height: 480 });
	});

	it('keeps a sliver at least one pixel wide', () => {
		expect(fitWithin(10000, 2)).toEqual({ width: PHOTO_EDGE, height: 1 });
	});
});

describe('a transparent photo', () => {
	afterEach(() => vi.unstubAllGlobals());

	it('is drawn on a light ground, not left to flatten onto black as a JPEG', async () => {
		// A JPEG has no alpha: an unfilled canvas turns a PNG's transparent pixels
		// black, and black ink on a transparent sticker reaches the model as nothing.
		const calls: string[] = [];
		const ctx = {
			fillStyle: '',
			fillRect: () => calls.push(`fill ${ctx.fillStyle}`),
			drawImage: () => calls.push('draw')
		};
		const canvas = { width: 0, height: 0, getContext: () => ctx, toDataURL: () => 'data:image/jpeg;base64,QQ==' };
		vi.stubGlobal('document', { createElement: () => canvas });
		vi.stubGlobal('createImageBitmap', async () => ({ width: 10, height: 10, close() {} }));
		const shot = await photoForChat(new Blob());
		expect(calls).toEqual([`fill ${token('--jv-text-bright')}`, 'draw']);
		expect(shot.base64).toBe('QQ==');
	});
});
