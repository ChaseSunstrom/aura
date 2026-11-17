/**
 * The decisions the app's own pages make, pure so vitest can hold them
 * (`tests/logic.test.ts`). The pages themselves only draw what these return.
 */

export type Policy = 'ask' | 'allow_always' | 'never';
export type TierName = 'AUTO' | 'NOTIFY' | 'CONFIRM';

export interface Action {
	id: string;
	tier: number;
	tier_name: TierName;
	description: string;
	available: boolean;
	unsupported_reason?: string;
	policy: Policy;
}

export interface Tiers {
	automation_enabled: boolean;
	panic: boolean;
	actions: Action[];
}

/**
 * The answers a person may remember for an action. A Tier 3 (CONFIRM) action
 * asks every single time and that cannot be remembered -- the agent refuses
 * `allow_always` for it, so the page never offers it.
 */
export function choicesFor(tier: TierName): Policy[] {
	return tier === 'CONFIRM' ? ['ask', 'never'] : ['ask', 'allow_always', 'never'];
}

export const CHOICE_WORDS: Record<Policy, string> = {
	ask: 'Ask',
	allow_always: 'Always allow',
	never: 'Never'
};

export const TIER_BY_NUMBER: Record<number, TierName | undefined> = { 1: 'AUTO', 2: 'NOTIFY', 3: 'CONFIRM' };

export const TIER_WORDS: Record<TierName, string> = {
	AUTO: 'Runs without asking',
	NOTIFY: 'Asks, and can remember',
	CONFIRM: 'Asks every time'
};

/** `policy set <id> <value>` for a choice, or null when the choice is not one the tier allows. */
export function policyArgs(action: Pick<Action, 'id' | 'tier_name'>, choice: Policy): string[] | null {
	if (!choicesFor(action.tier_name).includes(choice)) return null;
	return ['policy', 'set', action.id, choice];
}

export const SECTIONS = ['status', 'pair', 'permissions', 'activity', 'checks', 'updates'] as const;
export type Section = (typeof SECTIONS)[number];

