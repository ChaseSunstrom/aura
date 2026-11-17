// Barge-in: whether a sound heard while Jarvis speaks is the person cutting in.
//
// The console's open mic was deaf while a reply played (its level forced to 0), so
// only a press could interrupt. The microphone hears the speaker, so the reply's
// first LEARN_MS teach what Jarvis sounds like in it (nothing interrupts then), and
// after that only a level OVER_ECHO times that echo's peak, held for HOLD_MS, is a
// person; Jarvis's own louder syllables raise the echo as they come.
//
// The same rule as android-app's `BargeIn.kt`; both suites run the scenarios in
// `tests/contracts/barge-in.json`. Does not cover a person who talks no louder than
// the speaker: they are heard as echo and wait for the reply, or press.

export const LEARN_MS = 800;
export const HOLD_MS = 400;
export const OVER_ECHO = 2.2;
export const MIN_LEVEL = 0.02;
export const FLOOR_RATIO = 8;
export const ECHO_DECAY = 0.995;
/** How long after the last clip ends the speaker is still in the room (output latency, a
 * Bluetooth speaker, the room's ring): the console's open mic hears through this rule until then. */
export const TAIL_MS = 700;

export class BargeIn {
	private since = 0;
	private echo = 0;
	private aboveSince = 0;

	reset(nowMs: number): void {
		this.since = nowMs;
		this.echo = 0;
		this.aboveSince = 0;
	}

	/** One smoothed microphone level during the reply; true once it is the person. */
	hear(nowMs: number, level: number, floor: number): boolean {
		if (nowMs - this.since < LEARN_MS) {
			if (level > this.echo) this.echo = level;
			return false;
		}
		const edge = Math.max(MIN_LEVEL, floor * FLOOR_RATIO, this.echo * OVER_ECHO);
		if (level <= edge) {
			this.aboveSince = 0;
			this.echo = Math.max(this.echo * ECHO_DECAY, Math.min(level, edge / OVER_ECHO));
			return false;
		}
		if (this.aboveSince === 0) this.aboveSince = nowMs;
		return nowMs - this.aboveSince >= HOLD_MS;
	}
}
