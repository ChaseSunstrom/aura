<!--
@component
What the phone and the car show while the console cannot be reached (the owner,
2026-10-08: "the car HUD and phone show a console-style UI when the app can't
connect"). Prerendered on the console's tokens, inlined into one file by
`scripts/offline-asset.mjs` and shipped in the APK, so it is the console's look
with no server at all. The phone fills the ids (`ConsolePage.offline`): the
reason, the next try, and in the car what still works. The `away` line is the
usual cause off home Wi-Fi: the tunnel (T-0474). "Reconnect now" and "Phone
settings" are links the WebView catches (`jarvis-offline:reconnect`,
`:settings`); nothing here runs script.

The core is the v2 core's offline look, still: its light in the alert colour
(`REACTOR_TO_CORE.offline`) inside its rings, drawn in CSS because the asset
carries no script and `$lib/ds/core/Core.svelte` draws on a canvas.

Does not cover: the live core. Nothing here moves; a page that cannot reach
JARVIS has nothing to say by moving.
-->
<script lang="ts">
	import { wornName } from '$lib/ds/theme.svelte';
</script>

<svelte:head>
	<title>{wornName()} is offline</title>
</svelte:head>

<main class="offline" data-testid="offline-page">
	<div class="core" role="img" aria-label="{wornName()}, offline" data-state="offline" data-testid="offline-core">
		<span class="ring a"></span>
		<span class="ring b"></span>
		<span class="ring c"></span>
	</div>
	<p class="mark">{wornName()}</p>
	<h1 class="title" id="title">Can't reach {wornName()}</h1>
	<p class="detail" id="detail">This phone is trying again on its own.</p>
	<p class="still" id="still">Calls, texts and music keep working on the phone while it is away.</p>
	<p class="still" id="away">Away from home? {wornName()} is reached through the WireGuard tunnel: check it is set up in Phone settings.</p>
	<p class="next" id="next"></p>
	<div class="actions">
		<a class="again" href="jarvis-offline:reconnect" id="again">Reconnect now</a>
		<a class="quiet" href="jarvis-offline:settings" id="settings">Phone settings</a>
	</div>
</main>

<style>
	:global(body) {
		background: var(--jv-stage-ground);
	}
	.offline {
		min-block-size: 100vh;
		box-sizing: border-box;
		display: flex;
		flex-direction: column;
		align-items: center;
		justify-content: center;
		gap: var(--jv-space-3);
		padding: var(--jv-space-6) var(--jv-space-5);
		text-align: center;
		color: var(--jv-text);
		font-family: var(--jv-font-body);
	}
	/* The core at rest, offline: a dim shell of the alert's light and three hairline rings. */
	.core {
		position: relative;
		inline-size: min(40vw, 32vh, var(--jv-measure-boot));
		aspect-ratio: 1;
		margin-block-end: var(--jv-space-4);
		background: radial-gradient(
			closest-side,
			rgb(from var(--jv-danger) r g b / 0.45),
			rgb(from var(--jv-danger) r g b / 0.12) 45%,
			transparent 70%
		);
	}
	.ring {
		position: absolute;
		inset: 6%;
		border: 1px solid rgb(from var(--jv-danger) r g b / 0.4);
		border-radius: 50%;
	}
	.a {
		transform: rotate(-24deg) scaleY(0.34);
	}
	.b {
		transform: rotate(32deg) scaleY(0.5);
	}
	.c {
		inset: 14%;
		transform: rotate(90deg) scaleY(0.26);
	}
	.mark {
		margin: 0;
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-sm);
		letter-spacing: var(--jv-track-logo);
		color: var(--jv-text-dim);
	}
	.title {
		margin: 0;
		font-family: var(--jv-font-display);
		font-weight: var(--jv-weight-display);
		font-size: var(--jv-fs-xl);
		color: var(--jv-text-bright);
	}
	.detail,
	.still {
		margin: 0;
		max-inline-size: 34ch;
		font-size: var(--jv-fs-md);
		color: var(--jv-text-dim);
	}
	.next {
		margin: 0;
		min-block-size: 1lh;
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-sm);
		color: var(--jv-text-faint);
		font-variant-numeric: tabular-nums;
	}
	.actions {
		display: flex;
		flex-wrap: wrap;
		justify-content: center;
		gap: var(--jv-space-3);
		margin-block-start: var(--jv-space-2);
	}
	/* The v2 button's two looks (`$lib/ds/ui/Button.svelte`), as links the WebView catches. */
	.again,
	.quiet {
		display: inline-flex;
		align-items: center;
		min-block-size: var(--jv-touch);
		padding: 0 var(--jv-space-4);
		border: 1px solid transparent;
		border-radius: var(--jv-radius-sm);
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-xs);
		font-weight: var(--jv-weight-label);
		letter-spacing: var(--jv-track-control);
		text-transform: uppercase;
		text-decoration: none;
	}
	.again {
		border-color: var(--jv-accent);
		color: var(--jv-accent-lift);
		box-shadow: var(--jv-glow-sm);
		text-shadow: var(--jv-glow-text);
	}
	.quiet {
		color: var(--jv-text-dim);
		padding: 0 var(--jv-space-2);
	}
	.again:focus-visible,
	.quiet:focus-visible {
		outline: var(--jv-focus-outline);
		outline-offset: var(--jv-focus-offset);
	}
</style>
