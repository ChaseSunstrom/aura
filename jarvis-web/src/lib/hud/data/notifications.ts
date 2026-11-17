// `panel.notifications` from jarvis-core's moments and the `notice` content
// panels JARVIS pulls up.
//
// A moment's kind ("reflection", "alert", "reminder", "task") decides its
// level: an alert is danger, a question or a held task is attention, a task
// that finished is ok, everything else is information. The kind word itself
// is not shown — §14 removed the kind tag — the level's colour and the title
// carry it.

import type { MomentRow } from '$lib/dashboards/widgets';
import type { Notice } from '$lib/glass';
import { agoOf } from './time';

export function levelOf(kind: string): Notice['level'] {
	const k = kind.toLowerCase();
	if (/alert|error|fail|danger|alarm/.test(k)) return 'danger';
	if (/approv|question|ask|held|attention|warn|remind/.test(k)) return 'attention';
	if (/done|ok|success|complete|achiev/.test(k)) return 'ok';
	return 'info';
}

export function noticesView(moments: readonly MomentRow[], now = Date.now()): Notice[] {
	return moments
		.slice()
		.sort((a, b) => b.at - a.at)
		.map((m) => {
			const notice: Notice = {
				id: m.id,
				title: m.title,
				level: levelOf(m.kind),
				at: m.at ? agoOf(m.at * 1000, now) : undefined
			};
			if (m.body) notice.body = m.body;
			return notice;
		});
}

/** A `notice` content panel's payload as one notice. */
export function noticeOfPanel(id: string, title: string, payload: Record<string, unknown>, at?: string): Notice {
	const level = String(payload.level ?? 'info');
	const notice: Notice = {
		id,
		title: title || String(payload.text ?? ''),
		level:
			level === 'error' || level === 'danger'
				? 'danger'
				: level === 'warn' || level === 'warning' || level === 'attention'
					? 'attention'
					: level === 'ok' || level === 'success'
						? 'ok'
						: 'info',
		at
	};
	if (title && payload.text) notice.body = String(payload.text);
	return notice;
}

/**
 * The house-wide do not disturb (`companion.set_dnd`, T-0545) from a `companion.presence` or
 * `set_dnd` call made with return_response: true, null when the answer does not say (an older
 * house). The console's dock shows it, so a "do not disturb" said aloud shows here too.
 */
export function dndOf(result: unknown): boolean | null {
	const response = (result as { response?: { dnd?: unknown } } | null)?.response;
	return typeof response?.dnd === 'boolean' ? response.dnd : null;
}
