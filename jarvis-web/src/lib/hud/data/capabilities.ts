// How much JARVIS may do in each area without asking (v2 §3, T-0608): what
// `/hud capabilities` prints (tests/contracts/capability-levels.json), read by
// the Integrations screen's "What JARVIS may do" and Settings' privacy rows.
//
// A level is set only through the `capability_set` tool, which always asks the
// owner: the console hands JARVIS the request and the approval card is the
// yes. Nothing here writes.
//
// Does not cover: what a level lets through for one call (the matrix,
// `_shared/_levels.py` decide), or the kill switch, which wins over any level.

export const LEVELS = ['off', 'read', 'ask', 'allowed'] as const;
export type Level = (typeof LEVELS)[number];

/** The board's words for each level (Integrations.dc.html). */
export const LEVEL_LABEL: Readonly<Record<Level, string>> = { off: 'Off', read: 'Read', ask: 'Ask first', allowed: 'Allowed' };

/** The person's words for each capability id `_levels.py` lists. */
const NAMES: Readonly<Record<string, string>> = {
	mail: 'Email',
	texts: 'Texts',
	calendar: 'Calendar',
	contacts: 'Contacts',
	files: 'Files',
	house: 'The house',
	phone: 'Your phone',
	media: 'Media',
	shell: 'Commands and code',
	web: 'The web'
};

export interface Capability {
	id: string;
	name: string;
	/** null: no level set, so the approval matrix applies as written. */
	level: Level | null;
	tools: { name: string; write: boolean }[];
}

const isLevel = (v: unknown): v is Level => typeof v === 'string' && (LEVELS as readonly string[]).includes(v);

/**
 * The plugin's JSON, checked: anything that is not the contract's shape throws,
 * so the screen says it could not read the levels instead of drawing none.
 */
export function parseCapabilities(output: string): Capability[] {
	const raw: unknown = JSON.parse(output);
	const rows = (raw as { capabilities?: unknown })?.capabilities;
	if (!Array.isArray(rows)) throw new Error('The capability levels came back in a shape this console does not know.');
	return rows
		.filter((r): r is { id: string; level?: unknown; tools?: unknown } => typeof r?.id === 'string')
		.map((r) => ({
			id: r.id,
			name: NAMES[r.id] ?? r.id,
			level: isLevel(r.level) ? r.level : null,
			tools: Array.isArray(r.tools)
				? r.tools.filter((t): t is { name: string; write?: unknown } => typeof t?.name === 'string').map((t) => ({ name: t.name, write: t.write !== false }))
				: []
		}));
}

/**
 * What the console says to JARVIS to change a level: `capability_set` asks the
 * owner every time, so the change lands only after the card's yes.
 */
export function levelRequest(c: Pick<Capability, 'id' | 'name'>, level: Level): string {
	return `Set the ${c.id} capability (${c.name}) to ${level}: use capability_set.`;
}
