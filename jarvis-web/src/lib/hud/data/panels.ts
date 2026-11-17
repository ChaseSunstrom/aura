// The content panels JARVIS pulls up (`hud.panel.show`, contract
// `tests/contracts/hud-panels.json`), mapped onto the bodies by kind.
//
// GLASS_HUD §9.1's last paragraph is the table: text/code → document,
// table/chart/telemetry → telemetry, image/camera → image, terminal → task,
// web → browser, device → house, goal → goals, approval/clarify → approvals,
// notice → notifications, video → video, audio → audio, document (a file by
// url) → document. `panelTypes.ts` holds the kind → type map; this file holds
// the payload → props half, so the seventeen kinds land in eleven bodies
// instead of in one seventeen-branch component (`components/hud/PanelBody`).
//
// Every read is loose — `String(p.x ?? '')` — because the payload is the
// emitter's and a field it left out is a blank, never a crash on the HUD.

import type { ActivityState } from '$lib/hermes/activity';
import { parseGoalText } from '$lib/hermes/goal';
import type { Panel } from '$lib/hermes/panels';
import { mayEmbed, webPanelOrigins } from '$lib/hermes/webPanel';
import { PANEL_TYPE_NAMES, typeForKind, type ApprovalRequest, type Device, type Goal, type Notice, type TaskStep, type TelemetryReading } from '$lib/glass';
import { goalView } from './goals';
import { noticeOfPanel } from './notifications';
import { chartReadings, gpuReadings, tableReadings } from './telemetry';
import { clockOf } from './time';

const str = (value: unknown): string => (value === null || value === undefined ? '' : String(value));

/**
 * The types the adapter serves as an attachment, never inline
 * (`aura_adapter/artifacts.py` `EXECUTABLE_MIME`, through `safe_disposition`):
 * a browser would run them. `tests/contracts/hud-panels.json` `download_mimes`
 * is the one table both sides read; mappings.test.ts holds this copy to it.
 */
export const DOWNLOAD_MIMES: ReadonlySet<string> = new Set([
	'text/html',
	'application/xhtml+xml',
	'image/svg+xml',
	'application/xml',
	'text/xml',
	'application/javascript',
	'text/javascript'
]);

export function servedAsDownload(mime: string): boolean {
	return DOWNLOAD_MIMES.has(mime.split(';')[0].trim().toLowerCase());
}

/**
 * A media panel's caption: `tool · host` — the tool that made the file and
 * where it came from (the artifact store's provenance). Either half may be
 * missing; a source that is not a URL is shown as written.
 */
export function provenanceOf(p: Record<string, unknown>): string {
	const prov = (p.provenance ?? {}) as Record<string, unknown>;
	const source = str(prov.source_url);
	let host = source;
	try {
		host = new URL(source).host;
	} catch {
		/* not a URL: a file path or a name, shown as it is */
	}
	return [str(prov.tool), host].filter(Boolean).join(' · ');
}

/** What a content panel gives its body: the type's own props, nothing else. */
export type ContentBody =
	| { type: 'document'; kind: 'markdown' | 'code'; text: string; title: string; path: string }
	| { type: 'document'; kind: 'file'; url: string; mime: string; title: string; caption: string; download: boolean }
	| { type: 'video'; src: string; mime: string; title: string; caption: string }
	| { type: 'audio'; src: string; mime: string; title: string; caption: string }
	| { type: 'telemetry'; readings: TelemetryReading[] }
	| { type: 'image'; src: string; alt: string; caption: string; at: string }
	| { type: 'task'; title: string; steps: TaskStep[]; output: string }
	| { type: 'browser'; url: string; title: string; summary: string; embed: boolean }
	| { type: 'activity'; activity: ActivityState }
	| { type: 'house'; devices: Device[] }
	| { type: 'goals'; goal: Goal | null }
	| { type: 'approvals'; requests: ApprovalRequest[] }
	| { type: 'notifications'; notices: Notice[] };

/** A panel's id and its type: what the v2 projections key and label a content panel by. */
export interface PanelSpec {
	id: string;
	type: string;
}

export interface ContentPanel {
	spec: PanelSpec;
	title: string;
	body: ContentBody;
	/** The emitter's own id, for `hud dismiss`. */
	panelId: string;
}

/**
 * The layout's id for a content panel: the emitter's own, so `hud dismiss`
 * and the gates' `panel-<id>` test ids need no translation — unless it is a
 * name a shipped panel already holds (`chat`, `model`, …), in which case it
 * is suffixed rather than allowed to replace the panel of that name.
 */
export const CONTENT_SUFFIX = ':content';

export function contentIdOf(panelId: string, reserved: ReadonlySet<string> = RESERVED): string {
	return reserved.has(panelId) ? `${panelId}${CONTENT_SUFFIX}` : panelId;
}

/** The ids the shipped layouts use: every panel type is its own panel's id. */
export const RESERVED: ReadonlySet<string> = new Set(PANEL_TYPE_NAMES);

