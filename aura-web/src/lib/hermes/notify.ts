// A backgrounded tab surfaces what needs a person: the browser Notification
// API for a new approval request and for a goal that stopped (paused or
// achieved). In-app rendering is unchanged; this is the second channel the
// audit found missing (FE-03). Pure decisions here, the one side effect in
// `notifyBrowser`, so the choice of what to say is testable in Node.
import type { HudState } from './hud';
import { wornName } from '$lib/ds/theme.svelte';

export interface Notice {
	tag: string;
	title: string;
	body: string;
}

/** What changed between two HUD states that a person away from the tab should hear about. */
export function noticesFor(before: HudState, after: HudState): Notice[] {
	const out: Notice[] = [];
	const known = new Set(before.approvals.map((a) => a.requestId));
	for (const a of after.approvals) {
		if (known.has(a.requestId)) continue;
		out.push({
			tag: `approval:${a.requestId}`,
			title: `${wornName()} needs your approval: ${a.tool}`,
			body: [a.detail, a.reason].filter(Boolean).join(' — ').slice(0, 200)
		});
	}
	const stopped = after.goal.phase === 'paused' || after.goal.phase === 'achieved';
	if (stopped && before.goal.phase !== after.goal.phase) {
		out.push({
			tag: `goal:${after.goal.phase}`,
			title: after.goal.phase === 'achieved' ? `${wornName()}: goal achieved` : `${wornName()}: goal paused`,
			body: (after.goal.reason || after.goal.text || '').slice(0, 200)
		});
	}
	return out;
}

/** True when the page is not in front of the person, so a notification adds something. */
export function shouldNotify(doc: { hidden: boolean } | undefined, permission: NotificationPermission | undefined): boolean {
	return !!doc?.hidden && permission === 'granted';
}

/** Fire the notices through the browser. No-op without the API or permission. */
export function notifyBrowser(notices: Notice[]): number {
	if (typeof Notification === 'undefined' || typeof document === 'undefined') return 0;
	if (!shouldNotify(document, Notification.permission)) return 0;
	let shown = 0;
	for (const n of notices) {
		try {
			new Notification(n.title, { body: n.body, tag: n.tag });
			shown += 1;
		} catch {
			// A browser that refuses (e.g. no service worker on Android Chrome) is not an error here.
		}
	}
	return shown;
}
