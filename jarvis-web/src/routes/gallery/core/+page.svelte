<!--
@component
The v2 core's scratch page, `/gallery/core`: the core in each of its nine
states, with every ds component on the stage around it, so a state is judged
with the things it will be seen with.

What `e2e/ds-core.spec.ts` drives and the screenshots in `docs/screens/v2/core/`
are made of. Everything a spec needs is in the query string:

  ?state=thinking     the state it opens in (a button per state changes it live,
                      which tweens and fires the shock ring).
  ?t=2500&from=idle   one frozen frame, 2.5 s after a change from idle: the
                      screenshots and the pixel tests.
  ?renderer=canvas2d  skip the GPU (the fallback, side by side with WebGL).
  ?theme=jarvis       the theme pack it wears (default aura: the galaxy; jarvis:
                      the reactor). A button per theme switches live.
  ?chrome=0           no controls: the stage alone.

The sliders stand in for the voice pipeline's levels; at "auto" the core
synthesizes its own.
-->
<script lang="ts">
	import { onMount } from 'svelte';
	import { page } from '$app/state';
	import { DEFAULT_THEME, THEMES, THEME_IDS, type ThemeId } from '$lib/themes';

	import {
		CORE_STATES,
		Connector,
		Core,
		Figure,
		HoldButton,
		Label,
		LeaderLine,
		PresenceStrip,
		Projection,
		RuleChip,
		Subtitles,
		isCoreState,
		setTheme,
		type CoreState,
		type Point
	} from '$lib/ds';

	const q = page.url.searchParams;
	const asState = (v: string | null, or: CoreState): CoreState => (isCoreState(v) ? v : or);
	const t = Number(q.get('t'));
	const freeze = q.has('t') && Number.isFinite(t) ? { at: t, from: asState(q.get('from'), 'idle') } : undefined;
	const renderer = q.get('renderer') === 'canvas2d' ? 'canvas2d' : 'auto';
	const chrome = q.get('chrome') !== '0';
	const named = q.get('theme');

	let wear = $state<ThemeId>(THEME_IDS.find((id) => id === named) ?? DEFAULT_THEME);
	// The page wears the pack too, so the tokens around the core are its own.
	$effect(() => setTheme(wear, document.documentElement));

	let mode = $state<CoreState>(asState(q.get('state'), 'idle'));
	let level = $state<number | undefined>();
	let envelope = $state<number | undefined>();
	let music = $state<number | undefined>();
	let beats = $state(0);
	let confirmed = $state(0);

	/** What is said over each state: the person while listening, JARVIS otherwise. */
	const LINES: Record<CoreState, { text: string; speaker: 'jarvis' | 'user' }> = {
		idle: { text: 'All quiet. Three lights are on downstairs.', speaker: 'jarvis' },
		listening: { text: 'Turn the hall lights down and lock the front door', speaker: 'user' },
		speaking: { text: 'The hall is at thirty percent. The door needs you to hold.', speaker: 'jarvis' },
		thinking: { text: 'Working out which lights you mean.', speaker: 'jarvis' },
		embedding: { text: 'Remembering that you like the hall dim after eleven.', speaker: 'jarvis' },
		acting: { text: 'Dimming the hall.', speaker: 'jarvis' },
		music: { text: 'Playing Nils Frahm in the living room.', speaker: 'jarvis' },
		alert: { text: 'Someone is at the front door.', speaker: 'jarvis' },
		working: { text: 'Booking a table for two at eight.', speaker: 'jarvis' }
	};
	const STEPS = [{ label: 'Open the site' }, { label: 'Pick a time' }, { label: 'Confirm', confirm: true }];

	let stage: HTMLElement;
	let coreBox: HTMLElement;
	let outside: HTMLElement;
	let memory: HTMLElement;
	type Box = { l: number; r: number; x: number; y: number };
	const NOWHERE: Box = { l: 50, r: 50, x: 50, y: 50 };
	let at = $state({ core: NOWHERE, outside: NOWHERE, memory: NOWHERE, px: { x: 0, y: 0 } });

	/**
	 * Where things landed, as percent of the stage (the lines' space), and the
	 * core's centre in pixels (the projections'). The lines end at the core's
	 * edge, never over it, so the pixel tests see the core and nothing else.
	 */
	function measure() {
		const s = stage.getBoundingClientRect();
		if (!s.width || !s.height) return;
		const box = (el: HTMLElement): Box => {
			const b = el.getBoundingClientRect();
			const x = (v: number) => ((v - s.left) / s.width) * 100;
			return { l: x(b.left), r: x(b.right), x: x(b.left + b.width / 2), y: ((b.top + b.height / 2 - s.top) / s.height) * 100 };
		};
		const c = coreBox.getBoundingClientRect();
		at = {
			core: box(coreBox),
			outside: box(outside),
			memory: box(memory),
			px: { x: c.left + c.width / 2, y: c.top + c.height / 2 }
		};
	}

	onMount(() => {
		const ro = new ResizeObserver(measure);
		ro.observe(stage);
		return () => ro.disconnect();
	});

	const slider = (e: Event) => Number((e.currentTarget as HTMLInputElement).value) / 100;
	const line = $derived(LINES[mode]);
	// The memory's leader: out of its projection's near edge, an elbow, into the core's.
	const leader = $derived<Point[]>([
		[at.memory.l, at.memory.y],
		[(at.memory.l + at.core.r) / 2, at.memory.y],
		[at.core.r, at.core.y]
	]);
