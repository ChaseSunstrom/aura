<!--
@component
Devices → Offline maps (T-0979): the map regions the house holds for the car (a
state, a country), each with its state and size; a search of the regions that
can be downloaded; download one, or hold to remove one. The house fetches and
builds a region (minutes to an hour); a phone copies it on Wi-Fi afterwards, and
the car's map and turn-by-turn then work there with no connection.

Read from and written to jarvis-core through the console's relay
(`/api/maps/regions`, `/api/maps/catalogue`). The list reads itself again while
a region is on its way in or out. A refusal is said under the list.

Does not cover: which regions this phone has copied (the phone knows; the house
does not), or the home region's own bounds (`scripts/map-region.sh`).
-->
<script lang="ts">
	import { onDestroy } from 'svelte';

	import { HOME_REGION, regionLine, unsettled, type Offered, type Region } from '$lib/hud/regions';
	import type { Status } from '$lib/ui/status';
	import HoldButton from '../HoldButton.svelte';
	import Button from '../ui/Button.svelte';
	import Field from '../ui/Field.svelte';
	import State from '../ui/State.svelte';
	import { OFFERED_SHOWN, moreResults } from '$lib/hud/regions';
	import { regionsPoll } from './map-regions-poll';

	let regions = $state<Region[] | null>(null);
	let failed = $state('');
	let away = $state(false);
	let query = $state('');
	let offered = $state<Offered[] | null>(null);
	let busy = $state('');
	let said = $state('');

	const look: Status = $derived(away ? 'offline' : failed ? 'error' : regions === null ? 'loading' : regions.length ? 'ready' : 'empty');
	const held = $derived(new Set((regions ?? []).map((r) => r.id)));

	/** The house's own words for a refusal (`detail.message`), else its status. */
	async function why(res: Response): Promise<string> {
		const body = (await res.json().catch(() => ({}))) as { detail?: { message?: string } | string; message?: string };
		const detail = typeof body.detail === 'string' ? body.detail : body.detail?.message;
		return detail || body.message || `the house answered ${res.status}`;
	}

	async function load(): Promise<Region[]> {
		const res = await fetch('/api/maps/regions');
		if (!res.ok) throw new Error(await why(res));
		return ((await res.json()) as { regions?: Region[] }).regions ?? [];
	}
	const poll = regionsPoll(load, {
		ok(list) {
			regions = list;
			failed = '';
			away = false;
		},
		fail(e) {
			// fetch itself throwing is the console (or the link to it) being away, not a refusal.
			away = e instanceof TypeError;
			failed = e instanceof Error ? e.message : String(e);
		}
	});
	const read = poll.read;
	// Ends the poll for good, including a read still in flight (T-1268).
	onDestroy(() => poll.stop());

	$effect(() => {
		void read();
		const again = () => !document.hidden && regions && unsettled(regions) && void read();
		document.addEventListener('visibilitychange', again);
		return () => document.removeEventListener('visibilitychange', again);
	});

	async function act(what: string, doIt: () => Promise<Response>): Promise<boolean> {
		if (busy) return false;
		busy = what;
		try {
			const res = await doIt();
			if (!res.ok) throw new Error(await why(res));
			said = '';
			await read();
			return true;
		} catch (e) {
			said = `Couldn’t ${what}: ${e instanceof Error ? e.message : String(e)}`;
			return false;
		} finally {
			busy = '';
		}
	}

	async function find(e: SubmitEvent): Promise<void> {
		e.preventDefault();
		const q = query.trim();
		if (!q || busy) return;
		busy = 'search';
		try {
			const res = await fetch(`/api/maps/catalogue?q=${encodeURIComponent(q)}`);
			if (!res.ok) throw new Error(await why(res));
			const found = (await res.json()) as { ready?: boolean; results?: Offered[] };
			offered = found.results ?? [];
			said = found.ready ? (offered.length ? '' : `No region is called “${q}”.`) : 'The list of regions has not reached the house yet. Try again in a minute.';
		} catch (err) {
			said = `Couldn’t search: ${err instanceof Error ? err.message : String(err)}`;
		} finally {
			busy = '';
		}
	}

	const download = (r: Offered) =>
		void act(`download ${r.name}`, () =>
			fetch('/api/maps/regions', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ id: r.id }) })
		).then((ok) => ok && ((offered = null), (query = '')));
	const remove = (r: Region) => void act(`remove ${r.name}`, () => fetch(`/api/maps/regions/${encodeURIComponent(r.id)}`, { method: 'DELETE' }));
</script>

<State status={look} error={failed} empty="No offline maps yet. Find a state or a country below and download it." onretry={() => void read()} testid="regions-state">
	<ul class="rows" data-testid="regions">
		{#each regions ?? [] as r (r.id)}
			<li class="region" data-testid="region-{r.id}" data-state={r.state}>
				<div>
					<span class="name">{r.name}</span>
					<span class="meta" class:bad={r.state === 'failed'}>{regionLine(r)}</span>
				</div>
				{#if r.id !== HOME_REGION && r.state !== 'removing'}
					<HoldButton label="Remove {r.name}" tone="danger" disabled={Boolean(busy)} testid="region-remove-{r.id}" onconfirm={() => remove(r)} />
				{/if}
			</li>
		{/each}
	</ul>
</State>
{#if look !== 'error' && look !== 'loading' && look !== 'offline'}
	<form class="line find" onsubmit={find}>
		<Field label="A state or a country to download" placeholder="Utah, Germany…" bind:value={query} maxlength={80} testid="regions-query" />
		<Button type="submit" disabled={Boolean(busy) || !query.trim()} testid="regions-find">Find</Button>
	</form>
	{#if offered?.length}
		<ul class="rows" data-testid="regions-offered">
			{#each offered.slice(0, OFFERED_SHOWN) as o (o.id)}
				<li class="region" data-testid="offered-{o.id}">
					<div>
						<span class="name">{o.name}</span>
						{#if o.parent}<span class="meta">{o.parent}</span>{/if}
					</div>
					{#if held.has(o.id)}
						<span class="meta">On the house</span>
					{:else}
						<Button disabled={Boolean(busy)} testid="offered-download-{o.id}" onclick={() => download(o)}>Download</Button>
					{/if}
				</li>
			{/each}
		</ul>
		{#if moreResults(offered.length)}<p class="meta" data-testid="regions-more">{moreResults(offered.length)}</p>{/if}
	{/if}
{/if}
{#if said}
	<p class="said" role="status" data-testid="regions-said">{said}</p>
{/if}

<style>
	.region {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		justify-content: space-between;
		gap: var(--jv-space-2);
		padding-block: var(--jv-space-2);
	}
	.region > div {
		display: grid;
		gap: var(--jv-space-1);
	}
	.bad {
		color: var(--jv-danger-text);
	}
	.line {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: var(--jv-space-2);
	}
	.find {
		margin-block-start: var(--jv-space-2);
	}
	.said {
		margin: var(--jv-space-2) 0 0;
		color: var(--jv-text-dim);
		font-size: var(--jv-fs-sm);
	}
</style>
