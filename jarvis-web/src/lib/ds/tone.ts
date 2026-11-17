// The colours a ds component may be drawn in, by meaning, not by value.
//
// Violet is memory, embeddings and models; amber is "waiting on you"; pink is
// danger. Danger text uses the lifted pink: the base one is for strokes and
// fails AA as small text on the ground.

export const TONES = {
	accent: 'var(--jv-accent)',
	violet: 'var(--jv-violet)',
	warn: 'var(--jv-warn)',
	ok: 'var(--jv-ok)',
	danger: 'var(--jv-danger-text)',
	text: 'var(--jv-text)',
	dim: 'var(--jv-text-dim)',
	faint: 'var(--jv-text-faint)'
} as const;

export type Tone = keyof typeof TONES;

/** The CSS colour for a tone; anything unknown is the plain text colour. */
export function toneColor(tone: string | undefined): string {
	return tone && Object.hasOwn(TONES, tone) ? TONES[tone as Tone] : TONES.text;
}
