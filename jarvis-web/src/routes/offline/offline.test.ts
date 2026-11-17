import { describe, expect, it } from 'vitest';
import { render } from 'svelte/server';

import Page from './+page.svelte';

// The page the phone shows when it cannot reach Jarvis. Away from home the usual
// cause is the WireGuard tunnel, so the page says so (T-0474).
describe('offline page', () => {
	it('tells someone away from home to check the WireGuard tunnel in Settings', () => {
		const { body } = render(Page);
		expect(body).toContain('id="away"');
		expect(body).toMatch(/Away from home\?[^<]*WireGuard[^<]*Phone settings/);
		expect(body).toContain('href="jarvis-offline:settings"');
	});
});
