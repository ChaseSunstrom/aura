// The memory proposals queue on the wire (T-1503): the jarvis-hud plugin's command and the
// event it broadcasts. `tests/contracts/memory-proposals.json` is the table; the console's
// `memory-proposals.test.ts` and the plugin's tests both read it.

/** `jmemory @<sid> proposals | drop <id ...|all>`. There is no keep: that is asked of the assistant. */
export const MEMORY_COMMAND = 'jmemory';

/** `memory.proposals {open}`: how many wait now, and nothing a proposal says. */
export const MEMORY_PROPOSALS_EVENT = 'memory.proposals';

/** The count a `memory.proposals` carries; null for anything that is not a whole number of proposals. */
export function openOf(payload: unknown): number | null {
	if (!payload || typeof payload !== 'object' || Array.isArray(payload)) return null;
	const open = (payload as { open?: unknown }).open;
	return typeof open === 'number' && Number.isInteger(open) && open >= 0 ? open : null;
}
