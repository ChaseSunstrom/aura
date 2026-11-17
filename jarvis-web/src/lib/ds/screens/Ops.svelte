<!--
@component
The Ops board (BUILD_BRIEF §1 "Ops dashboard"; the Build Brief's Ops board): the
eight panes over the room at /ops — compute in isometric bars, the agents'
constellation, routing, the memory globe with its hourly bars, the house plan,
the network, the command terminal and queue, the health grid — and an Activity
pane that asks JARVIS for the audit digest (`audit_query`, summary mode) for
today, yesterday or the week and shows its answer.

Panes swing up into place staggered (the board's paneIn) and lift toward the
viewer on hover; everything stands still under reduced motion. The four states:
loading until the console's links are up, offline when they drop, error with a
retry (ds `State`), and each pane's own empty line.

A full screen of the v2 shell, under its top bar: the board's own header keeps
the live light and the way back to the core.
-->
<script lang="ts">
	import { wornName } from '$lib/ds/theme.svelte';
	import { isoBars, type OpsData, type Tone } from '$lib/hud/ops';
	import { fibonacci, TILT } from '$lib/orb/sphere';
	import { TOKENS } from '$lib/tokens';
	import type { Status } from '$lib/ui/status';
	import State from '../ui/State.svelte';

	interface Props {
		data: OpsData;
		status: Status;
		/** The latest reply, shown under the Activity pane's question. */
		reply: string;
		/** The Activity pane's question went out and its answer is streaming. */
		busy: boolean;
		onask: (question: string) => void;
		onclose: () => void;
		onretry?: () => void;
		onreconnect?: () => void;
		/** The adapter's `word: reason` (statusLabel); '' from an older adapter keeps Live / Offline. */
		health?: string;
	}
	let { data, status, reply, busy, onask, onclose, onretry, onreconnect, health = '' }: Props = $props();

	const faces = $derived(isoBars(data.gpus));
	const busyPct = $derived(data.gpus.length ? Math.round(data.gpus.reduce((s, g) => s + g.usedPct, 0) / data.gpus.length) : 0);
	const agentsAt = $derived(
		data.agents.map((a, i) => {
			const ang = -Math.PI / 2 + (i * 2 * Math.PI) / Math.max(1, data.agents.length);
			return { ...a, x: 210 + Math.cos(ang) * 165, y: 115 + Math.sin(ang) * 82 };
		})
	);
	/** A label cut to [n] letters says so with an ellipsis instead of ending mid-word. */
	const clip = (text: string, n: number) => (text.length > n ? `${text.slice(0, n - 1).trimEnd()}…` : text);
	const sources = $derived(data.routes.map((r, i) => ({ ...r, y: 20 + i * Math.min(35, 190 / Math.max(1, data.routes.length)) })));
	const hourPeak = $derived(Math.max(1, ...data.memory.hourly));
	const LEVEL: Record<string, Tone> = { ok: 'ok', running: 'accent', slow: 'warn', failing: 'danger', idle: 'tick' };
	let asked = $state('');
	const ASK = [
		['Today', 'What did you do today?'],
		['Yesterday', 'What did you do yesterday?'],
		['This week', 'What did you do this week?'],
		// What left the house, from the audit log's outbound rows: the egress list (T-0573) says
		// where the code may reach, this says what was actually sent.
		['Sent today', 'What went out of the house today? Use audit_query with kind=outbound for today and list each one.']
	] as const;

	// The memory globe: the board's 700 Fibonacci points in violet, cyan and amber.
	let globe: HTMLCanvasElement | undefined = $state();
	$effect(() => {
		const cv = globe;
		if (!cv) return;
		const ctx = cv.getContext('2d');
		if (!ctx) return;
		const pts = fibonacci(700, 16);
		const cols = [TOKENS['--jv-violet'], TOKENS['--jv-accent'], TOKENS['--jv-warn']];
		const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
		let raf = 0;
		let onScreen = true;
		const io = new IntersectionObserver((e) => {
			onScreen = e.some((x) => x.isIntersecting);
			if (onScreen && !raf && !reduced) raf = requestAnimationFrame(draw);
		});
		io.observe(cv);
		function draw(now: number) {
			raf = 0;
			if (!cv || !ctx) return;
			const W = cv.width;
			const c = W / 2;
			const R = W * 0.4;
			const a = now * 0.00025;
			const ca = Math.cos(a), sa = Math.sin(a), ct = Math.cos(TILT), st = Math.sin(TILT);
			ctx.clearRect(0, 0, W, W);
			ctx.globalCompositeOperation = 'lighter';
			for (let i = 0; i < 700; i++) {
				const x0 = pts[i * 4], y0 = pts[i * 4 + 1], z0 = pts[i * 4 + 2];
				const x = x0 * ca + z0 * sa;
				let z = -x0 * sa + z0 * ca;
				const y = y0 * ct - z * st;
				z = y0 * st + z * ct;
				const d = (z + 1) / 2;
				const s = 2.4 / (2.4 - z * 0.8);
				ctx.globalAlpha = 0.1 + d * 0.85;
				ctx.fillStyle = cols[pts[i * 4 + 3] ? 2 : i % 9 === 0 ? 1 : 0];
				ctx.beginPath();
				ctx.arc(c + x * R * s, c + y * R * s, 0.6 + d * 1.8, 0, Math.PI * 2);
				ctx.fill();
			}
			ctx.globalAlpha = 1;
			ctx.globalCompositeOperation = 'source-over';
			if (!reduced && onScreen && !document.hidden) raf = requestAnimationFrame(draw);
		}
		const wake = () => {
			if (!raf && !reduced && !document.hidden) raf = requestAnimationFrame(draw);
		};
		document.addEventListener('visibilitychange', wake);
		raf = requestAnimationFrame(draw);
		return () => {
			cancelAnimationFrame(raf);
			io.disconnect();
			document.removeEventListener('visibilitychange', wake);
		};
	});
</script>

<svelte:window
	onkeydown={(e) => {
		if (e.key === 'Escape') onclose();
	}}
/>

<section class="ops" aria-label="Ops" data-testid="screen-ops">
	<header>
		<h1 class="word">Ops</h1>
		<span class="lbl">8 panes</span>
		<span class="live" class:down={status === 'offline' || (health !== '' && health !== 'ready')} data-testid="ops-health"><span class="led" aria-hidden="true"></span>{status === 'offline' ? 'Offline' : health || 'Live'}</span>
		<button type="button" class="btn" onclick={onclose} data-testid="ops-close">Core</button>
	</header>
	<State {status} empty="The panes fill as the console’s links come up." error="the board did not load" onretry={onretry ?? onreconnect} testid="ops-state">
		<div class="grid">
			<section class="pane" style:--i="0">
				<div class="ph"><span class="no">01</span><span class="tt">Compute</span><span class="sub">· VRAM by user, per GPU</span><span class="kpi">{busyPct}% used</span></div>
				{#if faces.length}
					<svg viewBox="0 0 300 200" role="img" aria-label="Isometric bars: each GPU's memory by who holds it">
						{#each faces as f, i (i)}<polygon class="t-{f.tone}" points={f.points} fill-opacity={f.shade} />{/each}
					</svg>
					<div class="pf">{#each data.gpus as g (g.name)}<span>{g.name} · {g.usedPct}%</span>{/each}</div>
				{:else}<p class="empty">No GPUs reported. The model manager lists them once it is reachable.</p>{/if}
			</section>

			<section class="pane" style:--i="1">
				<div class="ph"><span class="no">02</span><span class="tt">Agents</span><span class="sub">· who's working on what</span><span class="kpi">{data.agents.length} agents</span></div>
				{#if agentsAt.length}
					<svg viewBox="0 0 420 230" role="img" aria-label="Agent constellation: {agentsAt.length} agents linked to the core">
						{#each agentsAt as a (a.id)}<line x1="210" y1="115" x2={a.x} y2={a.y} class="link t-{a.tone}" />{/each}
						<circle cx="210" cy="115" r="22" class="ring" /><circle cx="210" cy="115" r="12" class="core" /><circle cx="210" cy="115" r="3" class="dot" />
						{#each agentsAt as a (a.id)}
							<circle cx={a.x} cy={a.y} r="11" class="node t-{a.tone}" />
							<circle cx={a.x} cy={a.y} r="4" class="fill t-{a.tone}" class:pulse={a.working} />
							<text x={a.x} y={a.y + 22} text-anchor="middle" class="name">{a.name}</text>
							<text x={a.x} y={a.y + 32} text-anchor="middle" class="stat">{a.status}</text>
						{/each}
					</svg>
				{:else}<p class="empty">No agents running. A goal or a delegated task starts them.</p>{/if}
				<div class="pf"><span>working {data.agents.filter((a) => a.working).length}</span></div>
			</section>

			<section class="pane" style:--i="2">
				<div class="ph"><span class="no">03</span><span class="tt">Routing</span><span class="sub">· which toolset reaches which model</span><span class="kpi">{data.routes.length} routes</span></div>
				{#if sources.length}
					<svg viewBox="0 0 420 230" role="img" aria-label="Flow from toolsets on the left to the model on the right">
						{#each sources as s (s.from)}
							<path d="M158 {s.y + 11} C227 {s.y + 11}, 227 115, 296 115" class="flowband t-{s.tone}" />
							<path d="M158 {s.y + 11} C227 {s.y + 11}, 227 115, 296 115" class="flow t-{s.tone}" />
							<!-- 150 wide for 19 letters of the chrome face (7 units each): at 104 for 14,
							     "Web Search & S" ran into the box's edge and said nothing (T-0669). -->
							<rect x="8" y={s.y} width="150" height="22" class="box t-{s.tone}"><title>{s.from}</title></rect>
							<text x="14" y={s.y + 15} class="name">{clip(s.from, 19)}</text>
						{/each}
						<rect x="296" y="102" width="116" height="26" class="box t-accent" />
						<text x="302" y="119" class="name">{clip(data.routes[0].to, 15)}</text>
					</svg>
				{:else}<p class="empty">No model in use. Routes appear when a model answers.</p>{/if}
			</section>

			<section class="pane" style:--i="3">
				<div class="ph"><span class="no">04</span><span class="tt">Memory</span><span class="sub">· cards kept</span><span class="kpi">{data.memory.total} kept</span></div>
				<div class="split">
					<div class="legend">
						{#each data.memory.kinds as k (k.k)}<div><span class="t-{k.tone} sq">■</span> {k.k}<span>{k.v}</span></div>{/each}
						{#if !data.memory.kinds.length}<p class="empty">Nothing kept yet.</p>{/if}
					</div>
					<canvas bind:this={globe} width="440" height="440" class="globe" aria-hidden="true"></canvas>
				</div>
				<div class="hours" aria-hidden="true">
					{#each data.memory.hourly as h, i (i)}<span class:now={i === 23} style:height="{Math.round((0.08 + (0.92 * h) / hourPeak) * 100)}%" style:animation-delay="{400 + i * 30}ms"></span>{/each}
				</div>
				<div class="pf"><span>kept / hour · peak {hourPeak}</span><span>−24 h → now</span></div>
			</section>

			<section class="pane" style:--i="4">
				<div class="ph"><span class="no">05</span><span class="tt">House</span><span class="sub">· rooms and lights</span><span class="kpi">{data.rooms.filter((r) => r.on).length} lights on</span></div>
				{#if data.rooms.length}
					<div class="split">
						<div class="plan" aria-hidden="true">
							<div class="floor">
								{#each data.rooms as r (r.name)}<div class="room" class:on={r.on}></div>{/each}
							</div>
						</div>
						<div class="legend">{#each data.rooms as r (r.name)}<div class:lit={r.on}>{r.name}<span>{r.on ? 'on' : 'off'}</span></div>{/each}</div>
					</div>
				{:else}<p class="empty">No lights in the house yet. Add them in House.</p>{/if}
			</section>

			<section class="pane" style:--i="5">
				<div class="ph"><span class="no">06</span><span class="tt">Network</span><span class="sub">· people and devices on the LAN</span><span class="kpi">{data.devices.length} devices</span></div>
				{#if data.devices.length}
					<div class="devices">{#each data.devices as d (d.name)}<div><span class:t-ok={!d.away} class:t-tick={d.away}>●</span> {d.name}<span>{d.detail}</span></div>{/each}</div>
				{:else}<p class="empty">No tracked devices. The house's network scan lists them.</p>{/if}
				<div class="pf"><span>throughput: no source on this console yet</span></div>
			</section>

			<section class="pane" style:--i="6">
				<div class="ph"><span class="no">07</span><span class="tt">Commands</span><span class="sub">· what {wornName()} ran</span><span class="kpi">{data.queue.length} queued</span></div>
				<div class="term" data-testid="ops-terminal">
					{#each data.commands as c, i (i)}
						<div><span class="t-accent">$</span> {c.text} <span class:t-ok={c.state === 'ok'} class:t-warn={c.state === 'running'} class:t-danger={c.state === 'failed'}>{c.state === 'ok' ? '✓' : c.state === 'failed' ? '✕' : '…'}{c.ms ? ` ${(c.ms / 1000).toFixed(1)}s` : ''}</span></div>
					{/each}
					<div><span class="t-accent">$</span> <span class="caret" aria-hidden="true"></span></div>
				</div>
				<div class="queue">
					{#each data.queue as q (q.id)}<span class="qid">{q.id.slice(0, 6)}</span><span>{q.title}</span><span class="qw">{q.when}</span>{/each}
				</div>
			</section>

			<section class="pane" style:--i="7">
				<div class="ph"><span class="no">08</span><span class="tt">Health</span><span class="sub">· every source, last check</span><span class="kpi">{data.health.length} checks</span></div>
				<div class="cells" role="img" aria-label="Health grid: {data.health.filter((h) => h.state === 'ok').length} of {data.health.length} sources ok">
					{#each data.health as h, i (h.name)}<span class="cell t-{LEVEL[h.state]}" title="{h.name}: {h.state}" style:animation-delay="{500 + i * 12}ms"></span>{/each}
				</div>
				<div class="pf"><span>{data.health.filter((h) => h.state === 'failing').map((h) => h.name).join(', ') || 'all sources answering'}</span></div>
			</section>

			<section class="pane wide" style:--i="8" data-testid="ops-activity">
				<div class="ph"><span class="no">09</span><span class="tt">Activity</span><span class="sub">· what {wornName()} did, from the audit log</span></div>
				<div class="asks">
					{#each ASK as [label, q] (label)}
						<button type="button" class="btn" aria-pressed={asked === q} onclick={() => { asked = q; onask(q); }} data-testid="ops-ask">{label}</button>
					{/each}
				</div>
				{#if asked}
					<p class="lbl">{asked}</p>
					<p class="digest" aria-live="polite" data-testid="ops-digest">{reply || (busy ? 'Reading the audit log…' : '')}</p>
				{:else}<p class="empty">Choose a day: {wornName()} reads its audit log and summarises it, the kill switch, deploys, settings and messages first. Sent today lists only what went out of the house.</p>{/if}
			</section>
		</div>
	</State>
</section>

<style>
	.ops {
		flex: 1;
		min-inline-size: 0;
		color: var(--jv-text);
		padding-block-end: var(--jv-space-5);
	}
	header {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: var(--jv-space-3) var(--jv-space-5);
		padding: var(--jv-space-4) var(--jv-space-5);
		border-block-end: 1px solid var(--jv-line-soft);
	}
	.word {
		margin: 0;
		font-size: var(--jv-fs-lg);
		font-weight: inherit;
		font-family: var(--jv-font-display);
		letter-spacing: var(--jv-track-logo);
		text-transform: uppercase;
		color: var(--jv-text-bright);
	}
	.lbl,
	.sub,
	.kpi,
	.pf,
	.btn,
	.live,
	.tt {
		font-size: var(--jv-fs-2xs);
		letter-spacing: var(--jv-track-label);
		text-transform: uppercase;
		color: var(--jv-text-faint);
	}
	.live {
		margin-inline-start: auto;
		display: inline-flex;
		align-items: center;
		gap: var(--jv-space-2);
		padding: var(--jv-space-1) var(--jv-space-3);
		border: 1px solid var(--jv-ok);
		border-radius: var(--jv-radius-pill);
		color: var(--jv-ok);
	}
	.live.down {
		border-color: var(--jv-warn);
		color: var(--jv-warn);
	}
	.led {
		width: var(--jv-space-2);
		height: var(--jv-space-2);
		border-radius: var(--jv-radius-pill);
		background: currentColor;
	}
	.btn {
		min-height: var(--jv-touch);
		padding: 0 var(--jv-space-4);
		border: 1px solid var(--jv-material-control-edge);
		border-radius: var(--jv-radius-sm);
		background: transparent;
		color: var(--jv-text);
		letter-spacing: var(--jv-track-control);
		text-transform: uppercase;
		cursor: pointer;
	}
	.btn:hover,
	.btn[aria-pressed='true'] {
		background: var(--jv-wash);
		color: var(--jv-accent);
	}
	.grid {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(min(calc(var(--jv-measure-dialog) * 0.8), 100%), 1fr));
		gap: var(--jv-space-3);
		padding: var(--jv-space-4) var(--jv-space-5);
	}
	.pane {
		position: relative;
		display: flex;
		flex-direction: column;
		min-width: 0;
		background: var(--jv-material-panel-solid);
		border: 1px solid var(--jv-material-panel-edge);
		border-radius: var(--jv-radius-md);
		transition:
			transform var(--jv-dur-slow) var(--jv-ease-out),
			border-color var(--jv-dur-slow) var(--jv-ease-out),
			box-shadow var(--jv-dur-slow) var(--jv-ease-out);
		animation: jv-pane-in var(--jv-dur-enter) var(--jv-ease-out) both;
		animation-delay: calc(var(--i) * var(--jv-stagger-row));
	}
	.pane.wide {
		grid-column: 1 / -1;
	}
	.pane:hover {
		transform: perspective(var(--jv-perspective)) translateZ(var(--jv-space-4)) rotateX(calc(var(--jv-tilt) * 0.2));
		border-color: var(--jv-material-panel-edge-hover);
		box-shadow: var(--jv-elev-panel);
	}
	.pane::before,
	.pane::after {
		content: '';
		position: absolute;
		width: var(--jv-bracket);
		height: var(--jv-bracket);
		border: 0 solid var(--jv-accent);
		pointer-events: none;
	}
	.pane::before {
		inset-block-start: -1px;
		inset-inline-start: -1px;
		border-block-start-width: 1px;
		border-inline-start-width: 1px;
	}
	.pane::after {
		inset-block-end: -1px;
		inset-inline-end: -1px;
		border-block-end-width: 1px;
		border-inline-end-width: 1px;
	}
	@keyframes jv-pane-in {
		from {
			opacity: 0;
			transform: perspective(var(--jv-perspective)) rotateX(calc(var(--jv-tilt) * -2.8)) translateY(var(--jv-space-6));
			clip-path: inset(0 0 100% 0);
		}
		to {
			clip-path: inset(0);
		}
	}
	.ph {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: var(--jv-space-2);
		padding: var(--jv-space-3) var(--jv-space-4);
		border-block-end: 1px solid var(--jv-line-soft);
	}
	.no {
		font-size: var(--jv-fs-2xs);
		font-weight: var(--jv-weight-strong);
		padding: 0 var(--jv-space-1);
		background: var(--jv-accent);
		color: var(--jv-accent-ink);
	}
	.tt {
		color: var(--jv-text-bright);
	}
	.sub {
		text-transform: none;
		letter-spacing: 0;
	}
	.kpi {
		margin-inline-start: auto;
		color: var(--jv-accent);
		letter-spacing: var(--jv-track-control);
		text-transform: uppercase;
	}
	.pf {
		display: flex;
		flex-wrap: wrap;
		justify-content: space-between;
		gap: var(--jv-space-3);
		margin-block-start: auto;
		padding: var(--jv-space-2) var(--jv-space-4);
		border-block-start: 1px solid var(--jv-line-soft);
		text-transform: none;
		letter-spacing: 0;
	}
	.empty {
		margin: 0;
		padding: var(--jv-space-4);
		font-size: var(--jv-fs-sm);
		color: var(--jv-text-dim);
	}
	svg {
		display: block;
		width: 100%;
		padding: var(--jv-space-2) var(--jv-space-3);
	}
	.t-accent { --c: var(--jv-accent); color: var(--jv-accent); }
	.t-violet { --c: var(--jv-violet); color: var(--jv-violet); }
	.t-ok { --c: var(--jv-ok); color: var(--jv-ok); }
	.t-warn { --c: var(--jv-warn); color: var(--jv-warn); }
	.t-danger { --c: var(--jv-danger); color: var(--jv-danger); }
	.t-tick { --c: var(--jv-tick); color: var(--jv-tick); }
	polygon {
		fill: var(--c);
		stroke: var(--jv-bg);
		stroke-width: 0.5;
	}
	.link {
		stroke: var(--c);
		stroke-opacity: 0.5;
		stroke-dasharray: 3 5;
		animation: jv-flow calc(var(--jv-amb-blink-slow) * 1) linear infinite;
	}
	.ring {
		fill: none;
		stroke: var(--jv-accent);
		stroke-opacity: 0.4;
	}
	.core {
		fill: none;
		stroke: var(--jv-accent);
		stroke-width: 3;
	}
	.dot {
		fill: var(--jv-text-bright);
	}
	.node {
		fill: var(--jv-bg);
		stroke: var(--c);
		stroke-opacity: 0.6;
	}
	.fill {
		fill: var(--c);
		transform-box: fill-box;
		transform-origin: center;
	}
	.pulse {
		animation: jv-pulse calc(var(--jv-amb-blink-slow) * 0.85) ease-in-out infinite;
	}
	@keyframes jv-pulse {
		50% {
			opacity: 0.5;
			transform: scale(1.6);
		}
	}
	@keyframes jv-flow {
		to {
			stroke-dashoffset: -40;
		}
	}
	text {
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
	}
	.name {
		fill: var(--jv-text-dim);
	}
	.stat {
		fill: var(--jv-text-faint);
	}
	.flowband {
		fill: none;
		stroke: var(--c);
		stroke-opacity: 0.38;
		stroke-width: 6;
	}
	.flow {
		fill: none;
		stroke: var(--c);
		stroke-width: 1.2;
		stroke-dasharray: 4 36;
		animation: jv-flow calc(var(--jv-amb-blink-slow) * 1) linear infinite;
	}
	.box {
		fill: var(--jv-bg);
		stroke: var(--c);
		stroke-opacity: 0.7;
	}
	.split {
		display: grid;
		grid-template-columns: minmax(0, 1fr) minmax(0, 1.2fr);
		gap: var(--jv-space-3);
		align-items: center;
		padding: var(--jv-space-3) var(--jv-space-4);
	}
	.legend {
		display: flex;
		flex-direction: column;
		gap: var(--jv-space-2);
		font-size: var(--jv-fs-xs);
		color: var(--jv-text-dim);
	}
	.legend div,
	.devices div {
		display: flex;
		justify-content: space-between;
		gap: var(--jv-space-2);
	}
	.legend .lit {
		color: var(--jv-accent);
	}
	.globe {
		width: 100%;
		max-height: calc(var(--jv-space-7) * 4);
		aspect-ratio: 1;
		justify-self: center;
	}
	.hours {
		display: flex;
		align-items: flex-end;
		gap: var(--jv-rule-live);
		height: calc(var(--jv-space-7) + var(--jv-space-2));
		padding: 0 var(--jv-space-4) var(--jv-space-2);
	}
	.hours span {
		flex: 1;
		background: var(--jv-accent);
		transform-origin: bottom;
		animation: jv-bar-up var(--jv-dur-slow) var(--jv-ease-out) both;
	}
	.hours span.now {
		background: var(--jv-text-bright);
	}
	@keyframes jv-bar-up {
		from {
			transform: scaleY(0);
		}
	}
	.plan {
		display: flex;
		align-items: center;
		justify-content: center;
		height: calc(var(--jv-space-7) * 4);
		perspective: calc(var(--jv-perspective) * 0.65);
	}
	.floor {
		display: grid;
		grid-template-columns: repeat(3, minmax(0, 1fr));
		gap: var(--jv-rule-live);
		width: 80%;
		transform: rotateX(56deg) rotateZ(-40deg);
		transform-style: preserve-3d;
	}
	.room {
		aspect-ratio: 1.3;
		border: 1px solid var(--jv-material-panel-edge-hover);
		background: var(--jv-material-control-solid);
		box-shadow: var(--jv-rule-live) var(--jv-rule-live) 0 var(--jv-wash);
	}
	.room.on {
		border-color: var(--jv-accent);
		background: var(--jv-wash-strong);
		box-shadow:
			inset 0 0 var(--jv-space-5) var(--jv-glow),
			0 0 var(--jv-space-4) var(--jv-glow);
	}
	.devices {
		display: grid;
		grid-template-columns: repeat(2, minmax(0, 1fr));
		gap: 0 var(--jv-space-5);
		padding: var(--jv-space-3) var(--jv-space-4);
		font-size: var(--jv-fs-xs);
		color: var(--jv-text-dim);
	}
	.devices div {
		padding-block: var(--jv-space-1);
		border-block-start: 1px solid var(--jv-line-hair);
	}
	.term {
		margin: var(--jv-space-3) var(--jv-space-4) 0;
		padding: var(--jv-space-3);
		background: var(--jv-surface-sunken);
		border: 1px solid var(--jv-material-panel-edge);
		font-size: var(--jv-fs-xs);
		line-height: 1.8;
		overflow-wrap: anywhere;
	}
	.caret {
		display: inline-block;
		width: var(--jv-space-2);
		height: var(--jv-rel-caret);
		vertical-align: text-bottom;
		background: var(--jv-accent);
		animation: jv-caret var(--jv-amb-blink-slow) steps(1) infinite;
	}
	@keyframes jv-caret {
		50% {
			opacity: 0;
		}
	}
	.queue {
		display: grid;
		grid-template-columns: auto minmax(0, 1fr) auto;
		gap: var(--jv-space-1) var(--jv-space-3);
		padding: var(--jv-space-3) var(--jv-space-4);
		font-size: var(--jv-fs-xs);
	}
	.qid,
	.qw {
		color: var(--jv-text-faint);
	}
	.cells {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(var(--jv-space-5), 1fr));
		gap: var(--jv-rule-live);
		padding: var(--jv-space-3) var(--jv-space-4);
	}
	.cell {
		aspect-ratio: 1;
		background: var(--c);
		animation: jv-rise var(--jv-dur-enter) var(--jv-ease-out) both;
	}
	.cell.t-danger,
	.cell.t-warn,
	.cell.t-accent {
		box-shadow: 0 0 var(--jv-space-2) var(--c);
	}
	.cell.t-ok {
		opacity: 0.55;
	}
	@keyframes jv-rise {
		from {
			opacity: 0;
			transform: translateY(var(--jv-space-3));
		}
	}
	.asks {
		display: flex;
		flex-wrap: wrap;
		gap: var(--jv-space-2);
		padding: var(--jv-space-3) var(--jv-space-4);
	}
	.pane > .lbl {
		margin: 0;
		padding: 0 var(--jv-space-4);
	}
	.digest {
		margin: 0;
		padding: var(--jv-space-2) var(--jv-space-4) var(--jv-space-4);
		font-size: var(--jv-fs-sm);
		line-height: 1.6;
		white-space: pre-wrap;
		color: var(--jv-text-bright);
	}
	@media (prefers-reduced-motion: reduce) {
		.pane,
		.link,
		.flow,
		.pulse,
		.hours span,
		.cell,
		.caret {
			animation: none;
		}
		.pane,
		.pane:hover {
			transition: none;
			transform: none;
		}
	}
</style>
