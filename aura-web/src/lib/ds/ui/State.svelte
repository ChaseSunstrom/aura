<!--
@component
The four states a screen has besides its content: loading, empty, error and
offline, each said in words (never a spinner alone), with Retry where a retry
can help. `ready` renders the children.

Does not cover: a partial failure inside a list (say it on the row).
-->
<script lang="ts">
	import type { Snippet } from 'svelte';

	import type { Status } from '$lib/ui/status';

	interface Props {
		status: Status;
		/** What is missing, said when there is nothing: "No devices paired yet." */
		empty?: string;
		error?: string;
		onretry?: () => void;
		testid?: string;
		children?: Snippet;
	}
	let { status, empty = 'Nothing here yet.', error = '', onretry, testid, children }: Props = $props();

	const WORDS = { loading: 'Loading…', offline: 'Offline. This shows again when the link is back.' } as const;
</script>

{#if status === 'ready'}
	{@render children?.()}
{:else}
	<div class="state {status}" role={status === 'error' ? 'alert' : 'status'} data-testid={testid} data-state={status}>
		<p>
			{#if status === 'loading'}{WORDS.loading}{:else if status === 'offline'}{WORDS.offline}{:else if status === 'empty'}{empty}{:else}Couldn’t load this{error ? `: ${error}` : '.'}{/if}
		</p>
		{#if onretry && (status === 'error' || status === 'offline')}
			<button type="button" onclick={onretry}>Retry</button>
		{/if}
	</div>
{/if}

<style>
	.state {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: var(--jv-space-3);
		padding: var(--jv-space-3) 0;
		color: var(--jv-text-dim);
		font-size: var(--jv-fs-sm);
	}
	.loading p {
		animation: breathe var(--jv-dur-pulse) var(--jv-ease-in-out) infinite alternate;
	}
	.error {
		color: var(--jv-danger-text);
	}
	p {
		margin: 0;
	}
	button {
		min-block-size: var(--jv-touch);
		padding: 0 var(--jv-space-3);
		border: 1px solid var(--jv-line-hair);
		border-radius: var(--jv-radius-sm);
		background: transparent;
		color: var(--jv-text);
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		letter-spacing: var(--jv-track-control);
		text-transform: uppercase;
		cursor: pointer;
	}
	button:focus-visible {
		outline: var(--jv-focus-outline);
		outline-offset: var(--jv-focus-offset);
	}
	@keyframes breathe {
		to {
			opacity: 0.5;
		}
	}
	@media (prefers-reduced-motion: reduce) {
		.loading p {
			animation: none;
		}
	}
</style>
