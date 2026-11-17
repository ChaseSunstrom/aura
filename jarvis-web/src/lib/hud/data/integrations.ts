// `panel.integrations` from the five lists the old Tools screen drew.
//
// Plugins are jarvis-core's tools (its registry) and Hermes's toolsets are the
// model panel's business, not this one's; skills are both the house's folders
// and Hermes's `GET /v1/skills`; MCP servers are jarvis-core's; extensions are
// the installed registry; the catalogue is what could be installed. One kind
// word per row, and the id says which list it came from so an install or a
// removal reaches the right command.

import type { Skill as HermesSkill } from '$lib/hermes/protocol';
import type { Skill as HouseSkill, ToolRow } from '$lib/jarvisClient';
import type { McpServer } from '$lib/mcpDraft';
import type { Integration } from '$lib/glass';
import { agoOf, untilOf } from './time';

/** One row of `jarvis/extensions/list`, as much of it as the panel reads. */
export interface ExtensionRow {
	id: string;
	kind: 'skill' | 'mcp' | 'plugin' | string;
	key: string;
	version?: string;
	description?: string;
	enabled?: boolean;
	health?: { ok?: boolean; detail?: string };
}

/** One row of `jarvis/extensions/browse`. */
export interface CatalogueRow {
	id: string;
	kind: string;
	source?: string;
	version?: string;
	description?: string;
	installed?: boolean;
}

/**
 * The quarantine fence is for the model's context; a person reads the text
 * without it. Mirrors jarvis-core `quarantine.unwrap`, bound by
 * `tests/contracts/untrusted-fence.json`. Text that is not fenced is returned as is.
 */
export function unwrapUntrusted(text: string | undefined): string {
	const t = text ?? '';
	const open = '<untrusted_content>\n';
	const close = '\n</untrusted_content>';
	if (!t.startsWith(open) || !t.endsWith(close)) return t;
	// the notice is one line, an optional " Source (…): …" runs on from it; the body starts after the blank line
	const body = t.slice(open.length, t.length - close.length);
	const at = body.indexOf('\n\n');
	return (at === -1 ? '' : body.slice(at + 2)).replace(/\n$/, '');
}

// --- Accounts (T-1343): the house's `oauth.status`, one row per sign-in provider -------------
//
// Read through `call_service` with a response, like the held approvals: no command of its own.
// The house sends no token (`Connectors.status`); this copies only the fields it shows, so
// one that ever did would still not reach a row. Every action stays a sentence to JARVIS.

export type AccountState = 'connected' | 'needs-signin' | 'not-set-up';

export interface AccountRow {
	id: string;
	name: string;
	state: AccountState;
	/** The state in words; colour is only a second signal. */
	word: string;
	/** Last test, or what the person must do next. */
	detail: string;
	/** Lines under `detail`: the token's expiry and a failed refresh, each only when there is something to say. */
	notes: string[];
	/** Sentences for JARVIS; a missing key is a button the row does not have. */
	asks: { connect?: string; test?: string; disconnect?: string; examples: string[] };
}

/** What a connected account can be asked now. Only reads the plugins have; an account not named has Test alone. */
export const ACCOUNT_EXAMPLES: Record<string, readonly string[]> = {
	google: ["What's on my calendar today?", 'Any new email in my Gmail inbox?'],
	github: ["What's new in my GitHub notifications?"],
	microsoft: ["What's in my Outlook inbox?", "What's on my Outlook calendar today?"],
	slack: ['Which Slack channels am I in?'],
	discord: ['Which Discord servers am I in?'],
	gitlab: ['What are my open GitLab issues?'],
	spotify: ["What's playing on Spotify?"],
	notion: ['Which Notion workspace am I connected to?'],
	todoist: ["What's on my Todoist list today?", 'Add "buy milk" to my Todoist']
};

const WORDS: Record<AccountState, string> = { connected: 'Connected', 'needs-signin': 'Needs sign-in', 'not-set-up': 'Not set up' };
const text = (v: unknown, max = 40): string => (typeof v === 'string' ? v.slice(0, max) : '');

function testLine(last: unknown, now: number): string {
	const t = last as { at?: unknown; calls?: unknown } | null;
	if (!t || typeof t !== 'object' || typeof t.at !== 'number') return 'Not tested yet';
	const calls = (Array.isArray(t.calls) ? t.calls : []).filter((c): c is { name?: unknown; ok?: unknown } => !!c && typeof c === 'object');
	const when = `Tested ${agoOf(t.at * 1000, now)}`;
	if (!calls.length) return `${when}: no call ran`;
	const failed = calls.filter((c) => c.ok !== true).map((c) => text(c.name)).filter(Boolean);
	return `${when}: ${calls.length - failed.length} of ${calls.length} calls passed${failed.length ? `, ${failed.join(', ')} failed` : ''}`;
}

