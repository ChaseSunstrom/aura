<!--
@component
The Board screen (/board, T-0733): what JARVIS has been doing, from its own
records — the adapter's GET /api/board (Hermes's counts, the models' live
load) and the bench's capability rows — asked again every 10 s while the
screen is open and the page is visible.

The four states: loading until the first answer; error with the adapter's
status and a Retry; offline (the adapter out of reach) over the last board it
showed, or alone before one; and each panel's own empty line and its own
stale or unavailable state.

Does not cover: anything older than Hermes's window (24 hours) or what Hermes
did not record (no cost for a local model).
-->
<script lang="ts">
	import { wornName } from '$lib/ds/theme.svelte';
	import Board from '../board/Board.svelte';
	import { boardPanels, type BoardApi } from '../board/model';
	import State from '../ui/State.svelte';
	import Screen from './Screen.svelte';
	import capabilities from '$lib/hud/data/capabilities.json';
	import type { Status } from '$lib/ui/status';

	const EVERY_MS = 10_000;
	const URL = '/hermes/api/board';

	let api = $state.raw<BoardApi | null>(null);
	let status = $state<Status>('loading');
	let error = $state('');
	let now = $state(Date.now() / 1000);

	async function load(): Promise<void> {
		let res: Response;
		try {
			res = await fetch(URL, { headers: { accept: 'application/json' } });
		} catch {
			status = 'offline';
			return;
		}
		// 502 and 503 are the relay saying the adapter is not there: offline, not an error.
		if (res.status === 502 || res.status === 503) {
			status = 'offline';
			return;
		}
		const body = (await res.json().catch(() => null)) as BoardApi | null;
		if (!res.ok || !body?.sources) {
			error = `the adapter answered ${res.status}`;
			status = 'error';
			return;
		}
		api = body;
		now = Date.now() / 1000;
		status = 'ready';
	}

	$effect(() => {
		void load();
		const timer = setInterval(() => {
			if (!document.hidden) void load();
		}, EVERY_MS);
		const back = () => !document.hidden && void load();
		document.addEventListener('visibilitychange', back);
		return () => {
			clearInterval(timer);
			document.removeEventListener('visibilitychange', back);
		};
	});

	const panels = $derived(api ? boardPanels(api, capabilities, now) : []);
</script>

<Screen title="Board" lede="What {wornName()} has been doing today, from its own records." testid="screen-board" wide>
	<!-- One failed poll keeps the last good board under its line, rather than blanking it every 10 s. -->
	{#if api && (status === 'offline' || status === 'error')}
		<State {status} {error} onretry={() => void load()} testid="board-state" />
	{/if}
	{#if api && status !== 'loading'}
		<Board {panels} />
	{:else}
		<State {status} {error} onretry={() => void load()} testid="board-state" />
	{/if}
</Screen>
