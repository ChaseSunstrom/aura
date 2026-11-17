import { readFileSync } from 'node:fs';
import { describe, it, expect, beforeEach } from 'vitest';
import { resetIds } from '$lib/chat';
import {
	attachSession,
	beginTurn,
	detachSession,
	dismissPanelLocally,
	failTurn,
	fromHistory,
	initialHud,
	pruneBackground,
	prunePanels,
	QUIET_ENDS,
	reduce,
	runningRows,
	seedPanels,
	shapeReply,
	summariseToolArgs,
	VISUAL_HOLD_MS,
	visualsHeld,
	toolArgsFull,
	withoutApproval,
	withoutClarify,
	type HudState
} from './hud';
import type { HermesEvent } from './protocol';
import { THEMES } from '$lib/themes';

const SID = 'live-1';

const sessionless = (type: string, payload: Record<string, unknown> = {}): HermesEvent => ({ type, payload }) as HermesEvent;
const ev = (type: string, payload: Record<string, unknown> = {}, seq = 0): HermesEvent => ({
	type,
	session_id: SID,
	seq,
	payload
});

/** A full scripted turn, as the socket would deliver it, in order. */
function scriptedTurn(): HermesEvent[] {
	return [
		ev('message.start', {}, 1),
		ev('reasoning.delta', { text: 'the lab ' }, 2),
		ev('reasoning.delta', { text: 'strip' }, 3),
		ev('tool.start', { tool_id: 't1', name: 'turn_on', context: 'lab', args: { name: 'lab lights' } }, 4),
		ev('tool.progress', { tool_id: 't1', text: 'calling the house' }, 5),
		ev('tool.complete', { tool_id: 't1', name: 'turn_on', args: { name: 'lab lights' }, duration: 0.012, result: 'ok' }, 6),
		ev('message.delta', { text: 'Turning ' }, 7),
		ev('message.delta', { text: 'on the ' }, 8),
		ev('message.delta', { text: 'lab lights.' }, 9),
		ev('message.complete', { text: 'Turning on the lab lights.', status: 'complete', usage: { total: 12 } }, 10)
	];
}

function attached(): HudState {
	return attachSession(initialHud(), {
		sessionId: SID,
		storedSessionId: 'stored-1',
		info: { model: 'qwen3.8-27b', provider: 'custom', approval_mode: 'smart' }
	});
}

beforeEach(() => resetIds());

