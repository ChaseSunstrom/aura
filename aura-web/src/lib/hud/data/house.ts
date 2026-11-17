// `panel.house` from the house socket's registries and states.
//
// A row is a friendly name, an area and a state word; `on` is set only for
// the domains a person switches (light, switch, fan, siren, input_boolean),
// which is what stops the body offering to turn a temperature on. The cover,
// lock, climate and media rows of the old `EntityRow` editors are read here and
// acted on through the chat — "close the study blind" — which is the product's
// own answer to a control the panel does not draw.

import {
	areaForEntity,
	areaKey,
	domainOf,
	friendlyName,
	isOn,
	type AreaEntry,
	type DeviceRegistryEntry,
	type EntityRegistryEntry,
	type EntityState
} from '$lib/jarvisClient';
import type { Device } from '$lib/glass';

/** The domains a row gets a switch for. */
export const SWITCHABLE = new Set(['light', 'switch', 'fan', 'siren', 'input_boolean']);

/** The state in words a person reads: "on", "21.2 °C", "closed", "unavailable". */
export function stateWord(state: EntityState): string {
	const raw = String(state.state ?? '');
	if (!raw || raw === 'unknown') return 'unknown';
	const unit = state.attributes?.unit_of_measurement;
	if (unit && /^-?\d+(\.\d+)?$/.test(raw)) return `${raw} ${unit}`;
	return raw.replace(/_/g, ' ');
}

export interface HouseSources {
	states: readonly EntityState[];
	entries?: readonly EntityRegistryEntry[];
	areas?: readonly AreaEntry[];
	devices?: readonly DeviceRegistryEntry[];
}

export function devicesView(sources: HouseSources): Device[] {
	const entries = new Map((sources.entries ?? []).map((e) => [e.entity_id, e]));
	const registry = new Map((sources.devices ?? []).map((d) => [d.id, d]));
	const areaNames = new Map((sources.areas ?? []).map((a) => [areaKey(a), a.name]));
	const out: Device[] = [];
	for (const state of sources.states) {
		const entry = entries.get(state.entity_id);
		// A disabled or hidden entity is one somebody took off the house on
		// purpose; listing it here would put it back.
		if (entry?.disabled || entry?.hidden) continue;
		const areaId = areaForEntity(state.entity_id, entries, registry);
		const device: Device = {
			id: state.entity_id,
			name: friendlyName(state, entry),
			state: stateWord(state)
		};
		const area = areaId ? areaNames.get(areaId) : undefined;
		if (area) device.area = area;
		if (SWITCHABLE.has(domainOf(state.entity_id)) && state.state !== 'unavailable') {
			device.on = isOn(state);
		}
		out.push(device);
	}
	// Areas together, names in order: the body groups by area and a list that
	// changes order on every state change is a list a person cannot scan.
	return out.sort(
		(a, b) => (a.area ?? '￿').localeCompare(b.area ?? '￿') || a.name.localeCompare(b.name)
	);
}

/** One unnamed device the LAN census found, offered for a name. */
export interface NameOffer {
	mac: string;
	ip: string;
	/** The census's guess: tv, speaker, printer, nas, phone, pc, camera, bridge, cast, iot, router, unknown. */
	kind: string;
	confidence: string;
	/** What the guess rests on, in one line: vendor · mDNS · SSDP · ports. */
	evidence: string;
	/** The field's starting text; empty when the census knows nothing. */
	suggested: string;
}

const KIND_WORD: Record<string, string> = { tv: 'TV', nas: 'NAS', pc: 'PC', iot: 'device' };

/**
 * `sensor.lan_devices` (jarvis-core's netscan) as name offers: every device
 * without a name, in the sensor's order (by IP). Does not cover a device the
 * last sweep missed: an offline gadget is offered again when it next answers.
 */
export function nameOffers(states: readonly EntityState[]): NameOffer[] {
	const sensor = states.find((s) => s.entity_id === 'sensor.lan_devices');
	const rows: any[] = Array.isArray(sensor?.attributes?.devices) ? sensor!.attributes.devices : [];
	return rows
		.filter((d) => d && typeof d.mac === 'string' && !d.name)
		.map((d) => {
			const ev = d.evidence ?? {};
			const kind = typeof d.kind === 'string' ? d.kind : 'unknown';
			const parts: string[] = [];
			if (ev.vendor) parts.push(String(ev.vendor));
			if (Array.isArray(ev.mdns) && ev.mdns.length) parts.push(ev.mdns.join(', '));
			if (ev.ssdp) parts.push(String(ev.ssdp));
			if (Array.isArray(ev.ports) && ev.ports.length) parts.push(`ports ${ev.ports.join(', ')}`);
			if (d.private || ev.private_mac) parts.push('private MAC');
			const word = kind === 'unknown' ? '' : (KIND_WORD[kind] ?? kind);
			const suggested = ev.vendor
				? `${ev.vendor} ${word}`.trim()
				: word.charAt(0).toUpperCase() + word.slice(1);
			return {
				mac: d.mac,
				ip: String(d.ip ?? ''),
				kind,
				confidence: typeof d.confidence === 'string' ? d.confidence : 'low',
				evidence: parts.join(' · '),
				suggested
			};
		});
}

type ServiceCaller = {
	callService: (
		domain: string,
		service: string,
		data: Record<string, any>,
		extra: { returnResponse: boolean }
	) => Promise<any>;
};

/**
 * Name one LAN device: `netscan.name_device`, which also makes it a
 * `device_tracker`. Throws the server's own refusal (`{ok: false, error}`),
 * which a resolved socket command would otherwise hide.
 */
export async function nameDevice(link: ServiceCaller, mac: string, name: string): Promise<void> {
	const clean = name.trim();
	if (!clean) throw new Error('a name is required');
	const result = await link.callService('netscan', 'name_device', { mac, name: clean }, { returnResponse: true });
	if (result?.response?.ok === false) throw new Error(String(result.response.error ?? 'naming failed'));
}
