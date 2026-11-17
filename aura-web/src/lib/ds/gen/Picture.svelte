<!--
@component
A picture the model has as a `data:` URL (PNG, JPEG, WebP or GIF), never a
link: the console fetches nothing a model named.
Does not cover: a picture it only has a URL for. Fetch it server-side first.
-->
<script lang="ts">
	let { data }: { data: { src: string; alt: string; caption?: string } } = $props();
</script>

<figure class="picture" data-gen="image">
	<img src={data.src} alt={data.alt} />
	{#if data.caption}<figcaption>{data.caption}</figcaption>{/if}
</figure>

<style>
	.picture {
		margin: 0;
		display: grid;
		gap: var(--jv-space-2);
		min-width: var(--jv-measure-panel-min);
	}
	img {
		display: block;
		width: 100%;
		max-height: calc(var(--jv-measure-card-height) * 3);
		object-fit: contain;
		image-rendering: auto;
	}
	figcaption {
		font-size: var(--jv-fs-xs);
		color: var(--jv-text-dim);
	}
</style>
