import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { initialHud, reduce, type HudState, type ToolCard } from '$lib/hermes/hud';
import { NO_GOAL, parseGoalText } from '$lib/hermes/goal';
import type { HermesEvent, StatusReport } from '$lib/hermes/protocol';
import { REST, derive, inputsFrom, rankOf, segmentsOf, stateFrom, warmingProgress } from './reactorState';
import type { ReactorInputs } from './reactorState';
import { PRECEDENCE, STATES } from './spec';

/**
 * The state machine, held to `tests/contracts/reactor.json` `derivation`.
 *
 * The contract carries the truth table; this runs every case of it against the
 * console's `derive`, and the phone's `ReactorState.kt` and the console
 * agent's `reactor.py` run the same cases against theirs. That is what stops an
 * approval reading as "thinking" in the browser while the phone says "needs
 * you" — the failure the old five-state orb had, where each surface mapped
 * events to states in its own file.
 */

const CONTRACT = JSON.parse(
	readFileSync(new URL('../../../../tests/contracts/reactor.json', import.meta.url), 'utf8')
) as {
	derivation: {
		inputs: Record<string, string>;
		rules: { when: string; state: string }[];
		rest: Record<string, boolean | number>;
		cases: { name: string; inputs: Partial<ReactorInputs>; state: string; goal: boolean }[];
		sequences: { cases: { name: string; events: HermesEvent[]; state: string }[] };
	};
};

describe('the truth table', () => {
	it('starts from the contract’s resting inputs', () => {
		expect(REST).toEqual(CONTRACT.derivation.rest);
	});

	it('declares exactly the inputs the contract names', () => {
		expect(Object.keys(REST).sort()).toEqual(Object.keys(CONTRACT.derivation.inputs).sort());
	});

	for (const c of CONTRACT.derivation.cases) {
		it(c.name, () => {
			expect(derive(c.inputs)).toEqual({ state: c.state, goal: c.goal });
		});
	}

	it('covers every rule the contract lists', () => {
		// A rule with no case behind it is a branch nobody has ever exercised.
		const reached = new Set(CONTRACT.derivation.cases.map((c) => c.state));
		for (const rule of CONTRACT.derivation.rules) expect(reached).toContain(rule.state);
	});

	it('reaches every state in the precedence list', () => {
		const reached = new Set(CONTRACT.derivation.cases.map((c) => c.state));
		for (const state of PRECEDENCE) expect(reached, state).toContain(state);
	});
});

describe('precedence', () => {
	/** Each state, with a set of inputs that alone produces it. */
	const ALONE: Record<string, Partial<ReactorInputs>> = {
		kill: { kill: true },
		offline: { linkUp: false },
		alert: { errorEvent: true },
		attention: { attention: true },
		warming: { warming: true },
		speaking: { speaking: true },
		listening: { listening: true },
		tool: { toolsRunning: 1 },
		thinking: { turnRunning: true },
		idle: {}
	};

	it('gives each state a trigger of its own', () => {
		for (const [state, inputs] of Object.entries(ALONE)) {
			expect(derive(inputs).state, state).toBe(state);
		}
	});

	it('lets the higher of any two win, in both orders', () => {
		// Every pair, not just the neighbours: the ordering is a total order or
		// it is nothing, and a hand-written `if` chain can easily be neither.
		for (const a of PRECEDENCE) {
			for (const b of PRECEDENCE) {
				if (a === b) continue;
				const both = derive({ ...ALONE[a], ...ALONE[b] }).state;
				const expected = rankOf(a) < rankOf(b) ? a : b;
				expect(both, `${a} vs ${b}`).toBe(expected);
			}
		}
	});

	it('never returns goal as a state', () => {
		for (const inputs of Object.values(ALONE)) {
			expect(derive({ ...inputs, goalActive: true }).state).not.toBe('goal');
		}
	});

	it('wears the goal on every state but the two dark ones', () => {
		for (const [state, inputs] of Object.entries(ALONE)) {
			const dark = state === 'kill' || state === 'offline';
			expect(derive({ ...inputs, goalActive: true }).goal, state).toBe(!dark);
		}
	});
});

describe('transitions', () => {
	/** A turn from wake word to reply, as the reactor sees it. */
	it('walks a whole turn: listening, thinking, tool, speaking, idle', () => {
		const seen = [
			derive({ listening: true }),
			derive({ turnRunning: true }),
			derive({ turnRunning: true, toolsRunning: 1 }),
			derive({ turnRunning: true, toolsRunning: 0 }),
			derive({ speaking: true }),
			derive({})
		].map((d) => d.state);
		expect(seen).toEqual(['listening', 'thinking', 'tool', 'thinking', 'speaking', 'idle']);
	});

	it('holds a goal across the whole turn and drops it when the link goes', () => {
		expect(derive({ goalActive: true, listening: true }).goal).toBe(true);
		expect(derive({ goalActive: true, turnRunning: true }).goal).toBe(true);
		expect(derive({ goalActive: true, linkUp: false }).goal).toBe(false);
	});

	it('reads an adapter error as an alert mid-turn and as offline at rest', () => {
		// The judgement §10 asks for: mid-turn something was lost and the person
		// needs to know; at rest nothing was, and the honest reading is the link.
		expect(derive({ adapterError: true, turnRunning: true }).state).toBe('alert');
		expect(derive({ adapterError: true }).state).toBe('offline');
	});
});

