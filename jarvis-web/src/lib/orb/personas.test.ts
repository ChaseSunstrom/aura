// The persona rules (ADR-0043), and the invariant that keeps two of one identity
// off the screen.
import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

import {
	CALM_STATES,
	hueOf,
	IDENTITIES,
	PERSONA_THEME,
	PERSONAS,
	pool,
	themeFrom,
	switchTo,
	switched,
	tintFor,
	UNTINTED
} from './personas';
import { STATE_ACCENT } from '$lib/tokens';

/** The table the phone (`android-app/tools/persona_hue_test.py`) and the terminal read too. */
const CONTRACT = JSON.parse(
	readFileSync(fileURLToPath(new URL('../../../../tests/contracts/persona.json', import.meta.url)), 'utf-8')
) as {
	personas: string[];
	hues: Record<string, string>;
	untinted: string;
	calm_states: string[];
	themes: Record<string, string>;
	default: string;
};

describe('two identities, either of them primary', () => {
	it('is AURA and JARVIS, and nobody else (T-0936)', () => {
		expect([...PERSONAS]).toEqual(['AURA', 'JARVIS']);
	});

	it('gives each its own SOUL file, voice, hue and wake word', () => {
		for (const p of PERSONAS) {
			const spec = IDENTITIES[p];
			expect(spec.soul).toBeTruthy();
			expect(spec.voice).toBeTruthy();
			expect(spec.wakeWord).toBeTruthy();
			expect(hueOf(p), `${p} has a hue`).toMatch(/^#|rgb/);
		}
	});

	it('gives no two of them the same hue or the same wake word', () => {
		expect(new Set(PERSONAS.map(hueOf)).size).toBe(PERSONAS.length);
		expect(new Set(PERSONAS.map((p) => IDENTITIES[p].wakeWord)).size).toBe(PERSONAS.length);
	});

	it('paints each with the token the contract names, so the phone and the terminal wear the same hue', () => {
		expect(PERSONAS.map((p) => p.toLowerCase())).toEqual(CONTRACT.personas);
		for (const p of PERSONAS) expect(IDENTITIES[p].accent, p).toBe(CONTRACT.hues[p.toLowerCase()]);
		expect(UNTINTED.toLowerCase()).toBe(CONTRACT.untinted);
		expect([...CALM_STATES]).toEqual(CONTRACT.calm_states);
	});

	it('does not lean on hue alone, because under deuteranopia it is one colour', () => {
		// V3_PLAN §21 measured the worst pairwise separation at 1.00 — identical.
		// The blade density is the cue that survives, so the two must differ by it.
		expect(new Set(PERSONAS.map((p) => IDENTITIES[p].blades)).size).toBeGreaterThan(1);
	});
});

describe('the primary is never in the pool', () => {
	it('leaves exactly the other one orbiting, whoever is primary', () => {
		for (const p of PERSONAS) {
			expect(pool(p)).toHaveLength(1);
			expect(pool(p)).not.toContain(p);
		}
	});

	it('swaps the two on a switch, so there are never two of one identity', () => {
		const before = switched('JARVIS');
		expect(before.pool).toContain('AURA');

		const after = switched('AURA');
		expect(after.pool, 'the previous primary comes back').toContain('JARVIS');
		expect(after.pool, 'the new one leaves').not.toContain('AURA');
		expect(new Set([after.name, ...after.pool]).size).toBe(PERSONAS.length);
	});
});

describe('switching', () => {
	it('takes a name in any case, as a voice command gives it', () => {
		expect(switchTo('JARVIS', 'aura')).toBe('AURA');
		expect(switchTo('AURA', '  Jarvis ')).toBe('JARVIS');
	});

	it('keeps the current primary when asked for something that is not one', () => {
		// A mishearing must not empty the pool or blank the HUD; nor does a persona since removed (T-0936).
		expect(switchTo('AURA', 'auras')).toBe('AURA');
		expect(switchTo('AURA', '')).toBe('AURA');
		for (const gone of ['Ultron', 'Ada', 'Friday']) expect(switchTo('JARVIS', gone)).toBe('JARVIS'); // T-0936
	});

	it('is reversible', () => {
		expect(switchTo(switchTo('JARVIS', 'AURA'), 'JARVIS')).toBe('JARVIS');
	});

	it('hands a surface everything it has to change, in one object', () => {
		// The acceptance is "no stale accent colour or name left anywhere on any
		// surface", and a list kept per surface is a list that differs per surface.
		const s = switched('AURA');
		expect(s).toMatchObject({
			name: 'AURA',
			accent: '--jv-persona-aura',
			voice: 'aura',
			wakeWord: 'aura'
		});
		expect(s.hue).toBe(hueOf('AURA'));
		expect(s.soul).toContain('aura');
	});
});

describe('the theme pack the primary wears', () => {
	it('is the contract’s, and AURA’s when nothing says', () => {
		expect(PERSONA_THEME).toEqual(Object.fromEntries(CONTRACT.personas.map((p) => [p, CONTRACT.themes[p]])));
		expect(themeFrom({})).toBe(CONTRACT.default);
		expect(themeFrom({ persona: 'JARVIS' })).toBe('jarvis');
		// A persona since removed (T-0936) wears the default's pack.
		expect(themeFrom({ persona: 'Ada' })).toBe('aura'); // T-0936
		expect(themeFrom({ persona: 'AURA' })).toBe('aura');
		// The server's own word wins; a theme this console has no pack for does not.
		expect(themeFrom({ theme: 'jarvis', persona: 'aura' })).toBe('jarvis');
		expect(themeFrom({ theme: 'nebula', persona: 'jarvis' })).toBe('jarvis');
		expect(themeFrom({ theme: 'nebula', persona: 'nobody' })).toBe('aura');
	});
});

describe('the core wears the primary’s hue', () => {
	it('in the contract’s calm states, keeps its own colour in the rest, and JARVIS adds none', () => {
		// The console's core was cyan under another persona while the phone's reactor was pink (T-0807).
		for (const p of PERSONAS)
			for (const s of Object.keys(STATE_ACCENT)) {
				const tinted = CONTRACT.calm_states.includes(s) && p.toLowerCase() !== CONTRACT.untinted;
				expect(tintFor(p, s), `${p} ${s}`).toBe(tinted ? CONTRACT.hues[p.toLowerCase()] : undefined);
			}
	});
});
