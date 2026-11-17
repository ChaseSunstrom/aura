<!--
@component
Integrations (docs/design/v2/Integrations.dc.html): connect anything. A line
to search or describe a service, the sign-in accounts and their state (T-1343:
the house's own `oauth.status`, never a token; Connect, Test and Disconnect
are sentences to JARVIS like everything else here), the house's device keys
(T-1386: name, last used, paired or an older token to pair again, an approver
switch and a hold to revoke; never the key's text), what is connected, what is
popular, what JARVIS may do in each area, and the four ways to reach anything else.

Every other change goes through JARVIS: connecting, installing and setting a level
are tools behind the approval matrix (`integration_install`, `connect_start`,
`mcp_add`, `api_add`, `capability_set`), so this screen asks and the approval
card is where the person says yes. The House keys section is the one exception: its
switch and its hold call the house's own token commands, because a key that
JARVIS itself runs on is not something to revoke by asking JARVIS. It is headed
"House keys" (T-1411) because the Devices screen has a key list too ("Own keys",
the adapter's per-device keys): the two read alike, and revoking here leaves a
paired phone's adapter key alive, so each lede names the other list.

Does not cover: an integration's own settings (ask the worn persona), or OAuth's own
pages, which `connect_start` opens on the device that asked. House keys does not list
the key set in the house's environment (the house never lists it). The house marks
the key this console signed in with (T-1407) and that row alone gets no revoke; a
house older than that cannot say, and then a key that is not paired and is connected
right now gets none either.
-->
<script lang="ts">
	import { wornName } from '$lib/ds/theme.svelte';
	import Button from '$lib/ds/ui/Button.svelte';
	import Field from '$lib/ds/ui/Field.svelte';
	import State from '$lib/ds/ui/State.svelte';
	import Switch from '$lib/ds/ui/Switch.svelte';
	import { integrationsView } from '$lib/hud/data';
	import { levelRequest, type Capability, type Level } from '$lib/hud/data/capabilities';
	import type { Feeds } from '$lib/hud/feeds.svelte';
	import HoldButton from '../HoldButton.svelte';
	import Levels from './Levels.svelte';
	import Screen from './Screen.svelte';
	import Section from './Section.svelte';

	interface Props {
		feeds: Feeds;
		/** Hands JARVIS a request; the host submits it and says so. */
		onask: (text: string) => void;
	}
	let { feeds, onask }: Props = $props();

	const rows = $derived(integrationsView(feeds.integrations.data));
	const accounts = $derived(feeds.integrations.data.accounts);
	const connected = $derived(rows.filter((r) => r.installed && r.kind !== 'catalogue'));
	const popular = $derived(rows.filter((r) => r.kind === 'catalogue' && !r.installed).slice(0, 8));

	const ANYTHING = [
		{ title: 'Any API', sub: `Paste a link to its docs or an OpenAPI file. ${wornName()} writes the connector and tests it.`, ask: 'I want to connect an API. Ask me for its docs or OpenAPI file, then use api_add.' },
		{ title: 'Any MCP server', sub: `Add a server URL or a local command. Its tools show up for ${wornName()} right away.`, ask: 'I want to add an MCP server. Ask me for its URL or command, then use mcp_add.' },
		{ title: 'Any website', sub: `No API? ${wornName()} uses the site in a browser, signed in as you.`, ask: 'I want you to use a website for me. Ask me which one.' },
		{ title: 'Any app on your devices', sub: `${wornName()} can see the screen and click, type and scroll on your PC or phone.`, ask: 'I want you to use an app on one of my devices. Ask me which one.' }
	] as const;

	let query = $state('');
	function search(e: SubmitEvent): void {
		e.preventDefault();
		const q = query.trim();
		if (!q) return;
		query = '';
		onask(`I want to connect ${q}. Find it with integration_search, or connect_start if it signs in.`);
	}
	const level = (c: Capability, l: Level) => onask(levelRequest(c, l));

	// One token change at a time: every switch and hold is off while one is out, and a hold
	// already under way when another change started (its timer still fires) is dropped here, so
	// a second change cannot race the first one's re-read of the list. The feed re-reads either
	// way; this only keeps what the house said when it refused.
	let changing = $state(false);
	let refused = $state('');
	async function change(send: () => Promise<void>): Promise<void> {
		if (changing) return;
		changing = true;
		refused = '';
		try {
			await send();
		} catch (err) {
			refused = `The house did not make that change: ${(err instanceof Error ? err.message : String(err)).slice(0, 200)}`;
		} finally {
			changing = false;
		}
	}
</script>

<Screen title="Integrations" lede="Connect anything." testid="screen-integrations">
	<form class="search" role="search" onsubmit={search}>
		<Field label="Search or describe a service" placeholder="Search or describe a service" autocomplete="off" bind:value={query} testid="integrations-search" />
		<Button tone="primary" type="submit" disabled={!query.trim()}>Ask</Button>
	</form>

	<Section title="Accounts" count={feeds.integrations.status === 'ready' && accounts ? accounts.length : undefined} testid="integrations-accounts">
		<State status={feeds.integrations.status === 'ready' && !accounts ? 'empty' : feeds.integrations.status} error={feeds.integrations.error} empty="The house did not answer for its accounts." onretry={() => feeds.reload(feeds.integrations)}>
			<ul class="rows">
				{#each accounts ?? [] as a (a.id)}
					<li data-state={a.state}>
						<span class="name">{a.name}</span>
						<span class="word">{a.word}</span>
						<span class="meta detail">{a.detail}</span>
						{#each a.notes as note (note)}<span class="meta detail">{note}</span>{/each}
						{#if a.asks.connect}<Button tone="ghost" onclick={() => onask(a.asks.connect ?? '')}>Connect</Button>{/if}
						{#if a.asks.test}<Button tone="ghost" onclick={() => onask(a.asks.test ?? '')}>Test</Button>{/if}
						{#if a.asks.disconnect}<Button tone="ghost" onclick={() => onask(a.asks.disconnect ?? '')}>Disconnect</Button>{/if}
						{#each a.asks.examples as ask (ask)}
							<Button tone="ghost" onclick={() => onask(ask)}>{ask}</Button>
						{/each}
					</li>
				{/each}
			</ul>
		</State>
	</Section>

	<Section
		title="House keys"
		count={feeds.tokens.status === 'ready' ? feeds.tokens.data.length : undefined}
		lede="Every key the house itself has handed out, to consoles, displays and paired devices. Revoking one signs that device out of the house at once and cannot be undone. To lock a paired phone out of {wornName()} as well, revoke it under Devices, Own keys."
		testid="integrations-devices"
	>
		<State status={feeds.tokens.status} error={feeds.tokens.error} empty="The house holds no device keys." onretry={() => feeds.reload(feeds.tokens)} testid="integrations-devices-state">
			<ul class="rows">
				{#each feeds.tokens.data as t (t.id)}
					<li data-state={t.state} data-testid="integrations-device">
						<span class="name">{t.name}</span>
						<span class="word">{t.word}</span>
						<span class="meta detail">{t.detail}</span>
						{#if t.note}<span class="meta detail">{t.note}</span>{/if}
						{#if t.approver !== null}
							<Switch label="{t.name} may approve" on={t.approver} disabled={changing} testid="integrations-device-approver" onchange={(on) => change(() => feeds.setTokenApprover(t.id, on))} />
						{/if}
						{#if t.revocable}
							<HoldButton label="Revoke {t.name}" tone="danger" disabled={changing} testid="integrations-device-revoke" onconfirm={() => change(() => feeds.revokeToken(t.id))} />
						{/if}
					</li>
				{/each}
			</ul>
		</State>
		<!-- Outside the state: a refusal must still read when the list behind it went empty or offline. -->
		{#if refused}<p class="refused" role="alert" data-testid="integrations-devices-refused">{refused}</p>{/if}
	</Section>

	<Section title="Connected" count={feeds.integrations.status === 'ready' ? connected.length : undefined} testid="integrations-connected">
		<State status={feeds.integrations.status === 'ready' && !connected.length ? 'empty' : feeds.integrations.status} error={feeds.integrations.error} empty="Nothing connected yet." onretry={() => feeds.reload(feeds.integrations)}>
			<ul class="rows">
				{#each connected as r (r.id)}
					<li>
						<span class="name">{r.name}</span>
						<span class="meta">{r.kind === 'mcp' ? 'MCP server' : r.kind}{r.detail ? ` · ${r.detail}` : ''}</span>
					</li>
				{/each}
			</ul>
		</State>
	</Section>

	{#if popular.length}
		<Section title="Popular">
			<ul class="rows">
				{#each popular as r (r.id)}
					<li>
						<span class="name">{r.name}</span>
						{#if r.detail}<span class="meta">{r.detail}</span>{/if}
						<Button tone="ghost" onclick={() => onask(`Install ${r.name} with integration_install.`)}>Connect</Button>
					</li>
				{/each}
			</ul>
		</Section>
	{/if}

	<Section title="What {wornName()} may do" testid="integrations-levels">
		<Levels status={feeds.capabilities.status} error={feeds.capabilities.error} capabilities={feeds.capabilities.data} onlevel={level} onretry={() => feeds.reload(feeds.capabilities)} />
	</Section>

	<Section title="Anything else" testid="integrations-anything">
		<ul class="anything">
			{#each ANYTHING as a (a.title)}
				<li>
					<button type="button" onclick={() => onask(a.ask)}>
						<span class="name">{a.title}</span>
						<span class="meta">{a.sub}</span>
					</button>
				</li>
			{/each}
		</ul>
	</Section>
</Screen>

<style>
	.search {
		display: flex;
		gap: var(--jv-space-2);
		margin-block-end: var(--jv-space-4);
	}
	/* The state is in words; the colour only repeats it: green ok, amber waiting on a person. */
	.word {
		font: var(--jv-fs-sm) var(--jv-font-chrome);
	}
	li[data-state='connected'] .word,
	li[data-state='paired'] .word {
		color: var(--jv-ok);
	}
	li[data-state='needs-signin'] .word,
	li[data-state='older'] .word {
		color: var(--jv-warn);
	}
	.refused {
		margin: 0;
		color: var(--jv-warn);
		font: var(--jv-fs-sm) var(--jv-font-body);
	}
	li[data-state='not-set-up'] .word {
		color: var(--jv-text-dim);
	}
	/* A detail is a sentence: it takes the row's width so the buttons wrap below it. */
	.detail {
		flex: 1 1 100%;
	}
	.anything button:focus-visible {
		outline: var(--jv-focus-outline);
		outline-offset: var(--jv-focus-offset);
	}
	.anything {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(var(--jv-measure-card), 1fr));
		gap: var(--jv-space-3);
		margin: 0;
		padding: 0;
		list-style: none;
	}
	.anything button {
		display: grid;
		gap: var(--jv-space-1);
		inline-size: 100%;
		min-block-size: var(--jv-touch);
		padding: var(--jv-space-3) var(--jv-space-4);
		border: 1px solid var(--jv-line-hair);
		border-inline-start: 1px solid var(--jv-accent);
		border-radius: var(--jv-radius-sm);
		background: transparent;
		text-align: start;
		cursor: pointer;
	}
	.anything button:hover {
		background: var(--jv-wash);
	}
</style>