describe('a scripted turn', () => {
	it('takes words folded into a running turn: one pending reply, the words before it, still running (T-0794)', () => {
		let state = beginTurn(attached(), 'check every room', 1000);
		for (const event of scriptedTurn().slice(0, 4)) state = reduce(state, event, 2000);
		state = beginTurn(state, 'and the garage too', 3000);
		expect(state.turn.running).toBe(true);
		expect(state.messages.map((m) => m.role)).toEqual(['user', 'user', 'assistant']);
		expect(state.messages[1].content).toBe('and the garage too');
		expect(state.messages.filter((m) => m.pending)).toHaveLength(1);
		// The turn that was running goes on feeding the same reply.
		for (const event of scriptedTurn().slice(4)) state = reduce(state, event, 4000);
		expect(state.messages.filter((m) => m.role === 'assistant')).toHaveLength(1);
		expect(state.turn.running).toBe(false);
	});

	it('streams the reply, keeps the reasoning aside, and tracks the tool card from running to done', () => {
		let state = beginTurn(attached(), 'turn on the lab lights', 1000);
		expect(state.turn.running).toBe(true);
		expect(state.messages).toHaveLength(2);
		expect(state.messages[0]).toMatchObject({ role: 'user', content: 'turn on the lab lights' });
		expect(state.messages[1]).toMatchObject({ role: 'assistant', pending: true });

		const events = scriptedTurn();
		for (const event of events.slice(0, 4)) state = reduce(state, event, 2000);
		// After tool.start: one card, running, with its arguments; no token yet.
		expect(state.turn.tools).toHaveLength(1);
		expect(state.turn.tools[0]).toMatchObject({ id: 't1', name: 'turn_on', state: 'running', args: { name: 'lab lights' } });
		expect(state.turn.gotToken).toBe(false);
		expect(state.turn.reasoning).toBe('the lab strip');
		expect(state.messages[1].thinking).toBe('the lab strip');
		expect(state.messages[1].tools[0]).toMatchObject({ key: 't1', state: 'running' });

		state = reduce(state, events[4], 2100);
		expect(state.turn.tools[0].progress).toBe('calling the house');

		state = reduce(state, events[5], 2200);
		expect(state.turn.tools[0]).toMatchObject({ state: 'done', duration: 0.012, result: 'ok', error: null });
		expect(state.messages[1].tools[0]).toMatchObject({ state: 'ok', durationMs: 12 });

		state = reduce(state, events[6], 2300);
		expect(state.turn.gotToken).toBe(true);
		expect(state.turn.firstTokenAt).toBe(2300);
		for (const event of events.slice(7)) state = reduce(state, event, 2400);

		expect(state.turn.running).toBe(false);
		expect(state.turn.text).toBe('Turning on the lab lights.');
		expect(state.usage).toEqual({ total: 12 });
		expect(state.messages[1]).toMatchObject({ pending: false, content: 'Turning on the lab lights.' });
		expect(state.lastSeq).toBe(10);
	});

	it('takes the final text from message.complete when nothing streamed', () => {
		let state = beginTurn(attached(), 'hello');
		state = reduce(state, ev('message.complete', { text: 'Hello, Sir.', reasoning: 'a greeting' }));
		expect(state.turn.text).toBe('Hello, Sir.');
		expect(state.turn.reasoning).toBe('a greeting');
		expect(state.messages[1]).toMatchObject({ content: 'Hello, Sir.', thinking: 'a greeting', pending: false });
	});

	it('does not double the reply when message.complete repeats what streamed', () => {
		let state = beginTurn(attached(), 'hello');
		state = reduce(state, ev('message.delta', { text: 'Hello, Sir.' }));
		state = reduce(state, ev('message.complete', { text: 'Hello, Sir.' }));
		expect(state.messages[1].content).toBe('Hello, Sir.');
	});

	it('marks a failed tool failed, with its error, and does not drop it', () => {
		let state = beginTurn(attached(), 'lock up');
		state = reduce(state, ev('tool.start', { tool_id: 't9', name: 'lock_control', args: { entity_id: 'lock.front' } }));
		state = reduce(state, ev('tool.complete', { tool_id: 't9', name: 'lock_control', error: 'no such entity', duration: 0.4 }));
		expect(state.turn.tools[0]).toMatchObject({ state: 'failed', error: 'no such entity' });
		expect(state.messages[1].tools[0]).toMatchObject({ state: 'failed', error: 'no such entity' });
	});

	it("reads Hermes's duration_s, and a turn's first text stamps textAt", () => {
		let state = beginTurn(attached(), 'open it', 1000);
		state = reduce(state, ev('tool.start', { tool_id: 'n1', name: 'browser_navigate', args: { url: 'https://example.com' } }), 1100);
		state = reduce(state, ev('tool.complete', { tool_id: 'n1', name: 'browser_navigate', duration_s: 2.8 }), 3900);
		expect(state.turn.tools[0].duration).toBe(2.8);
		expect(state.turn.textAt ?? null).toBeNull();
		state = reduce(state, ev('message.delta', { text: 'Example Domain.' }), 4000);
		expect(state.turn.textAt).toBe(4000);
	});

	it('closes a card still running when the turn ends: "stopped" on an interrupt, and a late tool.complete still lands', () => {
		let state = beginTurn(attached(), 'open it');
		state = reduce(state, ev('tool.start', { tool_id: 'n1', name: 'browser_navigate', args: {} }));
		state = reduce(state, ev('message.complete', { text: '', status: 'interrupted' }));
		expect(state.turn.tools[0]).toMatchObject({ state: 'failed', error: 'stopped' });
		expect(state.messages.at(-1)?.tools[0]).toMatchObject({ state: 'failed', error: 'stopped' });
		state = reduce(state, ev('tool.complete', { tool_id: 'n1', name: 'browser_navigate', duration_s: 1 }));
		expect(state.turn.tools[0].state).toBe('done');

		let other = beginTurn(attached(), 'again');
		other = reduce(other, ev('tool.start', { tool_id: 'n2', name: 'terminal', args: {} }));
		other = reduce(other, ev('message.complete', { text: 'Done.' }));
		expect(other.turn.tools[0]).toMatchObject({ state: 'failed', error: 'did not finish' });
	});

	it('gives a tool.complete with no start a card anyway', () => {
		let state = beginTurn(attached(), 'x');
		state = reduce(state, ev('tool.complete', { tool_id: 'late', name: 'read_file', duration: 1 }));
		expect(state.turn.tools).toHaveLength(1);
		expect(state.turn.tools[0]).toMatchObject({ id: 'late', name: 'read_file', state: 'done' });
	});

	it('a turn the backend started (a goal continuing) gets a placeholder without a question', () => {
		let state = attached();
		state = reduce(state, ev('message.start'));
		expect(state.turn.running).toBe(true);
		expect(state.messages).toHaveLength(1);
		expect(state.messages[0]).toMatchObject({ role: 'assistant', pending: true });
		// The client then submits the goal's message: the question goes in front.
		state = beginTurn(state, 'Continue toward the goal');
		expect(state.messages.map((m) => m.role)).toEqual(['user', 'assistant']);
	});

	it('an error event ends the turn and lands on the message', () => {
		let state = beginTurn(attached(), 'x');
		state = reduce(state, ev('error', { message: 'model unavailable' }));
		expect(state.turn.running).toBe(false);
		expect(state.error).toBe('model unavailable');
		expect(state.messages[1]).toMatchObject({ pending: false, error: 'model unavailable' });
	});

	it('failTurn says so on the message when the socket never answered', () => {
		const state = failTurn(beginTurn(attached(), 'x'), 'cannot reach the server');
		expect(state.turn.running).toBe(false);
		expect(state.messages[1].error).toBe('cannot reach the server');
	});

	it('ignores another session’s events', () => {
		const state = attached();
		const other = reduce(state, { ...ev('message.delta', { text: 'no' }), session_id: 'someone-else' });
		expect(other).toBe(state);
		// And every session's before one is attached: another client's turn
		// must not stream into a page that has not said a word.
		const blank = initialHud();
		expect(reduce(blank, ev('message.delta', { text: 'no' }))).toBe(blank);
		expect(reduce(blank, ev('approval.request', { request_id: 'r', tool: 't' }))).toBe(blank);
		expect(reduce(blank, { type: 'gateway.ready' }).backendDown).toBe(false);
	});
});

