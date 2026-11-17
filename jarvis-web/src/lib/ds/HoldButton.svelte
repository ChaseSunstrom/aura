<!--
@component
Hold to confirm: press and keep holding (≈2.4s) to say yes to something that
cannot be undone. Letting go, sliding off or losing focus before the end
cancels. A light fills the button while it is held.

```svelte
<HoldButton label="Unlock the front door" onconfirm={approve} />
```

Mouse, touch and keyboard (Space or Enter held) all work. Reduced motion keeps
the hold — it is a safety measure, not decoration — and says "Keep holding"
instead of animating the fill.

Does not cover: a screen reader's single activation. TalkBack's double-tap
and hold and VoiceOver's hold gesture both send the long press this needs.
-->
<script lang="ts">
	import { MS } from './motion';
	import { toneColor } from './tone';

	interface Props {
		/** What holding agrees to. */
		label: string;
		onconfirm: () => void;
		tone?: 'warn' | 'danger' | 'accent';
		disabled?: boolean;
		testid?: string;
	}
	let { label, onconfirm, tone = 'warn', disabled = false, testid = '' }: Props = $props();

	let holding = $state(false);
	let timer: ReturnType<typeof setTimeout> | undefined;

	function start() {
		if (disabled || holding) return;
		holding = true;
		timer = setTimeout(() => {
			timer = undefined;
			holding = false;
			onconfirm();
		}, MS.hold);
	}

	function stop() {
		clearTimeout(timer);
		timer = undefined;
		holding = false;
	}

	$effect(() => () => clearTimeout(timer));

	const isKey = (e: KeyboardEvent) => e.key === ' ' || e.key === 'Enter';
</script>

<button
	type="button"
	class="hold"
	class:holding
	style:--tone={toneColor(tone)}
	{disabled}
	data-testid={testid || undefined}
	data-holding={holding || undefined}
	onpointerdown={(e) => {
		if (e.button === 0) start();
	}}
	onpointerup={stop}
	onpointerleave={stop}
	onpointercancel={stop}
	onblur={stop}
	oncontextmenu={(e) => e.preventDefault()}
	onkeydown={(e) => {
		if (!isKey(e)) return;
		e.preventDefault();
		if (!e.repeat) start();
	}}
	onkeyup={(e) => {
		if (isKey(e)) stop();
	}}
>
	<span class="fill" aria-hidden="true"></span>
	<span class="text">{label}</span>
	<span class="hint">{holding ? 'Keep holding' : 'Hold to confirm'}</span>
</button>

<style>
	.hold {
		position: relative;
		display: inline-grid;
		gap: var(--jv-space-1);
		justify-items: start;
		min-height: var(--jv-touch);
		min-width: var(--jv-touch);
		padding: var(--jv-space-2) var(--jv-space-4);
		overflow: hidden;
		border: 1px solid var(--tone);
		border-radius: var(--jv-radius-sm);
		background: transparent;
		color: var(--tone);
		font-family: var(--jv-font-chrome);
		cursor: pointer;
		touch-action: none;
		user-select: none;
		-webkit-user-select: none;
	}
	.hold:disabled {
		cursor: not-allowed;
		opacity: 0.5;
	}
	.fill {
		position: absolute;
		inset: 0;
		z-index: 0;
		background: rgb(from var(--tone) r g b / 0.22);
		transform: scaleX(0);
		transform-origin: left;
		transition: transform var(--jv-dur-fast) var(--jv-ease-out);
	}
	.holding .fill {
		transform: scaleX(1);
		transition: transform var(--jv-dur-hold) linear;
	}
	.text,
	.hint {
		position: relative;
		z-index: 1;
	}
	.text {
		font-size: var(--jv-fs-sm);
		font-weight: var(--jv-weight-strong);
	}
	.hint {
		font-size: var(--jv-fs-2xs);
		letter-spacing: var(--jv-track-label);
		text-transform: uppercase;
		color: var(--jv-text-dim);
	}
</style>
