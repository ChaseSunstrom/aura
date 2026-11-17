import { describe, it, expect } from 'vitest';
import { LLM_PREFIXES, hermesProxyTarget } from '$lib/server/hermes';

// `/llm/*` exposes llama-swap on the console's origin; only its OpenAI API may
// be reachable, never its UI, logs, unload or per-model upstream passthrough.
const BASE = 'http://jarvis-models:8000';
const target = (path: string, search = '') => hermesProxyTarget(path, BASE, search, LLM_PREFIXES);

describe('/llm relay target', () => {
	it('relays the OpenAI API with its query', () => {
		expect(target('v1/models')).toBe(`${BASE}/v1/models`);
		expect(target('v1/chat/completions', '?x=1')).toBe(`${BASE}/v1/chat/completions?x=1`);
	});

	it('refuses everything else llama-swap serves, including by traversal', () => {
		for (const path of ['ui', 'upstream/qwen/v1/models', 'logs', 'unload', 'running', 'v1', 'v1/../ui', 'v1/%2e%2e/logs', '//evil/v1/x', 'v1/\\..\\ui']) {
			expect(target(path), path).toBeNull();
		}
	});

	it('leaves the /hermes allow-list as it was', () => {
		expect(hermesProxyTarget('api/status', BASE)).toBe(`${BASE}/api/status`);
		expect(hermesProxyTarget('v1/models', BASE)).toBe(`${BASE}/v1/models`);
		expect(hermesProxyTarget('api/pair', BASE)).toBeNull();
	});
});