describe('approvals and clarifying questions', () => {
	it('reads confirm: phone off a sensitive ask, by the shared contract’s field names (T-0945)', async () => {
		const { readFileSync } = await import('node:fs');
		const spec = JSON.parse(readFileSync(new URL('../../../../tests/contracts/approval-event.json', import.meta.url), 'utf8')).phone_confirmation;
		// Sent with no session: it is the house's ask, shown whatever this page is attached to.
		const asked = (extra: Record<string, unknown>) =>
			reduce(initialHud(), sessionless('approval.request', { request_id: 'proof-1', command: '<ha_call_service> (plugin approval rule)', choices: spec.choices, ...extra })).approvals[0];
		expect(asked({ [spec.confirm_field]: spec.confirm_value }).onPhone).toBe(true);
		expect(asked({}).onPhone).toBeUndefined();
		expect(asked({ [spec.confirm_field]: 'anything else' }).onPhone).toBeUndefined();
	});

	it('queues an approval.request with the choices the backend offers, and dedupes by id', () => {
		let state = attached();
		state = reduce(state, ev('approval.request', { request_id: 'r1', tool: 'terminal', command: 'rm -rf /tmp/x', reason: 'destructive', choices: ['once', 'deny'] }));
		state = reduce(state, ev('approval.request', { request_id: 'r1', tool: 'terminal', command: 'rm -rf /tmp/x', choices: ['once', 'deny'] }));
		expect(state.approvals).toHaveLength(1);
		expect(state.approvals[0]).toMatchObject({ requestId: 'r1', tool: 'terminal', detail: 'rm -rf /tmp/x', reason: 'destructive', choices: ['once', 'deny'] });
	});

	it('offers all four choices when the backend names none, and reads args when there is no command', () => {
		const state = reduce(attached(), ev('approval.request', { request_id: 'r2', tool: 'write_file', args: { path: '/etc/hosts' } }));
		expect(state.approvals[0].choices).toEqual(['once', 'session', 'always', 'deny']);
		expect(state.approvals[0].detail).toBe('path: /etc/hosts');
	});

	it('names the tool on the shape Hermes actually emits, which carries no tool field', () => {
		// The regression. Every payload above is synthetic: Hermes emits no `tool`
		// and no `reason`, so the card was titled with the literal word "tool" on
		// every real approval. This is the payload a gateway notify callback gets,
		// captured from the installed Hermes in
		// `hermes/plugins/jarvis-guard/tests/test_guard.py`.
		const state = reduce(
			attached(),
			ev('approval.request', {
				request_id: 'r-real',
				command: '<ha_call_service> (plugin approval rule)',
				pattern_key: 'plugin_rule:house:service-call',
				description: 'ha_call_service acts on the house',
				allow_permanent: true,
				allow_session: true,
				choices: ['once', 'session', 'always', 'deny']
			})
		);
		expect(state.approvals[0].tool).toBe('ha_call_service');
		expect(state.approvals[0].reason).toBe('ha_call_service acts on the house');
		// The rendered label is not a command, so it is not shown as one.
		expect(state.approvals[0].detail).toBe('');
	});

	it('still shows the command for Hermes own dangerous-command gate, which has no tool', () => {
		// The other prompt shape: `command` is the literal command and the prompt
		// is about a command rather than a tool. The card must not lose it.
		const state = reduce(
			attached(),
			ev('approval.request', {
				request_id: 'r-shell',
				command: 'rm -rf /opt/data/work',
				pattern_key: 'dangerous_command:rm',
				description: 'Dangerous command'
			})
		);
		expect(state.approvals[0].detail).toBe('rm -rf /opt/data/work');
		expect(state.approvals[0].tool).toBe('tool');
	});

	it('drops an approval once answered, or once the backend resolves it', () => {
		let state = reduce(attached(), ev('approval.request', { request_id: 'r3', tool: 't' }));
		expect(withoutApproval(state, 'r3').approvals).toEqual([]);
		state = reduce(state, ev('approval.resolved', { request_id: 'r3' }));
		expect(state.approvals).toEqual([]);
	});

	it('reads the gateway\'s `choices`, and a batch of questions each by its qid', () => {
		// tui_gateway `_clarify_block`: a single question carries `question` and
		// `choices`; a batch carries `questions: [{qid, question, choices}]`. The
		// batch used to reach the panel as one request with no text.
		let state = reduce(attached(), ev('clarify.request', { request_id: 'q2', question: 'Which door?', choices: ['front', 'garage'] }));
		expect(state.clarifies[0]).toMatchObject({ requestId: 'q2', question: 'Which door?', options: ['front', 'garage'] });
		state = reduce(
			attached(),
			ev('clarify.request', {
				request_id: 'b1',
				questions: [
					{ qid: 'a', question: 'Which video?', choices: [], multi_select: false },
					{ qid: 'b', question: 'Which screen?', choices: ['tv', 'desk'], multi_select: false }
				]
			})
		);
		expect(state.clarifies.map((c) => [c.questionId, c.question, c.options])).toEqual([
			['a', 'Which video?', []],
			['b', 'Which screen?', ['tv', 'desk']]
		]);
		// Answering one leaves the other; a replay that carries locked answers skips them.
		expect(withoutClarify(state, 'b1', 'a').clarifies.map((c) => c.questionId)).toEqual(['b']);
		const replay = reduce(attached(), ev('clarify.request', { request_id: 'b1', questions: [{ qid: 'a', question: 'Which video?', choices: [] }, { qid: 'b', question: 'Which screen?', choices: [] }], answers: { a: 'cats' } }));
		expect(replay.clarifies.map((c) => c.questionId)).toEqual(['b']);
	});

	it('shows every clarify shape the adapter emits (tests/contracts/clarify.json, T-0802)', () => {
		const contract = JSON.parse(readFileSync(new URL('../../../../tests/contracts/clarify.json', import.meta.url), 'utf8'));
		expect(new Set(contract.cases.map((c: { kind: string }) => c.kind))).toEqual(new Set(['single', 'batch']));
		for (const c of contract.cases) {
			const state = reduce(attached(), ev('clarify.request', c.app_payload));
			// A batch is one card per question, each by its qid; the first is the one the phone shows.
			expect(state.clarifies.length, c.name).toBe(c.app_payload.questions?.length ?? 1);
			expect(state.clarifies[0], c.name).toMatchObject({ requestId: contract.srq, question: c.shown.question, options: c.shown.options });
		}
	});

	it('queues a clarify.request with its options', () => {
		let state = reduce(attached(), ev('clarify.request', { request_id: 'q1', question: 'Front door or garage?', options: ['front', 'garage'] }));
		expect(state.clarifies[0]).toMatchObject({ requestId: 'q1', question: 'Front door or garage?', options: ['front', 'garage'] });
		state = withoutClarify(state, 'q1');
		expect(state.clarifies).toEqual([]);
	});
});

