/**
 * How fast against the limit, for the car's Speed panel (the owner, 2026-09-25: "a
 * nice panel for speeding that will show my speed, and a bar that fills up ... up to
 * 20 MPH over the speed limit, with different text that says, safe, speedy, risky,
 * dangerous, deadly ... and Jarvis should say 'you should slow down sir' ... and it
 * should be determined by the average speed I'm going over a little bit, or by using
 * my location ... so its not inaccurate if I'm going like 20 on the interstate because
 * of traffic").
 *
 * The LIMIT is the road's (the house's map-match, `/api/maps/match`: OpenStreetMap's posted
 * `maxspeed` on the road the last fixes are on, or the road kind's usual one), never the
 * traffic's pace, so a jam on the interstate does not lower it. The SPEED judged is the average of the last [SpeedPrefs.windowMs], so a
 * moment's overtake is not a warning. Pure; `speed.test.ts` pins the words.
 */

import { metres } from './route';

/** What the house said about the road under the car (T-0755), and where the car asked. */
export interface RoadLimit {
	lat: number;
	lon: number;
	limit_mph: number | null;
	source: string;
}

/** Past this far from where it was asked, a limit is a road behind the car. */
export const LIMIT_STALE_M = 250;

/** The house's map-match answer as the limit at [lat],[lon]; null when it says none. */
export function roadLimit(body: unknown, lat: number, lon: number): RoadLimit | null {
	const o = body as { limit_mph?: unknown; limit_source?: unknown } | null;
	if (!o || typeof o !== 'object' || !('limit_mph' in o)) return null;
	const mph = typeof o.limit_mph === 'number' && Number.isFinite(o.limit_mph) && o.limit_mph > 0 ? o.limit_mph : null;
	return { lat, lon, limit_mph: mph, source: String(o.limit_source ?? 'none') };
}

/**
 * The limit for where the car is now: the last answer while the car is near where it asked,
 * else none. A failed ask used to leave the last limit standing, so the car showed a road it
 * had left kilometres back (the owner, 2026-11-08: "when it does have it, it's completely wrong").
 */
export function limitHere(limit: RoadLimit | null, lat: number, lon: number): RoadLimit | null {
	return limit && metres(limit.lat, limit.lon, lat, lon) <= LIMIT_STALE_M ? limit : null;
}

/**
 * The person's settings (`hud.car_speed_*`, the owner, 2026-09-25: "I should be able to
 * configure the speed stuff, and I only want it to say something if I am going 15 or
 * over"). These are the defaults; Settings or Jarvis can change each one.
 */
export interface SpeedPrefs {
	/** Over the limit by this much (mph) fills the bar; past it the bar goes past 100 %. */
	fullOver: number;
	/** Jarvis says nothing below this many mph over. */
	warnOver: number;
	/** The speed judged: the mean of this long, not a single fix. */
	windowMs: number;
	/** Jarvis says a warning at most this often, and again sooner only when it is worse. */
	warnEveryMs: number;
}

export const DEFAULT_PREFS: SpeedPrefs = { fullOver: 20, warnOver: 15, windowMs: 8_000, warnEveryMs: 90_000 };
export const FULL_OVER_MPH = DEFAULT_PREFS.fullOver;
export const WARN_EVERY_MS = DEFAULT_PREFS.warnEveryMs;

export const MPS_TO_MPH = 2.236936;

export type Band = 'safe' | 'speedy' | 'risky' | 'dangerous' | 'deadly';

export const BAND_WORD: Record<Band, string> = {
	safe: 'Safe',
	speedy: 'Speedy',
	risky: 'Risky',
	dangerous: 'Dangerous',
	deadly: 'Deadly'
};

/**
 * The word for being `over` mph above the limit. The steps scale with where the bar is
 * full: at twenty, safe to 2, speedy to 7, risky to 12, dangerous to 20, then deadly.
 */
export function bandOf(over: number, fullOver = FULL_OVER_MPH): Band {
	if (over <= fullOver * 0.1) return 'safe';
	if (over < fullOver * 0.35) return 'speedy';
	if (over < fullOver * 0.6) return 'risky';
	if (over < fullOver) return 'dangerous';
	return 'deadly';
}

const RANK: Record<Band, number> = { safe: 0, speedy: 1, risky: 2, dangerous: 3, deadly: 4 };

/** A rolling window of fixes, and their mean. */
export class SpeedWindow {
	private fixes: { at: number; mph: number }[] = [];

	/** [windowMs] may change between fixes (the setting moved); the next fix applies it. */
	add(at: number, metresPerSecond: number, windowMs = DEFAULT_PREFS.windowMs): void {
		this.fixes.push({ at, mph: Math.max(0, metresPerSecond) * MPS_TO_MPH });
		while (this.fixes.length && at - this.fixes[0].at > windowMs) this.fixes.shift();
	}

	get mph(): number {
		if (!this.fixes.length) return 0;
		return this.fixes.reduce((n, f) => n + f.mph, 0) / this.fixes.length;
	}
}

export interface Reading {
	mph: number;
	limit: number | null;
	over: number;
	/** 0.. (past 1 is past [full] over): how full the bar is. */
	fill: number;
	band: Band;
	/** The mph over at which the bar is full. */
	full: number;
}

export function reading(mph: number, limit: number | null, fullOver = FULL_OVER_MPH): Reading {
	const over = limit === null ? 0 : Math.max(0, mph - limit);
	return { mph, limit, over, fill: over / fullOver, band: limit === null ? 'safe' : bandOf(over, fullOver), full: fullOver };
}

/** What each persona says, per band. JARVIS's is the owner's own line. */
const LINES: Record<string, Partial<Record<Band, string[]>>> = {
	jarvis: {
		risky: ['You should slow down, sir.', 'A little quick, sir.', 'Perhaps ease off, sir.'],
		dangerous: ['Sir, you are well over the limit.', 'I would slow down, sir. Now.'],
		deadly: ['Sir, this is dangerous. Please slow down.', 'Twenty over, sir. Slow down.']
	}
};

/**
 * The line to say now, or null. Said only at [SpeedPrefs.warnOver] mph over or more, at
 * most every [SpeedPrefs.warnEveryMs], and sooner only for a worse band than the last
 * one said. A threshold set low enough to fall in a mild band says the risky words.
 */
export function warning(
	r: Pick<Reading, 'band' | 'over' | 'limit'>,
	persona: string,
	now: number,
	last: { at: number; band: Band } | null,
	prefs: Pick<SpeedPrefs, 'warnOver' | 'warnEveryMs'> = DEFAULT_PREFS,
	pick: (n: number) => number = (n) => Math.floor(Math.random() * n)
): { text: string; band: Band } | null {
	if (r.limit === null || r.over < prefs.warnOver) return null;
	const band: Band = RANK[r.band] < RANK.risky ? 'risky' : r.band;
	const worse = !last || RANK[band] > RANK[last.band];
	if (!worse && last && now - last.at < prefs.warnEveryMs) return null;
	const lines = (LINES[persona.toLowerCase()] ?? LINES.jarvis)[band] ?? LINES.jarvis[band]!;
	return { text: lines[pick(lines.length)], band };
}
