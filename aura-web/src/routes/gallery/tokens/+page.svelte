<!--
@component
Every token, rendered, at `/gallery/tokens`: the reference that used to be
`/styleguide`. Generated from `design/tokens.json`; nothing here types a value
of its own. Development only; it ships because the e2e suite tests the
production build. The v2 core's gallery is `/gallery/core`.

Text colours list their contrast on the ground so the page says which may
carry words.
-->
<script lang="ts">
	import { TOKENS, tokenMs, type TokenName } from '$lib/tokens';

	type Row = { name: TokenName; value: string };
	const all = Object.entries(TOKENS) as [TokenName, string][];
	const rows = (test: (n: string) => boolean): Row[] =>
		all.filter(([n]) => test(n)).map(([name, value]) => ({ name, value }));

	const colours = rows((n) => !/^--jv-(font|fs|weight|track|space|radius|glow|elev|dur|ease|stagger|drift|rx|grid|bracket|focus|material|depth|light|spring|scrim)-/.test(n) && n !== '--jv-drift' && !n.startsWith('--jv-scrim'));
	const isReactor = (n: string) => n.startsWith('--jv-orb-') || n.startsWith('--jv-reactor-');
	const chromeColours = colours.filter((r) => !isReactor(r.name));
	const orbColours = colours.filter((r) => isReactor(r.name));
	const families = rows((n) => n.startsWith('--jv-font-'));
	const sizes = rows((n) => n.startsWith('--jv-fs-'));
	const tracking = rows((n) => n.startsWith('--jv-track-'));
	const weights = rows((n) => n.startsWith('--jv-weight-'));
	const spaces = rows((n) => n.startsWith('--jv-space-'));
	const radii = rows((n) => n.startsWith('--jv-radius-'));
	const elevation = rows((n) => n.startsWith('--jv-glow-') || n.startsWith('--jv-elev-'));
	const durations = rows((n) => n.startsWith('--jv-dur-') || n.startsWith('--jv-stagger-'));
	const eases = rows((n) => n.startsWith('--jv-ease-'));
	const reactor = rows((n) => n.startsWith('--jv-rx-'));
	const chromeTokens = rows((n) => /^--jv-(measure|focus|rule)-/.test(n));
	const glass = rows((n) => /^--jv-(material|depth|light|spring)-/.test(n) || n.startsWith('--jv-scrim'));

	// WCAG contrast of a hex text colour on the ground, so the page says which
	// text colours may carry words (>= 4.5) and which are marks only.
	function lum(hex: string): number {
		const h = hex.replace('#', '');
		const n = parseInt(h.length === 3 ? h.replace(/(.)/g, '$1$1') : h.slice(0, 6), 16);
		const ch = (v: number) => {
			const s = v / 255;
			return s <= 0.04045 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4);
		};
		return 0.2126 * ch((n >> 16) & 255) + 0.7152 * ch((n >> 8) & 255) + 0.0722 * ch(n & 255);
	}
	function contrast(fg: string): string {
		if (!fg.startsWith('#')) return '—';
		const a = lum(fg),
			b = lum(TOKENS['--jv-bg']);
		const [hi, lo] = a > b ? [a, b] : [b, a];
		return ((hi + 0.05) / (lo + 0.05)).toFixed(1) + ':1';
	}
	const short = (n: string) => n.replace('--jv-', '');
</script>

<svelte:head>
	<title>Tokens</title>
	<meta name="robots" content="noindex" />
</svelte:head>

