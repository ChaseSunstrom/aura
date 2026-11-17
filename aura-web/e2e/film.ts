// The README's clips are filmed a frame at a time (T-0865): Chrome's virtual time holds every
// clock the page reads (animation frames, timers, CSS animations) and lets it move one
// thirtieth of a second per picture, so each frame is drawn whole however slow the machine,
// and a clip plays at a true thirty frames a second on a host that renders WebGL in software.
//
// What that asks of a script: nothing here may wait on the page's own clock. A Playwright
// click waits for two animation frames that never come while time is held, and a socket the
// page opens then never connects, so a film presses with the mouse at a place it measured,
// types with the keyboard, and waits for the page by rolling (`until`).
//
// `LiveFilm` is the other way, for what a held clock cannot film: the microphone runs on a
// real clock, so that clip is taken as the browser draws it and is only as smooth as the
// machine is quick.
//
// Not covered: sound. A clip is silent.
import { spawnSync } from 'node:child_process';
import { mkdirSync, readdirSync, rmSync, writeFileSync } from 'node:fs';

import type { CDPSession, Locator, Page } from '@playwright/test';

export const FPS = 30;
/** The README previews' encoder: one ladder for a filmed clip and a re-cut one. */
const PREVIEWS = new URL('../../scripts/media/readme-previews.mjs', import.meta.url).pathname;
/** Tasks the page may run back to back before its held clock is moved on regardless. */
const STARVED = 300;
/**
 * A rehearsal: FILM_STEP=6 draws one picture for every six, so a script's presses and waits
 * can be checked in a sixth of the time. The clip it cuts plays six times too fast.
 */
const STEP = Math.max(1, Number(process.env.FILM_STEP ?? 1));

export interface Cut {
	/** The MP4 a site plays: every frame, at the size it was filmed. */
	mp4: string;
	/** The looping preview a README shows. */
	webp: string;
	/** The preview's width; its height follows. */
	previewWidth: number;
	/** How a film's captions are set, read from the page it filmed. Needed only if it has any. */
	captions?: CaptionLook;
	/** The still a page shows before the film plays: a JPEG cut `at` a share (0..1) of the film's length. */
	poster?: { jpg: string; at: number };
}

/** A caption is the film's own subtitle, never part of the page: the README says which film has them. */
export interface CaptionLook {
	/** The font's family name and the folder that holds its file. */
	family: string;
	fonts: string;
	/** Red, green, blue, 0 to 255. */
	colour: [number, number, number];
	/** Letter height in the film's own pixels, and how far above the foot of the picture. */
	size: number;
	lift: number;
}

const stamp = (seconds: number): string => {
	const cs = Math.round(seconds * 100);
	const two = (n: number) => String(n).padStart(2, '0');
	return `${Math.floor(cs / 360000)}:${two(Math.floor(cs / 6000) % 60)}:${two(Math.floor(cs / 100) % 60)}.${two(cs % 100)}`;
};

/** Captions as an ASS subtitle file the encoder burns in. */
function subtitles(captions: { text: string; from: number; to: number }[], look: CaptionLook, width: number, height: number): string {
	const [r, g, b] = look.colour.map((c) => c.toString(16).padStart(2, '0').toUpperCase());
	return [
		'[Script Info]',
		'ScriptType: v4.00+',
		`PlayResX: ${width}`,
		`PlayResY: ${height}`,
		'',
		'[V4+ Styles]',
		'Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding',
		// Bottom centre, no outline or shadow, tracked like the console's own labels.
		`Style: Caption,${look.family},${look.size},&H00${b}${g}${r},&H00${b}${g}${r},&H00000000,&H00000000,0,0,0,0,100,100,${Math.round(look.size / 4)},0,1,0,0,2,0,0,${look.lift},1`,
		'',
		'[Events]',
		'Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text',
		...captions.map((c) => `Dialogue: 0,${stamp(c.from)},${stamp(c.to)},Caption,,0,0,0,,{\\fad(180,180)}${c.text.toUpperCase()}`),
		''
	].join('\n');
}

/** A step that hangs is named, not waited on for the test's whole timeout. */
function within<T>(step: Promise<T>, what: string, ms = 90_000): Promise<T> {
	let timer: NodeJS.Timeout;
	const late = new Promise<never>((_, reject) => (timer = setTimeout(() => reject(new Error(what)), ms)));
	return Promise.race([step, late]).finally(() => clearTimeout(timer));
}