describe('folding the console’s live sources', () => {
	const hudWith = (patch: Partial<HudState>): HudState => ({ ...initialHud(), ...patch });
	const tool = (state: ToolCard['state']): ToolCard => ({
		id: `t-${state}`,
		name: 'shell',
		context: '',
		args: {},
		state,
		progress: '',
		duration: null,
		error: null,
		result: null,
		startedAt: 0,
		finishedAt: null
	});

	it('is warming while the first connect is under way, and offline once a link is lost or refused', () => {
		// Not idle: "idle" claims the assistant is ready, and it is not. Not offline either:
		// every page load opened on the amber offline core for the moment the socket took (T-0671).
		expect(stateFrom({ hud: initialHud(), link: 'idle' }).state).toBe('warming');
		expect(stateFrom({ hud: initialHud(), link: 'connecting' }).state).toBe('warming');
		expect(stateFrom({ hud: initialHud(), link: 'reconnecting' }).state).toBe('offline');
		expect(stateFrom({ hud: initialHud(), link: 'offline' }).state).toBe('offline');
		expect(stateFrom({ hud: initialHud(), link: null }).state).toBe('offline');
	});

	it('is idle on a connected link with nothing happening', () => {
		expect(stateFrom({ hud: initialHud(), link: 'connected' }).state).toBe('idle');
	});

	it('counts only the running tools as lit segments, and all of them as segments', () => {
		const hud = hudWith({
			turn: { ...initialHud().turn, running: true, tools: [tool('done'), tool('running'), tool('failed')] }
		});
		expect(inputsFrom({ hud, link: 'connected' }).toolsRunning).toBe(1);
		// The ring is a record of the turn, not a gauge of what is left (§10 row 5).
		expect(segmentsOf(hud)).toEqual({ segments: 3, lit: 1 });
	});

	it('takes an approval, a clarify or a parked goal as attention', () => {
		const approval = hudWith({ approvals: [{ requestId: 'a' }] as HudState['approvals'] });
		expect(stateFrom({ hud: approval, link: 'connected' }).state).toBe('attention');
		const clarify = hudWith({ clarifies: [{ requestId: 'c' }] as HudState['clarifies'] });
		expect(stateFrom({ hud: clarify, link: 'connected' }).state).toBe('attention');
		const parked = hudWith({ goal: parseGoalText('⏳ parked: waiting on you', 0) });
		expect(parked.goal.phase).toBe('parked');
		expect(stateFrom({ hud: parked, link: 'connected' }).state).toBe('attention');
	});

	it('wears the goal only while it is live', () => {
		const live = hudWith({ goal: parseGoalText('⊙ goal: tidy the garage', 0) });
		expect(stateFrom({ hud: live, link: 'connected' }).goal).toBe(true);
		const achieved = hudWith({ goal: parseGoalText('✓ goal: done', 0) });
		expect(stateFrom({ hud: achieved, link: 'connected' }).goal).toBe(false);
		expect(stateFrom({ hud: hudWith({ goal: NO_GOAL }), link: 'connected' }).goal).toBe(false);
	});

	it('warms only while a turn is waiting on the model', () => {
		// llama-swap reports the model as starting whenever it is unloaded, which
		// at rest is most of the evening. A reactor stuck in "warming up" all
		// evening would say the assistant was busy when it was idle.
		const report = { ok: true, model: { state: 'starting' } };
		expect(inputsFrom({ hud: initialHud(), link: 'connected', report }).warming).toBe(false);
		const running = hudWith({ turn: { ...initialHud().turn, running: true } });
		expect(inputsFrom({ hud: running, link: 'connected', report }).warming).toBe(true);
		expect(stateFrom({ hud: running, link: 'connected', report }).state).toBe('warming');
	});

	it('reads the adapter’s down flag rather than guessing from the socket', () => {
		const down = hudWith({ backendDown: true, error: 'backend unavailable' });
		expect(stateFrom({ hud: down, link: 'connected' }).state).toBe('offline');
		const midTurn = hudWith({
			backendDown: true,
			error: 'backend unavailable',
			turn: { ...initialHud().turn, running: true }
		});
		expect(stateFrom({ hud: midTurn, link: 'connected' }).state).toBe('alert');
	});

	it('takes speaking and listening from the caller, which owns the audio', () => {
		const hud = initialHud();
		expect(stateFrom({ hud, link: 'connected', speaking: true }).state).toBe('speaking');
		expect(stateFrom({ hud, link: 'connected', recording: true }).state).toBe('listening');
		// Listening outranks speaking's own source only through the table.
		expect(stateFrom({ hud, link: 'connected', speaking: true, recording: true }).state).toBe(
			'speaking'
		);
	});

	it('is every state, given the right sources', () => {
		// The fold and the table have to be able to reach the whole §10 list, or
		// a state exists only in a test.
		const reached = new Set<string>();
		reached.add(stateFrom({ hud: initialHud(), link: 'connected' }).state);
		reached.add(stateFrom({ hud: initialHud(), link: 'connected', recording: true }).state);
		reached.add(stateFrom({ hud: initialHud(), link: 'connected', speaking: true }).state);
		reached.add(
			stateFrom({ hud: hudWith({ turn: { ...initialHud().turn, running: true } }), link: 'connected' })
				.state
		);
		reached.add(
			stateFrom({
				hud: hudWith({ turn: { ...initialHud().turn, running: true, tools: [tool('running')] } }),
				link: 'connected'
			}).state
		);
		reached.add(
			stateFrom({ hud: hudWith({ approvals: [{ requestId: 'a' }] as HudState['approvals'] }), link: 'connected' })
				.state
		);
		reached.add(
			stateFrom({
				hud: hudWith({ turn: { ...initialHud().turn, running: true } }),
				link: 'connected',
				report: { ok: true, model: { state: 'starting' } }
			}).state
		);
		reached.add(
			stateFrom({ hud: hudWith({ turn: { ...initialHud().turn, error: 'boom' } }), link: 'connected' }).state
		);
		reached.add(stateFrom({ hud: initialHud(), link: 'offline' }).state);
		reached.add(stateFrom({ hud: initialHud(), link: 'connected', kill: true }).state);
		expect([...reached].sort()).toEqual([...STATES].filter((s) => s !== 'goal').sort());
	});
});

