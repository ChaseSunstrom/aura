// A tool call, in the words a person would use for it (T-0707).
//
// The stage used to title a call with its tool name and show its raw arguments
// ("WEB EXTRACT", `urls: ["…"] · char_limit: 4000`). A step says what Jarvis is doing and
// what about: "Read bbc.co.uk", "Searched the web · weather tomorrow".
//
// Does not cover: every tool by name. One not in the table reads as its own name in
// sentence case, which is still words, not arguments.

/** [doing, done]: `{x}` takes the subject (or the third entry); otherwise the subject follows a ` · `. */
type Words = readonly [string, string, string?];

const DEVICES: Words = ["Checking the house's devices", "Checked the house's devices"];
const BROWSER: Words = ['Using the browser', 'Used the browser'];
const MEMORY: Words = ['Checking memory', 'Checked memory'];
const COMMAND: Words = ['Running a command', 'Ran a command'];

const WORDS: Record<string, Words> = {
	web_search: ['Searching the web', 'Searched the web'],
	web_extract: ['Reading {x}', 'Read {x}', 'a page'],
	ha_list_entities: DEVICES,
	house_devices: DEVICES,
	house_areas: ["Checking the house's rooms", "Checked the house's rooms"],
	ha_get_state: ['Checking {x}', 'Checked {x}', 'the house'],
	ha_call_service: ['Changing {x}', 'Changed {x}', 'a device'],
	house_device_action: ['Changing {x}', 'Changed {x}', 'a device'],
	house_history: ["Looking back at the house's history", "Looked back at the house's history"],
	house_since: ["Looking back at the house's history", "Looked back at the house's history"],
	house_automations: ['Checking automations', 'Checked automations'],
	terminal: COMMAND,
	execute_code: COMMAND,
	process: COMMAND,
	read_file: ['Reading {x}', 'Read {x}', 'a file'],
	search_files: ['Searching files', 'Searched files'],
	write_file: ['Writing {x}', 'Wrote {x}', 'a file'],
	patch: ['Editing {x}', 'Edited {x}', 'a file'],
	memory: MEMORY,
	fact_store: MEMORY,
	session_search: ['Searching past conversations', 'Searched past conversations'],
	calendar_events: ['Checking the calendar', 'Checked the calendar'],
	mail_search: ['Checking mail', 'Checked mail'],
	mail_read: ['Reading mail', 'Read mail'],
	delegate_task: ['Asking a helper', 'Asked a helper'],
	vision_analyze: ['Looking at an image', 'Looked at an image'],
	image_generate: ['Drawing an image', 'Drew an image'],
	route_eta: ['Checking the route', 'Checked the route'],
	weather_forecast: ['Checking the weather', 'Checked the weather'],
	calculate: ['Working it out', 'Worked it out']
};

/** Families the table does not name one by one. */
const FAMILIES: readonly [string, Words][] = [
	['browser_', BROWSER],
	['desktop_', ['Using the computer', 'Used the computer']],
	['calendar_', ['Checking the calendar', 'Checked the calendar']],
	['mail_', ['Checking mail', 'Checked mail']],
	['mcp__notes__', ['Checking notes', 'Checked notes']]
];

const MAX = 48;
const cut = (s: string): string => (s.length > MAX ? `${s.slice(0, MAX - 1)}…` : s);

function host(url: string): string {
	try {
		return new URL(url).hostname.replace(/^www\./, '');
	} catch {
		return '';
	}
}

/** What the call is about: a page's site, the question, the thing, the file; else Hermes's context. */
function subjectOf(args: Record<string, unknown> | undefined, context: string): string {
	const a = args ?? {};
	const urls = (Array.isArray(a.urls) ? a.urls : [a.url]).filter((u): u is string => typeof u === 'string');
	const hosts = [...new Set(urls.map(host).filter(Boolean))];
	if (hosts.length) return cut(hosts.join(', '));
	for (const key of ['query', 'q', 'question', 'entity_id', 'name', 'title', 'place']) {
		if (typeof a[key] === 'string' && a[key]) return cut(a[key] as string);
	}
	if (typeof a.path === 'string' && a.path) return cut(a.path.split('/').pop() || a.path);
	return context.trim() ? cut(context.trim()) : '';
}

/** Whether the tool has words of its own; without them `stepOf` falls back to its name. */
export const worded = (name: string): boolean => Boolean(WORDS[name] ?? FAMILIES.find(([prefix]) => name.startsWith(prefix)));

/**
 * The plain line for one call in its state. An empty string for a call that only presents
 * (`hud_*`: the projection or the voice is what the person sees, not a step).
 */
export function stepOf(
	name: string,
	args: Record<string, unknown> | undefined,
	context: string,
	state: 'running' | 'ok' | 'failed',
	error?: string | null
): string {
	if (name.startsWith('hud_')) return '';
	const words = WORDS[name] ?? FAMILIES.find(([prefix]) => name.startsWith(prefix))?.[1];
	const subject = subjectOf(args, context);
	let line: string;
	if (!words) {
		const plain = name.replace(/^mcp__\w+?__/, '').replace(/_/g, ' ');
		line = plain.charAt(0).toUpperCase() + plain.slice(1);
	} else {
		const phrase = words[state === 'ok' ? 1 : 0];
		line = phrase.includes('{x}') ? phrase.replace('{x}', subject || (words[2] ?? '')) : subject ? `${phrase} · ${subject}` : phrase;
	}
	return state === 'failed' ? `${line} — ${error || 'failed'}` : line;
}
