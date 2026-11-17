<!--
@component
`/gallery/board`: the Board's seven panels on fixture numbers, live and then in
its panel states (stale, unavailable, a quiet day), so a change to a mechanism
is judged without a live Hermes. Fixtures live here, in the unit tests and in
mock-hermes only; what ships reads GET /api/board (T-0733).
-->
<script lang="ts">
	import Board from '$lib/ds/board/Board.svelte';
	import { boardPanels, type BoardApi } from '$lib/ds/board/model';
	import capabilities from '$lib/hud/data/capabilities.json';

	const now = Date.now() / 1000;
	const ok = { status: 'ok' as const, age_s: 4, why: null };
	const live: BoardApi = {
		at: now,
		sources: { hermes: ok, models: ok, gpus: ok },
		hermes: {
			schema: 1,
			at: now - 4,
			window_s: 86400,
			models: [
				{ model: 'qwen-local', turns: 41, input_tokens: 182400, output_tokens: 9100, calls: 96 },
				{ model: 'qwen-small', turns: 6, input_tokens: 8800, output_tokens: 700, calls: 6 }
			],
			hours: Array.from({ length: 24 }, (_, i) => ({
				at: now - (23 - i) * 3600,
				turns: (i % 5 === 0 ? {} : { 'qwen-local': (i * 7) % 5, ...(i % 4 === 0 ? { 'qwen-small': 1 } : {}) }) as Record<string, number>
			})),
			flows: [
				{ kind: 'tui', model: 'qwen-local', turns: 28 },
				{ kind: 'cron', model: 'qwen-local', turns: 13 },
				{ kind: 'api_server', model: 'qwen-small', turns: 6 }
			],
			kinds: [
				{ kind: 'tui', turns: 28, answered: 23, cut_off: 1, no_reply: 3, running: 1 },
				{ kind: 'cron', turns: 13, answered: 13, cut_off: 0, no_reply: 0, running: 0 },
				{ kind: 'api_server', turns: 6, answered: 5, cut_off: 0, no_reply: 1, running: 0 }
			],
			latency: { turns: 41, p50_ms: 3800, p95_ms: 17200 },
			tools: {
				per_minute: Array.from({ length: 60 }, (_, i) => (i % 7 === 0 ? 0 : (i * 3) % 5)),
				top: [
					{ name: 'web_search', calls: 31 },
					{ name: 'ha_call_service', calls: 18 },
					{ name: 'hud_say', calls: 12 }
				]
			},
			cost: { usd: null, why: 'Hermes recorded no cost for these models' },
			errors: {}
		},
		models: [
			{ model: 'qwen-local', state: 'ready' },
			{ model: 'qwen-small', state: 'starting' }
		],
		gpus: [
			{ index: 0, name: 'RTX 3090', total_mib: 24576, used_mib: 21650, users: [{ who: 'models', mib: 20890 }, { who: 'voice', mib: 760 }] },
			{ index: 1, name: 'RTX 3060', total_mib: 12288, used_mib: 3100, users: [{ who: 'whisper', mib: 3100 }] }
		],
		// The last deploy failed at its third check; the rest passed on the one before.
		verified: {
			run: { tag: 'fixture-2', at: now - 1800, ok: false, failed: 'SSE chunks=0' },
			checks: ['health', 'approval matrix', 'streamed reply', 'context window', 'tool call', 'goal loop', 'failing gate', 'panel', 'layout', 'approval', 'speech round trip', 'persona voice'].map((words, i) => ({
				id: words,
				words,
				passed_at: i < 2 ? now - 1800 : now - 90000,
				tag: i < 2 ? 'fixture-2' : 'fixture-1',
				current: i < 2
			})),
			surfaces: [
				{ id: 'console', words: 'console', ok: true, at: now - 1500, passed_at: now - 1500, why: '' },
				{ id: 'car', words: 'car screen', ok: false, at: now - 1500, passed_at: now - 90000, why: 'the car screen did not draw' },
				{ id: 'phone', words: 'phone app', ok: null, at: now - 1500, passed_at: null, why: 'no phone attached (adb)' },
				{ id: 'desktop', words: 'desktop app', ok: null, at: null, passed_at: null, why: 'not run yet' }
			]
		}
	};
	const degraded: BoardApi = {
		...live,
		sources: { hermes: { status: 'stale', age_s: 5400, why: null }, models: { status: 'unavailable', age_s: null, why: '/running: ConnectError' }, gpus: { status: 'unavailable', age_s: null, why: '/api/models/gpus: ConnectError' } },
		hermes: { ...live.hermes!, flows: [], hours: live.hermes!.hours!.map((h) => ({ ...h, turns: {} })), kinds: [], tools: null, errors: { tools: 'no such column: tool_name' } },
		models: null,
		gpus: null
	};
	const cases = [
		{ key: 'live', panels: boardPanels(live, capabilities, now) },
		{ key: 'degraded', panels: boardPanels(degraded, capabilities, now) }
	];
</script>

<svelte:head><title>Board · gallery</title></svelte:head>

<main class="gallery" data-testid="board-gallery">
	{#each cases as c (c.key)}
		<section aria-label={c.key} data-testid="board-{c.key}">
			<Board panels={c.panels} />
		</section>
	{/each}
</main>

<style>
	.gallery {
		display: grid;
		gap: var(--jv-space-7);
		max-width: var(--jv-measure-wide);
		margin: 0 auto;
		padding: var(--jv-space-6) var(--jv-space-4);
		min-block-size: 100vh;
		overflow-x: clip;
		background: var(--jv-stage-ground);
		color: var(--jv-text);
		font-family: var(--jv-font-body);
	}
</style>
