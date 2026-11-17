// The two personas, AURA and JARVIS, and the rule that either can be the one you are
// talking to (ADR-0043). AURA is the default; each wears her own theme pack (T-0861,
// `tests/contracts/persona.json` `themes`). The other three went at the owner's word
// (T-0936).
//
// What a persona owns is a short list and this module is it: a SOUL file, a
// voice profile, a hue, and the wake word that follows the name. Memory, tools,
// skills, the task store, integrations and history are **shared** — ADR-0043
// argues the alternative rather than assuming this one.

import { DEFAULT_THEME, THEME_IDS, type ThemeId } from '$lib/themes';
import { TOKENS, type TokenName } from '$lib/tokens';

export const PERSONAS = ['AURA', 'JARVIS'] as const;
export type Persona = (typeof PERSONAS)[number];

export interface PersonaSpec {
	name: Persona;
	/** Its own SOUL file: name, voice, manner, greeting style. */
	soul: string;
	/** Its CosyVoice profile. */
	voice: string;
	/** The token its hue is typed in. The HUD's accents are derived from it. */
	accent: TokenName;
	/**
	 * The wake word that follows the persona.
	 *
	 * `aura-core/tests/test_packaging.py` pins `WAKE_WORD` across
	 * `docker-compose.yml`, `configuration.yaml` and `.env.example`. The brief's
	 * note is explicit that a per-persona wake word makes that a set
	 * rather than one value, and that the pin is to be **extended, not routed
	 * around**.
	 */
	wakeWord: string;
	/**
	 * Blades on its ring.
	 *
	 * Not decoration: a hue alone can read as the same colour under a colour-vision
	 * deficiency (`V3_PLAN` §21 measured it), so the hue never travels alone, and the
	 * density is the cue that survives every CVD simulation and greyscale, because it
	 * is not colour.
	 */
	blades: number;
}

export const IDENTITIES: Readonly<Record<Persona, PersonaSpec>> = {
	AURA: {
		name: 'AURA',
		soul: 'personas/aura/SOUL.md',
		voice: 'aura',
		accent: '--jv-persona-aura',
		wakeWord: 'aura',
		blades: 48
	},
	JARVIS: {
		name: 'JARVIS',
		soul: 'SOUL.md',
		voice: 'jarvis',
		accent: '--jv-persona-jarvis',
		wakeWord: 'jarvis',
		blades: 36
	}
};

/** The theme pack each persona wears: the contract's `themes` (each her own). */
export const PERSONA_THEME: Readonly<Record<string, ThemeId>> = {
	aura: 'aura',
	jarvis: 'jarvis'
};

/**
 * The theme a console wears: the one the server named (`/api/status` or
 * `persona.switch` `theme`) when this console has its pack, else the persona's
 * own, else the default's. Unknown or missing never leaves a surface unthemed.
 */
export function themeFrom({ theme, persona }: { theme?: string; persona?: string }): ThemeId {
	const named = (theme ?? '').toLowerCase();
	if ((THEME_IDS as readonly string[]).includes(named)) return named as ThemeId;
	return PERSONA_THEME[(persona ?? '').toLowerCase()] ?? DEFAULT_THEME;
}

/** The hue a persona paints with, resolved. */
export const hueOf = (p: Persona): string => TOKENS[IDENTITIES[p].accent];

/**
 * The reactor states in which the orb wears the primary's hue. Every other
 * state keeps its own colour because that colour is information: an approval
 * waiting in a persona's own hue is an approval nobody noticed. `tests/contracts/persona.json`
 * `calm_states` is the table the phone and the terminal read too.
 */
export const CALM_STATES: ReadonlySet<string> = new Set(['idle', 'speaking', 'tool', 'goal']);

/**
 * The one persona that adds no tint: its token is an alias of the reactor's own
 * light, and tinting with it would still brighten the deep idle colour into the
 * accent. The contract's `untinted`.
 */
export const UNTINTED: Persona = 'JARVIS';

/** The token the core wears for `primary` in reactor state `reactor`, or undefined for its own colour. */
export const tintFor = (primary: Persona, reactor: string): TokenName | undefined =>
	primary !== UNTINTED && CALM_STATES.has(reactor) ? IDENTITIES[primary]?.accent : undefined;

/**
 * The one that is not primary.
 *
 * *"The previous primary returns to the companion pool and the new one leaves
 * it, so there are never two of the same identity on screen."* Expressed as a
 * function of the primary rather than as a list somebody maintains, because a
 * maintained list is how you end up with two of one.
 */
export const pool = (primary: Persona): Persona[] => PERSONAS.filter((p) => p !== primary);

/**
 * Switch the primary identity.
 *
 * Returns the new primary, or the current one unchanged when asked for something
 * that is not a persona — a voice command mishearing "switch to Fridays" should
 * not empty the pool.
 */
export function switchTo(current: Persona, next: string): Persona {
	const found = PERSONAS.find((p) => p.toLowerCase() === next.trim().toLowerCase());
	return found ?? current;
}

/**
 * Everything a surface has to change when the primary does.
 *
 * Gathered in one place because the acceptance is *"no stale accent colour or
 * name left anywhere on any surface"*, and a list that lives in each surface is
 * a list that is different in each surface.
 */
export interface Switched {
	name: Persona;
	accent: TokenName;
	hue: string;
	voice: string;
	wakeWord: string;
	soul: string;
	/** Who orbits now. */
	pool: Persona[];
}

export const switched = (primary: Persona): Switched => ({
	name: primary,
	accent: IDENTITIES[primary].accent,
	hue: hueOf(primary),
	voice: IDENTITIES[primary].voice,
	wakeWord: IDENTITIES[primary].wakeWord,
	soul: IDENTITIES[primary].soul,
	pool: pool(primary)
});
