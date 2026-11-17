import { describe, expect, it } from 'vitest';
import { initialHud, type HudState } from './hud';
import { noticesFor, shouldNotify } from './notify';

const base = (): HudState => initialHud();

describe('noticesFor', () => {
	it('says nothing when nothing a person needs has changed', () => {
		expect(noticesFor(base(), base())).toEqual([]);
	});

	it('announces a new approval request once, with the tool and the reason', () => {
		const before = base();
		const after: HudState = {
			...before,
			approvals: [
				{ requestId: 'r1', sessionId: 's', tool: 'terminal', detail: 'rm -rf build', reason: 'destructive', choices: ['once', 'deny'], at: 1 }
			]
		};
		const notices = noticesFor(before, after);
		expect(notices).toEqual([
			{ tag: 'approval:r1', title: 'AURA needs your approval: terminal', body: 'rm -rf build — destructive' }
		]);
		// a re-announced request (reconnect replay) is not a second notification
		expect(noticesFor(after, after)).toEqual([]);
	});

	it('announces a goal that stopped, with the judge reason, but not a goal that is still going', () => {
		const before = base();
		const going: HudState = { ...before, goal: { ...before.goal, phase: 'continuing', reason: 'one file left', text: '', turn: 1, budget: 20, at: 1 } };
		expect(noticesFor(before, going)).toEqual([]);
		const done: HudState = { ...going, goal: { ...going.goal, phase: 'achieved', reason: 'both files exist' } };
		expect(noticesFor(going, done)).toEqual([{ tag: 'goal:achieved', title: 'AURA: goal achieved', body: 'both files exist' }]);
		const paused: HudState = { ...going, goal: { ...going.goal, phase: 'paused', reason: '20/20 turns used' } };
		expect(noticesFor(going, paused)[0].title).toBe('AURA: goal paused');
		// the same stopped phase twice in a row is not repeated
		expect(noticesFor(done, done)).toEqual([]);
	});
});

describe('shouldNotify', () => {
	it('needs a hidden page and a granted permission', () => {
		expect(shouldNotify({ hidden: true }, 'granted')).toBe(true);
		expect(shouldNotify({ hidden: false }, 'granted')).toBe(false);
		expect(shouldNotify({ hidden: true }, 'default')).toBe(false);
		expect(shouldNotify(undefined, 'granted')).toBe(false);
	});
});
