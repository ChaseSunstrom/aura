// The goal's crew (T-0291): every helper its turns delegated to, as a tree, and how much
// of the crew budget they have used.
//
// The plugin enforces the budget (`hermes/plugins/aura-goals/crew.py`); the console only
// reads the same limits and tag from `tests/contracts/crew-budget.json` (pinned by both
// suites) so "3/8 helpers" here is the number that refuses the ninth there.

import type { BackgroundRow } from '$lib/hermes/hud';

/** crew.py's SPAWNS, CALLS, MINUTES. */
export const CREW_LIMITS = { helpers: 8, calls: 120, minutes: 20 } as const;
/** crew.py's `_TAG`: a helper's type is its goal's leading `[tag]`. */
export const TAG = /^\s*\[([a-z]+)\]/;

export interface CrewNode {
	id: string;
	/** The worker type (`researcher`, `planner`, …); null for an untagged helper. */
	type: string | null;
	/** The goal without its tag. */
	goal: string;
	tools: number;
	state: BackgroundRow['state'];
	parent?: string;
	children: CrewNode[];
}

export interface Crew {
	/** Top-level helpers; a helper whose parent is no longer listed stands here too. */
	nodes: CrewNode[];
	helpers: number;
	calls: number;
	/** Whole minutes since the first listed helper started. */
	minutes: number;
	running: number;
}

export function workerType(goal: string): { type: string | null; goal: string } {
	const m = TAG.exec(goal);
	return m ? { type: m[1], goal: goal.slice(m[0].length).trim() } : { type: null, goal };
}

/**
 * The crew as the console has seen it. Does NOT match the plugin's count exactly: a helper
 * the dock has pruned, or one started before this console attached, is not counted here,
 * so the numbers are a floor of what the plugin holds, never above it.
 */
export function crewFrom(background: readonly BackgroundRow[], now: number): Crew {
	const rows = background.filter((r) => r.kind === 'subagent').sort((a, b) => a.at - b.at || (a.id < b.id ? -1 : 1));
	const byId = new Map<string, CrewNode>();
	for (const r of rows) {
		byId.set(r.id, {
			id: r.id,
			...workerType(r.label),
			tools: r.tools ?? 0,
			state: r.state,
			...(r.parent ? { parent: r.parent } : {}),
			children: []
		});
	}
	const nodes: CrewNode[] = [];
	for (const n of byId.values()) {
		const parent = n.parent ? byId.get(n.parent) : undefined;
		(parent ? parent.children : nodes).push(n);
	}
	return {
		nodes,
		helpers: rows.length,
		calls: rows.reduce((sum, r) => sum + (r.tools ?? 0), 0),
		minutes: rows.length ? Math.max(0, Math.floor((now - rows[0].at) / 60_000)) : 0,
		running: rows.filter((r) => r.state === 'running').length
	};
}
