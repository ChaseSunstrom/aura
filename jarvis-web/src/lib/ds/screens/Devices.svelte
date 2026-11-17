<!--
@component
Devices: the paired phones, desktops and watches, each with whether it is
connected and what it lets JARVIS do, and the way to pair another (a code and
its QR, behind the console password).

Each device paired since T-0569 holds its own key, listed under "Own keys" with a
Revoke that asks first and locks out that device alone: its JARVIS key, its house
token and its console sessions.

Does not cover: what a device does on its own screen (its app), a device paired
before its own keys (it holds the shared key until it is paired again), or the
house's own lights and sensors.
-->
<script lang="ts">
	import { wornName } from '$lib/ds/theme.svelte';
	import { onMount, tick, untrack } from 'svelte';

	import Button from '$lib/ds/ui/Button.svelte';
	import Field from '$lib/ds/ui/Field.svelte';
	import State from '$lib/ds/ui/State.svelte';
	import { pairingView } from '$lib/hud/data';
	import { apkQr, loadApps, type AppsLoad } from '$lib/hud/data/apps';
	import { keyMeta, loadDeviceKeys, revokeDeviceKey, type KeysLoad } from '$lib/hud/data/deviceKeys';
	import { loadPower, type PowerLoad } from '$lib/hud/data/power';
	import type { Feeds } from '$lib/hud/feeds.svelte';
	import { PairingFlow } from '$lib/hud/pairing.svelte';
	import MapRegions from './MapRegions.svelte';
	import Screen from './Screen.svelte';
	import Section from './Section.svelte';

	interface Props {
		feeds: Feeds;
	}
	let { feeds }: Props = $props();

	const devices = $derived(feeds.companions.data);
	const pairing = new PairingFlow();
	const shown = $derived(pairingView({ url: pairing.url, code: pairing.code, expiresAt: pairing.expiresAt, now: pairing.now }));
	let typed = $state('');
	const locked = $derived(pairing.lock === 'locked' || pairing.lock === 'choose');
	const secretNeeded = $derived(pairing.lock === 'open' && !pairing.secretHeld);
	function submit(e: SubmitEvent): void {
		e.preventDefault();
		const value = typed;
		typed = '';
		if (locked) void pairing.unlock(value);
		else if (secretNeeded) void pairing.adoptSecret(value);
	}
	onMount(() => {
		void pairing.start();
		void refreshApps();
		void refreshKeys();
		return () => pairing.dispose();
	});

	// The apps (ADR-0050), from the project's GitLab releases: how a device gets
	// its first copy; after that it updates itself from the same releases.
	let apps = $state<AppsLoad>({ status: 'loading' });
	async function refreshApps(): Promise<void> {
		apps = { status: 'loading' };
		apps = await loadApps();
	}
	const day = (iso: string): string => (iso ? iso.slice(0, 10) : '');

	// The phone's daily power ledger (T-0582), asked of the connected phone itself: today beside yesterday.
	let power = $state<PowerLoad>({ status: 'loading' });
	let powerAsked = 0;
	async function refreshPower(): Promise<void> {
		const mine = ++powerAsked;
		power = { status: 'loading' };
		const got = await loadPower(feeds.toolCaller(), devices);
		// A newer ask (the phone came or went meanwhile) owns the screen.
		if (mine === powerAsked) power = got;
	}
	const phoneId = $derived(devices.find((d) => d.connected && d.platform === 'android')?.device_id ?? '');
	$effect(() => {
		const listed = feeds.companions.status;
		void phoneId;
		if (listed === 'loading') power = { status: 'loading' };
		else if (listed === 'offline') power = { status: 'offline' };
		else if (listed === 'error') power = { status: 'error', detail: 'the paired devices did not load' };
		else untrack(() => void refreshPower());
	});

	let keys = $state<KeysLoad>({ status: 'loading' });
	let keysSaid = $state('');
	let confirming = $state<string | null>(null);
	async function refreshKeys(): Promise<void> {
		keys = await loadDeviceKeys();
	}
	const keysShown = $derived(keys.status === 'ready' && !keys.keys.length ? 'empty' : keys.status);
	/** The confirm swaps the button out from under the focus: hand it on, to Keep (the safe one) and back. */
	async function ask(id: string | null, row: string): Promise<void> {
		confirming = id;
		await tick();
		const target = id ? 'devices-key-revoke-no' : 'devices-key-revoke';
		document.querySelector<HTMLElement>(`[data-key="${CSS.escape(row)}"] [data-testid="${target}"]`)?.focus();
	}
	async function revoke(id: string): Promise<void> {
		confirming = null;
		try {
			keysSaid = (await revokeDeviceKey(id)) ?? '';
		} catch (e) {
			keysSaid = e instanceof Error ? e.message : String(e);
		}
		await refreshKeys();
	}