/** The section a `#hash` names, or the status page for anything else. */
export function sectionOf(hash: string): Section {
	const wanted = hash.replace(/^#/, '');
	return (SECTIONS as readonly string[]).includes(wanted) ? (wanted as Section) : 'status';
}

/**
 * What `#hash` asks the window to do: null for an empty hash (leave the page
 * where it is), else the section to bring into view and the data it re-reads.
 */
export function revealPlan(hash: string): { section: Section; load: 'permissions' | 'activity' | 'doctor' | null } | null {
	if (!hash.replace(/^#/, '')) return null;
	const section = sectionOf(hash);
	const load = section === 'permissions' ? 'permissions' : section === 'activity' ? 'activity' : section === 'checks' ? 'doctor' : null;
	return { section, load };
}

export type Verdict = 'approved' | 'approved_always' | 'denied';

/**
 * What a button on the approval prompt answers. Only the two allowing buttons
 * allow; anything else, including a name nobody knows, is a denial. Closing the
 * window never reaches here: the app answers "denied" for every held ask itself.
 */
export function verdictFor(button: string): Verdict {
	return button === 'approve' ? 'approved' : button === 'always' ? 'approved_always' : 'denied';
}

export interface AskFrame {
	id: string;
	description?: string;
	reason?: string;
	params?: Record<string, unknown>;
	/** 1 AUTO, 2 NOTIFY, 3 CONFIRM: the number the agent's ask frame carries. */
	tier?: number;
	allow_always?: boolean;
}

/** What the approval prompt shows for one held ask. */
export function askView(ask: AskFrame): {
	what: string;
	why: string;
	params: [string, string][];
	tier: string;
	showAlways: boolean;
} {
	const tierName = TIER_BY_NUMBER[ask.tier ?? 0];
	return {
		what: ask.description || 'An action on this computer',
		why: ask.reason || '',
		params: Object.entries(ask.params ?? {}).map(([k, v]): [string, string] => [k, typeof v === 'string' ? v : JSON.stringify(v)]),
		tier: tierName ? TIER_WORDS[tierName] : '',
		showAlways: !!ask.allow_always
	};
}

/** What the Updates page says for the app's update status. */
export function updateLine(update: { status: string; version: string }): string {
	switch (update.status) {
		case 'idle':
			return 'Checks every hour.';
		case 'checking':
			return 'Checking for a newer build…';
		case 'current':
			return 'This is the newest build.';
		case 'downloading':
			return `Downloading ${update.version}…`;
		case 'ready':
			return `${update.version} is downloaded. It installs itself the next time the app is idle and this window is not in use.`;
		default:
			return update.status;
	}
}

export interface AuditEntry {
	time: string;
	action_id: string;
	decision: string;
	status: string;
	source?: string;
	note?: string;
}

/** Newest first, the way a person reads an activity list. */
export function newestFirst(entries: AuditEntry[]): AuditEntry[] {
	return [...entries].sort((a, b) => (a.time < b.time ? 1 : a.time > b.time ? -1 : 0));
}

export type Point = [number, number];

/** What the overlay draws (T-0617): the agent's pointer, its target, its click, its typing. */
export interface OverlayView {
	visible: boolean;
	path: { from: Point; to: Point } | null;
	box: [number, number, number, number] | null;
	ripple: Point | null;
	caret: Point | null;
	scroll: { at: Point | null; direction: 'up' | 'down' } | null;
	keys: string | null;
}

export const OVERLAY_HIDDEN: OverlayView = Object.freeze({
	visible: false,
	path: null,
	box: null,
	ripple: null,
	caret: null,
	scroll: null,
	keys: null
});

/** Where the overlay window sits, in CSS pixels, and the screen's device pixel ratio. */
export interface OverlayScreen {
	left: number;
	top: number;
	scale: number;
}

const KEYS_SHOWN = 32;

const finite = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v);

/**
 * One overlay step from the agent, applied to what is drawn. The agent speaks in the screen's
 * device pixels (what its input backend moves); the page draws in CSS pixels from its own
 * corner, so a point is divided by the scale and moved by where the window starts -- a window
 * spanning a monitor left of the primary starts at a negative x.
 *
 * A frame it cannot draw returns `view` itself, unchanged: the shell only checked `op`, and a
 * half-drawn step (a box with no size, a point that is not two numbers) would point at the wrong
 * place. Does not cover monitors with different scales: one ratio maps the whole window.
 */
export function overlayFrame(view: OverlayView, frame: unknown, screen: OverlayScreen): OverlayView {
	if (!frame || typeof frame !== 'object') return view;
	const f = frame as Record<string, unknown>;
	const point = (v: unknown): Point | null =>
		Array.isArray(v) && v.length === 2 && v.every(finite)
			? [v[0] / screen.scale - screen.left, v[1] / screen.scale - screen.top]
			: null;
	const shown = { ...view, visible: true };
	switch (f.op) {
		case 'hide':
			return OVERLAY_HIDDEN;
		case 'path': {
			const from = point(f.from);
			const to = point(f.to);
			return from && to ? { ...shown, path: { from, to } } : view;
		}
		case 'highlight': {
			const b = f.box;
			if (!Array.isArray(b) || b.length !== 4 || !b.every(finite) || b[2] < 0 || b[3] < 0) return view;
			const at = point([b[0], b[1]]) as Point;
			return { ...shown, box: [at[0], at[1], b[2] / screen.scale, b[3] / screen.scale] };
		}
		case 'ripple': {
			const at = point(f.at);
			return at ? { ...shown, ripple: at } : view;
		}
		case 'caret': {
			const at = f.at === null ? null : point(f.at);
			return at || f.at === null ? { ...shown, caret: at } : view;
		}
		case 'scroll': {
			const at = f.at === null ? null : point(f.at);
			if ((!at && f.at !== null) || (f.direction !== 'up' && f.direction !== 'down')) return view;
			return { ...shown, scroll: { at, direction: f.direction } };
		}
		case 'key':
			return typeof f.keys === 'string' ? { ...shown, keys: f.keys.slice(0, KEYS_SHOWN) } : view;
		default:
			return view;
	}
}

/** One line for the agent's status: what it is doing, or why it is not running. */
export function agentLine(s: { agent_running: boolean; connected: boolean; waiting_for_pairing: boolean; label: string }): string {
	if (s.waiting_for_pairing) return 'Not paired yet. Pair this computer to start the agent.';
	if (!s.agent_running) return s.label;
	if (!s.connected) return 'Starting…';
	return s.label;
}
