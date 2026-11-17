/**
 * A photo for the chat, made small on the device before it is sent (T-0497).
 *
 * The model reads an image at well under 1600 px; a phone's full-size photo is
 * megabytes of base64 over the tunnel for nothing. Not covered: a photo's EXIF
 * orientation is applied by the browser's decoder (`createImageBitmap`'s
 * default), so an old browser that ignores it sends a sideways photo.
 */

import { token } from '$lib/tokens';

/** The long edge a sent photo is fit within, in pixels. */
export const PHOTO_EDGE = 1600;
const QUALITY = 0.85;

/** The size `width`×`height` is drawn at: within [PHOTO_EDGE], never enlarged. */
export function fitWithin(width: number, height: number, edge = PHOTO_EDGE): { width: number; height: number } {
	const scale = Math.min(1, edge / Math.max(width, height));
	return { width: Math.max(1, Math.round(width * scale)), height: Math.max(1, Math.round(height * scale)) };
}

/** The photo as a JPEG, base64 without the `data:` prefix, and a URL to show it by. */
export async function photoForChat(file: Blob): Promise<{ base64: string; preview: string }> {
	const bitmap = await createImageBitmap(file);
	const { width, height } = fitWithin(bitmap.width, bitmap.height);
	const canvas = document.createElement('canvas');
	canvas.width = width;
	canvas.height = height;
	const ctx = canvas.getContext('2d');
	if (ctx) {
		// A JPEG has no alpha: left unfilled, a PNG's transparent pixels turn
		// black, and black ink on a transparent sticker reaches the model as nothing.
		ctx.fillStyle = token('--jv-text-bright');
		ctx.fillRect(0, 0, width, height);
		ctx.drawImage(bitmap, 0, 0, width, height);
	}
	bitmap.close();
	const preview = canvas.toDataURL('image/jpeg', QUALITY);
	return { base64: preview.slice(preview.indexOf(',') + 1), preview };
}
