// Which tool is this approval prompt about?
//
// Hermes carries no structured tool field on an approval event. The payload
// `approval.request` puts on the wire is `{command, pattern_key, pattern_keys,
// description, allow_permanent, allow_session, request_id, event, run_id,
// timestamp, choices}`, and the tool's name exists only inside `command` —
// rendered by Hermes' `tools/approval.py` as `<tool_name> (plugin approval
// rule)` — and as the head of `pattern_key` after `plugin_rule:`.
//
// So the card used to read `p.tool`, which is never there, and every approval
// on the console was titled with the literal word "tool". The same missing
// field made every benchmark `approval_gate` checker with a `tool:` filter
// match nothing at all.
//
// The parse is the same one the audit log and the benchmark use
// (`hermes/plugins/_shared/_approval_event.py`), and it is in two languages, so
// `tests/contracts/approval-event.json` is the table both suites read rather
// than a comment asking the next person to keep them in step.

/** `<tool_name> (plugin approval rule)` — `display_target` in Hermes' `tools/approval.py`. */
const TOOL_IN_COMMAND = /^<([A-Za-z0-9_.:-]+)>\s*\(plugin approval rule\)/;

/** `plugin_rule:<rule_key>` — the grain an `[a]lways` answer remembers. */
const PLUGIN_RULE_PREFIX = 'plugin_rule:';

/** The structured fields tried before the parse, so it retires itself if Hermes adds one. */
const STRUCTURED_FIELDS = ['tool', 'tool_name'] as const;

/**
 * The parse itself, for a caller holding the two strings rather than the event.
 *
 * Returns `''` for a prompt that is about a command rather than a tool — Hermes'
 * own Tier-2 dangerous-command gate puts the literal shell command in `command`.
 * Empty is an answer, not a failure: a caller filtering by tool name should read
 * it as "not this tool", never as "no approval happened".
 */
export function toolFromStrings(command: string, patternKey = ''): string {
	const match = TOOL_IN_COMMAND.exec((command || '').trim());
	if (match) return match[1];
	const key = (patternKey || '').trim();
	if (key.startsWith(PLUGIN_RULE_PREFIX)) {
		return key.slice(PLUGIN_RULE_PREFIX.length).split(':', 1)[0];
	}
	return '';
}

/** The tool an approval event is about, or `''` for a shell-command prompt. */
export function toolOfApproval(approval: Record<string, unknown> | null | undefined): string {
	if (!approval || typeof approval !== 'object') return '';
	for (const field of STRUCTURED_FIELDS) {
		const value = (approval as Record<string, unknown>)[field];
		if (typeof value === 'string' && value) return value;
	}
	const command = approval.command;
	const patternKey = approval.pattern_key;
	return toolFromStrings(
		typeof command === 'string' ? command : '',
		typeof patternKey === 'string' ? patternKey : ''
	);
}

/**
 * Is this `command` the rendered label rather than a command someone typed?
 *
 * The two prompts Hermes raises put very different things in the same field. Its
 * own Tier-2 dangerous-command gate puts the literal shell command there, which
 * is the most useful line the card can show. A plugin-gated call puts
 * `<tool> (plugin approval rule)` there, which says nothing the card's title does
 * not already say, and the sentence the gate wrote is in `description` instead.
 */
export function isPluginRuleLabel(command: unknown): boolean {
	return typeof command === 'string' && TOOL_IN_COMMAND.test(command.trim());
}

/**
 * Why this approval is being asked for, in the gate's own words.
 *
 * `reason` is not a field Hermes emits — it is read first only so a mock or a
 * later Hermes that adds one wins. The approval matrix's message arrives as
 * `description` (`{"action": "approve", "message": ...}` becomes
 * `request_tool_approval`'s description), and that is the sentence explaining
 * which standing rule caught the call.
 */
export function reasonOfApproval(approval: Record<string, unknown> | null | undefined): string {
	if (!approval || typeof approval !== 'object') return '';
	for (const field of ['reason', 'description'] as const) {
		const value = approval[field];
		if (typeof value === 'string' && value.trim()) return value.trim();
	}
	return '';
}