function ffmpeg(args: string[]): void {
	const done = spawnSync(process.env.FFMPEG ?? 'ffmpeg', ['-v', 'error', '-y', ...args], { encoding: 'utf8' });
	if (done.status !== 0) throw new Error(`ffmpeg failed: ${done.stderr || done.error}`);
}

/** Frames (an ffmpeg input) as an MP4 and a looping preview. */
function encode(input: string[], to: Cut, seconds: number, subs = ''): void {
	// Captions go on first, at the film's own size, so the preview's are scaled with its picture.
	const burn = subs && to.captions ? `ass=f=${subs}:fontsdir=${to.captions.fonts},` : '';
	// yuv420p and an even size: what every browser's and phone's player decodes.
	ffmpeg([...input, '-vf', `${burn}fps=${FPS},scale=trunc(iw/2)*2:trunc(ih/2)*2`, '-c:v', 'libx264', '-preset', 'slow', '-crf', '20', '-pix_fmt', 'yuv420p', '-movflags', '+faststart', '-an', to.mp4]);
	// The preview is cut from that MP4 (captions and all) down the README's ladder, the same way
	// `make readme-previews` re-cuts it without filming: scripts/media/readme-previews.mjs.
	const cut = spawnSync(process.execPath, [PREVIEWS, '--one', to.mp4, to.webp, String(to.previewWidth)], { encoding: 'utf8' });
	if (cut.status !== 0) throw new Error(`the preview failed: ${cut.stderr || cut.error}`);
	// The still is cut from the MP4, not from a frame: it carries the captions the film carries.
	if (to.poster) ffmpeg(['-ss', (to.poster.at * seconds).toFixed(2), '-i', to.mp4, '-frames:v', '1', '-q:v', '4', to.poster.jpg]);
}

function emptied(frames: string): void {
	rmSync(frames, { recursive: true, force: true });
	mkdirSync(frames, { recursive: true });
}

export class Film {
	private n = 0;
	private readonly captions: { text: string; from: number; to: number }[] = [];
	private constructor(
		private readonly page: Page,
		private readonly cdp: CDPSession,
		private readonly frames: string
	) {}

	/** Hold the page's clocks from here on. The page is already open and settled. */
	static async start(page: Page, frames: string): Promise<Film> {
		emptied(frames);
		const cdp = await page.context().newCDPSession(page);
		await cdp.send('Emulation.setVirtualTimePolicy', { policy: 'pause' });
		return new Film(page, cdp, frames);
	}

	private async frame(): Promise<void> {
		const spent = new Promise<void>((resolve) => this.cdp.once('Emulation.virtualTimeBudgetExpired', () => resolve()));
		// The starvation count: a page that keeps posting tasks to itself (a stream of socket
		// frames, a decoder) never lets the held clock move, and the film stood still for good.
		await this.cdp.send('Emulation.setVirtualTimePolicy', { policy: 'advance', budget: (1000 / FPS) * STEP, maxVirtualTimeTaskStarvationCount: STARVED });
		await within(spent, `frame ${this.n}: the page's clock did not move`);
		const shot = await within(this.cdp.send('Page.captureScreenshot', { format: 'png', optimizeForSpeed: true }), `frame ${this.n}: no picture came`);
		writeFileSync(`${this.frames}/f${String(this.n++).padStart(5, '0')}.png`, Buffer.from(shot.data, 'base64'));
	}

	/** Let the page's clocks run for this long, a picture every thirtieth of a second. */
	async roll(seconds: number): Promise<void> {
		const count = Math.max(1, Math.round((seconds * FPS) / STEP));
		for (let i = 0; i < count; i++) await this.frame();
	}

	/** Roll until the page has done what is waited for: its own work needs its clock. */
	async until(done: () => boolean | Promise<boolean>, what: string, seconds = 4): Promise<void> {
		for (let i = 0; i < (seconds * FPS) / STEP; i++) {
			if (await done()) return;
			await this.frame();
		}
		if (!(await done())) throw new Error(`${what}: not after ${seconds} s of film`);
	}

	/** Type as a person does: a key, then a few frames. */
	async type(text: string, framesPerKey = 2): Promise<void> {
		for (const key of text) {
			await this.page.keyboard.type(key);
			await this.roll(framesPerKey / FPS);
		}
	}

