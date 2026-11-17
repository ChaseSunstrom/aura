// The README's looping previews, cut from the films the Pages site plays (T-1518).
//
//   node scripts/media/readme-previews.mjs [name …]     every clip in the table, or the ones named
//   node scripts/media/readme-previews.mjs --one MP4 WEBP WIDTH    one film (what film.ts runs)
//
// A preview is a WebP loop in docs/media/<name>.webp, made from site/media/<name>.mp4 (which
// already carries the film's captions). Each is encoded down one ladder, sharpest first, and the
// first rung under the table's byte budget is kept: a short clip stays sharp and smooth, a long
// one gives up frames and detail rather than the budget. The budget is the README check's
// (400 KB an image): over it, GitHub pages load slowly and the check fails.
//
// film.ts (the filming) calls this after it writes each MP4, so a re-filmed clip and a
// re-encoded one come out the same; `make readme-previews` re-encodes them without filming.
//
// Not covered: how the preview looks. A rung that fits can still be soft: look at it.

import { spawnSync } from 'node:child_process';
import { readFileSync, renameSync, statSync, unlinkSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..');

/**
 * Width cap, frames a second and WebP quality, sharpest first. A clip's own preview width caps
 * the first number (a phone's film stays a phone's size).
 */
export const LADDER = [
	[720, 8, 60],
	[720, 6, 55],
	[640, 6, 50],
	[560, 6, 45],
	[480, 5, 40],
	[400, 4, 35]
];

function ffmpeg(args) {
	const done = spawnSync(process.env.FFMPEG ?? 'ffmpeg', ['-v', 'error', '-y', ...args], { encoding: 'utf8' });
	if (done.status !== 0) throw new Error(`ffmpeg failed: ${done.stderr || done.error}`);
}

/** Encode `mp4` into `webp` at the first rung under `budget` bytes; returns that rung. */
export function preview(mp4, webp, width, budget) {
	const trial = `${webp}.trial.webp`;
	for (const [cap, fps, quality] of LADDER) {
		const w = Math.min(width, cap);
		// Compression level 4, not 6: at 6 one twenty-second preview was still encoding after nine
		// minutes here, for a file 8 % smaller on the clip it was measured on.
		ffmpeg(['-i', mp4, '-vf', `fps=${fps},scale=${w}:-2:flags=lanczos`, '-c:v', 'libwebp_anim', '-lossless', '0', '-q:v', String(quality), '-compression_level', '4', '-loop', '0', '-an', trial]);
		const bytes = statSync(trial).size;
		if (bytes <= budget) {
			renameSync(trial, webp);
			return { width: w, fps, quality, bytes };
		}
	}
	unlinkSync(trial);
	throw new Error(`${path.basename(mp4)}: no rung of the ladder fits ${budget} bytes; shorten the film`);
}

if (import.meta.url === pathToFileURL(process.argv[1] ?? '').href) {
	const table = JSON.parse(readFileSync(path.join(ROOT, 'tests', 'contracts', 'readme-clips.json'), 'utf8'));
	if (process.argv[2] === '--one') {
		// film.ts: one film just written, its preview's path and width.
		const [mp4, webp, width] = process.argv.slice(3);
		preview(mp4, webp, Number(width), table.limits.preview_bytes);
		process.exit(0);
	}
	const named = process.argv.slice(2);
	const clips = named.length ? table.clips.filter((c) => named.includes(c.name)) : table.clips;
	if (named.length && clips.length !== named.length) {
		console.error(`readme-previews: not in the table: ${named.filter((n) => !clips.some((c) => c.name === n)).join(', ')}`);
		process.exit(2);
	}
	for (const clip of clips) {
		const got = preview(
			path.join(ROOT, 'site', 'media', `${clip.name}.mp4`),
			path.join(ROOT, 'docs', 'media', `${clip.name}.webp`),
			clip.preview_width,
			table.limits.preview_bytes
		);
		console.log(`${clip.name}: ${got.width}px, ${got.fps} fps, q${got.quality}, ${got.bytes} bytes`);
	}
}
