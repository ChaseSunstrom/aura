<!--
@component
Settings: one short screen, only what a person sets (the v2 brief): the
console sign-in, the people JARVIS knows by voice, who JARVIS is and whether
replies are read aloud, what JARVIS may do without asking, and the devices.

The legacy Reactor 2D/3D, Panels Tabs/Tiles and Selection switches are gone
with the window manager they configured. Everything else a setting used to
be is a request to JARVIS (`settings_find` / `settings_set`, behind the
approval card).

Account carries the lock's preferences (minutes of quiet, voice unlock, lock
now); they are this browser's, held by the shell. People names, levels,
renames, enrols and forgets; the enrolment sheet is the shell's.

Every row is a control with one line under it that says what it does, "More"
and "Ask" (`SettingRow`, its words in `settings-rows.ts`); the search box at
the top filters the rows across sections and hides a section with none left.

Recent changes (`RecentChanges`, T-0700) follows Devices: what was changed by
asking, and Put back.

Does not cover: the house's own configuration, or the open mic's tuning.
-->
<script lang="ts">
	import { wornName } from '$lib/ds/theme.svelte';
	import { onMount, type ComponentProps } from 'svelte';

	import Button from '$lib/ds/ui/Button.svelte';
	import Field from '$lib/ds/ui/Field.svelte';
	import Segmented from '$lib/ds/ui/Segmented.svelte';
	import Switch from '$lib/ds/ui/Switch.svelte';
	import { levelRequest, type Capability, type Level } from '$lib/hud/data/capabilities';
	import type { Feeds } from '$lib/hud/feeds.svelte';
	import { LOCK_AFTER_CHOICES, LOCK_AFTER_MAX, parseLockAfter } from '$lib/hud/lock';
	import { PHONE_SCREENS, phoneBridge, type PhoneBridge } from '$lib/hud/phone';
	import { PERSONAS, IDENTITIES, type Persona } from '$lib/orb/personas';
	import { THEMES, THEME_IDS } from '$lib/themes';
	import type { Pack } from '../pack';
	import ThemeTile from '../ThemeTile.svelte';
	import { VOICE_CHOICES, device, setDeviceTheme, setDeviceVoice, theme, type Draft } from '../theme.svelte';
	import Levels from './Levels.svelte';
	import People from './People.svelte';
	import RecentChanges from './RecentChanges.svelte';
	import Screen from './Screen.svelte';
	import Section from './Section.svelte';
	import SettingRow from './SettingRow.svelte';
	import { SECTION_ICON, SETTING_ROWS, matchRows, type SettingSection } from './settings-rows';
	import SignIn from './SignIn.svelte';

	interface Props {
		feeds: Feeds;
		/** The primary persona, as Hermes says it is. */
		primary: Persona;
		/** Whether replies are read aloud off the core; null with no voice. */
		readAloud: boolean | null;
		/** The lock: whether a password exists, the minutes of quiet before it, and voice unlock. */
		lock: { configured: boolean; after: number; voice: boolean };
		/** Bumped when an enrolment closes, so People reads again. */
		peopleVersion?: number;
		onpersona: (p: Persona) => void;
		onreadaloud: (on: boolean) => void;
		onask: (text: string) => void;
		onlockafter: (minutes: number) => void;
		onlockvoice: (on: boolean) => void;
		onlocknow: () => void;
		onenrol: (label: string) => void;
		/** A theme drafted by voice and not worn yet (`theme.preview`), or null. */
		preview?: Draft | null;
		/** Put the draft away without applying it. */
		ondismisspreview?: () => void;
		/** Say a short line in this voice, for the A/B. */
		onsample?: (voice: string) => void;
		/** The open mic's wake word on this device (T-0875); null where the phone listens for it. */
		wake?: { on: boolean; listening: boolean; why: string } | null;
		onwake?: (on: boolean) => void;
		/** The settings plugin's log of changes and its undo (T-0700); undefined while the link is down. */
		changes?: ComponentProps<typeof RecentChanges>['io'];
		/** Bumped on every `settings.changed`, so Recent changes reads again. */
		changed?: number;
	}
	let { feeds, primary, readAloud, lock, peopleVersion = 0, onpersona, onreadaloud, onask, onlockafter, onlockvoice, onlocknow, onenrol, preview = null, ondismisspreview, onsample, wake = null, onwake, changes, changed = 0 }: Props = $props();

	const packOf = (id: (typeof THEME_IDS)[number]) => THEMES[id] as Pack;
	// The A/B is the worn theme's: three stock voices, the pack's own first.
	const choices = $derived(VOICE_CHOICES[theme.id] ?? []);
	// The worn theme's name, as the wake word hears it: "Aura", "Jarvis".
	const wakeName = $derived((THEMES[theme.id]?.wake?.phrases?.[0] ?? '').replace(/\b\w/g, (c) => c.toUpperCase()));

	const personas = PERSONAS.map((p) => ({ value: p, label: IDENTITIES[p].name }));
	// "Never lock", not "Never": the row has no caption (Settings is held to a short screen and a
	// caption wrapped it), and six bare durations did not say what they set (T-0670).
	const afters = LOCK_AFTER_CHOICES.map((m) => ({ value: String(m), label: m === 0 ? 'Never lock' : `${m} min` }));
	// Any number of minutes beside the presets (the owner: "should be customizeable").
	let custom = $state('');
	$effect(() => {
		custom = lock.after > 0 && !LOCK_AFTER_CHOICES.includes(lock.after) ? String(lock.after) : '';
	});
	// The search: the rows it shows, and so the sections that still have one.
	let query = $state('');
	const shown = $derived(matchRows(query));
	const gone = (section: SettingSection) => !SETTING_ROWS.some((r) => r.section === section && shown.has(r.id));
	const level = (c: Capability, l: Level) => onask(levelRequest(c, l));
	const devices = $derived(feeds.companions.data.length);
	// Inside the phone app, its own screens (the owner, 2026-09-27: "theres no options for
	// the car and phone stuff"); read on mount, as the server has no `window`.
	let phone = $state<PhoneBridge | null>(null);
	onMount(() => (phone = phoneBridge()));