describe('the cold start’s progress', () => {
	it('is null when the adapter reports none, so the renderer ramps on the clock', () => {
		expect(warmingProgress(null)).toBeNull();
		expect(warmingProgress({ ok: true, model: { state: 'starting' } })).toBeNull();
	});

	it('takes a fraction as a fraction and a percentage as a percentage', () => {
		// Cast because the adapter does not report a figure yet, so the protocol
		// type has no field for one; this pins the reader for when it does.
		const withProgress = (progress: number): StatusReport =>
			({ ok: true, model: { state: 'starting', progress } }) as unknown as StatusReport;
		expect(warmingProgress(withProgress(0.4))).toBe(0.4);
		expect(warmingProgress(withProgress(40))).toBe(0.4);
		expect(warmingProgress(withProgress(250))).toBe(1);
	});
});

describe('the kill switch has a source', () => {
	// Phase 7. The `kill` input was hard-false until the switch existed; the
	// adapter now reads it off the Hermes volume and reports it in
	// `GET /api/status`, so the console folds it in like every other input.
	// Read off the volume rather than asked of Hermes on purpose: the HUD has to
	// be able to show the switch while the gateway is wedged, and `kill` is the
	// one state the contract's precedence puts above `offline`.
	const report = (kill?: { engaged?: boolean; reason?: string }): StatusReport =>
		({ ok: !kill?.engaged, kill_switch: kill }) as StatusReport;

	it('reads the engaged switch out of the status report', () => {
		expect(inputsFrom({ link: 'connected', report: report({ engaged: true }) }).kill).toBe(true);
		expect(stateFrom({ link: 'connected', report: report({ engaged: true }) }).state).toBe('kill');
	});

	it('is released when the report says so, and when it says nothing', () => {
		expect(inputsFrom({ link: 'connected', report: report({ engaged: false }) }).kill).toBe(false);
		expect(inputsFrom({ link: 'connected', report: report() }).kill).toBe(false);
		// An adapter older than the switch sends no key at all; that is released,
		// not unknown, because every earlier build had no switch to engage.
		expect(inputsFrom({ link: 'connected', report: { ok: true } }).kill).toBe(false);
	});

	it('shows the switch even when the link is down', () => {
		// The precedence row that matters: `kill` outranks `offline`, so a
		// wedged gateway does not hide the fact that the owner stopped it.
		const sources = { link: 'offline' as const, report: report({ engaged: true }) };
		expect(stateFrom(sources).state).toBe('kill');
	});

	it('lets a caller force the state without the report overriding it', () => {
		// The styleguide and the screenshot goldens render `kill` deliberately;
		// `??` rather than `||` is what makes an explicit `false` stick.
		expect(inputsFrom({ kill: true, report: report({ engaged: false }) }).kill).toBe(true);
		expect(inputsFrom({ kill: false, report: report({ engaged: true }) }).kill).toBe(false);
	});
});

describe('what the reducer feeds the table', () => {
	// The contract's `derivation.sequences`: the same events go through the phone's and the
	// terminal's own reducers. A table of inputs alone could not see the fault this pins, where
	// the precedence was right and the reducer handed it a stale error (T-1483).
	it.each(CONTRACT.derivation.sequences.cases)('$name', ({ events, state }) => {
		const hud = events.reduce((at, event) => reduce(at, event), initialHud());
		expect(stateFrom({ hud, link: 'connected' }).state).toBe(state);
	});

	it('names more than one sequence, so an emptied table is not a pass', () => {
		expect(CONTRACT.derivation.sequences.cases.length).toBeGreaterThan(2);
	});
});
