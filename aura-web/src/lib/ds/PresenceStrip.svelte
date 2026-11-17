<!--
@component
The strip that says JARVIS has its hands on a device. It unrolls sideways out
of the core (which has risen into it), shows the task, the steps with the one
under way, and how to take over; it folds back into the core when the job ends.

```svelte
<PresenceStrip
	device="Chrome · desk"
	task="Booking a table at Noma"
	steps={[{ label: 'Open site' }, { label: 'Pick time' }, { label: 'Confirm', confirm: true }]}
	current={1}
	ontakeover={() => stopDriving()}
/>
```

Esc anywhere takes over while it is shown (unless something nearer already
handled the key); so does the button, for touch.

Does not cover: stopping the job. `ontakeover` reports the user's intent; the
host stops the agent and folds the strip.
-->
<script lang="ts">
	import { wornName } from '$lib/ds/theme.svelte';
	import Label from './Label.svelte';
	import { MS } from './motion';
	import { animationOffset } from './stage';

	interface Step {
		label: string;
		/** This step needs the user's yes: drawn amber. */
		confirm?: boolean;
	}

	interface Props {
		/** What it is doing, in a few words. */
		task: string;
		/** What it is driving: "Chrome · desk". */
		device?: string;
		steps: readonly Step[];
		/** The step under way; those before it are done. `steps.length` means all done. */
		current: number;
		leaving?: boolean;
		from?: number;
		/** The user takes over: Esc or the button. Without it no take-over is offered. */
		ontakeover?: () => void;
		testid?: string;
	}
	let {
		task,
		device = '',
		steps,
		current,
		leaving = false,
		from,
		ontakeover,
		testid = ''
	}: Props = $props();

	const at = $derived(Math.min(steps.length, Math.max(0, Math.floor(current) || 0)));
	// It unrolls slower than a projection appears; the fold has to fit the
	// stage's exit, or the scene is gone before the strip has folded.
	const delay = $derived(
		animationOffset(leaving, from ?? (leaving ? 1 : 0), { exit: MS.exit, appear: MS.unroll })
	);

	function onkeydown(event: KeyboardEvent) {
		if (event.key !== 'Escape' || event.defaultPrevented || leaving || !ontakeover) return;
		event.preventDefault();
		ontakeover();
	}
</script>

<svelte:window {onkeydown} />

<section
	class="strip"
	class:leaving
	style:animation-delay="{delay}ms"
	aria-label="{wornName()} is acting"
	data-testid={testid || undefined}
	data-step={at}
>
	<div class="head">
		<Label tone="accent">{device ? `Acting · ${device}` : 'Acting'}</Label>
		<p class="task">{task}</p>
	</div>
	<ol class="steps" aria-label="Steps">
		{#each steps as step, i (i)}
			<li
				class:done={i < at}
				class:now={i === at}
				class:confirm={step.confirm}
				aria-current={i === at ? 'step' : undefined}
			>
				<span class="bar"></span>
				<span class="name">{step.label}</span>
			</li>
		{/each}
	</ol>
	{#if ontakeover}
		<button type="button" class="takeover" onclick={() => ontakeover?.()} disabled={leaving}>
			<kbd>Esc</kbd> to take over
		</button>
	{/if}
</section>

<style>
	.strip {
		display: grid;
		grid-template-columns: 1fr auto;
		gap: var(--jv-space-2) var(--jv-space-4);
		align-items: center;
		width: var(--jv-strip-width);
		max-width: 100%;
		padding: var(--jv-space-3) var(--jv-space-4);
		color: var(--jv-text);
		background: var(--jv-projection-tint), var(--jv-projection-backing);
		border-top: 1px solid rgb(from var(--jv-accent) r g b / 0.5);
		border-bottom: 1px solid rgb(from var(--jv-accent) r g b / 0.5);
		text-shadow: var(--jv-glow-text);
		transform-origin: center;
		animation: unroll var(--jv-dur-unroll) var(--jv-ease-unroll) both;
	}
	.strip.leaving {
		animation: fold var(--jv-dur-exit) var(--jv-ease-unroll) reverse both;
	}
	.head {
		display: grid;
		gap: var(--jv-space-1);
		min-width: 0;
	}
	.task {
		margin: 0;
		font-family: var(--jv-font-display);
		font-weight: var(--jv-weight-display);
		font-size: var(--jv-fs-lg);
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	.steps {
		grid-column: 1 / -1;
		grid-row: 2;
		display: grid;
		grid-auto-flow: column;
		grid-auto-columns: 1fr;
		gap: var(--jv-space-2);
		margin: 0;
		padding: 0;
		list-style: none;
	}
	.steps li {
		display: grid;
		gap: var(--jv-space-1);
		min-width: 0;
		color: var(--jv-text-faint);
	}
	.bar {
		height: 1px;
		background: currentColor;
		opacity: 0.5;
	}
	.done {
		color: var(--jv-accent);
	}
	.done .bar,
	.now .bar {
		opacity: 1;
	}
	.now {
		color: var(--jv-text);
	}
	.now .bar {
		background: var(--jv-accent);
		box-shadow: var(--jv-glow-sm);
	}
	.confirm,
	.confirm.now {
		color: var(--jv-warn);
	}
	.confirm.now .bar {
		background: var(--jv-warn);
	}
	.name {
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		letter-spacing: var(--jv-track-label);
		text-transform: uppercase;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	.takeover {
		min-height: var(--jv-touch);
		min-width: var(--jv-touch);
		padding: 0 var(--jv-space-3);
		border: 1px solid rgb(from var(--jv-accent) r g b / 0.4);
		border-radius: var(--jv-radius-sm);
		background: transparent;
		color: var(--jv-text-dim);
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		letter-spacing: var(--jv-track-control);
		text-transform: uppercase;
		cursor: pointer;
	}
	.takeover:hover,
	.takeover:focus-visible {
		color: var(--jv-text);
		border-color: var(--jv-accent);
	}
	kbd {
		font-family: inherit;
		color: var(--jv-accent);
	}
	/* Out of the core sideways: a sliver at the centre, then the full width. */
	@keyframes unroll {
		0% { opacity: 0; transform: scaleX(0.02); }
		20% { opacity: 1; }
		100% { opacity: 1; transform: scaleX(1); }
	}
	@keyframes fold {
		0% { opacity: 0; transform: scaleX(0.02); }
		20% { opacity: 1; }
		100% { opacity: 1; transform: scaleX(1); }
	}
</style>
