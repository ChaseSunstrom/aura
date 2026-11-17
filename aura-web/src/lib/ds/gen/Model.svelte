<!--
@component
A 3D model or a point cloud, turning slowly (one turn per `--jv-amb-drift-slow`;
still under reduced motion). Points are dots; with `faces`, the faces' edges
are drawn as lines, which is how the HUD draws a solid.

Does not cover: a browser without WebGL (it says so instead) or more than a
handful at once: each holds a WebGL context until it is destroyed, when the
context is released at once rather than left to the collector.
-->
<script lang="ts">
	import { onMount } from 'svelte';

	import { tokenMs, tokenPx } from '$lib/tokens';

	import { prefersReducedMotion } from '../motion';
	import { toneColor } from '../tone';

	let { data }: { data: { points: number[][]; faces?: number[][]; tone?: string } } = $props();

	let canvas: HTMLCanvasElement | undefined = $state();
	let failed = $state(false);

	onMount(() => {
		let gone = false;
		let stop = () => {};
		void import('three').then((THREE) => {
			if (gone || !canvas) return;
			let renderer: InstanceType<typeof THREE.WebGLRenderer>;
			try {
				renderer = new THREE.WebGLRenderer({ canvas, alpha: true, antialias: true });
			} catch {
				failed = true;
				return;
			}
			const w = canvas.clientWidth || 1;
			const h = canvas.clientHeight || 1;
			renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
			renderer.setSize(w, h, false);
			const color = new THREE.Color(getComputedStyle(canvas).color);

			const geo = new THREE.BufferGeometry();
			geo.setAttribute('position', new THREE.Float32BufferAttribute(data.points.flat(), 3));
			geo.center();
			geo.computeBoundingSphere();
			const r = geo.boundingSphere?.radius || 1;
			geo.scale(1 / r, 1 / r, 1 / r);

			const scene = new THREE.Scene();
			const group = new THREE.Group();
			scene.add(group);
			const dotMat = new THREE.PointsMaterial({
				color,
				size: 2 * tokenPx('--jv-rule-live'),
				sizeAttenuation: false
			});
			group.add(new THREE.Points(geo, dotMat));

			// A face naming a point that is not there is dropped, not drawn to the origin.
			const n = data.points.length;
			const faces = (data.faces ?? []).filter((f) => f.every((k) => k < n));
			let edges: InstanceType<typeof THREE.EdgesGeometry> | undefined;
			let lineMat: InstanceType<typeof THREE.LineBasicMaterial> | undefined;
			if (faces.length) {
				const solid = geo.clone();
				solid.setIndex(faces.flat());
				edges = new THREE.EdgesGeometry(solid, 1);
				solid.dispose();
				lineMat = new THREE.LineBasicMaterial({ color, transparent: true, opacity: 0.85 });
				group.add(new THREE.LineSegments(edges, lineMat));
			}

			const camera = new THREE.PerspectiveCamera(40, w / h, 0.1, 100);
			camera.position.set(0, 0.6, 3.2);
			camera.lookAt(0, 0, 0);
			group.rotation.x = 0.35;

			const turn = tokenMs('--jv-amb-drift-slow');
			const still = prefersReducedMotion();
			let raf = 0;
			const frame = (t: number) => {
				group.rotation.y = ((t % turn) / turn) * Math.PI * 2;
				renderer.render(scene, camera);
				if (!still) raf = requestAnimationFrame(frame);
			};
			frame(0);

			stop = () => {
				cancelAnimationFrame(raf);
				geo.dispose();
				edges?.dispose();
				dotMat.dispose();
				lineMat?.dispose();
				renderer.dispose();
				renderer.forceContextLoss();
			};
		});
		return () => {
			gone = true;
			stop();
		};
	});
</script>

<div
	class="model"
	data-gen="model"
	role="img"
	aria-label="A 3D model of {data.points.length} points"
	style:--tone={toneColor(data.tone ?? 'accent')}
>
	<canvas bind:this={canvas} aria-hidden="true"></canvas>
	{#if failed}<p class="note">This screen cannot draw 3D.</p>{/if}
</div>

<style>
	.model {
		position: relative;
		min-width: var(--jv-measure-panel-min);
	}
	canvas {
		display: block;
		width: 100%;
		height: calc(var(--jv-measure-card-height) * 2);
		color: var(--tone);
		filter: drop-shadow(var(--jv-glow-sm));
	}
	.note {
		position: absolute;
		inset: 0;
		display: grid;
		place-items: center;
		margin: 0;
		font-size: var(--jv-fs-xs);
		color: var(--jv-text-dim);
	}
</style>