<main class="tokens" data-testid="gallery-tokens">
	<h2 class="title">Every token, rendered</h2>
	<p class="sg-note">Generated from design/tokens.json by design/build.py. {all.length} tokens.</p>

	<section data-tokens="color" aria-labelledby="h-color">
		<h2 id="h-color">Colour</h2>
		<p class="sg-note">Text colours list their contrast on the ground. Below 4.5:1 is a mark, not a word — <code>tick</code> is the one such token, and it says so.</p>
		<ul class="swatches">
			{#each chromeColours as c (c.name)}
				<li class="swatch">
					<span class="chip" style={`background:var(${c.name})`}></span>
					<span class="sw-name">{short(c.name)}</span>
					<span class="sw-value">{c.value}</span>
					<span class="sw-meta">{contrast(c.value)}</span>
				</li>
			{/each}
		</ul>
		<h3>The reactor's palette</h3>
		<p class="sg-note">The state colour C per reactor state (<code>color.reactor.*</code>, GLASS_HUD §10), each an alias of a base colour; the housing and the hub are the fixed parts.</p>
		<ul class="swatches compact">
			{#each orbColours as c (c.name)}
				<li class="swatch"><span class="chip" style={`background:var(${c.name})`}></span><span class="sw-name">{short(c.name)}</span><span class="sw-value">{c.value}</span></li>
			{/each}
		</ul>
	</section>

	<section data-tokens="type" aria-labelledby="h-type">
		<h2 id="h-type">Type</h2>
		<div class="families">
			{#each families as f (f.name)}
				<div class="family" style={`font-family:var(${f.name})`}>
					<span class="sw-name">{short(f.name)}</span>
					<p class="specimen">The kitchen lamp is at 40 % — I'll remind you at 18:00.</p>
					<span class="sw-value">{f.value}</span>
				</div>
			{/each}
		</div>
		<ol class="ramp">
			{#each sizes as s (s.name)}
				<li><span class="sw-name">{short(s.name)}</span><span class="sample" style={`font-size:var(${s.name})`}>Two minutes fourteen</span><span class="sw-value">{s.value}</span></li>
			{/each}
		</ol>
		<ul class="pairs">
			{#each tracking as t (t.name)}<li><span class="sw-name">{short(t.name)}</span><span class="tracked" style={`letter-spacing:var(${t.name})`}>Listening</span><span class="sw-value">{t.value}</span></li>{/each}
			{#each weights as w (w.name)}<li><span class="sw-name">{short(w.name)}</span><span style={`font-weight:var(${w.name})`}>Weight {w.value}</span><span class="sw-value">{w.value}</span></li>{/each}
		</ul>
	</section>

	<section data-tokens="space" aria-labelledby="h-space">
		<h2 id="h-space">Space</h2>
		<ul class="bars">
			{#each spaces as s (s.name)}
				<li><span class="sw-name">{short(s.name)}</span><span class="bar" style={`width:var(${s.name})`}></span><span class="sw-value">{s.value}</span></li>
			{/each}
		</ul>
	</section>

	<section data-tokens="radius" aria-labelledby="h-radius">
		<h2 id="h-radius">Radius</h2>
		<ul class="boxes">
			{#each radii as r (r.name)}
				<li><span class="box" style={`border-radius:var(${r.name})`}></span><span class="sw-name">{short(r.name)}</span><span class="sw-value">{r.value}</span></li>
			{/each}
		</ul>
	</section>

	<section data-tokens="elevation" aria-labelledby="h-elevation">
		<h2 id="h-elevation">Elevation</h2>
		<p class="sg-note">Glow is light, and it is budgeted: the reactor core, the current step, the push-to-talk ring. Everything else is flat.</p>
		<ul class="boxes">
			{#each elevation as e (e.name)}
				<li><span class="box" style={`box-shadow:var(${e.name})`}></span><span class="sw-name">{short(e.name)}</span><span class="sw-value">{e.value}</span></li>
			{/each}
		</ul>
	</section>

	<section data-tokens="motion" aria-labelledby="h-motion">
		<h2 id="h-motion">Motion</h2>
		<p class="sg-note">Each bar sweeps at its token's own duration. Under <code>prefers-reduced-motion</code> nothing here moves.</p>
		<ul class="bars motion">
			{#each durations as d (d.name)}
				<li><span class="sw-name">{short(d.name)}</span><span class="sweep" style={`animation-duration:var(${d.name})`}></span><span class="sw-value">{d.value} {tokenMs(d.name)} ms</span></li>
			{/each}
			{#each eases as e (e.name)}
				<li><span class="sw-name">{short(e.name)}</span><span class="sweep eased" style={`animation-timing-function:var(${e.name})`}></span><span class="sw-value">{e.value}</span></li>
			{/each}
		</ul>
		<h3>The reactor's clock</h3>
		<ul class="pairs">
			{#each reactor as r (r.name)}<li><span class="sw-name">{short(r.name)}</span><span class="sw-value">{r.value}</span></li>{/each}
		</ul>
	</section>

	<section data-tokens="chrome" aria-labelledby="h-chrome">
		<h2 id="h-chrome">Chrome</h2>
		<p class="sg-note">
			The measures the frame is built to — how wide a dialog gets, how large the reactor may grow,
			the focus ring — and the one live rule. Reactor II draws no grid and no corner brackets.
		</p>
		<ul class="pairs">
			{#each chromeTokens as c (c.name)}<li><span class="sw-name">{short(c.name)}</span><span class="sw-value">{c.value}</span></li>{/each}
		</ul>
	</section>

	<section data-tokens="glass" aria-labelledby="h-glass">
		<h2 id="h-glass">Glass</h2>
		<p class="sg-note">The material, depth, light and spring groups the glass library consumes (GLASS_HUD §5, §6, §7, §8).</p>
		<ul class="pairs">
			{#each glass as g (g.name)}<li><span class="sw-name">{short(g.name)}</span><span class="sw-value">{g.value}</span></li>{/each}
		</ul>
	</section>
</main>

<style>
	:global(body) { background: var(--jv-stage-ground); }
	/* A reading measure for a reference page: two dialogs and a bit, from the one measure token. */
	.tokens { display: grid; gap: var(--jv-space-6); color: var(--jv-text); max-width: calc(var(--jv-measure-dialog) * 2.25); margin: 0 auto; padding: var(--jv-space-6) var(--jv-space-5) var(--jv-space-7); font-family: var(--jv-font-body); }
	.title { margin: 0; font-size: var(--jv-fs-2xl); font-weight: var(--jv-weight-display); color: var(--jv-text-bright); }
	h2 { margin: 0 0 var(--jv-space-3); font-size: var(--jv-fs-lg); font-weight: var(--jv-weight-label); color: var(--jv-text-bright); }
	h3 { margin: var(--jv-space-4) 0 var(--jv-space-2); font-size: var(--jv-fs-md); font-weight: var(--jv-weight-label); }
	.sg-note { color: var(--jv-text-dim); max-width: 70ch; margin: 0 0 var(--jv-space-4); }
	ul, ol { margin: 0; padding: 0; list-style: none; }
	.sw-name { font-family: var(--jv-font-chrome); font-size: var(--jv-fs-2xs); color: var(--jv-text); }
	.sw-value { font-family: var(--jv-font-chrome); font-size: var(--jv-fs-2xs); color: var(--jv-text-faint); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
	.sw-meta { font-family: var(--jv-font-chrome); font-size: var(--jv-fs-2xs); color: var(--jv-text-dim); text-align: right; }
	.swatches { display: grid; grid-template-columns: repeat(auto-fill, minmax(14rem, 1fr)); gap: var(--jv-space-2); }
	.swatch { display: grid; grid-template-columns: auto 1fr auto; grid-template-areas: 'chip name meta' 'chip value value'; column-gap: var(--jv-space-3); align-items: center; padding: var(--jv-space-2); border: 1px solid var(--jv-line-hair); border-radius: var(--jv-radius-md); background: var(--jv-panel); }
	.swatch .chip { grid-area: chip; width: var(--jv-space-6); height: var(--jv-space-6); border-radius: var(--jv-radius-sm); border: 1px solid var(--jv-line-soft); }
	.swatch .sw-name { grid-area: name; }
	.swatch .sw-value { grid-area: value; }
	.swatch .sw-meta { grid-area: meta; }
	.compact .swatch { grid-template-columns: auto 1fr; grid-template-areas: 'chip name' 'chip value'; }
	.families { display: grid; grid-template-columns: repeat(auto-fit, minmax(18rem, 1fr)); gap: var(--jv-space-4); margin-bottom: var(--jv-space-5); }
	.family { display: grid; gap: var(--jv-space-2); padding: var(--jv-space-4); border: 1px solid var(--jv-line-hair); border-radius: var(--jv-radius-md); background: var(--jv-panel); }
	.specimen { font-size: var(--jv-fs-lg); color: var(--jv-text-bright); margin: 0; }
	.ramp li, .pairs li, .bars li, .boxes li { display: grid; grid-template-columns: 8rem 1fr auto; align-items: center; gap: var(--jv-space-4); padding: var(--jv-space-2) 0; border-bottom: 1px solid var(--jv-line-hair); }
	.ramp .sample { color: var(--jv-text-bright); font-family: var(--jv-font-display); font-weight: var(--jv-weight-display); line-height: 1.2; }
	.tracked { font-family: var(--jv-font-body); font-weight: var(--jv-weight-label); font-size: var(--jv-fs-xs); }
	.bars .bar { display: block; height: var(--jv-space-2); background: var(--jv-accent-deep); border-radius: var(--jv-radius-sm); }
	.boxes .box { display: block; width: var(--jv-space-7); height: var(--jv-space-6); background: var(--jv-panel); border: 1px solid var(--jv-line); }
	.boxes li { grid-template-columns: auto 8rem 1fr; }
	.motion .sweep { display: block; height: var(--jv-space-2); background: linear-gradient(90deg, var(--jv-accent-deep), var(--jv-accent)); transform-origin: left; animation: sweep infinite alternate var(--jv-ease-in-out); border-radius: var(--jv-radius-sm); }
	.motion .sweep.eased { animation-duration: var(--jv-dur-enter); }
	@keyframes sweep {
		from { transform: scaleX(0.05); }
		to { transform: scaleX(1); }
	}
	@media (prefers-reduced-motion: reduce) {
		.motion .sweep { animation: none; transform: scaleX(1); }
	}
</style>
