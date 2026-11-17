<!--
@component
"Devices" in the top bar: the paired phones and desktops, each a link to its
row on the Devices screen, and the way to pair another. Read fresh each time
it opens, so a phone that just joined is there.

Does not cover: anything a device can do (the Devices screen), or the house's
own lights and sensors (ask JARVIS, or the core's projections).
-->
<script lang="ts">
	import Sheet from '$lib/ds/ui/Sheet.svelte';
	import State from '$lib/ds/ui/State.svelte';
	import type { CompanionDevice, JarvisClient } from '$lib/jarvisClient';
	import type { Status } from '$lib/ui/status';

	interface Props {
		open: boolean;
		/** The house's client; null while the house is not connected. */
		client: JarvisClient | null;
	}
	let { open = $bindable(), client }: Props = $props();

	let devices = $state<CompanionDevice[]>([]);
	let status = $state<Status>('loading');
	let error = $state('');
	// Not state: a later open's answer wins over a slow earlier one.
	let asked = 0;

	async function load(c: JarvisClient | null): Promise<void> {
		const mine = ++asked;
		if (!c) {
			status = 'offline';
			return;
		}
		status = 'loading';
		try {
			const list = await c.listCompanions();
			if (mine !== asked) return;
			devices = list;
			status = list.length ? 'ready' : 'empty';
		} catch (e) {
			if (mine !== asked) return;
			error = e instanceof Error ? e.message : String(e);
			status = 'error';
		}
	}

	$effect(() => {
		if (open) void load(client);
	});

	const close = () => (open = false);
</script>

<Sheet bind:open label="Your devices" testid="devices-sheet">
	<State {status} {error} empty="No devices paired yet." onretry={() => load(client)} testid="devices-state">
		<nav aria-label="Your devices">
			<ul>
				{#each devices as d (d.device_id)}
					<li>
						<a href="/devices#{encodeURIComponent(d.device_id)}" onclick={close}>
							<span class="name">{d.name}</span>
							<span class="meta">{d.platform ?? 'device'} · {d.connected ? 'connected' : 'away'}</span>
							<span class="dot" class:on={d.connected} aria-hidden="true"></span>
						</a>
					</li>
				{/each}
			</ul>
		</nav>
	</State>
	<a class="pair" href="/devices" onclick={close}>Pair a device</a>
</Sheet>

<style>
	ul {
		display: grid;
		gap: var(--jv-space-1);
		margin: 0;
		padding: 0;
		list-style: none;
	}
	a {
		display: grid;
		grid-template-columns: 1fr auto;
		align-items: center;
		column-gap: var(--jv-space-3);
		min-block-size: var(--jv-touch);
		padding: var(--jv-space-1) var(--jv-space-3);
		border-radius: var(--jv-radius-sm);
		color: var(--jv-text);
		text-decoration: none;
	}
	a:hover {
		background: var(--jv-wash);
	}
	a:focus-visible {
		outline: var(--jv-focus-outline);
		outline-offset: var(--jv-focus-offset);
	}
	.meta {
		grid-column: 1;
		font-size: var(--jv-fs-xs);
		color: var(--jv-text-dim);
	}
	.dot {
		grid-column: 2;
		grid-row: 1 / span 2;
		inline-size: var(--jv-space-2);
		block-size: var(--jv-space-2);
		border-radius: var(--jv-radius-pill);
		background: var(--jv-text-faint);
	}
	.dot.on {
		background: var(--jv-ok);
	}
	.pair {
		display: flex;
		margin-block-start: var(--jv-space-3);
		border: 1px solid var(--jv-line-hair);
		color: var(--jv-accent);
		font-family: var(--jv-font-chrome);
		letter-spacing: var(--jv-track-control);
		text-transform: uppercase;
	}
</style>