describe('the goal, the session and the background', () => {
	it('parses status.update kind=goal and keeps other kinds as the status line', () => {
		let state = attached();
		state = reduce(state, ev('status.update', { kind: 'goal', text: '↻ Continuing toward goal (2/20): one file left' }));
		expect(state.goal).toMatchObject({ phase: 'continuing', turn: 2, budget: 20, reason: 'one file left' });
		state = reduce(state, ev('status.update', { kind: 'compress', text: 'context compressed' }));
		expect(state.turn.status).toBe('context compressed');
		expect(state.goal.phase).toBe('continuing');
		state = reduce(state, ev('status.update', { kind: 'goal', text: '✓ Goal achieved: both done' }));
		expect(state.goal).toMatchObject({ phase: 'achieved', reason: 'both done' });
	});

	it('takes the model and the title from session.info', () => {
		let state = reduce(attached(), ev('session.info', { model: 'qwen3.8-27b', provider: 'custom', approval_mode: 'smart', running: false }));
		expect(state).toMatchObject({ model: 'qwen3.8-27b', provider: 'custom', approvalMode: 'smart' });
		state = reduce(state, ev('session.title', { title: 'Lights' }));
		expect(state.title).toBe('Lights');
		// A resumed session that is mid-turn is running.
		state = reduce(state, ev('session.info', { model: 'qwen3.8-27b', running: true }));
		expect(state.turn.running).toBe(true);
	});

	it('marks the backend down on adapter.error and up on gateway.ready', () => {
		let state = reduce(initialHud(), { type: 'adapter.error', payload: { error: 'backend unavailable' } });
		expect(state.backendDown).toBe(true);
		state = reduce(state, { type: 'gateway.ready' });
		expect(state.backendDown).toBe(false);
	});

	it('follows subagents and processes as background rows, and prunes the finished ones later', () => {
		let state = attached();
		state = reduce(state, ev('subagent.start', { id: 's1', goal: 'review the diff' }), 1000);
		state = reduce(state, ev('process.start', { pid: 42, command: 'npm test' }), 1000);
		expect(runningRows(state).background.map((r) => r.label)).toEqual(['review the diff', 'npm test']);
		state = reduce(state, ev('subagent.complete', { id: 's1', status: 'done', summary: 'looks fine' }), 1500);
		state = reduce(state, ev('process.exit', { pid: 42, exit_code: 1 }), 1500);
		expect(state.background[0]).toMatchObject({ state: 'done', detail: 'looks fine' });
		expect(state.background[1]).toMatchObject({ state: 'failed' });
		expect(runningRows(state).background).toEqual([]);
		expect(pruneBackground(state, 2000).background).toHaveLength(2);
		expect(pruneBackground(state, 100_000).background).toHaveLength(0);
	});

	it('keeps a subagent’s parent, depth, session and tool count, and finishes each child by its own id', () => {
		// The shape Hermes sends (`tui_gateway/server.py`, the `subagent.` branch):
		// `subagent_id`, `parent_id` only when the delegator is itself a subagent,
		// `depth` 0 for JARVIS' own child.
		let state = attached();
		state = reduce(state, ev('subagent.start', { subagent_id: 'sa-0', goal: 'plan the move', depth: 0, child_session_id: 'child-0', tool_count: 0 }), 1000);
		state = reduce(state, ev('subagent.start', { subagent_id: 'sa-1', goal: 'pack the lab', parent_id: 'sa-0', depth: 1 }), 1001);
		state = reduce(state, ev('subagent.start', { subagent_id: 'sa-2', goal: 'pack the study', parent_id: 'sa-0', depth: 1 }), 1002);
		expect(state.background[0]).toMatchObject({ id: 'sa-0', depth: 0, session: 'child-0', tools: 0 });
		expect(state.background[0].parent).toBeUndefined();
		expect(state.background[1]).toMatchObject({ id: 'sa-1', parent: 'sa-0', depth: 1 });
		state = reduce(state, ev('subagent.tool', { subagent_id: 'sa-1', tool_name: 'terminal', tool_count: 3 }), 1003);
		state = reduce(state, ev('subagent.tool', { subagent_id: 'sa-0', tool_name: 'read_file' }), 1004);
		expect(state.background.map((r) => r.tools)).toEqual([1, 3, 0]);
		// A complete with no id marks nothing: a batch of children must not all
		// go done on the first finisher.
		state = reduce(state, ev('subagent.complete', { status: 'completed' }), 1500);
		expect(state.background.map((r) => r.state)).toEqual(['running', 'running', 'running']);
		state = reduce(state, ev('subagent.complete', { subagent_id: 'sa-1', status: 'interrupted', summary: 'stopped by the user' }), 1600);
		expect(state.background[1]).toMatchObject({ state: 'interrupted', summary: 'stopped by the user', detail: 'stopped by the user' });
		expect(state.background.map((r) => r.state)).toEqual(['running', 'interrupted', 'running']);
		state = reduce(state, ev('subagent.complete', { subagent_id: 'sa-2', status: 'timeout' }), 1700);
		state = reduce(state, ev('subagent.complete', { subagent_id: 'sa-0', status: 'completed', summary: 'all packed', tool_count: 4 }), 1800);
		expect(state.background[2]).toMatchObject({ state: 'failed' });
		expect(state.background[2].summary).toBeUndefined();
		expect(state.background[0]).toMatchObject({ state: 'done', summary: 'all packed', tools: 4 });
		expect(runningRows(state).background).toEqual([]);
	});

	it('attaches a session with its transcript, and detaches it keeping what it runs on', () => {
		const state = attachSession(initialHud(), {
			sessionId: 'live-2',
			storedSessionId: 'stored-2',
			info: { model: 'm', title: 'Back door' },
			messages: [
				{ role: 'user', text: 'is the back door shut?', timestamp: 10 },
				{ role: 'tool', name: 'get_state', args: { entity_id: 'lock.back_door' } },
				{ role: 'assistant', text: 'It is, Sir.', timestamp: 12, reasoning: 'checked the lock' }
			]
		});
		expect(state).toMatchObject({ sessionId: 'live-2', storedSessionId: 'stored-2', model: 'm', title: 'Back door' });
		expect(state.messages).toHaveLength(2);
		expect(state.messages[1]).toMatchObject({ role: 'assistant', content: 'It is, Sir.', thinking: 'checked the lock' });
		expect(state.messages[1].tools[0]).toMatchObject({ name: 'get_state', state: 'ok', arguments: { entity_id: 'lock.back_door' } });
		const gone = detachSession(state);
		expect(gone.sessionId).toBeNull();
		expect(gone.messages).toEqual([]);
		expect(gone.model).toBe('m');
	});

	it('fromHistory keeps a tool call with no reply after it', () => {
		const messages = fromHistory([{ role: 'user', text: 'go' }, { role: 'tool', name: 'terminal', args: { cmd: 'ls' } }], 's');
		expect(messages).toHaveLength(2);
		expect(messages[1].tools[0].name).toBe('terminal');
	});

	it('summarises arguments on one line', () => {
		expect(summariseToolArgs({ path: '/a', content: '', n: 2 })).toBe('path: /a · n: 2');
		expect(summariseToolArgs(undefined)).toBe('');
	});

	it('shapes a streamed reply into flowing paragraphs, collapsing blank runs', () => {
		// The gap the owner saw: several blank lines between segments. A run of
		// blank lines becomes one paragraph break; whitespace-only lines vanish.
		expect(shapeReply('First segment.\n\n\n\nSecond segment.')).toBe('First segment.\n\nSecond segment.');
		expect(shapeReply('First.\n   \n\t\nSecond.')).toBe('First.\n\nSecond.');
		expect(shapeReply('  \n\nThe kitchen is warm.\n\n  ')).toBe('The kitchen is warm.');
		// A single newline (a soft break within a thought) is left alone.
		expect(shapeReply('line one\nline two')).toBe('line one\nline two');
		// A run of deltas concatenated with trailing newlines: one gap, not five.
		expect(shapeReply('Trying SearXNG instead.\n\n\n\n\nPrice data is fine.')).toBe(
			'Trying SearXNG instead.\n\nPrice data is fine.'
		);
		expect(shapeReply('')).toBe('');
	});

	it('gives the full arguments for a hover title, uncapped per field but bounded overall', () => {
		// summariseToolArgs caps each value at 60 chars and shows three; the title
		// shows every field and the whole value, so a long command is all there.
		const long = 'for url in "https://www.coindesk.com/markets" "https://example.com/very/long/path/that/exceeds/sixty/characters"; do curl "$url"; done';
		const full = toolArgsFull({ command: long, timeout: 90, cwd: '/tmp' });
		expect(full).toContain('command: ' + long);
		expect(full).toContain('timeout: 90');
		expect(full).toContain('cwd: /tmp');
		// The visible line, by contrast, cuts the command to 60 and stops at three.
		expect(summariseToolArgs({ command: long, timeout: 90, cwd: '/tmp' }).length).toBeLessThan(full.length);
		expect(toolArgsFull(undefined)).toBe('');
		// Pathological length is bounded so a title cannot be megabytes long.
		expect(toolArgsFull({ blob: 'x'.repeat(5000) }).length).toBeLessThanOrEqual(800);
	});
});

