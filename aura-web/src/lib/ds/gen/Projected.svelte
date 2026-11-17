<!--
@component
One `hud.projection`, drawn: the model's typed visual, out of the core on a
`Projection`. The projection layer passes the frame's `type` and `data` and
where the visual sits; this picks the component for the type and the state.

```svelte
<Projected type={p.type} data={p.data} placement={{ core, edge: 'left', leaving }} />
```

States, on `[data-state]`: `loading` while `data` is undefined, `error` with the
validator's first complaint when the payload breaks the contract (the plugin
refuses the same payload, so this is a stale or hand-made frame), `empty` when
the type's `empty_when` fields are all empty, `ready` otherwise. New data
remounts the type's component, so a replaced projection plays in again.

Does not cover: layout. The host sizes and places it; `placement.leaving` plays
the exit (and removes a custom visual's frame at once), and the host removes
this after `--jv-dur-exit`.
-->
<script lang="ts">
	import type { Component } from 'svelte';

	import Label from '../Label.svelte';
	import Projection from '../Projection.svelte';

	import Approval from './Approval.svelte';
	import Bar from './Bar.svelte';
	import Code from './Code.svelte';
	import Comparison from './Comparison.svelte';
	import CustomHtml from './CustomHtml.svelte';
	import Device from './Device.svelte';
	import Diff from './Diff.svelte';
	import Line from './Line.svelte';
	import List from './List.svelte';
	import MapView from './MapView.svelte';
	import Media from './Media.svelte';
	import Model from './Model.svelte';
	import Person from './Person.svelte';
	import Picture from './Picture.svelte';
	import Progress from './Progress.svelte';
	import Scene from './Scene.svelte';
	import Stat from './Stat.svelte';
	import Table from './Table.svelte';
	import Timeline from './Timeline.svelte';
	import { isEmptyProjection, validateProjection, type Placement } from './contract';

	interface Props {
		type: string;
		/** The frame's data; undefined while it is on its way. */
		data: unknown;
		placement?: Placement;
		testid?: string;
	}
	let { type, data, placement = {}, testid = '' }: Props = $props();

	const VIEWS: Record<string, Component<{ data: never; leaving: boolean }>> = {
		timeline: Timeline,
		stat: Stat,
		line: Line,
		bar: Bar,
		map: MapView,
		list: List,
		table: Table,
		comparison: Comparison,
		image: Picture,
		model: Model,
		code: Code,
		diff: Diff,
		approval: Approval,
		progress: Progress,
		media: Media,
		device: Device,
		person: Person,
		scene: Scene,
		custom_html: CustomHtml
	};

	const error = $derived(data === undefined ? null : validateProjection(type, data));
	const shown = $derived(
		data === undefined ? 'loading' : error ? 'error' : isEmptyProjection(type, data) ? 'empty' : 'ready'
	);
	const title = $derived.by(() => {
		if (shown === 'loading' || shown === 'error') return '';
		const t = (data as { title?: unknown }).title;
		return typeof t === 'string' ? t : '';
	});
	const View = $derived(Object.hasOwn(VIEWS, type) ? VIEWS[type] : undefined);
</script>

<Projection
	core={placement.core}
	leaving={placement.leaving}
	from={placement.from}
	edge={placement.edge}
	label={title || type}
	{testid}
>
	<div class="gen" data-state={shown} data-type={type}>
		{#if title}<div class="title"><Label tone="dim">{title}</Label></div>{/if}
		{#if shown === 'loading'}
			<div class="scan" aria-hidden="true"></div>
			<Label>Projecting…</Label>
		{:else if shown === 'error'}
			<Label tone="danger">Can't draw this</Label>
			<p class="why">{error}</p>
		{:else if shown === 'empty'}
			<p class="none">Nothing to show</p>
		{:else if View}
			{#key data}
				<View data={data as never} leaving={!!placement.leaving} />
			{/key}
		{/if}
	</div>
</Projection>

<style>
	.gen {
		display: grid;
		gap: var(--jv-space-2);
		min-width: 0;
	}
	.title {
		margin-bottom: var(--jv-space-1);
	}
	.why,
	.none {
		margin: 0;
		color: var(--jv-text-dim);
		font-size: var(--jv-fs-xs);
		overflow-wrap: anywhere;
	}
	.scan {
		height: 1px;
		background: linear-gradient(90deg, transparent, var(--jv-accent), transparent);
		transform-origin: left;
		animation: scan var(--jv-dur-scan) var(--jv-ease-scan) infinite alternate;
	}
	@keyframes scan {
		from { transform: scaleX(0.15); opacity: 0.4; }
		to { transform: scaleX(1); opacity: 1; }
	}
</style>
