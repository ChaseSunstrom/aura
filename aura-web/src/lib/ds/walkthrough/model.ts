// The first-run walkthrough (T-0629): six steps in a fixed order, what is done, what was
// skipped, and where to resume.
//
// Pure. A step whose result the house can show (an account, an enrolled person, a paired
// device) is done only when the house says so; nothing the screen sends can mark it. The other
// three (persona, privacy, integrations) have valid defaults, so they are done when the owner
// has seen them and said so. Progress is one small JSON row kept by the console's server.
// Not covered: whether a step's result is good (a person enrolled with a poor sample is done).

export const STEPS = ['account', 'people', 'persona', 'privacy', 'devices', 'integrations'] as const;
export type StepId = (typeof STEPS)[number];
export type Mark = 'done' | 'skipped';
export type StepStatus = Mark | 'open';

export interface Progress {
	schema: 1;
	marks: Partial<Record<StepId, Mark>>;
	/** It has been offered once, or was opened from Settings; only Settings opens it again. */
	closed: boolean;
}

/** What the house knows, read fresh each time. */
export interface House {
	firstRun: boolean;
	people: number;
	/** Paired devices other than this console. */
	devices: number;
}

const FACT: Partial<Record<StepId, (h: House) => boolean>> = {
	account: (h) => !h.firstRun,
	people: (h) => h.people > 0,
	devices: (h) => h.devices > 0
};

export const fresh = (): Progress => ({ schema: 1, marks: {}, closed: false });

/** The stored row, or null when there is none or it cannot be read (never a throw). */
export function parse(raw: string | null | undefined): Progress | null {
	if (!raw) return null;
	try {
		const p = JSON.parse(raw);
		if (p?.schema !== 1 || typeof p.marks !== 'object' || p.marks === null) return null;
		const marks: Progress['marks'] = {};
		for (const s of STEPS) if (p.marks[s] === 'done' || p.marks[s] === 'skipped') marks[s] = p.marks[s];
		return { schema: 1, marks, closed: p.closed === true };
	} catch {
		return null;
	}
}

export const serialize = (p: Progress): string => JSON.stringify(p);

export function status(step: StepId, p: Progress | null, house: House): StepStatus {
	const fact = FACT[step];
	if (fact) return fact(house) ? 'done' : p?.marks[step] === 'skipped' ? 'skipped' : 'open';
	return p?.marks[step] ?? 'open';
}

/** The step to stand on: the first one still open, or null when none is. */
export function current(p: Progress | null, house: House): StepId | null {
	return STEPS.find((s) => status(s, p, house) === 'open') ?? null;
}

/**
 * Whether the walkthrough opens by itself. An owner who was set up before it existed has no
 * row and is not first-run, so it stays shut until Settings asks (`asked`).
 */
export function shown(p: Progress | null, house: House, asked = false): boolean {
	if (asked) return true;
	if (p?.closed) return false;
	if (house.firstRun) return true;
	return p !== null && current(p, house) !== null;
}

/** Record the owner's word on a step. `done` on a step the house decides changes nothing. */
export function mark(p: Progress | null, step: StepId, m: Mark): Progress {
	const base = p ?? fresh();
	if (m === 'done' && FACT[step]) return base;
	return { ...base, marks: { ...base.marks, [step]: m } };
}

export const close = (p: Progress | null): Progress => ({ ...(p ?? fresh()), closed: true });

export interface StepText {
	title: string;
	/** What the step settles, in one line. */
	what: string;
	/** What the house must show before the step is done; empty when the owner's word is enough. */
	needs: string;
	/** The screen where the owner does it by hand. */
	open: { path: string; label: string };
	/** What JARVIS is asked: it names the first-run skill's section, which names the tools. */
	ask: string;
}

const ASK = (step: StepId, doing: string): string =>
	`First-run setup, step "${step}" (the first-run skill has a section for it). ${doing} Propose what you would set, do what needs no approval, and ask before the rest.`;

export const TEXT: Readonly<Record<StepId, StepText>> = {
	account: {
		title: 'Account',
		what: 'Your name and the console password, which locks the console and pairs your devices.',
		needs: 'Done once the console has a password.',
		open: { path: '/settings', label: 'Settings → Account' },
		ask: ASK('account', 'Tell me what the console password protects and where I choose it.')
	},
	people: {
		title: 'Voice and people',
		what: 'Who lives here, what each may do, and whose voice is whose.',
		needs: 'Done once one person’s voice is enrolled.',
		open: { path: '/settings', label: 'Settings → People' },
		ask: ASK('people', 'Help me name the people in the house and their levels.')
	},
	persona: {
		title: 'Persona',
		what: 'The name, voice and look your assistant wears.',
		needs: '',
		open: { path: '/settings', label: 'Settings → Persona' },
		ask: ASK('persona', 'Help me choose the persona and voice.')
	},
	privacy: {
		title: 'Privacy and approvals',
		what: 'What may be done without asking, what always asks, and what never leaves the house.',
		needs: '',
		open: { path: '/integrations', label: 'Integrations → levels' },
		ask: ASK('privacy', 'Walk me through the capability levels and what stays local.')
	},
	devices: {
		title: 'Devices',
		what: 'Your phone, desktop and car, paired to this house.',
		needs: 'Done once one device is paired.',
		open: { path: '/devices', label: 'Devices' },
		ask: ASK('devices', 'Help me pair my phone and my desktop.')
	},
	integrations: {
		title: 'Integrations',
		what: 'Calendar, mail, music and anything else you want connected.',
		needs: '',
		open: { path: '/integrations', label: 'Integrations' },
		ask: ASK('integrations', 'Ask me which services I use and connect the ones you can.')
	}
};