describe('the projections of a turn', () => {
	const projection = (id: string, title: string) =>
		ev('hud.projection', { id, type: 'line', data: { title, x: ['1 PM', '2 PM'], series: [{ name: 'Temperature', points: [50, 51] }] }, origin: 'agent' });

	it('files a hud.projection frame on the current turn, replaces it by id, and the next turn clears it', () => {
		let state = beginTurn(attached(), "what's the temperature", 1_000);
		state = reduce(state, projection('weather', 'Next 24 hours · home'));
		state = reduce(state, projection('rain', 'Rain'));
		expect(state.turn.visuals.map((v) => v.id)).toEqual(['weather', 'rain']);
		// The same id replaces in place: a redraw, not a second chart.
		state = reduce(state, projection('weather', 'Next 24 hours · Oslo'));
		expect(state.turn.visuals.map((v) => [v.id, (v.data as { title: string }).title])).toEqual([
			['weather', 'Next 24 hours · Oslo'],
			['rain', 'Rain']
		]);
		// Still there once the reply is in: the answer scene draws them while it speaks.
		state = reduce(state, ev('message.complete', { text: 'It is 51.', status: 'complete' }));
		expect(state.turn.visuals).toHaveLength(2);
		state = beginTurn(state, 'thanks', 2_000);
		expect(state.turn.visuals).toEqual([]);
	});

	// T-1472: the stage drew a visual only while a turn ran or its answer was spoken, so with
	// speech off a list left the screen as the model finished, and a frame another session
	// shared with a resting console was filed and never drawn.
	it('holds what was drawn for a while after the frame arrived, and a redraw starts that again', () => {
		let state = beginTurn(attached(), 'show the shopping list', 1_000);
		expect(visualsHeld(state, 1_000)).toBe(false);
		state = reduce(state, projection('list.a', 'Shopping'), 2_000);
		state = reduce(state, ev('message.complete', { text: 'Milk and eggs.', status: 'complete' }), 3_000);
		expect(visualsHeld(state, 3_000)).toBe(true);
		expect(visualsHeld(state, 2_000 + VISUAL_HOLD_MS - 1)).toBe(true);
		expect(visualsHeld(state, 2_000 + VISUAL_HOLD_MS)).toBe(false);
		// "Add eggs" from another session redraws the list under its id: still needed.
		state = reduce(state, projection('list.a', 'Shopping'), 2_000 + VISUAL_HOLD_MS - 1);
		expect(visualsHeld(state, 2_000 + VISUAL_HOLD_MS + 5)).toBe(true);
		// The next turn takes everything back.
		state = beginTurn(state, 'thanks', 2_000 + VISUAL_HOLD_MS + 10);
		expect(visualsHeld(state, 2_000 + VISUAL_HOLD_MS + 10)).toBe(false);
	});

	it('holds a frame that arrives while no turn runs: one another session shared', () => {
		const state = reduce(attached(), projection('weather', 'Next 24 hours · home'), 9_000);
		expect(state.turn.running).toBe(false);
		expect(state.turn.visuals.map((v) => v.id)).toEqual(['weather']);
		expect(visualsHeld(state, 9_001)).toBe(true);
		expect(visualsHeld(null, 9_001)).toBe(false);
	});

	it('a refused frame holds nothing', () => {
		const state = reduce(attached(), ev('hud.projection', { id: 'bad id!', type: 'line', data: {} }), 9_000);
		expect(visualsHeld(state, 9_001)).toBe(false);
	});

	it('drops a frame with no usable id or type, and keeps the newest few', () => {
		let state = beginTurn(attached(), 'compare', 1_000);
		state = reduce(state, ev('hud.projection', { id: 'bad id!', type: 'line', data: {} }));
		state = reduce(state, ev('hud.projection', { id: 'x', data: {} }));
		expect(state.turn.visuals).toEqual([]);
		for (const id of ['a', 'b', 'c', 'd', 'e']) state = reduce(state, projection(id, id));
		expect(state.turn.visuals.map((v) => v.id)).toEqual(['b', 'c', 'd', 'e']);
	});
});