function bodyOf(panel: Panel, embedOrigins: string[]): ContentBody {
	const p = panel.payload;
	switch (panel.kind) {
		case 'text':
			return { type: 'document', kind: 'markdown', text: str(p.markdown ?? p.text), title: '', path: '' };
		case 'code':
			return {
				type: 'document',
				kind: 'code',
				text: str(p.text),
				title: '',
				path: [str(p.path), str(p.language)].filter(Boolean).join(', ')
			};
		case 'table':
			return { type: 'telemetry', readings: tableReadings(p) };
		case 'chart':
			return { type: 'telemetry', readings: chartReadings(p) };
		case 'telemetry':
			return { type: 'telemetry', readings: gpuReadings(p) };
		case 'image':
			return { type: 'image', src: str(p.url), alt: str(p.alt) || panel.title, caption: str(p.caption), at: '' };
		case 'camera': {
			const taken = Date.parse(str(p.taken_at));
			return {
				type: 'image',
				src: str(p.url),
				alt: `${str(p.camera) || 'Camera'} still`,
				caption: str(p.camera),
				at: Number.isFinite(taken) ? clockOf(taken) : str(p.taken_at)
			};
		}
		case 'terminal': {
			const exit = p.exit_code;
			const exited = exit !== undefined && exit !== null;
			return {
				type: 'task',
				title: `$ ${str(p.command)}`,
				steps: exited
					? [{ id: `${panel.id}:exit`, label: `exit ${str(exit)}`, state: Number(exit) === 0 ? 'done' : 'failed' }]
					: p.live
						? [{ id: `${panel.id}:live`, label: 'running', state: 'running' }]
						: [],
				output: str(p.output)
			};
		}
		case 'web': {
			const url = str(p.url);
			return {
				type: 'browser',
				url,
				title: str(p.title),
				summary: str(p.summary),
				// Framing is a security decision, not a payload flag: only the
				// origins the console's allow-list names are embedded.
				embed: mayEmbed(url, embedOrigins)
			};
		}
		case 'device': {
			const attributes = (p.attributes ?? {}) as Record<string, unknown>;
			const rawState = str(p.state);
			const unit = str(attributes.unit_of_measurement);
			const domain = str(p.entity_id).split('.')[0];
			const device: Device = {
				id: str(p.entity_id),
				name: str(p.name ?? attributes.friendly_name) || str(p.entity_id),
				state: unit && /^-?\d/.test(rawState) ? `${rawState} ${unit}` : rawState.replace(/_/g, ' ')
			};
			if (['light', 'switch', 'fan', 'siren', 'input_boolean'].includes(domain)) device.on = rawState === 'on';
			return { type: 'house', devices: [device] };
		}
		case 'goal':
			return { type: 'goals', goal: goalView(parseGoalText(str(p.text ?? p.phase), panel.createdAt), str(p.goal)) };
		case 'approval':
		case 'clarify': {
			const request: ApprovalRequest = {
				id: `hermes:${str(p.request_id ?? p.id ?? panel.id)}`,
				kind: panel.kind,
				title: panel.kind === 'clarify' ? str(p.question) : str(p.reason) || `Run ${str(p.tool) || 'this'}?`,
				at: clockOf(panel.createdAt)
			};
			const detail =
				panel.kind === 'clarify'
					? Array.isArray(p.options) && p.options.length
						? `Options: ${(p.options as unknown[]).map(String).join(', ')}`
						: ''
					: str(p.command) ||
						Object.entries((p.args as Record<string, unknown>) ?? {})
							.map(([k, v]) => `${k}: ${typeof v === 'object' ? JSON.stringify(v) : String(v)}`)
							.join(', ');
			if (detail) request.detail = detail;
			if (panel.kind === 'approval' && p.tool) request.tool = str(p.tool);
			return { type: 'approvals', requests: [request] };
		}
		case 'notice':
			return { type: 'notifications', notices: [noticeOfPanel(panel.id, panel.title, p, clockOf(panel.createdAt))] };
		case 'video':
			return { type: 'video', src: str(p.url), mime: str(p.mime), title: str(p.title) || panel.title, caption: provenanceOf(p) };
		case 'audio':
			return { type: 'audio', src: str(p.url), mime: str(p.mime), title: str(p.title) || panel.title, caption: provenanceOf(p) };
		case 'document': {
			const mime = str(p.mime);
			return { type: 'document', kind: 'file', url: str(p.url), mime, title: str(p.title) || panel.title, caption: provenanceOf(p), download: servedAsDownload(mime) };
		}
		default:
			// A kind the contract adds later lands on `document` as readable text
			// rather than on nothing: the payload, pretty-printed, is still what
			// JARVIS wanted shown.
			return { type: 'document', kind: 'code', text: JSON.stringify(p, null, 2), title: '', path: panel.kind };
	}
}

/** One content panel as the layout places it and its body draws it. */
export function contentPanel(panel: Panel, embedOrigins = webPanelOrigins()): ContentPanel {
	return {
		spec: { id: contentIdOf(panel.id), type: typeForKind(panel.kind) },
		title: panel.title || panel.kind,
		body: bodyOf(panel, embedOrigins),
		panelId: panel.id
	};
}

/** Every content panel on the surface, oldest first so a new one lands at the end. */
export function contentPanels(panels: readonly Panel[], embedOrigins = webPanelOrigins()): ContentPanel[] {
	return panels
		.slice()
		.sort((a, b) => a.createdAt - b.createdAt || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0))
		.map((panel) => contentPanel(panel, embedOrigins));
}