</script>

<Screen title="Settings" testid="screen-settings">
	{#snippet tools()}
		<Field label="Search settings" placeholder="Search settings" type="search" bind:value={query} testid="settings-search" />
	{/snippet}
	{#if shown.size === 0}
		<div class="line none" role="status" data-testid="settings-none">
			<span class="meta">No setting here matches “{query.trim()}”.</span>
			<Button tone="ghost" testid="settings-none-ask" onclick={() => onask(`Find the setting for “${query.trim()}”.`)}>Ask {wornName()}</Button>
		</div>
	{/if}

	<Section title="Account" icon={SECTION_ICON.account} hidden={gone('account')} testid="settings-account">
		<SignIn onchange={() => feeds.reload(feeds.speaker)} />
		<div class="line"><Button href="/setup" testid="settings-setup">Open the setup walkthrough</Button></div>
		<SettingRow id="lock-after" {shown} {onask}>
		<div class="line lock">
			<Segmented label="Lock after" options={afters} value={String(lock.after)} disabled={!lock.configured} onchange={(m) => onlockafter(Number(m))} testid="lock-after" />
			<Field
				label="Lock after this many minutes of quiet (1–{LOCK_AFTER_MAX})"
				placeholder="Minutes"
				type="number"
				min="1"
				max={LOCK_AFTER_MAX}
				inputmode="numeric"
				disabled={!lock.configured}
				bind:value={custom}
				onchange={() => onlockafter(parseLockAfter(custom))}
				testid="lock-after-custom"
			/>
		</div>
		</SettingRow>
		<SettingRow id="voice-unlock" {shown} {onask}>
		<div class="line lock">
			<Switch label="Voice unlock" on={lock.voice} disabled={!lock.configured} testid="lock-voice-toggle" onchange={onlockvoice} />
			<Button disabled={!lock.configured} testid="lock-now" onclick={onlocknow}>Lock now</Button>
		</div>
		</SettingRow>
		{#if !lock.configured}<p class="meta">Set a console password to use the lock.</p>{/if}
	</Section>

	<Section title="People and voices" icon={SECTION_ICON.people} hidden={gone('people')} testid="settings-people">
		<SettingRow id="people" {shown} {onask}>
			<People {onenrol} version={peopleVersion} />
		</SettingRow>
	</Section>

	<Section title="Persona and voice" icon={SECTION_ICON.persona} hidden={gone('persona')} testid="settings-persona">
		<SettingRow id="persona" {shown} {onask}>
			<Segmented label="Who answers" options={personas} value={primary} onchange={onpersona} testid="settings-persona-pick" />
		</SettingRow>
		{#if readAloud !== null || (wake && wakeName)}
			{#if readAloud !== null}
				<SettingRow id="replies" {shown} {onask}>
				<Segmented
					label="Replies off the core"
					options={[
						{ value: 'chat', label: 'Written' },
						{ value: 'voice', label: 'Read aloud' }
					]}
					value={readAloud ? 'voice' : 'chat'}
					onchange={(v) => onreadaloud(v === 'voice')}
					testid="settings-replies"
				/>
				</SettingRow>
			{/if}
			{#if wake && wakeName}
				<SettingRow id="wake" {shown} {onask}>
				<Segmented
					label="The open mic"
					options={[
						{ value: 'wake', label: `After “${wakeName}”` },
						{ value: 'all', label: 'Every word' }
					]}
					value={wake.on ? 'wake' : 'all'}
					onchange={(v) => onwake?.(v === 'wake')}
					testid="settings-wake"
				/>
				</SettingRow>
			{/if}
		{/if}
		{#if wake && wakeName}
			<div aria-live="polite">
				{#if wake.on && wake.why}
					<p class="meta" data-testid="settings-wake-state">The wake word could not start, so every word is sent: {wake.why}</p>
				{:else if wake.on && wake.listening}
					<p class="meta" data-testid="settings-wake-state">Listening for “{wakeName}” on this device.</p>
				{/if}
			</div>
		{/if}
		{#if choices.length}
			<SettingRow id="voice" {shown} {onask}>
			<!-- Two rows, not a row per voice, to keep Settings short (v2-screens.spec).
			     The one chosen speaks on this device. -->
			<Segmented
				label="The voice on this device"
				options={choices.map((v, i) => ({ value: v, label: i === 0 ? 'Voice A, the theme’s' : `Voice ${String.fromCharCode(65 + i)}` }))}
				value={device.voice ?? ''}
				onchange={(v) => setDeviceVoice(v)}
				testid="voice-pick"
			/>
			<div class="line voice">
				<span class="actions">
					{#if onsample}
						{#each choices as v, i (v)}
							<Button tone="ghost" testid="voice-sample-{i}" onclick={() => onsample(v)}>Hear {String.fromCharCode(65 + i)}</Button>
						{/each}
					{/if}
					{#if device.voice}<Button tone="ghost" testid="voice-follow" onclick={() => setDeviceVoice(null)}>The persona’s own</Button>{/if}
				</span>
			</div>
			</SettingRow>
		{/if}
	</Section>

	<Section title="Theme" icon={SECTION_ICON.theme} hidden={gone('theme')} testid="settings-theme">
		<SettingRow id="theme" {shown} {onask}>
		<div class="themes">
			{#each THEME_IDS as id (id)}
				<ThemeTile
					pack={packOf(id)}
					worn={theme.id === id && !theme.draft}
					note={device.theme === id ? 'Kept on this device' : `${packOf(id).name}'s own`}
					testid="theme-tile-{id}"
				>
					<Button tone="ghost" aria-pressed={device.theme === id} aria-label="Keep {packOf(id).name} on this device" testid="theme-keep-{id}" onclick={() => setDeviceTheme(id)}>Keep here</Button>
				</ThemeTile>
			{/each}
			{#if preview}
				<ThemeTile pack={preview.pack} note="Drafted, not worn yet" testid="theme-draft">
					<Button testid="theme-draft-apply" onclick={() => onask(`Apply the drafted theme ${preview.name} (${preview.id}).`)}>Apply</Button>
					{#if ondismisspreview}<Button tone="ghost" testid="theme-draft-dismiss" onclick={ondismisspreview}>Not now</Button>{/if}
				</ThemeTile>
			{/if}
		</div>
		{#if device.theme}
			<div class="line follow">
				<Button tone="ghost" testid="theme-follow" onclick={() => setDeviceTheme(null)}>Follow the persona</Button>
			</div>
		{/if}
		</SettingRow>
	</Section>

	<Section title="Privacy and approvals" icon={SECTION_ICON.privacy} hidden={gone('privacy')} testid="settings-privacy">
		<SettingRow id="levels" {shown} {onask}>
			<Levels status={feeds.capabilities.status} error={feeds.capabilities.error} capabilities={feeds.capabilities.data} onlevel={level} onretry={() => feeds.reload(feeds.capabilities)} note={false} />
		</SettingRow>
	</Section>

	<Section title="Devices" icon={SECTION_ICON.devices} hidden={gone('devices')} testid="settings-devices">
		<SettingRow id="devices" {shown} {onask}>
		<div class="line">
			<span class="meta">{feeds.companions.status === 'ready' ? `${devices} paired` : feeds.companions.status === 'offline' ? 'The house is offline.' : ''}</span>
			<Button tone="ghost" href="/devices">Your devices</Button>
		</div>
		</SettingRow>
	</Section>

	<!-- Not a setting, so a search does not match it: it steps aside while one is typed. -->
	<RecentChanges io={changes} version={changed} hidden={!!query.trim()} />

	{#if phone?.open && !query.trim()}
		<Section title="This phone" lede="The phone's own screens: its settings, the car, its tasks." testid="settings-this-phone">
			<div class="line screens">
				{#each PHONE_SCREENS as p (p.screen)}
					<Button tone="ghost" title={p.hint} testid="open-phone-{p.screen}" onclick={() => phone?.open?.(p.screen)}>{p.label}</Button>
				{/each}
			</div>
		</Section>
	{/if}
</Screen>

<style>
	.line {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		justify-content: space-between;
		gap: var(--jv-space-3);
	}
	.lock {
		justify-content: flex-start;
	}
	.screens,
	.follow,
	.none {
		justify-content: flex-start;
	}
	.follow {
		margin-block-start: var(--jv-space-3);
	}
	.themes {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(calc(var(--jv-space-7) * 4), 1fr));
		gap: var(--jv-space-3);
	}
	.actions {
		display: flex;
		gap: var(--jv-space-2);
	}
	/* Wide enough for its "Minutes" hint: at one space-7 it read "M". */
	.lock :global(.field) {
		flex: 0 1 calc(var(--jv-space-7) * 2);
	}
</style>