describe('the panel surface', () => {
	const panelShow = (over: Record<string, unknown> = {}) =>
		ev('hud.panel.show', {
			id: 'p_1',
			kind: 'telemetry',
			title: 'GPUs',
			payload: { gpus: [] },
			origin: 'agent',
			created_at: '2026-09-03T00:41:12Z',
			rev: 1,
			...over
		});

	it('shows, updates in place, and dismisses a panel through the reducer', () => {
		let state = reduce(attached(), panelShow());
		expect(state.panels.map((p) => p.id)).toEqual(['p_1']);
		expect(state.panels[0]).toMatchObject({ kind: 'telemetry', title: 'GPUs', rev: 1 });

		state = reduce(state, ev('hud.panel.update', { id: 'p_1', rev: 2, title: 'GPUs (live)', payload: { gpus: [{ gpu: '0' }] } }));
		expect(state.panels[0]).toMatchObject({ rev: 2, title: 'GPUs (live)' });
		expect(state.panels[0].payload).toEqual({ gpus: [{ gpu: '0' }] });

		state = reduce(state, ev('hud.panel.dismiss', { id: 'p_1' }));
		expect(state.panels).toEqual([]);
	});

	it('ignores an update with a lower rev than held', () => {
		let state = reduce(attached(), panelShow({ rev: 5 }));
		state = reduce(state, ev('hud.panel.update', { id: 'p_1', rev: 2, title: 'stale' }));
		expect(state.panels[0].title).toBe('GPUs');
	});

	it('dismisses every panel on the * id', () => {
		let state = reduce(attached(), panelShow({ id: 'a' }));
		state = reduce(state, panelShow({ id: 'b' }));
		expect(state.panels).toHaveLength(2);
		state = reduce(state, ev('hud.panel.dismiss', { id: '*' }));
		expect(state.panels).toEqual([]);
	});

	it('does not draw panels from another session', () => {
		const state = reduce(attached(), { ...panelShow(), session_id: 'someone-else' });
		expect(state.panels).toEqual([]);
	});

	it('drops panels beside a session, and keeps none on detach', () => {
		const state = reduce(attached(), panelShow());
		expect(detachSession(state).panels).toEqual([]);
	});

	it('seeds from a hud list for the current session only, and prunes an expired ttl', () => {
		const state = attached(); // sessionId is SID
		const list = [
			{ id: 's1', kind: 'text', title: 'seeded', payload: { markdown: 'x' }, rev: 1 },
			{ id: 's2', kind: 'text', title: 'gone soon', payload: {}, rev: 1, ttl_s: 10 }
		];
		const seeded = seedPanels(state, SID, list, 1000);
		expect(seeded.panels.map((p) => p.id)).toEqual(['s1', 's2']);
		// A list for a session that is no longer the one on screen is dropped.
		expect(seedPanels(state, 'other', list, 1000)).toBe(state);
		// The ttl fallback prunes s2 once its window has passed.
		expect(prunePanels(seeded, 20_000).panels.map((p) => p.id)).toEqual(['s1']);
	});

	it('dismisses a panel locally, for the optimistic path', () => {
		const state = reduce(attached(), panelShow());
		expect(dismissPanelLocally(state, 'p_1').panels).toEqual([]);
	});
});

