// The two clocks a panel body is handed (GLASS_HUD §11): an absolute time for
// a timeline or audit row, a relative one for running copy.
//
// Both here rather than inline in each mapping, because the old screens had
// four spellings of "2 minutes ago" and two of the clock — one of which put
// seconds on a row and one of which did not — and a HUD whose timeline and
// audit disagree on what 09:41 looks like reads as two products.

const pad = (n: number): string => n.toString().padStart(2, '0');

/** `hh:mm:ss`, local time, for a row that carries the clock. */
export function clockOf(ms: number | null | undefined): string {
	if (typeof ms !== 'number' || !Number.isFinite(ms) || ms <= 0) return '';
	const d = new Date(ms);
	return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

/** `YYYY-MM-DD hh:mm:ss`, the audit panel's form: a log line has a date. */
export function stampOf(ms: number | null | undefined): string {
	if (typeof ms !== 'number' || !Number.isFinite(ms) || ms <= 0) return '';
	const d = new Date(ms);
	return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${clockOf(ms)}`;
}

/**
 * "just now", "2 minutes ago", "3 hours ago", "yesterday", "4 days ago".
 *
 * A future `ms` is "just now" rather than a negative number: a clock skew
 * between the browser and the backend is a fact of life on a house network and
 * "-1 minutes ago" is what the old moments strip printed on one.
 */
export function agoOf(ms: number, now = Date.now()): string {
	const s = Math.max(0, Math.round((now - ms) / 1000));
	if (s < 45) return 'just now';
	const m = Math.round(s / 60);
	if (m < 60) return m === 1 ? 'a minute ago' : `${m} minutes ago`;
	const h = Math.round(m / 60);
	if (h < 24) return h === 1 ? 'an hour ago' : `${h} hours ago`;
	const d = Math.round(h / 24);
	if (d === 1) return 'yesterday';
	return `${d} days ago`;
}

/** "in 4 minutes", "in an hour", "at 03:00" (later today or tomorrow), "on Tuesday". */
export function untilOf(ms: number, now = Date.now()): string {
	const s = Math.round((ms - now) / 1000);
	if (s <= 0) return 'now';
	if (s < 60) return 'in under a minute';
	const m = Math.round(s / 60);
	if (m < 60) return m === 1 ? 'in a minute' : `in ${m} minutes`;
	const h = Math.round(m / 60);
	if (h < 6) return h === 1 ? 'in an hour' : `in ${h} hours`;
	const at = new Date(ms);
	const day = Math.round(h / 24);
	if (day < 1) return `at ${pad(at.getHours())}:${pad(at.getMinutes())}`;
	if (day < 7) {
		return `on ${at.toLocaleDateString(undefined, { weekday: 'long' })} at ${pad(at.getHours())}:${pad(at.getMinutes())}`;
	}
	return `on ${at.toLocaleDateString()}`;
}

/** Seconds, as the backends report durations, to whole milliseconds. */
export function secondsToMs(seconds: number | null | undefined): number | null {
	return typeof seconds === 'number' && Number.isFinite(seconds) ? Math.round(seconds * 1000) : null;
}
