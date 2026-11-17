// `panel.automations` from jarvis-core's automation rows.
//
// The trigger is turned into a sentence — "When the front door opens", "Every
// day at 07:00", "When the study temperature drops below 18" — because that is
// what a person checks the list against; the YAML it came from is what the old
// editor showed and what a coding job or the chat edits now. Anything the
// sentence-maker does not recognise is named by its platform rather than
// hidden, so a trigger nobody wrote a phrase for is still visible.

import type { AutomationRow, EntityState } from '$lib/jarvisClient';
import type { Automation } from '$lib/glass';

type Trigger = Record<string, unknown>;

const asRecord = (value: unknown): Trigger =>
	value && typeof value === 'object' && !Array.isArray(value) ? (value as Trigger) : {};

const str = (value: unknown): string => (value === null || value === undefined ? '' : String(value));

/** A Home Assistant time without its seconds: `23:00:00` is a clock nobody reads to the second. */
const clock = (value: string): string => value.replace(/^(\d{1,2}:\d{2}):00$/, '$1');

/** The entity's readable stem: `binary_sensor.front_door` → "front door". */
function nameOf(entityId: unknown): string {
	const id = Array.isArray(entityId) ? str(entityId[0]) : str(entityId);
	const stem = id.includes('.') ? id.slice(id.indexOf('.') + 1) : id;
	return stem.replace(/_/g, ' ');
}

export function describeTrigger(raw: unknown): string {
	const t = asRecord(raw);
	const platform = str(t.platform ?? t.trigger);
	switch (platform) {
		case 'state': {
			const who = nameOf(t.entity_id);
			const to = str(t.to);
			const from = str(t.from);
			if (to) return `When the ${who} turns ${to.replace(/_/g, ' ')}`;
			if (from) return `When the ${who} leaves ${from.replace(/_/g, ' ')}`;
			return `When the ${who} changes`;
		}
		case 'numeric_state': {
			const who = nameOf(t.entity_id);
			if (t.below !== undefined) return `When the ${who} drops below ${str(t.below)}`;
			if (t.above !== undefined) return `When the ${who} rises above ${str(t.above)}`;
			return `When the ${who} crosses a threshold`;
		}
		case 'time':
			return `Every day at ${clock(str(t.at)) || 'a set time'}`;
		case 'time_pattern': {
			const minutes = str(t.minutes);
			const hours = str(t.hours);
			if (minutes.startsWith('/')) return `Every ${minutes.slice(1)} minutes`;
			if (hours.startsWith('/')) return `Every ${hours.slice(1)} hours`;
			return 'On a time pattern';
		}
		case 'sun':
			return `At ${str(t.event) === 'sunrise' ? 'sunrise' : 'sunset'}${t.offset ? ` (${str(t.offset)})` : ''}`;
		case 'homeassistant':
			return str(t.event) === 'shutdown' ? 'When the house shuts down' : 'When the house starts';
		case 'event':
			return `When a ${str(t.event_type).replace(/_/g, ' ') || 'bus'} event fires`;
		case 'mqtt':
			return `When ${str(t.topic) || 'a topic'} is published`;
		case 'webhook':
			return 'When its webhook is called';
		case 'zone':
			return `When ${nameOf(t.entity_id)} ${str(t.event) === 'leave' ? 'leaves' : 'enters'} ${nameOf(t.zone)}`;
		case 'template':
			return 'When a template becomes true';
		case '':
			return 'Run by hand';
		default:
			return `On a ${platform.replace(/_/g, ' ')} trigger`;
	}
}

/** Every trigger, as one sentence; several are joined with "or". */
export function describeTriggers(triggers: readonly unknown[]): string {
	const parts = triggers.map(describeTrigger).filter(Boolean);
	if (!parts.length) return 'Run by hand';
	if (parts.length === 1) return parts[0];
	return `${parts.slice(0, -1).join(', ')} or ${parts[parts.length - 1].replace(/^When |^At |^Every /, (m) => m.toLowerCase())}`;
}

/**
 * The rows as the panel draws them. Whether one is on comes from its entity's
 * live state when the house has reported it (`automation.<id>` is `on` or
 * `off`, and `automation.turn_off` changes that at once through
 * `state_changed`); the row's own `enabled` is the fallback for a list read
 * before the states arrived. Reading the row alone left the switch on after
 * a turn_off until the next full reload.
 */
export function automationsView(rows: readonly AutomationRow[], states: readonly EntityState[] = []): Automation[] {
	const live = new Map(states.map((s) => [s.entity_id, s.state]));
	return rows.map((row) => {
		const state = live.get(row.entity_id);
		return {
			id: row.id,
			title: row.alias,
			// ` · ` between the clauses, the separator the rest of the console uses
			// for one: "Every day at 23:00. Asks before it runs: can lock." was a
			// sentence, a colon and a fragment in a line one row tall.
			when: row.needs_approval
				? [describeTriggers(row.trigger), 'asks before it runs', row.reach].filter(Boolean).join(' · ')
				: describeTriggers(row.trigger),
			enabled: state === 'on' || state === 'off' ? state === 'on' : row.enabled !== false,
			surface: 'house'
		};
	});
}