describe('how a turn ends', () => {
	const REACTOR = JSON.parse(readFileSync(new URL('../../../../tests/contracts/reactor.json', import.meta.url), 'utf8'));

	it('holds the quiet endings to the contract the terminal reads too', () => {
		expect([...QUIET_ENDS]).toEqual(REACTOR.derivation.quiet_ends.statuses);
	});

	it('an interrupted turn is stopped, not failed; any other status is a failure', () => {
		let state = { ...initialHud(), sessionId: SID } as HudState;
		state = reduce(beginTurn(state, 'delete the scratch folder'), ev('message.complete', { text: 'Not running it.', status: 'interrupted' }));
		expect(state.turn.error).toBeNull();
		expect(state.turn.running).toBe(false);
		state = reduce(beginTurn(state, 'again'), ev('message.complete', { text: '', status: 'timeout' }));
		expect(state.turn.error).toBe('timeout');
	});
});

describe('a persona switch', () => {
	it('keeps who is primary and the theme pack the event names', () => {
		let state = attached();
		expect(state.theme).toBe('');
		state = reduce(state, ev('persona.switch', { active: 'JARVIS', theme: 'Jarvis' }));
		expect([state.persona, state.theme]).toEqual(['jarvis', 'jarvis']);
		// An older plugin's event names no theme: the persona's own pack answers (themeFrom).
		state = reduce(state, ev('persona.switch', { active: 'aura' }));
		expect([state.persona, state.theme]).toEqual(['aura', '']);
	});
});

