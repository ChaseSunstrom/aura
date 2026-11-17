// `panel.timeline` from the turn's tool cards and the background rows.
//
// One list, in the order things started: the tool calls of the turn in flight
// and the processes and subagents it left running. The old console drew these
// as two strips (`HermesRuns`, `ToolActivity`) with two orderings; §9.1 says
// the timeline is "tool calls, sub-agents, background processes, in order".

import type { BackgroundRow, HudState, ToolCard } from '$lib/hermes/hud';
import { summariseToolArgs } from '$lib/hermes/hud';
import type { TimelineCall } from '$lib/glass';
import { clockOf, secondsToMs } from './time';
import { stepOf } from './toolWords';

function callOfTool(card: ToolCard): TimelineCall {
	const state = card.state === 'running' ? 'running' : card.state === 'failed' ? 'failed' : 'ok';
	const call: TimelineCall = {
		id: `tool:${card.id}`,
		name: card.name,
		args: summariseToolArgs(card.args) || card.progress || undefined,
		step: stepOf(card.name, card.args, card.context, state, card.error),
		state,
		ms: card.state === 'running' ? null : secondsToMs(card.duration),
		at: clockOf(card.startedAt)
	};
	if (card.error) call.error = card.error;
	return call;
}

function callOfBackground(row: BackgroundRow): TimelineCall {
	return {
		id: `${row.kind}:${row.id}`,
		name: row.label,
		args: row.detail || undefined,
		// A stopped child did not finish its work: the timeline has three words and
		// "ok" is the one it is not.
		state: row.state === 'running' ? 'running' : row.state === 'failed' || row.state === 'interrupted' ? 'failed' : 'ok',
		ms: null,
		at: clockOf(row.at)
	};
}

export function timelineCalls(hud: HudState | null | undefined): TimelineCall[] {
	if (!hud) return [];
	const rows: { at: number; call: TimelineCall }[] = [
		...hud.turn.tools.map((t) => ({ at: t.startedAt, call: callOfTool(t) })),
		...hud.background.map((b) => ({ at: b.at, call: callOfBackground(b) }))
	];
	// Stable on equal stamps: a sort that reorders two rows started in the same
	// millisecond would make the timeline flicker as the list is rebuilt.
	return rows
		.map((row, index) => ({ ...row, index }))
		.sort((a, b) => a.at - b.at || a.index - b.index)
		.map((row) => row.call);
}