/** Under the Tested line: when the token runs out, and the word for why the last refresh failed. No token is read. */
function tokenLines(p: Record<string, any>, state: AccountState, now: number): string[] {
	const out: string[] = [];
	if (state === 'connected' && typeof p.expires_at === 'number' && Number.isFinite(p.expires_at)) {
		out.push(p.expires_at * 1000 <= now ? 'Token expired, renewed on next use' : `Expires ${untilOf(p.expires_at * 1000, now)}`);
	}
	const e = p.last_refresh_error as { reason?: unknown; at?: unknown } | null | undefined;
	// The house picks one of three words; anything else is not shown rather than trusted.
	if (state !== 'not-set-up' && e && typeof e === 'object' && typeof e.at === 'number' && ['refused', 'unreachable', 'busy'].includes(e.reason as string)) {
		out.push(`Last refresh failed: ${e.reason}, ${agoOf(e.at * 1000, now)}`);
	}
	return out;
}

/** Null when the house did not answer with a provider table: the section says so, the screen still loads. */
export function accountsView(reply: unknown, now = Date.now()): AccountRow[] | null {
	const r = reply as Record<string, any> | null | undefined;
	const body = r?.response ?? r?.service_response ?? r;
	const providers = body?.providers;
	if (!providers || typeof providers !== 'object' || Array.isArray(providers)) return null;
	const out: AccountRow[] = [];
	for (const [id, p] of Object.entries(providers as Record<string, any>)) {
		if (!/^[a-z_]{1,40}$/.test(id) || !p || typeof p !== 'object') continue;
		const name = text(p.label) || id;
		const state: AccountState = p.status === 'connected' ? 'connected' : p.status === 'not_configured' || p.configured === false ? 'not-set-up' : 'needs-signin';
		const env = `OAUTH_${id.toUpperCase()}`;
		out.push({
			id,
			name,
			state,
			word: WORDS[state],
			detail:
				state === 'connected'
					? testLine(p.last_test, now)
					: state === 'not-set-up'
						? `No client set up. Register an app with ${name}, put its client id and secret in the house settings (${env}_CLIENT_ID and ${env}_CLIENT_SECRET) and restart the house.`
						: p.status === 'needs_consent'
							? 'Access lapsed. Sign in again.'
							: 'Not signed in yet.',
			notes: tokenLines(p, state, now),
			asks: {
				...(state === 'needs-signin' ? { connect: `Connect my ${name} account (connect_start, provider ${id}).` } : {}),
				...(state === 'connected'
					? { test: `Test my ${name} connection (connect_status, provider ${id}).`, disconnect: `Disconnect my ${name} account (connect_revoke, provider ${id}).` }
					: {}),
				examples: state === 'connected' ? [...(ACCOUNT_EXAMPLES[id] ?? [])] : []
			}
		});
	}
	return out.length ? out : null;
}

const tier = (n: number | undefined): string =>
	n === 3 ? 'asks before it runs' : n === 2 ? 'runs, and is logged' : n === 1 ? 'runs freely' : '';

export interface IntegrationSources {
	tools?: readonly ToolRow[];
	skills?: readonly HouseSkill[];
	hermesSkills?: readonly HermesSkill[];
	mcp?: readonly McpServer[];
	extensions?: readonly ExtensionRow[];
	catalogue?: readonly CatalogueRow[];
}

export function integrationsView(sources: IntegrationSources): Integration[] {
	const out: Integration[] = [];
	for (const t of sources.tools ?? []) {
		out.push({
			id: `tool:${t.name}`,
			name: t.name,
			kind: 'plugin',
			// ` · `, not `. `: the tier is a clause and joining it with a full stop
			// gave "Lock or unlock a door. asks before it runs" on every row.
			detail: [t.description, tier(t.tier)].filter(Boolean).join(' · '),
			installed: true
		});
	}
	for (const s of sources.skills ?? []) {
		out.push({ id: `skill:${s.name}`, name: s.name, kind: 'skill', detail: s.description, installed: true });
	}
	for (const s of sources.hermesSkills ?? []) {
		out.push({
			id: `hermes-skill:${s.name}`,
			name: s.name,
			kind: 'skill',
			detail: [s.category, s.description].filter(Boolean).join('. '),
			installed: true
		});
	}
	for (const m of sources.mcp ?? []) {
		out.push({
			id: `mcp:${m.name}`,
			name: m.name,
			kind: 'mcp',
			detail: m.connected
				? `${m.tool_count} tool${m.tool_count === 1 ? '' : 's'}, connected`
				: m.error
					? `Not connected: ${m.error}`
					: m.enabled
						? 'Not connected yet'
						: 'Off',
			installed: true
		});
	}
	for (const e of sources.extensions ?? []) {
		const health = e.health?.ok === false ? e.health.detail || 'not healthy' : '';
		out.push({
			id: `extension:${e.id}`,
			name: e.key,
			kind: 'extension',
			detail: [e.version ? `v${e.version}` : '', unwrapUntrusted(e.description), e.enabled === false ? 'off' : '', health]
				.filter(Boolean)
				.join('. '),
			installed: true
		});
	}
	for (const c of sources.catalogue ?? []) {
		out.push({
			id: `catalogue:${c.id}`,
			name: c.id,
			kind: 'catalogue',
			detail: [c.kind, c.version ? `v${c.version}` : '', unwrapUntrusted(c.description)].filter(Boolean).join('. '),
			installed: c.installed === true
		});
	}
	return out;
}