</script>

<svelte:head>
	<title>Core states</title>
	<meta name="robots" content="noindex" />
</svelte:head>

<main class="page" data-testid="core-gallery" data-chrome={chrome ? 'on' : 'off'}>
	{#if chrome}
		<nav class="controls" aria-label="Core state">
			<div class="states">
				{#each CORE_STATES as s (s)}
					<button
						type="button"
						class="state"
						aria-pressed={mode === s}
						data-testid="state-{s}"
						onclick={() => (mode = s)}>{s}</button
					>
				{/each}
				{#each THEME_IDS as id (id)}
					<button
						type="button"
						class="state"
						aria-pressed={wear === id}
						data-testid="theme-{id}"
						onclick={() => (wear = id)}>{THEMES[id].name}</button
					>
				{/each}
			</div>
			<div class="inputs">
				<label>mic <input type="range" data-testid="input-level" oninput={(e) => (level = slider(e))} /></label>
				<label
					>voice <input type="range" data-testid="input-envelope" oninput={(e) => (envelope = slider(e))} /></label
				>
				<label>beat <input type="range" data-testid="input-music" oninput={(e) => (music = slider(e))} /></label>
				<button type="button" class="state" onclick={() => (level = envelope = music = undefined)}>auto</button>
			</div>
		</nav>
	{/if}

	<section class="stage" bind:this={stage} data-testid="core-stage" aria-label="The stage">
		<Connector start={[at.core.l, at.core.y]} end={[at.outside.r, at.outside.y]} beat={beats} testid="gallery-connector" />
		<LeaderLine points={leader} waiting={mode === 'alert'} testid="gallery-leader" />

		<div class="spot outside" bind:this={outside}>
			<Projection core={at.px} edge="left" label="Outside" testid="projection-outside">
				<Label>Outside · garden</Label>
				<Figure value={21} unit="°C" size="lg" />
			</Projection>
		</div>

		<div class="centre" bind:this={coreBox}>
			<Core state={mode} theme={wear} {level} {envelope} {music} {renderer} {freeze} onbeat={(n) => (beats = n)} />
		</div>

		<div class="spot memory" bind:this={memory}>
			<Projection core={at.px} edge="right" label="Memory" testid="projection-memory">
				<Label tone="violet">Memory · {mode === 'embedding' ? 'writing' : 'recalled'}</Label>
				<Figure value={157} unit="facts" size="lg" tone="violet" />
			</Projection>
		</div>

		<div class="under">
			<Subtitles text={line.text} speaker={line.speaker} />
			<PresenceStrip
				task="Booking a table"
				device="desktop"
				steps={STEPS}
				current={mode === 'working' || mode === 'acting' ? beats % STEPS.length : 0}
				ontakeover={() => (mode = 'idle')}
			/>
			<div class="row">
				<HoldButton label="Unlock the front door" tone="warn" onconfirm={() => (confirmed += 1)} />
				<RuleChip text="Dim the hall after 23:00" kind="rule" onremove={() => {}} />
			</div>
			<p class="note" data-testid="beats">{beats} beats · {confirmed} confirmed</p>
		</div>
	</section>
</main>

<style>
	.page {
		min-height: 100vh;
		background: var(--jv-stage-ground);
		color: var(--jv-text);
		font-family: var(--jv-font-body);
	}
	.controls {
		display: flex;
		flex-wrap: wrap;
		gap: var(--jv-space-3);
		padding: var(--jv-space-3) var(--jv-space-4);
		justify-content: space-between;
	}
	.states,
	.inputs,
	.row {
		display: flex;
		flex-wrap: wrap;
		gap: var(--jv-space-2);
		align-items: center;
	}
	.state,
	.inputs label,
	.note {
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		letter-spacing: var(--jv-track-label);
		text-transform: uppercase;
		color: var(--jv-text-dim);
	}
	.state {
		background: transparent;
		border: 1px solid var(--jv-line-hair);
		padding: var(--jv-space-1) var(--jv-space-2);
		cursor: pointer;
	}
	.state[aria-pressed='true'] {
		color: var(--jv-accent);
		border-color: var(--jv-accent);
	}
	.stage {
		position: relative;
		display: grid;
		/* minmax(0, …): a bare 1fr grows to its widest child's min-content, which
		   pushed the presence strip off a phone's screen. */
		grid-template-columns: minmax(0, 1fr) auto minmax(0, 1fr);
		grid-template-areas: 'outside centre memory' 'under under under';
		align-items: center;
		gap: var(--jv-space-4);
		padding: var(--jv-space-5);
	}
	.centre {
		grid-area: centre;
		display: grid;
		place-items: center;
		inline-size: min(var(--jv-core-width), 46vw, 70vh);
	}
	.outside {
		grid-area: outside;
		justify-self: end;
	}
	.memory {
		grid-area: memory;
		justify-self: start;
	}
	.under {
		grid-area: under;
		display: grid;
		grid-template-columns: minmax(0, 1fr);
		justify-items: center;
		gap: var(--jv-space-3);
	}
	@media (max-width: 720px) {
		.stage {
			grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
			grid-template-areas: 'centre centre' 'outside memory' 'under under';
			padding: var(--jv-space-3);
		}
		.centre {
			inline-size: min(var(--jv-core-width), 92vw);
			justify-self: center;
		}
		.outside,
		.memory {
			justify-self: center;
		}
	}
</style>
