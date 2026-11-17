import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { toolFromStrings, toolOfApproval } from './approvalEvent';

// The cases are not written here. `tests/contracts/approval-event.json` is the
// table, and `hermes/plugins/tests/test_approval_event.py` reads the same file,
// so a rendering change in Hermes that breaks the parse breaks both suites at
// once instead of leaving the console quietly mislabelling every approval card.
const CONTRACT = JSON.parse(
	readFileSync(
		fileURLToPath(new URL('../../../../tests/contracts/approval-event.json', import.meta.url)),
		'utf8'
	)
) as {
	cases: { name: string; event: Record<string, unknown>; tool: string }[];
	rendering: { plugin_rule_command: string; pattern_key_prefix: string };
	structured_fields_tried_first: string[];
};

describe('the tool an approval event is about', () => {
	it('has cases to answer', () => {
		expect(CONTRACT.cases.length).toBeGreaterThan(8);
	});

	for (const testCase of CONTRACT.cases) {
		it(testCase.name, () => {
			expect(toolOfApproval(testCase.event)).toBe(testCase.tool);
		});
	}

	it('reads the two strings directly for a caller that has no event', () => {
		expect(toolFromStrings('<terminal> (plugin approval rule)')).toBe('terminal');
		expect(toolFromStrings('', 'plugin_rule:terminal:destructive')).toBe('terminal');
	});

	it('takes the rule namespace from a pattern key, which is only sometimes the tool', () => {
		// `plugin_rule:house:service-call` is the smart-home rule for a call whose
		// tool is `ha_call_service`. The key's head is the rule's namespace, not
		// the tool, which is exactly why `command` is read first and this fallback
		// second — pinned here so nobody "fixes" the order.
		expect(toolFromStrings('', 'plugin_rule:house:service-call')).toBe('house');
		expect(
			toolFromStrings('<ha_call_service> (plugin approval rule)', 'plugin_rule:house:service-call')
		).toBe('ha_call_service');
	});

	it('answers empty for anything that is not an event object', () => {
		// The reducer hands it a payload that is `unknown` until proven otherwise;
		// a null payload must not throw inside the reducer.
		expect(toolOfApproval(null)).toBe('');
		expect(toolOfApproval(undefined)).toBe('');
		expect(toolOfApproval({ command: 42 })).toBe('');
	});
});