</script>

<Screen title="Devices" lede="Your phone, desktop and watch." testid="screen-devices">
	<Section title="Paired" count={feeds.companions.status === 'ready' ? devices.length : undefined} testid="devices-paired">
		<State status={feeds.companions.status} error={feeds.companions.error} empty="No devices paired yet. Pair one below." onretry={() => feeds.reload(feeds.companions)} testid="devices-screen-state">
			<ul class="rows">
				{#each devices as d (d.device_id)}
					<li id={encodeURIComponent(d.device_id)} data-device={d.device_id}>
						<span class="dot" class:on={d.connected} aria-hidden="true"></span>
						<span class="name">{d.name}</span>
						<span class="meta">
							{d.platform ?? 'device'} · {d.connected ? 'connected' : 'away'}{d.area ? ` · ${d.area}` : ''}{d.action_count ? ` · ${d.action_count} things it can do` : ''}
						</span>
					</li>
				{/each}
			</ul>
		</State>
	</Section>

	<Section
		title="Phone power"
		lede="What the phone's features cost, today beside yesterday. A feature's drop is its share of the battery lost unplugged while it was on, not a measurement of the feature alone."
		testid="devices-power"
	>
		<State
			status={power.status}
			error={power.status === 'error' ? power.detail : ''}
			empty="No phone is connected, so there is no power ledger to show."
			onretry={() => void refreshPower()}
			testid="devices-power-state"
		>
			{#if power.status === 'ready'}
				<div class="days" data-testid="devices-power-days">
					{#each power.columns as c (c.label)}
						<section class="day" data-testid="devices-power-day" data-day={c.label.toLowerCase()}>
							<h3 class="name">{c.label} <span class="meta">{c.date}</span></h3>
							{#if c.status === 'none'}
								<p class="meta" data-testid="devices-power-none">{c.said}</p>
							{:else}
								<p class="meta">Readings cover {c.covered}.</p>
								<ul class="rows">
									{#each c.rows as r (r.key)}
										<li data-feature={r.key}>
											<span class="name">{r.label}</span>
											<span class="meta">{[r.on, r.drop, r.peak].filter(Boolean).join(' · ')}</span>
										</li>
									{/each}
									<li data-feature="total">
										<span class="name">All together</span>
										<span class="meta">{[`${c.total.cpu} of CPU`, `${c.total.drop} unplugged`, c.total.peak && `peak ${c.total.peak}`].filter(Boolean).join(' · ')}</span>
									</li>
								</ul>
							{/if}
						</section>
					{/each}
				</div>
			{/if}
		</State>
	</Section>

	<Section
		title="Own keys"
		count={keys.status === 'ready' ? keys.keys.length : undefined}
		lede="Each phone paired here gets a key of its own. Revoke one to lock that device out of {wornName()} and the house; the rest keep working. A phone paired before still holds the shared key: pair it again to give it its own. Keys the house holds for anything else, such as a console or a display, are under Integrations, House keys."
		testid="devices-keys"
	>
		<State
			status={keysShown}
			error={keys.status === 'error' ? keys.detail : ''}
			empty="No device has its own key yet. Pair a phone below and it gets one."
			onretry={() => void refreshKeys()}
			testid="devices-keys-state"
		>
			<ul class="rows">
				{#each keys.status === 'ready' ? keys.keys : [] as k (k.id)}
					<li data-testid="devices-key" data-key={k.id}>
						<div class="key">
							<span class="name">{k.name}</span>
							<span class="meta">{keyMeta(k)}</span>
						</div>
						{#if confirming === k.id}
							<span class="confirm">
								<Button tone="danger" testid="devices-key-revoke-yes" onclick={() => void revoke(k.id)}>Revoke</Button>
								<Button tone="quiet" testid="devices-key-revoke-no" onclick={() => void ask(null, k.id)}>Keep</Button>
							</span>
						{:else}
							<Button tone="ghost" aria-label="Revoke {k.name}" testid="devices-key-revoke" onclick={() => void ask(k.id, k.id)}>Revoke</Button>
						{/if}
					</li>
				{/each}
			</ul>
		</State>
		{#if keysSaid}<p class="said" role="alert" data-testid="devices-keys-said">{keysSaid}</p>{/if}
	</Section>

	<Section title="Pair a device" lede="Open the {wornName()} app on the phone or desktop and scan the code, or type it." testid="devices-pair">
		{#if pairing.lock === 'unknown'}
			<State status="loading" />
		{:else if locked || secretNeeded}
			<form class="unlock" onsubmit={submit}>
				<Field
					type="password"
					autocomplete={pairing.lock === 'choose' ? 'new-password' : 'current-password'}
					label={secretNeeded ? 'The pairing secret' : pairing.lock === 'choose' ? 'Choose a console password' : 'Console password'}
					placeholder={secretNeeded ? 'The pairing secret' : pairing.lock === 'choose' ? `Choose a password (${pairing.minChars}+ characters)` : 'Console password'}
					bind:value={typed}
					disabled={pairing.busy}
				/>
				<Button tone="primary" type="submit" disabled={pairing.busy || !typed}>{secretNeeded ? 'Use it' : 'Unlock pairing'}</Button>
			</form>
			{#if pairing.passwordHint || pairing.error}<p class="said" role="alert">{pairing.passwordHint || pairing.error}</p>{/if}
		{:else if shown.code}
			<div class="code" data-testid="pairing-code">
				<img src={shown.qr} alt="Pairing code as a QR" />
				<div>
					<p class="digits">{shown.code}</p>
					<p class="meta">{shown.expires}</p>
					<Button tone="ghost" disabled={pairing.busy} onclick={() => void pairing.issue()}>New code</Button>
				</div>
			</div>
		{:else}
			<State status="error" error={pairing.error || 'No pairing code yet.'} onretry={() => void pairing.issue()} />
		{/if}
	</Section>

	<Section title="Offline maps" lede="Map regions the house keeps for the car. A phone copies them on Wi-Fi, and the map and turn-by-turn then work with no connection." testid="devices-maps">
		<MapRegions />
	</Section>

	<Section title="Get the apps" lede="The newest phone and desktop builds. Once installed, each keeps itself up to date." testid="settings-apps">
		<div data-status={apps.status} data-testid="apps">
			{#if apps.status === 'offline'}
				<!-- GitLab is on the home network: off it, that is where the downloads are, not an error to fix. -->
				<div class="line" role="status">
					<p class="meta">GitLab is not reachable from here. The downloads are on the home network.</p>
					<Button tone="ghost" testid="apps-retry" onclick={() => void refreshApps()}>Retry</Button>
				</div>
			{:else if apps.status !== 'ready'}
				<State status={apps.status} error={apps.status === 'error' ? apps.detail : ''} onretry={() => void refreshApps()} />
			{:else if !apps.apps.android && !apps.apps.desktop}
				<State status="empty" empty="No builds have been published yet. The pipeline publishes one on its next green run." />
			{:else}
				<ul class="rows">
					{#if apps.apps.android}
						{@const apk = apps.apps.android.files[0]}
						<li class="app" data-testid="apps-android">
							<div>
								<span class="name">Phone</span>
								<span class="meta">{apps.apps.android.version} · {day(apps.apps.android.released)}{#if apps.apps.android.from} · from {apps.apps.android.from}{/if}</span>
								<a class="get" href={apk.url} download data-testid="apps-android-download">Download the APK</a>
								<span class="meta">Scan with the phone’s camera to install.</span>
							</div>
							<img class="qr" src={apkQr(apk.url)} alt="QR code of the APK’s download link" data-testid="apps-android-qr" />
						</li>
					{/if}
					{#if apps.apps.desktop}
						<li class="app" data-testid="apps-desktop">
							<div>
								<span class="name">Desktop</span>
								<span class="meta">{apps.apps.desktop.version} · {day(apps.apps.desktop.released)}</span>
								<span class="gets">
									{#each apps.apps.desktop.files as f (f.url)}
										<a class="get" href={f.url} download data-testid="apps-desktop-download">{f.label}</a>
									{/each}
								</span>
							</div>
						</li>
					{/if}
				</ul>
			{/if}
		</div>
	</Section>
</Screen>

<style>
	.dot {
		inline-size: var(--jv-space-2);
		block-size: var(--jv-space-2);
		border-radius: var(--jv-radius-pill);
		background: var(--jv-text-faint);
	}
	.dot.on {
		background: var(--jv-ok);
	}
	.key {
		display: grid;
		flex: 1 1 0;
		gap: var(--jv-space-1);
		min-inline-size: 0;
	}
	.days {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(var(--jv-measure-qr), 1fr));
		gap: var(--jv-space-5);
	}
	.day {
		display: grid;
		align-content: start;
		gap: var(--jv-space-2);
		min-inline-size: 0;
	}
	.day h3,
	.day p {
		margin: 0;
	}
	.confirm {
		display: flex;
		gap: var(--jv-space-1);
	}
	.unlock {
		display: flex;
		flex-wrap: wrap;
		gap: var(--jv-space-2);
	}
	.said {
		margin: 0;
		color: var(--jv-warn);
		font: var(--jv-fs-sm) var(--jv-font-body);
	}
	.code {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: var(--jv-space-5);
	}
	.code img {
		inline-size: var(--jv-measure-qr);
		block-size: var(--jv-measure-qr);
	}
	.code p {
		margin: 0 0 var(--jv-space-2);
	}
	.line {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: var(--jv-space-3);
	}
	.app {
		align-items: flex-start;
		justify-content: space-between;
	}
	.app div {
		display: grid;
		gap: var(--jv-space-1);
	}
	.gets {
		display: flex;
		flex-wrap: wrap;
		gap: var(--jv-space-3);
	}
	/* A link a thumb can hit: the row's touch height, not the text's. */
	.get {
		display: inline-flex;
		align-items: center;
		min-block-size: var(--jv-touch);
		color: var(--jv-accent);
		font: var(--jv-fs-sm) var(--jv-font-body);
		text-underline-offset: var(--jv-space-1);
	}
	.get:focus-visible {
		outline: var(--jv-focus-outline);
		outline-offset: var(--jv-focus-offset);
	}
	/* Half the pairing QR: a row on a screen, still well above what a phone camera needs for a short link. */
	.qr {
		inline-size: calc(var(--jv-measure-qr) / 2);
		block-size: auto;
	}
	.digits {
		color: var(--jv-text-bright);
		font: var(--jv-fs-2xl) var(--jv-font-display);
		letter-spacing: var(--jv-track-logo);
	}
</style>