	/** Press and let go at the middle of what a locator names, with the frames between drawn. */
	async press(target: Locator, held = 0.1): Promise<void> {
		// A control below the fold is scrolled to first, as a person would: the mouse presses a
		// place on the screen, and a place off it presses nothing.
		const moved = await target.evaluate((el) => {
			const r = el.getBoundingClientRect();
			if (r.top >= 0 && r.bottom <= window.innerHeight) return false;
			el.scrollIntoView({ block: 'center' });
			return true;
		});
		if (moved) await this.roll(0.3);
		const box = await target.boundingBox();
		if (!box) throw new Error('nothing to press: the element is not on the page');
		await this.page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
		await this.roll(0.1);
		await this.page.mouse.down();
		await this.roll(held);
		await this.page.mouse.up();
	}

	/** Give the page its clocks back. */
	async stop(): Promise<void> {
		await this.cdp.send('Emulation.setVirtualTimePolicy', { policy: 'advance', budget: 600_000 }).catch(() => undefined);
		await this.cdp.detach().catch(() => undefined);
	}

	/**
	 * Words under the picture from this frame until the next caption; '' ends the last one.
	 * The film's own subtitle: nothing is added to the page.
	 */
	caption(text: string): void {
		const at = this.n / FPS;
		const open = this.captions[this.captions.length - 1];
		if (open && open.to === Infinity) open.to = at;
		if (text) this.captions.push({ text, from: at, to: Infinity });
	}

	cut(to: Cut): void {
		if (readdirSync(this.frames).length === 0) throw new Error('no frames were filmed');
		let subs = '';
		if (this.captions.length > 0) {
			if (!to.captions) throw new Error('this film has captions and the cut does not say how they are set');
			const size = this.page.viewportSize() ?? { width: 0, height: 0 };
			const end = this.n / FPS;
			subs = `${this.frames}/captions.ass`;
			writeFileSync(subs, subtitles(this.captions.map((c) => ({ ...c, to: Math.min(c.to, end) })), to.captions, size.width, size.height));
		}
		encode(['-framerate', String(FPS), '-i', `${this.frames}/f%05d.png`], to, this.n / FPS, subs);
	}
}

export class LiveFilm {
	private readonly shots: { file: string; at: number }[] = [];
	private constructor(
		private readonly cdp: CDPSession,
		private readonly frames: string
	) {}

	/** Take the page as the browser draws it, from now. */
	static async start(page: Page, frames: string): Promise<LiveFilm> {
		emptied(frames);
		const cdp = await page.context().newCDPSession(page);
		const film = new LiveFilm(cdp, frames);
		cdp.on('Page.screencastFrame', (frame) => {
			const file = `f${String(film.shots.length).padStart(5, '0')}.jpg`;
			writeFileSync(`${frames}/${file}`, Buffer.from(frame.data, 'base64'));
			film.shots.push({ file, at: frame.metadata.timestamp ?? Date.now() / 1000 });
			void cdp.send('Page.screencastFrameAck', { sessionId: frame.sessionId }).catch(() => undefined);
		});
		await cdp.send('Page.startScreencast', { format: 'jpeg', quality: 95, everyNthFrame: 1 });
		return film;
	}

	async stop(): Promise<void> {
		await this.cdp.send('Page.stopScreencast').catch(() => undefined);
		await this.cdp.detach().catch(() => undefined);
	}

	/** How many pictures a second the browser managed, so a slow take is seen and not shipped. */
	rate(): number {
		if (this.shots.length < 2) return 0;
		return (this.shots.length - 1) / (this.shots[this.shots.length - 1].at - this.shots[0].at);
	}

	cut(to: Cut): void {
		if (this.shots.length < 2) throw new Error('no frames were filmed');
		// Each picture is held until the next was drawn: the browser's own timing, kept.
		const rows = ['ffconcat version 1.0'];
		this.shots.forEach((shot, i) => {
			const next = this.shots[i + 1];
			rows.push(`file '${shot.file}'`, `duration ${(next ? next.at - shot.at : 1 / FPS).toFixed(4)}`);
		});
		rows.push(`file '${this.shots[this.shots.length - 1].file}'`);
		writeFileSync(`${this.frames}/frames.ffconcat`, rows.join('\n') + '\n');
		const seconds = this.shots[this.shots.length - 1].at - this.shots[0].at;
		encode(['-f', 'concat', '-safe', '0', '-i', `${this.frames}/frames.ffconcat`], to, seconds);
	}
}
