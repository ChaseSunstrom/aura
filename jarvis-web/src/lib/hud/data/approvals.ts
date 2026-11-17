// `panel.approvals` from both places a request can come from.
//
// Hermes raises `approval.request` and `clarify.request` on its socket;
// jarvis-core holds tier-3 tool calls until a person answers on the house
// socket. The old console showed them in two banners with two vocabularies
// (`HermesApprovals`, `Approvals`); the panel shows one list, and the id says
// which link answers it so the host can route the reply.

import type { ApprovalRequest as HermesApproval, ClarifyRequest, HudState } from '$lib/hermes/hud';
import type { PendingApproval } from '$lib/jarvisClient';
import type { ApprovalRequest } from '$lib/glass';
import { clockOf } from './time';

/** The links a request can belong to, encoded in the row's id; `phone` is a phone's own prompt, relayed by the house. */
export type ApprovalSource = 'hermes' | 'core' | 'phone';

export const HERMES_PREFIX = 'hermes:';
export const CORE_PREFIX = 'core:';
export const PHONE_PREFIX = 'phone:';

export function sourceOf(id: string): { source: ApprovalSource; requestId: string; questionId?: string } {
	if (id.startsWith(CORE_PREFIX)) return { source: 'core', requestId: id.slice(CORE_PREFIX.length) };
	if (id.startsWith(PHONE_PREFIX)) return { source: 'phone', requestId: id.slice(PHONE_PREFIX.length) };
	const raw = id.startsWith(HERMES_PREFIX) ? id.slice(HERMES_PREFIX.length) : id;
	// `request#qid`: one question of a batch clarify, answered by its id.
	const hash = raw.indexOf('#');
	if (hash > 0) return { source: 'hermes', requestId: raw.slice(0, hash), questionId: raw.slice(hash + 1) };
	return { source: 'hermes', requestId: raw };
}

function fromHermesApproval(a: HermesApproval): ApprovalRequest {
	return {
		id: `${HERMES_PREFIX}${a.requestId}`,
		kind: 'approval',
		// The reason is the model's own sentence about why; the tool is what
		// would run. A person answers the question, so the reason leads.
		title: a.reason || `Run ${a.tool}?`,
		detail: a.detail || undefined,
		tool: a.tool,
		at: clockOf(a.at)
	};
}

function fromClarify(c: ClarifyRequest): ApprovalRequest {
	return {
		// A question of a batch carries its id after a `#`, so the answer can
		// name it (`approvalSourceOf` splits it back out).
		id: `${HERMES_PREFIX}${c.requestId}${c.questionId ? `#${c.questionId}` : ''}`,
		kind: 'clarify',
		title: c.question,
		detail: c.options.length ? `Options: ${c.options.join(', ')}` : undefined,
		at: clockOf(c.at)
	};
}

function argsLine(args: Record<string, unknown> | undefined): string {
	return Object.entries(args ?? {})
		.filter(([, v]) => v !== null && v !== undefined && v !== '')
		.map(([k, v]) => `${k}: ${typeof v === 'object' ? JSON.stringify(v) : String(v)}`)
		.join(', ');
}

function fromCore(p: PendingApproval): ApprovalRequest | null {
	const requestId = String(p.request_id ?? p.id ?? '');
	if (!requestId) return null;
	if (p.phone) {
		// The phone asks for a yes or a no, never words: always an approval.
		return {
			id: `${PHONE_PREFIX}${requestId}`,
			kind: 'approval',
			title: `On ${p.phone}: ${p.description || `run ${p.tool}?`}`,
			detail: argsLine(p.arguments) || undefined,
			tool: p.tool,
			at: clockOf(typeof p.created === 'number' ? p.created * 1000 : undefined)
		};
	}
	// `answerable` marks a QUESTION: the reply is words, not a yes.
	const question = Boolean(p.answerable);
	const detail = question
		? p.choices?.length
			? `Options: ${p.choices.join(', ')}`
			: undefined
		: argsLine(p.arguments) || undefined;
	const row: ApprovalRequest = {
		id: `${CORE_PREFIX}${requestId}`,
		kind: question ? 'clarify' : 'approval',
		title: p.description || `Run ${p.tool}?`,
		detail,
		at: clockOf(typeof p.created === 'number' ? p.created * 1000 : undefined)
	};
	if (!question) row.tool = p.tool;
	return row;
}

/** Every open request, Hermes's first (they are the turn in flight), then the house's. */
export function approvalRequests(
	hud: HudState | null | undefined,
	pending: readonly PendingApproval[] = []
): ApprovalRequest[] {
	const out: ApprovalRequest[] = [];
	for (const a of hud?.approvals ?? []) out.push(fromHermesApproval(a));
	for (const c of hud?.clarifies ?? []) out.push(fromClarify(c));
	for (const p of pending) {
		const row = fromCore(p);
		if (row) out.push(row);
	}
	return out;
}
