// `panel.audit` from what the console has actually seen run.
//
// There is no audit endpoint the console reads yet: the settings plugin
// appends `logs/settings.jsonl` on the Hermes volume and jarvis-core keeps its
// traces, neither of which the socket serves as a list. What the console does
// hold is every tool call of the session and every approval a person
// answered, with the internal names §11 confines to this panel. That is the
// audit trail it can be honest about; the settings history joins it when the
// plugin's log gets a reader.

import type { ChatToolCall } from '$lib/chat';
import type { HudState, ToolCard } from '$lib/hermes/hud';
import { summariseToolArgs } from '$lib/hermes/hud';
import type { AuditEntry } from '$lib/glass';
import { stampOf } from './time';

/** A decision a person made on this console, kept so the audit says who answered. */
export interface Decision {
	requestId: string;
	tool: string;
	choice: string;
	at: number;
}

function ofTool(card: ToolCard): AuditEntry {
	return {
		id: `tool:${card.id}`,
		at: stampOf(card.startedAt),
		actor: card.name,
		action: card.state === 'running' ? 'running' : 'ran',
		detail: summariseToolArgs(card.args) || undefined,
		outcome: card.state === 'failed' ? 'failed' : 'ok'
	};
}

function ofDecision(d: Decision): AuditEntry {
	return {
		id: `decision:${d.requestId}`,
		at: stampOf(d.at),
		actor: 'you',
		action: d.choice === 'deny' ? 'denied' : d.choice === 'answer' ? 'answered' : 'approved',
		detail: d.tool || undefined,
		outcome: d.choice === 'deny' ? 'refused' : 'ok'
	};
}

/** A tool call the transcript remembers from an earlier turn — a resumed session's history. */
function ofArchived(call: ChatToolCall, at: number, turnId: string): AuditEntry {
	return {
		id: `tool:${turnId}:${call.key}`,
		at: stampOf(at),
		actor: call.name,
		action: call.state === 'running' ? 'running' : 'ran',
		detail: summariseToolArgs(call.arguments) || undefined,
		outcome: call.state === 'failed' ? 'failed' : 'ok'
	};
}

/**
 * Newest first: an audit is read from the last thing that happened.
 *
 * The turn in flight has its cards; every turn before it lives in the
 * transcript, where the reducer folds each call into its message. Both are
 * read, or a reload — which resumes the session and rebuilds the transcript
 * from history — would empty the audit of everything that ran before it.
 */
export function auditView(hud: HudState | null | undefined, decisions: readonly Decision[] = []): AuditEntry[] {
	const live = new Set((hud?.turn.tools ?? []).map((t) => t.id));
	const rows = [
		...(hud?.turn.tools ?? []).map((t) => ({ at: t.startedAt, entry: ofTool(t) })),
		...(hud?.messages ?? []).flatMap((m) =>
			m.tools.filter((call) => !live.has(call.key)).map((call) => ({ at: m.at, entry: ofArchived(call, m.at, m.id) }))
		),
		...decisions.map((d) => ({ at: d.at, entry: ofDecision(d) }))
	];
	return rows
		.map((row, index) => ({ ...row, index }))
		.sort((a, b) => b.at - a.at || b.index - a.index)
		.map((row) => row.entry);
}
