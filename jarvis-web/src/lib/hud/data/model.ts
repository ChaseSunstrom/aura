// `panel.model` from the adapter's status report, the session's own model and
// the toolset roster.
//
// Three sources say what the model is: the session (`session.info`), the
// status poller (`/api/status`) and, while llama-swap loads it, the warming
// flag. The link already folds the first two into `snapshot.model`; this adds
// §10 row 8's cold start as the body's `starting` state and the toolsets with
// the on/off lists applied, so the body shows one switch per toolset that
// says what a new session would start with.

import type { HermesSnapshot } from '$lib/hermes/link';
import type { Toolset as RosterToolset } from '$lib/hermes/protocol';
import { toolsetOn, type ToolsetLists } from '$lib/hermes/settings';
import { warmingProgress } from '$lib/reactor/reactorState';
import type { Toolset } from '$lib/glass';

export interface ModelView {
	model: string;
	provider: string;
	state: 'ready' | 'starting' | 'offline';
	/** 0–100 while starting; null when the loader reports no fraction. */
	progress: number | null;
	context: string;
	toolsets: Toolset[];
}

export const EMPTY_LISTS: ToolsetLists = { enabled: [], disabled: [] };

/**
 * Hermes labels its toolsets with a leading pictograph ("🔍 Web Search & Scraping") and
 * the design system has no emoji as markers (Reactor II, 2026-09-06: the owner's "no
 * emojis"). The strip is by Unicode property, not a list, so a new label needs no change
 * here; the variation selector and joiner that ride with a pictograph go with it.
 */
export function plainLabel(label: string): string {
	return label
		.replace(/[\p{Extended_Pictographic}\uFE0F\u200D]/gu, '')
		.replace(/\s+/g, ' ')
		.trim();
}

export function toolsetViews(roster: readonly RosterToolset[], lists: ToolsetLists = EMPTY_LISTS): Toolset[] {
	return roster.map((set) => ({
		id: set.name,
		label: plainLabel(set.label || set.name) || set.name,
		enabled: toolsetOn(set.name, lists, set.enabled !== false),
		tools: set.tools?.length ?? 0
	}));
}

export function modelView(
	snap: HermesSnapshot | null | undefined,
	roster: readonly RosterToolset[] = [],
	lists: ToolsetLists = EMPTY_LISTS
): ModelView {
	const report = snap?.report ?? null;
	// `unreachable` is the adapter failing to reach llama-swap at all: the link is up and
	// the model is not, and a turn would wait on a dead host (2026-10-06).
	const linkUp = snap?.status === 'connected' && !snap.hud.backendDown && report?.model?.state !== 'unreachable';
	const progress = warmingProgress(report);
	const loaded = report?.model?.loaded?.filter((m) => m && m !== snap?.model) ?? [];
	return {
		model: snap?.model || 'No model yet',
		provider: snap?.hud.provider || '',
		state: !linkUp ? 'offline' : snap?.warmingUp ? 'starting' : 'ready',
		progress: progress === null ? null : Math.round(progress * 100),
		// "Also loaded" is the one fact the old bar hid that a person asks about
		// when a reply is slow: another model is holding the GPU.
		context: loaded.length ? `Also loaded: ${loaded.join(', ')}.` : '',
		toolsets: toolsetViews(roster, lists)
	};
}
