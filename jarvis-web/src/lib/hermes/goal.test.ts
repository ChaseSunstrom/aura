import { describe, it, expect } from 'vitest';
import { goalIsLive, goalPhaseLabel, goalTone, parseGoalText } from './goal';

// Every glyph the protocol document names, with the reason text kept verbatim.
describe('parseGoalText', () => {
	it('reads ⊙ as set, with the budget and the goal', () => {
		const state = parseGoalText('⊙ Goal set (20-turn budget): tidy the notes folder', 5);
		expect(state.phase).toBe('set');
		expect(state.budget).toBe(20);
		expect(state.turn).toBeNull();
		expect(state.reason).toBe('tidy the notes folder');
		expect(state.at).toBe(5);
	});

	it('reads ↻ as continuing, with n/N and the judge’s reason word for word', () => {
		const reason = 'the first file is written; the second does not exist yet';
		const state = parseGoalText(`↻ Continuing toward goal (3/20): ${reason}`);
		expect(state.phase).toBe('continuing');
		expect(state.turn).toBe(3);
		expect(state.budget).toBe(20);
		expect(state.reason).toBe(reason);
		expect(state.text).toBe(`↻ Continuing toward goal (3/20): ${reason}`);
	});

	it('reads ⏳ as parked and keeps what it is parked on', () => {
		const state = parseGoalText('⏳ Goal (parked waiting on pid 4242)');
		expect(state.phase).toBe('parked');
		expect(state.reason).toBe('parked waiting on pid 4242');
	});

	it('reads ⏸ as paused with the turns used', () => {
		const state = parseGoalText('⏸ Goal paused — 3/20 turns used · /goal resume to continue');
		expect(state.phase).toBe('paused');
		expect(state.turn).toBe(3);
		expect(state.budget).toBe(20);
		expect(state.reason).toContain('3/20 turns used');
	});

	it('reads ✓ as achieved with the reason', () => {
		const state = parseGoalText('✓ Goal achieved: both files exist with the right contents');
		expect(state.phase).toBe('achieved');
		expect(state.reason).toBe('both files exist with the right contents');
	});

	it('reads “Goal cleared” and “No active goal”', () => {
		expect(parseGoalText('Goal cleared').phase).toBe('cleared');
		expect(parseGoalText('✓ Goal cleared.').phase).toBe('cleared');
		expect(parseGoalText('No active goal. Set one with /goal <text>.').phase).toBe('none');
	});

	it('keeps a line it does not recognise as a status line rather than dropping it', () => {
		const state = parseGoalText('Goal judge: thinking…');
		expect(state.phase).toBe('status');
		expect(state.text).toBe('Goal judge: thinking…');
	});

	it('gives an empty line the no-goal state', () => {
		expect(parseGoalText('').phase).toBe('none');
		expect(parseGoalText(undefined as unknown as string).phase).toBe('none');
	});
});

describe('labels and tones', () => {
	it('names the turn in the label while continuing', () => {
		expect(goalPhaseLabel(parseGoalText('↻ Continuing toward goal (2/9): x'))).toBe(
			'CONTINUING · TURN 2/9'
		);
		expect(goalPhaseLabel(parseGoalText('⊙ Goal set (20-turn budget): x'))).toBe('SET');
		expect(goalPhaseLabel(parseGoalText(''))).toBe('NO GOAL');
	});

	it('is live while set, continuing or parked, and not once achieved', () => {
		expect(goalIsLive(parseGoalText('⊙ Goal set (20-turn budget): x'))).toBe(true);
		expect(goalIsLive(parseGoalText('⏳ Goal (parked …)'))).toBe(true);
		expect(goalIsLive(parseGoalText('✓ Goal achieved: done'))).toBe(false);
		expect(goalIsLive(parseGoalText('Goal cleared'))).toBe(false);
	});

	it('uses semantic tones: ok when achieved, warn when held, live while going', () => {
		expect(goalTone(parseGoalText('✓ Goal achieved: done'))).toBe('ok');
		expect(goalTone(parseGoalText('⏸ Goal paused — 1/2 turns used'))).toBe('warn');
		expect(goalTone(parseGoalText('↻ Continuing toward goal (1/2): go'))).toBe('live');
		expect(goalTone(parseGoalText(''))).toBe('neutral');
	});
});