describe('a drafted theme', () => {
	const draft = () => {
		const pack = structuredClone(THEMES.aura) as unknown as Record<string, any>;
		pack.id = 'sunrise';
		pack.name = 'Sunrise';
		pack.color.accent = '#ff8a3d';
		return pack;
	};

	it('shows a preview without wearing it', () => {
		let state = reduce(attached(), ev('persona.switch', { active: 'aura', theme: 'aura' }));
		state = reduce(state, ev('theme.preview', { id: 'sunrise', name: 'Sunrise', pack: draft() }));
		expect(state.themePreview?.id).toBe('sunrise');
		expect(state.themePreview?.pack.color.accent).toBe('#ff8a3d');
		expect(state.theme).toBe('aura');
		expect(state.themeApplied).toBeNull();
		// A pack that could carry more than colours never reaches the screen.
		const bad = { ...draft(), color: { ...draft().color, accent: 'url(x)' } };
		const after = reduce(state, ev('theme.preview', { id: 'sunrise', name: 'S', pack: bad }));
		expect(after).toBe(state);
	});

	it('wears it on apply, the pack from the event or (named only) from /api/themes/<id>', () => {
		let state = reduce(attached(), ev('theme.preview', { id: 'sunrise', name: 'Sunrise', pack: draft() }));
		state = reduce(state, ev('theme.apply', { id: 'sunrise', pack: draft() }));
		expect(state.theme).toBe('sunrise');
		expect(state.themeApplied?.pack?.color.accent).toBe('#ff8a3d');
		expect(state.themePreview).toBeNull(); // the preview became the theme
		// Back to a shipped pack: no pack on the event, none needed.
		state = reduce(state, ev('theme.apply', { id: 'JARVIS', pack: null }));
		expect([state.theme, state.themeApplied?.pack]).toEqual(['jarvis', null]);
		// A hostile pack is dropped, and the theme stays.
		const bad = { ...draft(), color: { ...draft().color, accent: 'var(--x)' } };
		expect(reduce(state, ev('theme.apply', { id: 'sunrise', pack: bad }))).toBe(state);
	});
});
