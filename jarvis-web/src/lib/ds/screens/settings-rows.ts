/**
 * Every row of Settings, as data: its section, the name a person would search for, one line
 * that says what it does, a little more on demand, and what "ask" hands the assistant.
 * The screen draws the hint, the "more" and the ask control from this table and the search
 * box filters it, so a row cannot have a control without the three (T-0693).
 *
 * Does not cover: the rows inside People and inside Privacy's levels (each list is one row
 * here; its own lines carry their own controls), or whether a row is shown on this device
 * (the screen hides the open mic where the phone listens for it).
 */

export const SETTING_SECTIONS = ['account', 'people', 'persona', 'theme', 'privacy', 'devices'] as const;
export type SettingSection = (typeof SETTING_SECTIONS)[number];

/** Each section's mark: one stroke path on a 24 grid, drawn in the label's ink. */
export const SECTION_ICON: Record<SettingSection, string> = {
	account: 'M7 11V8a5 5 0 0 1 10 0v3M5 11h14v9H5z',
	people: 'M9 11a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM3 20a6 6 0 0 1 12 0M16 5.2a3 3 0 0 1 0 5.6M17.5 14.4A6 6 0 0 1 21 20',
	persona: 'M4 10v4M8 6v12M12 3v18M16 7v10M20 10v4',
	theme: 'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zM12 3v18M12 8h7.9M12 12h9M12 16h7.9',
	privacy: 'M12 3l8 3v6c0 4.5-3.2 7.8-8 9-4.8-1.2-8-4.5-8-9V6zM9 12l2 2 4-4',
	devices: 'M8 3h8v18H8zM11 18h2'
};

export interface SettingRow {
	id: string;
	section: SettingSection;
	label: string;
	/** One line beside More and Ask in the screen's narrow column: 46 characters at most. */
	hint: string;
	/** Shown on demand; two lines at most on a phone. */
	more: string;
	/** Other words a person might type for it. */
	words?: string;
}

export const SETTING_ROWS: readonly SettingRow[] = [
	{
		id: 'lock-after',
		section: 'account',
		label: 'Lock after',
		hint: 'Quiet minutes before the password is asked.',
		more: 'Kept in this browser only. Any number of minutes works, not just the presets.',
		words: 'timeout password idle screen'
	},
	{
		id: 'voice-unlock',
		section: 'account',
		label: 'Voice unlock',
		hint: 'A voice the house knows opens the lock.',
		more: 'Only an enrolled voice counts, and a guest’s never does.',
		words: 'speaker biometric'
	},
	{
		id: 'people',
		section: 'people',
		label: 'People and voices',
		hint: 'Who the house knows by voice, and their level.',
		more: 'Enrol a voice with a few spoken lines; forgetting one deletes its print.',
		words: 'enrol guest family speaker level'
	},
	{
		id: 'persona',
		section: 'persona',
		label: 'Who answers',
		hint: 'The persona that speaks, on every device.',
		more: 'Its name, voice and theme come with it, unless this device keeps its own.',
		words: 'assistant name identity'
	},
	{
		id: 'replies',
		section: 'persona',
		label: 'Replies off the core',
		hint: 'Written only, or read aloud as well.',
		more: 'Reading aloud needs a voice on the house; a spoken question is always answered aloud.',
		words: 'speak tts read aloud mute'
	},
	{
		id: 'wake',
		section: 'persona',
		label: 'The open mic',
		hint: 'Send what follows the name, or every word.',
		more: 'The name is heard on this device; nothing before it leaves the browser.',
		words: 'wake word microphone listening hotword'
	},
	{
		id: 'voice',
		section: 'persona',
		label: 'The voice on this device',
		hint: 'One of three on this device; hear each first.',
		more: 'Kept on this device only. The persona’s own voice stays everywhere else.',
		words: 'tts sound speaker sample'
	},
	{
		id: 'theme',
		section: 'theme',
		label: 'Theme',
		hint: 'Each tile is that theme, live. Keep one here.',
		more: 'A theme drafted by voice shows as a tile until you apply it or put it away.',
		words: 'colour color look appearance dark'
	},
	{
		id: 'levels',
		section: 'privacy',
		label: 'Privacy and approvals',
		hint: 'What may happen in each area without asking.',
		more: 'A level changes only after your yes. “Ask first” asks on whichever device you’re using.',
		words: 'permission capability allow ask first email calendar'
	},
	{
		id: 'devices',
		section: 'devices',
		label: 'Devices',
		hint: 'Phones and desktops paired with this house.',
		more: 'Pair, rename or revoke one under Your devices.',
		words: 'pair phone desktop revoke'
	}
];

export const settingRow = (id: string): SettingRow => {
	const row = SETTING_ROWS.find((r) => r.id === id);
	if (!row) throw new Error(`no settings row ${id}`);
	return row;
};

/**
 * The ids of the rows a search shows: every word typed must be in the row's label, hint,
 * section or other words. Nothing typed shows every row.
 */
export function matchRows(query: string, rows: readonly SettingRow[] = SETTING_ROWS): Set<string> {
	const words = query.toLowerCase().split(/\s+/).filter(Boolean);
	return new Set(
		rows
			.filter((r) => {
				const hay = `${r.label} ${r.hint} ${r.section} ${r.words ?? ''}`.toLowerCase();
				return words.every((w) => hay.includes(w));
			})
			.map((r) => r.id)
	);
}

/** What the row's ask control says to the assistant: a request, never a value set from here. */
export const askFor = (row: SettingRow): string => `Help me change the “${row.label}” setting.`;
