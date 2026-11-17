<!--
@component
The v2 console (docs/design/v2/Core2.dc.html): the core on its stage, what
the moment needs coming out of it and going back, and three ways out of the
room -- "All apps" (the launcher), "Search or command" (⌘K) and "Devices" --
where the legacy console had a rail, a dock and a window manager.

The shell owns the one decision a scene change is: the links and the voice
say what JARVIS is doing (`scene.ts`), the stage machine (`ds/stage.ts`) turns
that into a scene that leaves whole before the next enters, and this file
ticks it while it moves and stops when it has landed. Everything else is
handed down: the stage draws, the card answers through `answer`, the sheets
navigate.

It keeps the contracts other surfaces lean on: `jarvis:push-to-talk` (the
desktop app), the popup's title mood (`popupTitle`), the phone's
`JarvisPhone` bridge, `hud.say`, and `jarvis:orb-demo` for screenshots.

Does not cover: what a full screen shows (`ds/screens/`), the car's display
(`/car`), the lock, setup and enrol screens, or the open mic's hangover from
the settings (the controller's default holds until Settings writes it).
-->
<script lang="ts">
	import { wornName } from '$lib/ds/theme.svelte';
	import { onMount, untrack } from 'svelte';
	import { on } from 'svelte/events';

	import { goto } from '$app/navigation';
	import { Label, MS, POSES, createStage, go, ms, prefersReducedMotion, view, watchReducedMotion, type Scene, type StageView } from '$lib/ds';
	import { liveSteps } from '$lib/ds/activity/live';
	import { useHermes } from '$lib/hermes/store.svelte';
	import { approvalSourceOf, chatTurns, contentPanels, goalView, jobsView, readingsView, skyView, timelineCalls } from '$lib/hud/data';
	import { sayInCar } from '$lib/hud/car.svelte';
	import { faviconFor, themeIcon, titleFor } from '$lib/hud/favicon';
	import { Feeds } from '$lib/hud/feeds.svelte';
	import { useConsole } from '$lib/hud/links.svelte';
	import type { PaletteItem } from '$lib/hud/palette';
	import { NOT_A_PHONE, NOT_A_PHONE_AFTER_MS, PHONE_ACTION_EVENT, PHONE_EVENTS, ROUTINES_EVENT, onAndroid, onCarPage, phoneBridge, phoneResultArg } from '$lib/hud/phone';
	import { DICTATED_EVENT, DICTATE_EVENT, DICTATED_SHOWN_MS, acknowledges, dictatedSegment, pageDictation } from '$lib/hud/dictation';
	import { NOTICE_MS, popupMood, popupTitle } from '$lib/hud/popupMood';
	import { VoiceController, thisSurface } from '$lib/hud/voice.svelte';
	import { companionFor } from '$lib/orb/companions';
	import { IDENTITIES, PERSONAS, switched, themeFrom, tintFor, type Persona } from '$lib/orb/personas';
	import { THEMES, THEME_IDS } from '$lib/themes';
	import { baseOf, type Pack } from '../pack';
	import Starfield from '../Starfield.svelte';
	import ThemeTile from '../ThemeTile.svelte';
	import { askDraft, chooseTheme, device, starfieldWorn, theme, voiceFor, wearTheme, type Draft, type Worn } from '../theme.svelte';
	import Button from '../ui/Button.svelte';
	import { stateFrom, wordOf } from '$lib/reactor';
	import type { SessionSummary } from '$lib/hermes/protocol';
	import { reportErrors } from '$lib/hud/errorReport';
	import { PrivateChat } from '$lib/hud/private.svelte';
	import type { Status } from '$lib/ui/status';
	import Approvals from '../screens/Approvals.svelte';
	import { secretChange, undoSaid, type ProposalsIo } from '../screens/settings-proposals';
	import { MEMORY_PROPOSALS_EVENT, openOf } from '$lib/hermes/memory-proposals';
	import { memoryCards } from '../screens/memory-proposals';
	import Chat from '../screens/Chat.svelte';
	import Ops from '../screens/Ops.svelte';
	import Board from '../screens/Board.svelte';
	import Models from '../screens/Models.svelte';
	import Enrol from '../screens/Enrol.svelte';
	import Lock from '../screens/Lock.svelte';
	import Setup from '../screens/Setup.svelte';
	import { LOCK_AFTER_KEY, LOCK_VOICE_KEY, lockEndsSession, parseLockAfter, shouldLock } from '$lib/hud/lock';
	import { crewFrom } from '$lib/hud/data/crew';
	import { modelView } from '$lib/hud/data/model';
	import { statusLabel } from '$lib/hermes/status';
	import { gpusView } from '$lib/hud/data/models';
	import { opsData } from '$lib/hud/ops';
	import Devices from '../screens/Devices.svelte';
	import Integrations from '../screens/Integrations.svelte';
	import Screen from '../screens/Screen.svelte';
	import Settings from '../screens/Settings.svelte';
	import Texts from '../screens/Texts.svelte';
	import Walkthrough from '../screens/Walkthrough.svelte';
	import ApprovalCard from './ApprovalCard.svelte';
	import type { Ask } from './approval';
	import { askWhyLine, asksFrom, batchable, grantsFrom } from './approval';
	import DevicesSheet from './DevicesSheet.svelte';
	import Palette from './Palette.svelte';
	import Popup from './Popup.svelte';
	import type { IslandState } from './island';
	import Car from '../screens/Car.svelte';
	import { projectionsFor, stripFor } from './projections';
	import { loadTiles, saveTiles, swap } from './tiling';
	import { APPS, screenOf, titleOf, type Screen as ScreenId } from './routes';
	import { SCENE_OF, demoScene, sceneFor } from './scene';
	import { VISUAL_HOLD_MS, visualsHeld } from '$lib/hermes/hud';
	import { close as closeSetup, fresh as freshSetup, parse as parseSetup, serialize as serializeSetup, shown as setupShown, type Progress as SetupProgress } from '../walkthrough/model';
	import Stage from './Stage.svelte';
	import Sheet from '../ui/Sheet.svelte';
	import Grab from '../ui/Grab.svelte';
	import { dragToClose } from '../ui/drag';

	interface Props {
		path: string;
		/** A Hermes session to open (`?session=`). */
		session?: string;
	}
	let { path, session = '' }: Props = $props();

	const screen = $derived(screenOf(path));
	const popup = typeof location !== 'undefined' && new URLSearchParams(location.search).get('popup') === '1';
	/** How often the phone is asked whether it is in the car (`carLinked`). */
	const CAR_POLL_MS = 3000;
	/** How long the console's answer about this page's session is waited for before the shell goes on without it. */
	const GATE_WAIT_MS = 8000;

	// --- the links -------------------------------------------------------------

	const hermes = useHermes();
	const house = useConsole();
	const hermesSnap = $derived(hermes?.view.snap ?? null);
	const hud = $derived(hermesSnap?.hud ?? null);
	const houseSnap = $derived(house?.snap ?? null);
	const feeds = new Feeds();
	const voice = hermes ? new VoiceController(hermes.link) : null;
	/** A conversation kept in this page only, never Hermes' (T-0462, docs/explanation/private-chats.md). */
	const privateChat = new PrivateChat();
	if (voice) voice.privately = privateChat;
	// Uncaught errors reach the adapter's log, never during a private chat (T-0475).
	onMount(() => reportErrors(() => privateChat.on));

	// The stage reads the sky (moon, next pass), the readings and the next job at rest; the
	// card reads the house's and the phone's requests whatever is open.
	// A full screen adds what it reads, and drops it when the person leaves.
	const SCREEN_FEEDS: Partial<Record<typeof screen, readonly string[]>> = {
		integrations: ['integrations', 'capabilities'],
		settings: ['voice', 'capabilities', 'devices'],
		texts: ['messages'],
		devices: ['devices'],
		setup: ['capabilities', 'devices'],
		chat: ['models'],
		ops: ['models', 'memory', 'model', 'automations', 'integrations'],
		car: ['settings']
	};
	// The popup also reads the house's notices, to pull a fresh one up.
	$effect(() => {
		const more = SCREEN_FEEDS[screen] ?? [];
		untrack(() => feeds.activate(['approvals', 'jobs', 'telemetry', ...more, ...(popup ? ['notifications'] : [])]));
	});
	$effect(() => {
		const conn = houseSnap?.status === 'connected' ? (house?.link.connection ?? null) : null;
		const link = hermesSnap?.status === 'connected' ? (hermes?.link ?? null) : null;
		untrack(() => feeds.setSources({ conn, hermes: link }));
	});
	const client = $derived(houseSnap?.status === 'connected' ? (house?.link.connection?.client ?? null) : null);

	// --- who is primary (ADR-0043) ---------------------------------------------

	const primary = $derived.by((): Persona => {
		// Before the adapter says, the default persona (the contract's `default`, AURA).
		const name = (hud?.persona || hermesSnap?.report?.persona?.active || '').toLowerCase();
		return PERSONAS.find((p) => p.toLowerCase() === name) ?? PERSONAS[0];
	});
	const identity = $derived(switched(primary));
	// The persona's voice, or the one this device chose in Settings' A/B for the worn theme.
	$effect(() => {
		if (voice) voice.voice = voiceFor(theme.id, identity.voice, device.voice);
	});
	// The worn theme's name is what the open mic waits for (T-0875); a draft wears its base's.
	$effect(() => {
		const w = THEMES[theme.id]?.wake;
		const word = w ? { model: w.model, threshold: w.threshold } : null;
		untrack(() => voice?.setWakeWord(word));
	});
	// The theme the house says is worn: a shipped pack by id, or a drafted one applied by
	// voice. The draft rides on `theme.apply`; after a reload only its id comes back (the
	// status report), so the house's copy is fetched.
	const syncedId = $derived((hud?.theme || hermesSnap?.report?.persona?.theme || '').toLowerCase());
	let fetched = $state.raw<Draft | null>(null);
	// The id to fetch, or ''. A string, so the effect below runs when it changes and not on
	// every HUD event (reading `hud` there fetched the pack once per event: five on a reload).
	const toFetch = $derived.by(() => {
		const id = syncedId;
		const carried = hud?.themeApplied?.id === id && !!hud.themeApplied.pack;
		return !id || carried || (THEME_IDS as readonly string[]).includes(id) ? '' : id;
	});
	$effect(() => {
		const id = toFetch;
		if (!id || untrack(() => fetched?.id) === id) return;
		// Asked again while the house cannot answer: one failed fetch wore the base theme
		// until the next theme.apply.
		return askDraft(id, (d) => (fetched = d));
	});
	// A pack carried by theme.apply is newer than the copy fetched before it: drop that copy, or
	// a re-draft under the same id came back by id alone as the old one.
	// Keyed on the apply itself: HUD events re-spread the state but keep this object, and a
	// persona change sets the theme without one, so a draft fetched after it is not dropped.
	const applied = $derived(hud?.themeApplied ?? null);
	$effect(() => {
		const a = applied;
		if (a?.pack && untrack(() => fetched?.id) === a.id) fetched = null;
	});
	const synced = $derived.by((): Worn => {
		const applied = hud?.themeApplied;
		const draft: Draft | null =
			applied?.pack && applied.id === syncedId
				? { id: applied.id, name: applied.pack.name, pack: applied.pack }
				: fetched?.id === syncedId
					? fetched
					: null;
		if (draft) return { id: baseOf(draft.pack), draft };
		return { id: themeFrom({ theme: syncedId, persona: primary }), draft: null };
	});
	// What this console wears: this device's own pick beats the house. `data-theme`
	// re-points the tokens and the core its renderer; a change dissolves and tweens.
	const worn = $derived(chooseTheme(synced, device.theme));
	$effect(() => {
		const w = worn;
		untrack(() => wearTheme(w.id, w.draft));
	});
	// A theme drafted by voice, shown on every screen and worn by none until applied.
	let dismissed = $state.raw<Pack | null>(null);
	const preview = $derived.by((): Draft | null => {
		const p = hud?.themePreview;
		return p && p.pack !== dismissed ? { id: p.id, name: p.name, pack: p.pack } : null;
	});
	const applyDraft = (d: Draft) => ask(`Apply the drafted theme ${d.name} (${d.id}).`);
	const sample = (v: string) => voice?.say(THEMES[theme.id].persona.strings.ready, v);

	// --- what JARVIS is doing --------------------------------------------------

	const reactor = $derived(
		stateFrom({
			hud,
			// No client yet (before mount) is the first connect not begun: warming, not offline.
			link: hermesSnap?.status ?? 'idle',
			report: hermesSnap?.report ?? null,
			speaking: voice?.speaking ?? false,
			// Woken by its name, it listens for the command as it does while recording (T-0769).
			recording: Boolean(voice?.recording || voice?.awake),
			transcribing: voice?.transcribing ?? false
		}).state
	);
	const goal = $derived(goalView(hud?.goal));
	const asks = $derived(asksFrom(hud, feeds.pending.data ?? []));
	// A house or phone request is not in Hermes's state, so the reactor does not
	// see it: at rest it is still something waiting on the person.
	const reactorNow = $derived(asks.length && (reactor === 'idle' || reactor === 'goal') ? 'attention' : reactor);
	// What a turn drew stays out at rest for a while (T-1472): a list is worked through after the
	// last word, and a frame another session shared arrives while nothing runs here. It goes
	// back when the hold runs out, on Escape, or with the next turn (the reducer clears it).
	/** The stamp of the visuals the person sent back; a redraw is a new stamp and comes out again. */
	let sentBack = $state<number | null>(null);
	/** Moved on when the hold runs out, so `holding` reads the clock again. */
	let holdEnded = $state(0);
	const holding = $derived.by(() => {
		void holdEnded;
		const at = hud?.turn.visualsAt ?? null;
		return at !== null && at !== sentBack && visualsHeld(hud);
	});
	$effect(() => {
		const at = hud?.turn.visualsAt ?? null;
		if (at === null) return;
		const left = at + VISUAL_HOLD_MS - Date.now();
		if (left <= 0) return;
		const timer = setTimeout(() => (holdEnded += 1), left);
		return () => clearTimeout(timer);
	});
	const live = $derived(sceneFor({ reactor: reactorNow, goal: goal !== null, visual: holding }));
	const tint = $derived(tintFor(primary, reactorNow));

	/** A scene or state forced for a screenshot or a test (`jarvis:orb-demo`); null follows the links. */
	let demo = $state<ReturnType<typeof demoScene>>(null);
	// Up once mounted, with the demo listener attached: until then a `jarvis:orb-demo` is lost, and
	// a test that opened the server-rendered shell and forced a scene saw it stay idle (T-0831).
	let ready = $state(false);
	const demoTarget = $derived(demo === null ? null : Object.hasOwn(SCENE_OF, demo) ? SCENE_OF[demo as keyof typeof SCENE_OF] : (demo as Scene));
	const demoLook = $derived(demo === null ? null : Object.hasOwn(SCENE_OF, demo) ? sceneFor({ reactor: demo as keyof typeof SCENE_OF }).core : POSES[demo as Scene].state);
	const target = $derived(demoTarget ?? live.scene);
	const coreState = $derived(demoLook ?? live.core);

	// --- the stage machine -----------------------------------------------------
	//
	// A plain variable: the machine is advanced here and read through `v`, so a
	// frame is one assignment and nothing else re-derives from the clock.
	let stage = createStage('idle', 0);
	let v = $state.raw<StageView>(view(stage, 0));
	let frame = 0;
	function tick(): void {
		frame = 0;
		v = view(stage, performance.now());
		if (!v.settled) frame = requestAnimationFrame(tick);
	}
	function restart(reduced: boolean): void {
		// Reduced motion: both phases take no time, so a scene changes at once.
		stage = createStage(stage.target, performance.now(), { exit: ms('exit', reduced), appear: ms('appear', reduced) });
		tick();
	}
	$effect(() => {
		const to = target;
		untrack(() => {
			const next = go(stage, to, performance.now());
			if (next === stage) return;
			stage = next;
			if (!frame) tick();
		});
	});
	onMount(() => {
		// Read here, not at the top: on the server there is no media query and the
		// helper says "reduce", which would freeze a browser that never asked.
		if (prefersReducedMotion()) restart(true);
		const unwatch = watchReducedMotion(restart);
		const onDemo = (e: Event) => (demo = demoScene((e as CustomEvent<unknown>).detail));
		window.addEventListener('jarvis:orb-demo', onDemo);
		ready = true;
		return () => {
			unwatch();
			window.removeEventListener('jarvis:orb-demo', onDemo);
			cancelAnimationFrame(frame);
			frame = 0;
		};
	});

	// --- what the stage shows --------------------------------------------------

	const turns = $derived(chatTurns(hud?.messages ?? []));
	const lastReply = $derived.by(() => {
		const i = turns.findLastIndex((t) => t.role === 'you');
		return turns.slice(i + 1).filter((t) => t.role === 'jarvis').map((t) => t.text).join('\n\n');
	});
	/** The persona a subagent wears: the same hash for its projection and its voice, never the primary. */
	const wornBy = (id: string) => IDENTITIES[companionFor(id, primary).name as Persona];
	// A running subagent is someone, so it comes out of the core as its persona;
	// a background process is a row, not an identity, and stays out.
	const helpers = $derived(
		(hud?.background ?? [])
			.filter((r) => r.kind === 'subagent' && r.state === 'running')
			.map((r) => ({ id: r.id, who: wornBy(r.id)?.name ?? 'Helper', goal: r.label }))
	);
	// The owner's arrangement of each scene's panels on this device (T-0951).
	const tiles = $state(loadTiles());
	const shown = $derived(
		projectionsFor(v.scene, {
			sky: skyView(feeds.sky.data),
			readings: readingsView(feeds.readings.data),
			jobs: jobsView(feeds.scheduled.data, feeds.cron.data),
			steps: liveSteps(hud),
			content: contentPanels(hud?.panels ?? []),
			visuals: hud?.turn.visuals,
			helpers
		}, tiles[v.scene])
	);
	const strip = $derived(stripFor(goal));
	function retile(id: string, tile: number): void {
		tiles[v.scene] = swap(Object.fromEntries(shown.map((s) => [s.id, s.tile])), id, tile);
		saveTiles(tiles);
	}

	// The Ops board reads the machines, the helpers, the routing and the house,
	// and only while it is open: nine panes of derivation on every turn token is
	// work the core never shows. Read while its panel slides out too, or it goes blank first.
	const ops = $derived.by(() => {
		if (panel !== 'ops') return null;
		const model = modelView(hermesSnap, feeds.model.data.toolsets, feeds.model.data.lists);
		const named = { models: feeds.models, memory: feeds.memory, model: feeds.model, jobs: feeds.scheduled, cron: feeds.cron, readings: feeds.readings, sky: feeds.sky, notices: feeds.moments, approvals: feeds.pending, routines: feeds.automations, integrations: feeds.integrations };
		return opsData({
			gpus: gpusView(feeds.models.data.gpus),
			crew: crewFrom(hud?.background ?? [], Date.now()).nodes,
			model: model.model,
			toolsets: model.toolsets.map((t) => ({ name: t.label, enabled: t.enabled })),
			memory: feeds.memory.data,
			states: (houseSnap?.states ?? []) as { entity_id: string; state: string }[],
			calls: timelineCalls(hud),
			jobs: jobsView(feeds.scheduled.data, feeds.cron.data),
			feeds: Object.entries(named).map(([name, f]) => ({ name, status: f.status }))
		});
	});
	const opsStatus = $derived.by((): Status => {
		const links = [hermesSnap?.status, houseSnap?.status];
		if (links.includes('connected')) return 'ready';
		return links.some((s) => s === 'connecting' || s === 'reconnecting' || s === 'idle' || s === undefined) ? 'loading' : 'offline';
	});

	// --- the status line -------------------------------------------------------

	let status = $state('');
	let statusTimer: ReturnType<typeof setTimeout> | undefined;
	function say(text: string): void {
		status = text;
		clearTimeout(statusTimer);
		statusTimer = setTimeout(() => (status = ''), NOTICE_MS);
	}
	const reason = (e: unknown) => (e instanceof Error ? e.message : String(e));

	/**
	 * A screen's change handed to JARVIS: connecting, installing and levels are
	 * tools behind the approval matrix, so the card is where the person says yes.
	 */
	function ask(text: string): void {
		if (!voice) {
			say(`${wornName()} is not connected, so nothing was asked.`);
			return;
		}
		voice.resetClock();
		void voice.submit(text);
		say(`Asked ${wornName()}. Say yes on the card when it asks.`);
	}
	async function pickPersona(p: Persona): Promise<void> {
		if (!hermes) return say(`${wornName()} is not connected.`);
		const outcome = await hermes.link.switchPersona(p);
		if (outcome.kind === 'error') say(`Couldn’t switch to ${IDENTITIES[p].name}: ${outcome.message}`);
	}
	/** Whether the screen that lists requests can say anything: a link that raises them is up, or coming. */
	const asksStatus = $derived.by(() => {
		const links = [hermesSnap?.status, houseSnap?.status];
		if (links.includes('connected')) return 'ready' as const;
		return links.some((s) => s === 'connecting' || s === 'reconnecting' || s === 'idle') ? ('loading' as const) : ('offline' as const);
	});

	/** Esc in the working scene: the person takes over, and the goal stops where it is. */
	async function takeover(): Promise<void> {
		if (!hermes) {
			say(`Took over. ${wornName()} is not connected, so nothing was running.`);
			return;
		}
		const outcome = await hermes.link.dispatch('goal', 'pause');
		say(outcome.kind === 'error' ? `Couldn’t pause the goal: ${outcome.message}` : 'Took over — the goal is paused.');
	}

	/** The guard's standing "always" answers (T-0570), listed and revoked through its own command. */
	async function guard(arg: string): Promise<string> {
		if (!hermes) throw new Error(`${wornName()} is not connected`);
		const outcome = await hermes.link.dispatchPlugin(arg, 'guard');
		if (outcome.kind === 'error') throw new Error(outcome.message);
		return outcome.kind === 'output' ? outcome.output.trim() : '';
	}
	const loadGrants = async () => grantsFrom(await guard('grants'));
	async function revokeGrant(baseKey: string): Promise<void> {
		const out = await guard(`revoke ${baseKey}`);
		if (!out.startsWith('revoked:')) throw new Error(out || 'the guard did not answer');
	}

	/** The settings plugin's waiting changes (T-1316). One object: a new one per render would re-read the list. */
	const proposalsIo: ProposalsIo = {
		load: () => hermes!.link.settingsProposals(),
		apply: (id) => hermes!.link.settingsApply(id),
		discard: (id) => hermes!.link.settingsDiscard(id)
	};

	/** What waits to be remembered, and its Drop (T-1503). One object, as `proposalsIo`. */
	const memoryIo = {
		load: () => hermes!.link.memoryProposals(),
		drop: (id: string) => hermes!.link.memoryProposalDrop(id)
	};
	/** Counts `memory.proposals` heard, so the Approvals screen reads that list again. */
	let memoryHeard = $state(0);
	/**
	 * How many proposals wait to be remembered, on the All apps button and the launcher's
	 * Approvals (T-1505), so one is noticed without opening the screen: read once each time
	 * JARVIS connects, then the count each `memory.proposals` carries. Does not cover a change
	 * while the link is down (the next connect reads it).
	 */
	let memoryWaiting = $state(0);
	const memoryLinked = $derived(Boolean(hermes) && hermesSnap?.status === 'connected');
	$effect(() => {
		if (!memoryLinked) {
			memoryWaiting = 0;
			return;
		}
		untrack(() => {
			memoryIo.load().then(
				(answer) => (memoryWaiting = memoryCards(answer).length),
				() => undefined // the screen says why when it is opened; the button shows no count it does not know
			);
		});
	});

	// --- a setting was changed: Undo (T-0700) ----------------------------------
	//
	// The console writes no Hermes setting itself, so every `settings.changed` it hears is a
	// change made by asking (a tool), by an applied proposal or by another app. The shell offers
	// to put it back for `MS.undo`, and Settings lists it under Recent changes after that.
	// Does not cover: the car and the popup, which draw no offer; a change made while this
	// console was not connected (Recent changes has it).

	/** The key Undo is offered for; '' when it is not. */
	let changedKey = $state('');
	let changedTimer: ReturnType<typeof setTimeout> | undefined;
	/** Counts the changes heard, so Recent changes reads again on each. */
	let settingsWrites = $state(0);
	/** Counts `settings.proposals` heard, so the Approvals screen reads its waiting changes again (T-1322). */
	let proposalsHeard = $state(0);
	/** The keys this console is putting back: the write an Undo makes is not offered an Undo. */
	const puttingBack = new Set<string>();
	/** Offer Undo for `key` ('' takes the offer away). Held, it stays until offered again: a pointer or the focus is on it. */
	function offerUndo(key: string, held = false): void {
		clearTimeout(changedTimer);
		changedKey = key;
		if (key && !held) changedTimer = setTimeout(() => (changedKey = ''), MS.undo);
	}
	function settingChanged(key: string): void {
		settingsWrites++;
		// Nor for a secret: the plugin keeps no old value of one, so the offer could only fail.
		if (!key || puttingBack.delete(key) || secretChange(key, null, null, false)) return;
		offerUndo(key);
	}
	/** Put `key` back and say what came of it. Never throws: the offer and Recent changes both call it. */
	async function undoSetting(key: string): Promise<void> {
		if (changedKey === key) offerUndo('');
		if (!hermes || hermesSnap?.status !== 'connected') return say(`${wornName()} is not connected, so ${key} was not put back.`);
		puttingBack.add(key);
		try {
			say(undoSaid(key, await hermes.link.settingsUndo(key)));
		} catch (e) {
			say(`Couldn’t put ${key} back: ${reason(e)}`);
		} finally {
			// The plugin announces a write before it answers, so the event has been heard by now.
			puttingBack.delete(key);
		}
	}
	/** One object, as `proposalsIo`: a new one per render would re-read the list. */
	const changesIo = { load: () => hermes!.link.settingsHistory(), undo: undoSetting };

	let busy = $state(false);
	/** The card's answer, sent back to whichever link asked. */
	async function answer(ask: Ask, choice: 'once' | 'session' | 'always' | 'deny', text = ''): Promise<boolean> {
		const { source, requestId, questionId } = approvalSourceOf(ask.id);
		busy = true;
		try {
			if (source === 'hermes' && hermes) {
				if (ask.kind === 'clarify') await hermes.link.respondClarify(requestId, text, questionId);
				else await hermes.link.respondApproval(requestId, choice);
			} else if (source === 'core' && client) {
				await client.resolveApproval(requestId, choice !== 'deny', text || undefined);
				feeds.reload(feeds.pending);
			} else if (source === 'phone' && client) {
				await client.answerPhoneConsent(requestId, choice !== 'deny');
				feeds.reload(feeds.pending);
			} else {
				say('Couldn’t answer that: its link is not connected.');
				return false;
			}
			// An approval's deny-with-text ("Ask why") is a no first: say so. No approval link carries
			// the text to the turn (Hermes's takes a choice, the phone's a boolean, the house's hands it
			// only to a tool with an answerable argument), so it goes out as the next thing said; if the
			// turn is still busy with the no, `send` leaves it in the composer.
			const why = Boolean(text) && ask.kind !== 'clarify' && choice === 'deny';
			say(why ? 'Denied. Asked why.' : text ? 'Answered.' : choice === 'deny' ? 'Denied.' : 'Approved.');
			if (why) void send(askWhyLine(ask.what, text));
			return true;
		} catch (e) {
			say(`Couldn’t answer that: ${reason(e)}`);
			return false;
		} finally {
			busy = false;
		}
	}

	/** The queue's ids "Approve all" would answer, as drawn. */
	const batch = $derived(batchable(asks));
	/**
	 * Approve exactly the ids the button was drawn with, one at a time, each "once". An id that has
	 * left the queue, or whose ask is no longer an undoable off-phone approval, is skipped; the first
	 * failure stops the rest (its reason is already said) rather than answering on through an outage.
	 */
	async function approveAll(ids: readonly string[]): Promise<void> {
		for (const id of ids) {
			const now = asks.find((a) => a.id === id);
			if (!now || now.kind !== 'approval' || !now.undoable || now.onPhone) continue;
			if (!(await answer(now, 'once'))) return;
		}
	}

	// --- talking ---------------------------------------------------------------

	let draft = $state('');
	async function send(text: string): Promise<void> {
		if (!voice || !text.trim()) return;
		voice.resetClock();
		const prefill = await voice.submit(text);
		if (prefill) draft = prefill;
	}
	function onCompose(e: SubmitEvent): void {
		e.preventDefault();
		const text = draft;
		draft = '';
		void send(text);
	}
	/** The open mic is off: muted by the person, or it would not open (`micWhy`). */
	const micMuted = $derived(!voice?.openMic);
	// Why it would not open: pressed (`openMicError`) or restored at load
	// (`micUnavailable`, which says nothing until asked: the button's title).
	const micWhy = $derived(voice?.openMicError || voice?.micUnavailable || undefined);
	function toggleMic(): void {
		if (!voice) return;
		if (voice.recording) void voice.stop();
		else void voice.start();
	}

	// --- the chat screen ---------------------------------------------------------

	let chatSessions = $state<SessionSummary[] | null>(null);
	let chatListError = $state('');
	function refreshSessions(): void {
		hermes?.link
			.listSessions()
			.then((rows) => ((chatSessions = rows), (chatListError = '')))
			.catch((e) => ((chatSessions = []), (chatListError = reason(e))));
	}
	$effect(() => {
		if (screen === 'chat' && hermesSnap?.status === 'connected') untrack(refreshSessions);
	});
	// A turn that finished may have titled or started a conversation: the list follows.
	let chatWasBusy = false;
	$effect(() => {
		const busyNow = Boolean(hud?.turn.running);
		if (screen === 'chat' && chatWasBusy && !busyNow) untrack(refreshSessions);
		chatWasBusy = busyNow;
	});
	const chatStatus = $derived.by((): Status => {
		if (privateChat.on) return privateChat.messages.length ? 'ready' : 'empty';
		const s = hermesSnap?.status;
		if (s === 'connected') return (hud?.messages.length ?? 0) > 0 ? 'ready' : 'empty';
		return s === 'connecting' || s === 'reconnecting' || s === undefined ? 'loading' : 'offline';
	});
	function setPrivate(on: boolean): void {
		voice?.stopSpeaking();
		privateChat.set(on);
		// The select shows the model a private turn runs on: it starts on this chat's.
		if (on && !privateChat.model) privateChat.model = hud?.model ?? '';
	}
	// Private lives in the chat: however the person leaves it, the private
	// conversation ends, so the core's mic is never quietly private.
	$effect(() => {
		if (screen !== 'chat' && untrack(() => privateChat.on)) setPrivate(false);
	});
	async function sendChat(text: string, photo: string | null = null): Promise<void> {
		if (!voice) return;
		voice.resetClock();
		await voice.submit(text, null, photo);
	}
	/**
	 * Stop: an interrupt alone is cooperative, and a turn held on an approval has
	 * no point at which to stop until it is answered. Stopping is the person's no
	 * to what it was waiting for, so its approvals are denied first.
	 */
	async function stopTurn(): Promise<void> {
		if (privateChat.on) return privateChat.stop();
		const link = hermes?.link;
		if (!link) return;
		try {
			for (const a of asks) {
				const { source, requestId } = approvalSourceOf(a.id);
				if (source === 'hermes' && a.kind !== 'clarify') await link.respondApproval(requestId, 'deny');
			}
			await link.interrupt();
		} catch (e) {
			say(`Couldn’t stop the run: ${reason(e)}`);
		}
	}
	// --- the lock, first run and enrolment (the owner, 2026-09-10) ----------------
	//
	// Minutes of quiet before the console is behind the lock; whether an enrolled
	// voice may open it; and whether a console password exists at all, without
	// which the lock cannot hold (`lock.ts` says what the lock is not). Both
	// preferences are this browser's.
	let lockAfter = $state(0);
	let lockVoice = $state(false);
	let locked = $state(false);
	let lockConfigured = $state(false);
	// T-0387: this browser holds no session while a password exists, so the
	// relays refuse it. The lock is then the sign-in: password only (a voice
	// opens no session), and a reload after it so the refused sockets dial again.
	let signedOut = $state(false);
	let activeAt = Date.now();
	try {
		lockAfter = parseLockAfter(localStorage.getItem(LOCK_AFTER_KEY));
		lockVoice = localStorage.getItem(LOCK_VOICE_KEY) === '1';
	} catch {
		/* a private window, or the server render */
	}
	function remember(key: string, value: string): void {
		try {
			localStorage.setItem(key, value);
		} catch {
			/* not remembered */
		}
	}
	function setLockAfter(minutes: number): void {
		lockAfter = minutes;
		activeAt = Date.now();
		remember(LOCK_AFTER_KEY, String(minutes));
	}
	function setLockVoice(on: boolean): void {
		lockVoice = on;
		remember(LOCK_VOICE_KEY, on ? '1' : '0');
	}
	function lockNow(): void {
		if (!lockConfigured) return;
		locked = true;
		launcherOpen = paletteOpen = devicesOpen = false;
		// Nothing goes on being said behind a locked door; the hold below keeps it from
		// hearing (held, not muted: a mute is written to storage and outlived the unlock).
		voice?.stopSpeaking();
		// The server's door as well (T-0946): the session ends, and the reload drops this
		// page's open sockets, which were admitted while it was signed in. It comes back
		// signed out, behind the same pane.
		// Only once the server says it ended: reloading after a sign-out that never arrived
		// would bring the page back signed in and unlocked. Until then the pane holds.
		if (!signedOut && lockEndsSession(lockVoice, sessionKeyed)) {
			void fetch('/api/console', { method: 'DELETE' })
				.then((res) => res.ok && location.reload())
				.catch(() => undefined);
		}
	}
	function unlocked(by?: string | null): void {
		if (signedOut) return location.reload();
		locked = false;
		activeAt = Date.now();
		// Who opened it is worth a line: the house now credits what they say to them.
		if (by) say(`Opened by ${by}.`);
	}
	// The setup sheet shows once: while the console has never had a password and
	// nobody said "later" (`GET /api/console` `firstRun`). `?setup=1` forces it.
	let firstRun = $state(false);
	/** This page's session was opened by a key (the phone's or the car's web view). */
	let sessionKeyed = false;
	let setupMin = $state(10);
	let enrolling = $state<string | null>(null);
	// Nothing is heard behind the lock, nor while someone reads phrases into the enrolment
	// sheet: the open mic sent each phrase as a turn, and one set a real timer (the owner,
	// 2026-11-09, T-0768). JARVIS stops talking too, or its voice is in the samples. One
	// place decides, so an unlock while enrolling does not let the mic listen again.
	$effect(() => {
		const hold = locked || !gateKnown || enrolling !== null;
		untrack(() => {
			if (!voice) return;
			if (hold) {
				voice.stopSpeaking();
				voice.holdOpenMic();
			} else voice.resumeOpenMic();
		});
	});
	let peopleVersion = $state(0);
	const forcedSetup = typeof location !== 'undefined' && new URLSearchParams(location.search).get('setup') === '1';
	/**
	 * False until the console has said once whether this page is signed in. A page that
	 * "Lock now" reloaded starts out not knowing it is locked, and a ⌘K in that gap opened
	 * the palette, which then stood above the lock (CI, 2026-11-12, T-1158).
	 */
	let gateKnown = $state(false);
	// Whatever locks it, nothing stays open above the lock or the setup sheet: a sheet
	// opened by a click in that same gap closes when the answer arrives.
	$effect(() => {
		if (locked || firstRun) launcherOpen = paletteOpen = devicesOpen = false;
	});
	async function askLockable(): Promise<void> {
		try {
			// Bounded: the shortcuts wait on this answer, and one that never came would
			// leave the keyboard dead for good.
			const body = (await (await fetch('/api/console', { signal: AbortSignal.timeout(GATE_WAIT_MS) })).json()) as { configured?: boolean; authenticated?: boolean; keyed?: boolean; firstRun?: boolean; minChars?: number };
			lockConfigured = Boolean(body.configured);
			sessionKeyed = Boolean(body.keyed);
			if (lockConfigured && body.authenticated === false) {
				signedOut = true;
				locked = true;
			}
			// Never on the head unit or the desktop popup: a password is set at a console,
			// not while driving or in a window the size of a card.
			firstRun = !popup && screen !== 'car' && (forcedSetup || Boolean(body.firstRun));
			if (typeof body.minChars === 'number') setupMin = body.minChars;
		} catch {
			lockConfigured = false;
		} finally {
			// Also after a failed read, as before this flag existed: a console whose own
			// server cannot answer is not locked by it either, and must stay usable.
			gateKnown = true;
		}
	}
	/**
	 * A walkthrough the first-run sheet started and the owner left opens once more on the core,
	 * and is then closed for good: after that only Settings opens it (T-0629). Only a console
	 * with an open progress row: an owner set up before the walkthrough existed has none and
	 * is never shown it unasked. Devices not listed yet count as paired, so an unknown never
	 * opens it.
	 */
	let setupOffered = false;
	const saveSetup = (row: SetupProgress): void =>
		void fetch('/api/console/walkthrough', { method: 'PUT', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ progress: serializeSetup(row) }) }).catch(() => {});
	async function resumeSetup(): Promise<void> {
		if (setupOffered || popup || screen !== 'core' || firstRun || locked) return;
		setupOffered = true;
		try {
			const row = parseSetup(((await (await fetch('/api/console/walkthrough', { signal: AbortSignal.timeout(GATE_WAIT_MS) })).json()) as { progress?: string }).progress);
			if (!row || row.closed) return;
			const speaker = (await (await fetch('/api/voice/speaker', { signal: AbortSignal.timeout(GATE_WAIT_MS) })).json()) as { people?: { enrolled?: boolean }[] };
			const people = (speaker.people ?? []).filter((p) => p.enrolled).length;
			const devices = feeds.companions.status === 'ready' ? (feeds.companions.data?.length ?? 0) : 1;
			// The answers took a moment: someone who has moved on is not pulled back.
			if (popup || screen !== 'core' || locked || !setupShown(row, { firstRun: false, people, devices })) return;
			saveSetup(closeSetup(row));
			void goto('/setup');
		} catch {
			/* a console that cannot read it is not sent anywhere */
		}
	}
	// Asked again whenever the Hermes link changes: a refused redial is how a
	// browser whose session expired finds out, and it then meets the lock.
	$effect(() => {
		void hermesSnap?.status;
		untrack(() => void askLockable().then(resumeSetup));
	});
	// Any input is activity; the timer looks once every few seconds.
	$effect(() => {
		const touch = () => (activeAt = Date.now());
		const events: (keyof WindowEventMap)[] = ['pointerdown', 'pointermove', 'keydown', 'wheel', 'touchstart'];
		for (const e of events) window.addEventListener(e, touch, { passive: true });
		const timer = setInterval(() => {
			if (!locked && lockConfigured && shouldLock({ activeAt, afterMin: lockAfter }, Date.now())) lockNow();
		}, 5000);
		return () => {
			for (const e of events) window.removeEventListener(e, touch);
			clearInterval(timer);
		};
	});

	async function switchModel(id: string): Promise<boolean> {
		if (privateChat.on) {
			privateChat.model = id;
			return true;
		}
		if (!hermes) return false;
		try {
			await hermes.link.setModel(id);
			return true;
		} catch (e) {
			say(`Couldn’t switch the model: ${reason(e)}`);
			return false;
		}
	}

	// Said aloud on the core, where the reply is the room's; on a full screen
	// only when the person asked replies to be read. The spoken ack follows it.
	// Never on the car page: its audio reaches the car only through the screen-share
	// grant, and the car's own turn speaks its reply through the car (T-0743).
	const repliesWanted = (): boolean => !onCarPage() && (screen === 'core' || Boolean(voice?.speakReplies));
	if (voice) voice.ackWanted = repliesWanted;
	$effect(() => {
		const turn = hud?.turn;
		if (!voice) return;
		untrack(() => {
			voice.followTurn(turn);
			voice.speakTurn(turn, repliesWanted());
		});
	});
	$effect(() => {
		const problem = hermesSnap?.sessionError ?? '';
		if (problem && voice) voice.notice = problem;
	});
	$effect(() => {
		if (voice?.notice) untrack(() => say(voice.notice));
	});

	// The page's mic stands down with the phone in the car and on a hidden
	// Android page (`voice.setAway`): what made the car show a call on every connect.
	let pageHidden = $state(typeof document !== 'undefined' && document.visibilityState === 'hidden');
	let phoneInCar = $state(false);
	$effect(() => {
		const seen = (): void => {
			pageHidden = document.visibilityState === 'hidden';
		};
		document.addEventListener('visibilitychange', seen);
		const bridge = phoneBridge();
		const check = (): void => {
			try {
				phoneInCar = Boolean(bridge?.carLinked?.());
			} catch {
				phoneInCar = false;
			}
		};
		// Now as well as every CAR_POLL_MS: opened in the car, the page took the mic for 3 s first (T-0993).
		if (bridge?.carLinked) check();
		const poll = bridge?.carLinked ? setInterval(check, CAR_POLL_MS) : null;
		return () => {
			document.removeEventListener('visibilitychange', seen);
			if (poll) clearInterval(poll);
		};
	});
	$effect(() => {
		voice?.setAway(onCarPage() || phoneInCar || (pageHidden && onAndroid()));
	});
	$effect(() => {
		if (!voice || onCarPage()) return;
		// Untracked: a mic that will not open sets openMic back to false, which
		// re-ran this and opened it again until Svelte stopped the page (2026-09-25).
		untrack(() => void voice.restoreOpenMic());
	});

	// A finished subagent reports in its own persona's voice, once: who is
	// talking can be told without looking. A row re-sent keeps its place in the set.
	const reported = new Set<string>();
	$effect(() => {
		const rows = hud?.background ?? [];
		if (!voice) return;
		untrack(() => {
			for (const row of rows) {
				if (row.kind !== 'subagent' || row.state === 'running' || reported.has(row.id)) continue;
				reported.add(row.id);
				if (row.state === 'done' && row.detail) voice.sayAs(row.detail, wornBy(row.id)?.voice ?? 'jarvis', repliesWanted());
			}
		});
	});

	// --- the sheets and the keys -----------------------------------------------

	let launcherOpen = $state(false);
	let paletteOpen = $state(false);
	let devicesOpen = $state(false);
	const anySheet = $derived(launcherOpen || paletteOpen || devicesOpen);

	// --- the side panel (T-0725) ------------------------------------------------
	// A screen opens beside the core instead of in its place: the stage stays
	// mounted and eases into the space left. On a close the panel keeps the
	// screen it showed until its slide out ends, so the screen leaves with it
	// instead of vanishing first; reduced motion has no slide to wait for.
	let closing = $state<ScreenId | null>(null);
	let lastOpen: ScreenId | null = null;
	$effect(() => {
		const now = screen === 'core' || screen === 'car' ? null : screen;
		untrack(() => {
			closing = !now && lastOpen && !prefersReducedMotion() ? lastOpen : null;
			lastOpen = now;
		});
	});
	const panel = $derived(screen === 'core' || screen === 'car' ? closing : screen);
	// A cancelled slide settles too, or the closed screen stays mounted off the edge
	// (and on a phone the composer stays hidden behind data-panel). Svelte's element
	// types have no onanimationcancel, so it is attached with `on`.
	function settle(e: AnimationEvent): void {
		if (e.target === e.currentTarget && closing) closing = null;
	}
	// A sheet moves the core aside the same way; when it closes the core glides back.
	let sheetBack = $state(false);
	let hadSheet = false;
	$effect(() => {
		const now = anySheet;
		untrack(() => {
			sheetBack = hadSheet && !now && !panel && !prefersReducedMotion();
			hadSheet = now;
		});
	});
	function glided(e: AnimationEvent): void {
		if (e.target === e.currentTarget) sheetBack = false;
	}
	const room = $derived(panel ? (closing ? 'out' : 'in') : anySheet ? 'sheet' : sheetBack ? 'back' : undefined);

	const paletteItems = $derived.by((): PaletteItem[] => {
		const items: PaletteItem[] = APPS.map((a) => ({ id: `app:${a.screen}`, group: 'Sections', title: a.label, sub: a.hint, run: () => void goto(a.path) }));
		items.push({ id: 'app:core', group: 'Sections', title: 'Back to the core', run: () => void goto('/') });
		if (voice) {
			items.push({ id: 'mic', group: 'Actions', title: voice.recording ? 'Stop listening' : `Talk to ${wornName()}`, keys: 'Space', run: toggleMic });
		}
		if (goal && hermes) {
			const link = hermes.link;
			items.push({ id: 'goal:pause', group: 'Actions', title: `Pause the goal: ${goal.title}`, run: () => void link.dispatch('goal', 'pause') });
			items.push({ id: 'goal:resume', group: 'Actions', title: `Resume the goal: ${goal.title}`, run: () => void link.dispatch('goal', 'resume') });
		}
		return items;
	});

	function typing(target: EventTarget | null): boolean {
		return target instanceof Element && target.closest('input, textarea, select, button, a, [contenteditable="true"], dialog') !== null;
	}
	function onKeyDown(e: KeyboardEvent): void {
		// Behind the lock or first run no shortcut reaches the console: ⌘K would
		// open the palette as a modal above the lock, and Space would listen.
		// Nor before the console has said whether this page is signed in at all.
		if (!gateKnown || locked || firstRun) return;
		if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
			e.preventDefault();
			launcherOpen = false;
			devicesOpen = false;
			paletteOpen = !paletteOpen;
			return;
		}
		// Escape slides an open screen back out, unless it would leave a field or
		// close a dialog first (a sheet, or a screen's own).
		if (e.key === 'Escape' && panel && !closing && !document.querySelector('dialog[open]') && !(e.target instanceof Element && e.target.closest('input, textarea, select, [contenteditable="true"]'))) {
			void goto('/');
			return;
		}
		// Escape sends what is held out at rest back into the core, from the composer too (where
		// the focus is after a typed turn). Not over a sheet or a dialog: those close first. Not
		// while the answer is being spoken: the scene is the reply's then, and would stay.
		if (e.key === 'Escape' && holding && reactorNow === 'idle' && v.scene === 'answer' && !panel && !anySheet && !e.defaultPrevented && !document.querySelector('dialog[open]')) {
			e.preventDefault();
			sentBack = hud?.turn.visualsAt ?? null;
			return;
		}
		// Space is push-to-talk wherever it would not press or type something.
		// Not while enrolling either: the phrase being read would go to JARVIS (T-0768).
		if (e.key !== ' ' || e.repeat || !voice || anySheet || enrolling || typing(e.target)) return;
		e.preventDefault();
		void voice.start();
	}
	function onKeyUp(e: KeyboardEvent): void {
		if (e.key !== ' ' || !voice?.recording || typing(e.target)) return;
		void voice.stop();
	}

	/**
	 * The desktop app's push-to-talk (ADR-0051): `detail.down` true is its key
	 * going down and false coming up; no `down` (a tray click) toggles.
	 */
	function onPushToTalk(e: Event): void {
		if (!voice || !gateKnown || locked || firstRun || enrolling) return;
		const down = (e as CustomEvent<{ down?: boolean } | null>).detail?.down;
		if (down === true || (down === undefined && !voice.recording)) void voice.start();
		else void voice.stop();
	}

	/**
	 * The desktop app's hold-to-dictate key (T-1258), the popup only: a recorder of its own, not the
	 * voice turn's, and the words go back to the app in the title (`dictatedSegment`), which sends
	 * them to the agent to type. The segment is cleared again so what was said does not sit in the
	 * window's title.
	 */
	let dictated = $state('');
	let dictatedTimer: ReturnType<typeof setTimeout> | undefined;
	const dictation = popup
		? pageDictation({
				surface: thisSurface(),
				allowed: () => Boolean(voice) && gateKnown && !locked && !firstRun && !enrolling,
				emit: (id, text) => {
					clearTimeout(dictatedTimer);
					dictated = dictatedSegment(id, text);
					dictatedTimer = setTimeout(() => (dictated = ''), DICTATED_SHOWN_MS);
				}
			})
		: null;
	function onDictate(e: Event): void {
		void dictation?.handle((e as CustomEvent<{ down?: boolean; id?: string } | null>).detail);
	}
	/** The app has the words: they leave the title now; the timer above is only the fall-back. */
	function onDictated(e: Event): void {
		if (!acknowledges(dictated, (e as CustomEvent<{ id?: string } | null>).detail)) return;
		clearTimeout(dictatedTimer);
		dictated = '';
	}

	// --- the session's own events ----------------------------------------------

	onMount(() => {
		const stops: (() => void)[] = [];
		window.addEventListener('jarvis:push-to-talk', onPushToTalk);
		stops.push(() => window.removeEventListener('jarvis:push-to-talk', onPushToTalk));
		if (dictation) {
			window.addEventListener(DICTATE_EVENT, onDictate);
			window.addEventListener(DICTATED_EVENT, onDictated);
			stops.push(
				() => window.removeEventListener(DICTATE_EVENT, onDictate),
				() => window.removeEventListener(DICTATED_EVENT, onDictated),
				() => clearTimeout(dictatedTimer)
			);
		}
		if (popup) {
			document.documentElement.dataset.popup = '';
			stops.push(() => delete document.documentElement.dataset.popup);
		}
		if (hermes) {
			const link = hermes.link;
			// The phone app's home screen: what is the phone's to do goes to the
			// phone (phone.ts), and its answer to a phone action comes back.
			const phone = phoneBridge();
			if (phone) {
				stops.push(
					link.client.onEvent((event) => {
						if (PHONE_EVENTS.has(event.type)) phone.event(event.type, JSON.stringify(event.payload ?? {}));
					})
				);
				const answered = (e: Event) => {
					const d = (e as CustomEvent<{ id?: string; body?: unknown }>).detail ?? {};
					const arg = phoneResultArg(link.sessionId ?? '', String(d.id ?? ''), d.body);
					if (arg) void link.dispatch('hud', arg);
				};
				window.addEventListener('jarvis:phone-result', answered);
				stops.push(() => window.removeEventListener('jarvis:phone-result', answered));
				// The routines this phone watches, once per session it is on.
				let pulledFor = '';
				stops.push(
					link.subscribe(() => {
						const sid = link.sessionId;
						if (!sid || sid === pulledFor) return;
						pulledFor = sid;
						void link.dispatch('hud', `@${sid} routines`).then((outcome) => {
							const out = outcome.kind === 'output' ? outcome.output : '';
							if (out.trim().startsWith('{')) phone.event(ROUTINES_EVENT, out);
						});
					})
				);
			} else {
				// A browser has no phone to hand a phone action to, and the tool waited
				// 90 s for an answer that never came, three times in one turn
				// (2026-10-06). Say so, after a moment in which a phone answers first.
				const timers = new Set<ReturnType<typeof setTimeout>>();
				stops.push(
					link.client.onEvent((event) => {
						if (event.type !== PHONE_ACTION_EVENT) return;
						const id = String(event.payload?.id ?? '');
						const t = setTimeout(() => {
							timers.delete(t);
							const arg = phoneResultArg(link.sessionId ?? '', id, NOT_A_PHONE);
							if (arg) void link.dispatch('hud', arg);
						}, NOT_A_PHONE_AFTER_MS);
						timers.add(t);
					})
				);
				stops.push(() => timers.forEach(clearTimeout));
			}
			// `hud.say` (the `hud_say` tool): one line the person asked to hear,
			// in the named persona's voice or the primary's.
			stops.push(
				link.client.onEvent((event) => {
					if (event.type !== 'hud.say' || !voice) return;
					const text = String(event.payload?.text ?? '').trim();
					if (!text) return;
					if (onCarPage() && sayInCar(text)) return;
					const named = String(event.payload?.voice ?? '');
					voice.say(text, named || (IDENTITIES[primary]?.voice ?? 'jarvis'));
				})
			);
			// A Hermes setting written through the gateway (the aura-settings plugin): the car page,
			// the settings feed's one reader, re-reads then, not on a timer (T-0746). And the shell
			// offers to undo it (T-0700).
			stops.push(
				link.client.onEvent((event) => {
					if (event.type === 'settings.proposals') proposalsHeard++; // a count only (T-1322)
					if (event.type === MEMORY_PROPOSALS_EVENT) {
						memoryHeard++; // a count only, as above (T-1503)
						const open = openOf(event.payload);
						if (open !== null) memoryWaiting = open;
					}
					if (event.type !== 'settings.changed') return;
					feeds.reload(feeds.settings);
					settingChanged(String(event.payload?.key ?? ''));
				})
			);
			if (session) void link.openSession(session);
			if (voice) void voice.prime();
		}
		return () => {
			for (const stop of stops) stop();
			clearTimeout(statusTimer);
			clearTimeout(changedTimer);
			feeds.dispose();
			voice?.dispose();
		};
	});

	// --- the popup's notice ----------------------------------------------------

	// A notice that arrived while the popup is up takes its line for a moment
	// (the owner, 2026-09-25: "things it can pull up for notifications"), and
	// its title says so, so the app raises it unfocused. The ones there at load
	// are not news.
	let seenNotices: Set<string> | null = null;
	let pulled = $state('');
	let pulledTimer: ReturnType<typeof setTimeout> | undefined;
	$effect(() => {
		const list = feeds.moments.data;
		if (!popup || !feeds.moments.loaded) return;
		untrack(() => {
			const fresh = seenNotices ? list.find((m) => !seenNotices!.has(m.id)) : undefined;
			seenNotices = new Set(list.map((m) => m.id));
			if (!fresh) return;
			pulled = fresh.title;
			clearTimeout(pulledTimer);
			pulledTimer = setTimeout(() => (pulled = ''), NOTICE_MS);
		});
	});
	onMount(() => () => clearTimeout(pulledTimer));

	// --- the tab ---------------------------------------------------------------

	// The popup's title is the desktop app's mood channel; every other surface
	// carries the state word, so a background tab still says it is waiting.
	/** The popup's island (T-0695): its pill shrinks the desktop app's window (`popupTitle`). */
	let island = $state<IslandState>('card');
	$effect(() => {
		document.title = popup
			? popupTitle(identity.name, popupMood({ waiting: asks.length, notice: Boolean(voice?.notice) || pulled !== '', reactor: reactorNow }), island === 'pill', island === 'hidden', dictated)
			: titleFor(titleOf(screen, identity.name), reactorNow, wordOf(reactorNow));
	});
	// At rest both icons are the worn theme's; busy, the SVG one carries the state (Safari
	// reads only the .ico, so that one stays the theme's).
	$effect(() => {
		const rest = themeIcon(theme.id);
		const ico = document.querySelector<HTMLLinkElement>('link[rel="icon"]:not([type="image/svg+xml"]):not([data-state])');
		if (ico) ico.href = rest;
		const svg = document.querySelector<HTMLLinkElement>('link[rel="icon"][type="image/svg+xml"], link[rel="icon"][data-state]');
		if (!svg) return;
		svg.dataset.state = '';
		const busy = reactorNow !== 'idle';
		svg.type = busy ? 'image/svg+xml' : 'image/x-icon';
		svg.href = busy ? faviconFor(reactorNow) : rest;
	});
</script>

<svelte:window onkeydown={onKeyDown} onkeyup={onKeyUp} />

<div
	class="shell"
	data-testid="shell"
	data-ready={String(ready && gateKnown)}
	data-session-ready={String(hermesSnap?.ready ?? false)}
	data-reactor={reactorNow}
	data-scene={v.scene}
	data-target={v.target}
	data-phase={v.phase}
	data-settled={String(v.settled)}
	data-screen={screen}
	class:floating={popup}
>
	<!-- Not under a gate: the lock and setup draw their own sky, and a locked desktop would twinkle an unseen one for hours. -->
	<!-- Not under the car: its page is see-through to the phone's map (T-0976), or opaque over it. -->
	{#if !popup && !locked && !firstRun && screen !== 'car' && starfieldWorn()}
		<Starfield />
	{/if}
	{#if preview && !popup && screen !== 'settings' && screen !== 'car'}
		<aside class="drafted" aria-label="A drafted theme">
			<ThemeTile pack={preview.pack} note="Drafted, not worn yet" testid="theme-preview">
				<Button testid="theme-preview-apply" onclick={() => applyDraft(preview)}>Apply</Button>
				<Button tone="ghost" testid="theme-preview-dismiss" onclick={() => (dismissed = preview.pack)}>Not now</Button>
			</ThemeTile>
		</aside>
	{/if}
	{#if popup}
		<Popup
			name={identity.name}
			look={coreState}
			reactor={reactorNow}
			{tint}
			word={wordOf(reactorNow)}
			said={hud?.turn?.text || lastReply}
			heard={voice?.transcript ?? ''}
			{status}
			recording={voice?.recording ?? false}
			level={voice?.recording ? voice.level : undefined}
			envelope={voice?.speaking ? voice.level : undefined}
			notice={pulled}
			ask={asks[0]}
			more={asks.length - 1}
			{batch}
			onbatch={approveAll}
			{busy}
			ready={Boolean(voice)}
			bind:draft
			bind:island
			onanswer={answer}
			onsend={onCompose}
			ontalk={toggleMic}
		/>
	{:else if screen === 'car'}
		<!-- The head unit: no top bar, launcher or composer; the core is the car's talk button. -->
		<Car name={identity.name} reactor={reactorNow} {feeds} />
	{:else}
	<header class="bar">
		<div class="side">
			<button
				type="button"
				class="icon apps-button"
				aria-label={memoryWaiting ? `All apps, ${memoryWaiting} to remember` : 'All apps'}
				aria-haspopup="dialog"
				onclick={() => (launcherOpen = true)}
			>
				<svg viewBox="0 0 24 24" aria-hidden="true">
					{#each [5, 12, 19] as y (y)}
						{#each [5, 12, 19] as x (x)}<circle cx={x} cy={y} r="1.6" />{/each}
					{/each}
				</svg>
				<!-- Amber: it waits on a person (T-1505). The name above says it; this is for the eye. -->
				{#if memoryWaiting}<span class="waiting-count" aria-hidden="true" data-testid="apps-waiting">{memoryWaiting}</span>{/if}
			</button>
			<a class="mark" href="/" aria-label="{identity.name}, the core">{identity.name}</a>
		</div>
		<div class="side">
			<button type="button" class="icon" aria-label="Search or command" aria-haspopup="dialog" aria-keyshortcuts="Meta+K Control+K" onclick={() => (paletteOpen = true)}>
				<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="10.5" cy="10.5" r="6" class="line" /><path d="M15 15l5 5" class="line" /></svg>
			</button>
			<button type="button" class="icon" aria-label="Devices" aria-haspopup="dialog" onclick={() => (devicesOpen = true)}>
				<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="2.5" y="4.5" width="13" height="10" rx="1.5" class="line" /><path d="M6 19h6" class="line" /><rect x="17.5" y="8.5" width="4" height="11" rx="1" class="line" /></svg>
			</button>
		</div>
	</header>

	<!-- The page's one main landmark, round the core and an open screen: without one a
	     screen reader's "jump to main" had nowhere to go. The core stays on its stage
	     under every screen (T-0725): a screen slides in beside it as a panel. -->
	<main class="room" data-panel={room}>
		<div class="core" onanimationend={glided} {@attach (el) => on(el, 'animationcancel', glided)}>
		<!-- Handed to the stage only while something waits: the alert scene is also where a fault
		     puts the core, and a card with nothing to ask was drawn as an empty box with its amber
		     line pinned beside the core (T-1480). -->
		{#snippet approval()}
			{#if asks[0]}<ApprovalCard ask={asks[0]} more={asks.length - 1} {batch} onbatch={approveAll} {busy} onanswer={answer} />{/if}
		{/snippet}
		<Stage view={v} look={coreState} {tint} {shown} {strip} notice={panel ? '' : status} {turns} running={Boolean(hud?.turn?.running)} heard={voice?.transcript ?? ''} persona={identity.name} level={voice?.recording ? voice.level : undefined} envelope={voice?.speaking ? voice.level : undefined} ontakeover={anySheet ? undefined : takeover} onstop={() => void stopTurn()} onretile={retile} aside={Boolean(panel) || anySheet} card={asks[0] && screen === 'core' ? approval : undefined} />
		<!-- Beside Chat its own composer is the one to type in: two side by side read as a fault. -->
		{#if panel !== 'chat'}
		<form class="composer" onsubmit={onCompose}>
			<input data-testid="chat-input" aria-label="Message {identity.name}" placeholder="Ask {identity.name} anything" autocomplete="off" bind:value={draft} />
			<!-- The mic is open and this mutes it (the owner, 2026-09-18: "a microphone
			     (that is open) and can just be muted"); Space is still push-to-talk. A
			     push-to-talk button here left the open mic with no way to stop it. -->
			<button type="button" class="icon" data-testid="mic" data-muted={micMuted || undefined} aria-label="Mute the microphone" aria-pressed={micMuted} title={micWhy} disabled={!voice} onclick={() => void voice?.setOpenMic(micMuted)}>
				<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="9" y="3" width="6" height="11" rx="3" class="line" /><path d="M5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21" class="line" />{#if micMuted}<path d="M4 4l16 16" class="line" />{/if}</svg>
			</button>
			<button type="submit" class="icon" aria-label="Send" disabled={!voice || !draft.trim()}>
				<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 12h15M13 6l6 6-6 6" class="line" /></svg>
			</button>
		</form>
		{/if}
		</div>
		{#if panel}
			<div class="panel screens" data-testid="side-panel" onanimationend={settle} {@attach (el) => on(el, 'animationcancel', settle)} {@attach (el) => dragToClose(el, () => void goto('/'))}>
				<Grab />
				<div class="pane">
					{#if panel === 'chat'}
						<Chat
							messages={privateChat.on ? privateChat.messages : (hud?.messages ?? [])}
							busy={privateChat.on ? privateChat.busy : Boolean(hud?.turn.running)}
							status={chatStatus}
							errorDetail={chatListError}
							title={hud?.title ?? ''}
							persona={identity.name}
							sessions={chatSessions}
							current={privateChat.on ? null : (hud?.storedSessionId ?? null)}
							recording={voice?.recording ?? false}
							onsend={(text, photo) => void sendChat(text, photo)}
							onstop={() => void stopTurn()}
							onnew={() => (privateChat.on ? setPrivate(true) : void hermes?.link.newSession().then(refreshSessions))}
							onopen={(id) => {
								if (privateChat.on) setPrivate(false);
								void hermes?.link.openSession(id);
							}}
							ondelete={(id) =>
								void hermes?.link
									.deleteSession(id)
									.then(refreshSessions)
									.catch((e) => say(`Couldn’t delete the conversation: ${reason(e)}`))}
							onrefresh={refreshSessions}
							onclose={() => void goto('/')}
							onspeak={voice ? (text) => voice.sayAs(text, identity.voice, true) : undefined}
							onmicdown={voice ? () => voice.press() : undefined}
							onmicup={voice ? () => voice.release() : undefined}
							onretry={refreshSessions}
							privateMode={privateChat.on}
							onprivate={voice ? setPrivate : undefined}
							models={feeds.models.data.list.models}
							model={privateChat.on ? privateChat.model : (hud?.model ?? '')}
							onmodel={switchModel}
						/>
					{:else if panel === 'ops' && ops}
						<Ops data={ops} status={opsStatus} health={statusLabel(hermesSnap?.report, hermesSnap?.statusError)} reply={lastReply} busy={Boolean(hud?.turn.running)} onask={(q) => void send(q)} onclose={() => void goto('/')} onretry={() => hermes?.link.reconnectNow()} />
					{:else if panel === 'board'}
						<Board />
					{:else if panel === 'models'}
						<Models />
					{:else if panel === 'integrations'}
						<Integrations {feeds} onask={ask} />
					{:else if panel === 'settings'}
						<Settings
							{feeds}
							{primary}
							readAloud={voice ? voice.speakReplies : null}
							lock={{ configured: lockConfigured, after: lockAfter, voice: lockVoice }}
							{peopleVersion}
							onpersona={(p) => void pickPersona(p)}
							onreadaloud={(on) => voice && (voice.speakReplies = on)}
							wake={voice && thisSurface() !== 'android' ? { on: voice.wakeOn, listening: voice.wakeListening, why: voice.wakeError } : null}
							onwake={(on) => voice?.setWakeOn(on)}
							onask={ask}
							onlockafter={setLockAfter}
							onlockvoice={setLockVoice}
							onlocknow={lockNow}
							onenrol={(label) => (enrolling = label)}
							{preview}
							ondismisspreview={() => (dismissed = preview?.pack ?? null)}
							onsample={voice ? sample : undefined}
							changes={hermes && hermesSnap?.status === 'connected' ? changesIo : undefined}
							changed={settingsWrites}
						/>
					{:else if panel === 'setup'}
						<Walkthrough {feeds} {firstRun} waiting={asks.length} reply={lastReply} busy={Boolean(hud?.turn.running)} onask={ask} />
					{:else if panel === 'texts'}
						<Texts {feeds} />
					{:else if panel === 'devices'}
						<Devices {feeds} />
					{:else if panel === 'approvals'}
						<Approvals {asks} status={asksStatus} {busy} onanswer={answer} onbatch={approveAll} loadgrants={hermes ? loadGrants : undefined} onrevoke={revokeGrant} proposals={hermes && hermesSnap?.status === 'connected' ? proposalsIo : undefined} proposalsChanged={proposalsHeard} memory={hermes && hermesSnap?.status === 'connected' ? memoryIo : undefined} memoryChanged={memoryHeard} onask={ask} working={Boolean(hud?.turn.running)} />
					{:else}
						<Screen title={titleOf(panel)} testid="screen-{panel}" />
					{/if}
				</div>
			</div>
		{/if}
	</main>
	<!-- A request is answerable from every screen: the oldest, docked, so a hold is never a navigation away. -->
	{#if asks[0] && screen !== 'core' && screen !== 'approvals'}
		<aside class="docked" aria-label="Waiting for your OK">
			<ApprovalCard ask={asks[0]} more={asks.length - 1} {batch} onbatch={approveAll} {busy} onanswer={answer} />
		</aside>
	{/if}

	<!-- Always mounted, so the offer is announced when it arrives. The timer waits while a pointer or
	     the focus is on it: five seconds is short for a keyboard, and Put back under Settings is the
	     untimed way to the same thing. -->
	<div class="changed" role="status" aria-live="polite">
		{#if changedKey}
			<div
				class="offer"
				data-testid="settings-undo"
				role="group"
				aria-label="A setting was changed"
				onpointerenter={() => offerUndo(changedKey, true)}
				onpointerleave={() => offerUndo(changedKey)}
				onfocusin={() => offerUndo(changedKey, true)}
				onfocusout={() => offerUndo(changedKey)}
			>
				<Label>Changed</Label>
				<span class="key" data-testid="settings-undo-key">{changedKey}</span>
				<Button tone="ghost" testid="settings-undo-button" aria-label="Undo the change to {changedKey}" onclick={() => void undoSetting(changedKey)}>Undo</Button>
			</div>
		{/if}
	</div>

	<p class="status" class:announced={screen === 'core'} data-beside={panel ? '' : undefined} data-testid="shell-status" role="status" aria-live="polite">{status}</p>
	{/if}
</div>

{#if firstRun && !locked}
	<Setup
		minChars={setupMin}
		onready={(name, enrol) => {
			firstRun = false;
			lockConfigured = true;
			if (enrol) enrolling = name;
			// On to the rest of setup; the voice sheet, if asked for, stands over it first. The open
			// row is what offers it once more if they leave now.
			setupOffered = true;
			saveSetup(freshSetup());
			void goto('/setup');
		}}
		onlater={() => (firstRun = false)}
	/>
{/if}
{#if enrolling && !locked}
	<Enrol
		label={enrolling}
		onclose={(enrolled) => {
			if (enrolled) say(`${enrolling} is enrolled. {wornName()} answers that voice now.`);
			enrolling = null;
			peopleVersion += 1;
		}}
	/>
{/if}
<!-- Last, so it is the top layer over any sheet a person left open. -->
{#if locked}
	<Lock configured={lockConfigured} voice={lockVoice && !signedOut} name={identity.name} onunlock={unlocked} />
{/if}

<Sheet bind:open={launcherOpen} label="All apps" testid="launcher">
	<nav aria-label="All apps">
		<ul class="apps">
			{#each APPS as app (app.path)}
				<li>
					<a href={app.path} onclick={() => (launcherOpen = false)}>
						<span class="app-name">{app.label}</span>
						<span class="app-hint">{app.hint}</span>
						{#if app.screen === 'approvals' && memoryWaiting}<span class="app-waiting" data-testid="launcher-waiting">{memoryWaiting} to remember</span>{/if}
					</a>
				</li>
			{/each}
		</ul>
	</nav>
</Sheet>
<!-- The popup is the core and the input: no launcher, palette or devices to open over it. -->
{#if !popup}
	<Palette bind:open={paletteOpen} items={paletteItems} onask={(q) => void send(q)} />
	<DevicesSheet bind:open={devicesOpen} {client} />
{/if}

<style>
	.shell {
		position: fixed;
		inset: 0;
		display: flex;
		flex-direction: column;
		overflow: hidden;
		/* The phone's page reaches under its camera's cutout, its ground to the edge, and keeps
		   its content clear of it by what the phone tells it (ConsolePage.fit, T-0974): the
		   cutout's margin was a black band across the top. 0 everywhere else. */
		padding: var(--safe-top, 0) var(--safe-right, 0) var(--safe-bottom, 0) var(--safe-left, 0);
		background: var(--jv-stage-ground);
		color: var(--jv-text);
		font-family: var(--jv-font-body);
	}
	/* The desktop app's window is clear round the popup's card. */
	.shell.floating {
		background: transparent;
	}
	.bar {
		position: relative;
		z-index: 2;
		display: flex;
		align-items: center;
		justify-content: space-between;
		padding: var(--jv-space-4) var(--jv-space-5);
	}
	.side {
		display: flex;
		align-items: center;
		gap: var(--jv-space-3);
	}
	.mark {
		color: var(--jv-text-bright);
		font: var(--jv-fs-md) var(--jv-font-display);
		letter-spacing: var(--jv-track-logo);
		text-decoration: none;
		text-transform: uppercase;
	}
	.mark:focus-visible,
	.icon:focus-visible,
	.composer input:focus-visible,
	.apps a:focus-visible {
		outline: var(--jv-focus-outline);
		outline-offset: var(--jv-focus-offset);
	}
	.icon {
		display: inline-grid;
		place-items: center;
		flex: none;
		width: var(--jv-touch);
		height: var(--jv-touch);
		padding: 0;
		border: 1px solid var(--jv-line);
		border-radius: var(--jv-radius-pill);
		background: transparent;
		color: var(--jv-accent);
		cursor: pointer;
	}
	.icon:hover:not(:disabled) {
		background: var(--jv-wash);
	}
	.icon:disabled {
		color: var(--jv-text-faint);
		cursor: default;
	}
	.icon[aria-pressed='true'] {
		background: var(--jv-wash-strong);
		color: var(--jv-text-bright);
	}
	svg {
		width: 50%;
		height: 50%;
		fill: currentColor;
	}
	svg .line {
		fill: none;
		stroke: currentColor;
		stroke-width: 1.6;
		stroke-linecap: round;
	}
	.composer {
		position: relative;
		z-index: 2;
		display: flex;
		align-items: center;
		gap: var(--jv-space-2);
		width: 100%;
		max-width: var(--jv-strip-width);
		margin: 0 auto;
		padding: 0 var(--jv-space-3) var(--jv-space-4);
	}
	.composer input {
		flex: 1;
		min-width: 0;
		min-height: var(--jv-touch);
		padding: 0 var(--jv-space-4);
		border: 1px solid var(--jv-line-soft);
		border-radius: var(--jv-radius-pill);
		background: var(--jv-surface-sunken);
		color: var(--jv-text-bright);
		font: var(--jv-fs-md) var(--jv-font-body);
	}
	.composer input::placeholder {
		color: var(--jv-text-faint);
	}
	.status {
		position: absolute;
		left: 50%;
		bottom: calc(var(--jv-touch) + var(--jv-space-6));
		z-index: 3;
		margin: 0;
		max-width: var(--jv-strip-width);
		transform: translateX(-50%);
		color: var(--jv-text-bright);
		font: var(--jv-fs-sm) var(--jv-font-chrome);
		text-align: center;
		pointer-events: none;
	}
	/* Beside an open screen the line stands on the stage's part of the room (T-1504): at half
	   the window it was the panel's edge at 1440, so "Asked ..." ran into the screen's text,
	   and at 390 it lay on the risen panel's rows. Both edges are given, not a centre: a box
	   set at 50% may only be half as wide (the Undo offer's note below). */
	.status[data-beside] {
		inset-inline: var(--jv-space-4) calc(var(--jv-measure-panel) + var(--jv-space-4));
		margin-inline: auto;
		transform: none;
	}
	@media (max-width: 720px) {
		.status[data-beside] {
			inset-inline: var(--jv-space-4);
			bottom: calc(var(--jv-measure-panel-rise) + var(--jv-space-2));
		}
	}
	/* The Undo offer (T-0700): under the bar at its start, clear of the drafted reply at the
	   other end, the status line and the docked approval at the foot. Not centred: a box set
	   at 50% may only be half as wide, which broke a key over four lines at 390. Solid ground:
	   it can lie over a screen's own text. */
	.changed {
		position: absolute;
		inset: calc(var(--jv-touch) + var(--jv-space-6)) auto auto var(--jv-space-4);
		z-index: 4;
		max-inline-size: calc(100% - var(--jv-space-6));
	}
	.offer {
		display: flex;
		align-items: center;
		gap: var(--jv-space-3);
		padding: var(--jv-space-2) var(--jv-space-2) var(--jv-space-2) var(--jv-space-4);
		border: 1px solid var(--jv-line-hair);
		border-inline-start-color: var(--jv-line);
		background: var(--jv-panel-solid);
		box-shadow: var(--jv-elev-float);
		animation: offer-in var(--jv-dur-fade) var(--jv-ease-decelerate);
	}
	.offer .key {
		min-inline-size: 0;
		color: var(--jv-text-bright);
		font: var(--jv-fs-sm) var(--jv-font-chrome);
		overflow-wrap: anywhere;
	}
	@keyframes offer-in {
		from {
			opacity: 0;
			translate: 0 calc(-1 * var(--jv-space-2));
		}
	}
	/* On the core the stage draws the line in its words column (T-0703); this copy only announces it. */
	.status.announced {
		width: 1px;
		height: 1px;
		overflow: hidden;
		clip-path: inset(50%);
		white-space: nowrap;
	}
	/* The room is the core's stage and, while a screen is open, the panel beside
	   it (T-0725): from the inline end, or up from the bottom on a phone. The
	   stage takes its new size once, as the panel comes or goes, and the core
	   glides there by translate alone, so nothing lays out again per frame (the
	   stage measures its lines relative to itself, so a translate moves them too). */
	.room {
		--panel-w: var(--jv-measure-panel);
		--panel-h: var(--jv-measure-panel-rise);
		--panel-from: 100% 0;
		--aside-x: calc(var(--panel-w) / 2);
		--aside-y: 0;
		position: relative;
		flex: 1;
		display: flex;
		min-block-size: 0;
		container-type: size;
	}
	.core {
		flex: 1;
		display: flex;
		flex-direction: column;
		min-inline-size: 0;
		min-block-size: 0;
		container-type: size;
	}
	/* Beside a panel the stage sizes the core and the strip to its own box, not the window. */
	.room[data-panel='in'] .core {
		--u-w: 1cqw;
		--u-h: 1cqh;
		animation: core-aside var(--jv-dur-appear) var(--jv-ease-appear);
	}
	.room[data-panel='sheet'] .core {
		--u-w: 1cqw;
		--u-h: 1cqh;
		margin-inline-end: var(--panel-w);
		animation: core-aside var(--jv-dur-appear) var(--jv-ease-appear);
	}
	.room[data-panel='out'] .core,
	.room[data-panel='back'] .core {
		animation: core-back var(--jv-dur-exit) var(--jv-ease-exit);
	}
	.panel {
		position: relative;
		z-index: 1;
		flex: 0 0 var(--panel-w);
		display: flex;
		min-inline-size: 0;
		min-block-size: 0;
		border-inline-start: 1px solid var(--jv-glass-edge, var(--jv-line-soft));
		/* The stage shows through; under a starfield pack the panel is its glass (base.css). */
		background: var(--jv-glass, none);
		animation: panel-in var(--jv-dur-appear) var(--jv-ease-appear);
	}
	/* The screen scrolls inside the panel, so the panel's corner and rim stay where they are. */
	.pane {
		flex: 1;
		display: flex;
		/* A screen lays itself out by the panel's width, not the window's (Chat's @container). */
		container-type: inline-size;
		min-inline-size: 0;
		min-block-size: 0;
		overflow-y: auto;
	}
	/* The panel wears the projections' corner (T-0974; the owner, 2026-11-10: "match those
	   settings/options views with the matching glass panel look that the panels that aura
	   spawns have"): JARVIS's bracket, or under the star marker her star, as Projection draws. */
	.panel::after {
		content: '';
		position: absolute;
		top: 0;
		left: 0;
		width: var(--jv-projection-bracket);
		height: var(--jv-projection-bracket);
		border-top: 1px solid var(--jv-accent);
		border-left: 1px solid var(--jv-accent);
		box-shadow: var(--jv-glow-bracket);
		pointer-events: none;
	}
	:global(:root[data-marker='star']) .panel {
		border: 0;
		border-start-start-radius: var(--jv-radius-md);
		border-end-start-radius: var(--jv-radius-md);
	}
	:global(:root[data-marker='star']) .panel::after {
		border: none;
		box-shadow: none;
		background: var(--jv-theme-accent-gradient);
		clip-path: polygon(50% 0, 60% 40%, 100% 50%, 60% 60%, 50% 100%, 40% 60%, 0 50%, 40% 40%);
	}
	/* Her gradient rim: only its 1px ring shows, the content box masked out of the whole. */
	:global(:root[data-marker='star']) .panel::before {
		content: '';
		position: absolute;
		inset: 0;
		z-index: -1;
		padding: 1px;
		border-radius: inherit;
		background: var(--jv-theme-accent-gradient);
		mask:
			linear-gradient(var(--jv-bg) 0 0) content-box exclude,
			linear-gradient(var(--jv-bg) 0 0);
		opacity: 0.45;
		pointer-events: none;
	}
	/* Out of the flow as it leaves: the stage is whole at once and the core glides back with it. */
	.room[data-panel='out'] .panel {
		position: absolute;
		inset-block: 0;
		inset-inline-end: 0;
		inline-size: var(--panel-w);
		animation: panel-out var(--jv-dur-exit) var(--jv-ease-exit) forwards;
	}
	@keyframes panel-in {
		from {
			translate: var(--panel-from);
		}
	}
	@keyframes panel-out {
		to {
			translate: var(--panel-from);
		}
	}
	@keyframes core-aside {
		from {
			translate: var(--aside-x) var(--aside-y);
		}
	}
	@keyframes core-back {
		from {
			translate: calc(-1 * var(--aside-x)) calc(-1 * var(--aside-y));
		}
	}
	/* A phone: the panel rises over the lower two thirds, the core keeps the top. */
	@media (max-width: 720px) {
		.room {
			--panel-from: 0 100%;
			--aside-x: 0;
			--aside-y: calc(var(--panel-h) / 2);
			/* No scale here: the stage already shrinks the core as it travels (`coreGlide`,
			   T-0974), and a second one on this box made it jump a frame (pipeline 814). */
			flex-direction: column;
		}
		/* At the top the core has the box to itself: most of its height, not the two fifths
		   it takes under a conversation (85 px there, a handful of dots). */
		.room[data-panel='in'] .core,
		.room[data-panel='sheet'] .core {
			--core-share: 76;
		}
		/* The frame every risen menu has, a sheet's (`ui/Sheet`): its ground, edge and corners. */
		.panel {
			flex-basis: var(--panel-h);
			flex-direction: column;
			border-inline-start: 0;
			border-block-start: 1px solid var(--jv-glass-edge, var(--jv-line-hair));
			border-radius: var(--jv-radius-md) var(--jv-radius-md) 0 0;
			background: var(--jv-glass, var(--jv-panel-solid));
			box-shadow: var(--jv-elev-float);
		}
		/* Rising from the foot, its rounded edge is the top one. */
		:global(:root[data-marker='star']) .panel {
			border: 0;
			border-radius: var(--jv-radius-md) var(--jv-radius-md) 0 0;
		}
		.room[data-panel='out'] .panel {
			inset: auto 0 0;
			inline-size: auto;
			block-size: var(--panel-h);
		}
		.room[data-panel='sheet'] .core {
			margin-inline-end: 0;
			margin-block-end: var(--panel-h);
		}
		.room[data-panel] .composer {
			display: none;
		}
	}
	@media (prefers-reduced-motion: reduce) {
		.panel,
		.offer,
		.room[data-panel] .core {
			animation: none;
		}
	}
	.docked {
		position: absolute;
		inset: auto var(--jv-space-4) var(--jv-space-4) auto;
		z-index: 3;
		inline-size: min(var(--jv-measure-dialog), calc(100% - var(--jv-space-6)));
		max-block-size: 70%;
		overflow-y: auto;
	}
	/* Under the top bar, clear of the docked approval at the foot. */
	.drafted {
		position: absolute;
		inset: calc(var(--jv-touch) + var(--jv-space-6)) var(--jv-space-4) auto auto;
		z-index: 3;
		inline-size: min(calc(var(--jv-space-7) * 4), calc(100% - var(--jv-space-6)));
	}
	/* The live theme switch's dissolve (theme.svelte.ts): one pulse long, as the tween. */
	:global(::view-transition-old(root)),
	:global(::view-transition-new(root)) {
		animation-duration: var(--jv-dur-pulse);
	}
	.apps {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(var(--jv-measure-card), 1fr));
		gap: var(--jv-space-3);
		margin: 0;
		padding: 0;
		list-style: none;
	}
	.apps a {
		display: grid;
		gap: var(--jv-space-1);
		min-height: var(--jv-touch);
		padding: var(--jv-space-3) var(--jv-space-4);
		border: 1px solid var(--jv-line-hair);
		border-radius: var(--jv-radius-md);
		color: var(--jv-text-bright);
		text-decoration: none;
	}
	.apps a:hover {
		background: var(--jv-wash);
	}
	.app-name {
		font: var(--jv-fs-md) var(--jv-font-chrome);
	}
	.app-hint {
		color: var(--jv-text-dim);
		font: var(--jv-fs-sm) var(--jv-font-body);
	}
	.app-waiting {
		color: var(--jv-warn);
		font: var(--jv-fs-2xs) var(--jv-font-chrome);
		letter-spacing: var(--jv-track-control);
		text-transform: uppercase;
	}
	.apps-button {
		position: relative;
	}
	.waiting-count {
		position: absolute;
		inset-block-start: calc(-1 * var(--jv-space-1));
		inset-inline-end: calc(-1 * var(--jv-space-1));
		min-inline-size: var(--jv-space-4);
		padding-inline: var(--jv-space-1);
		border: 1px solid var(--jv-warn);
		border-radius: var(--jv-radius-pill);
		background: var(--jv-bg);
		color: var(--jv-warn);
		font: var(--jv-weight-strong) var(--jv-fs-2xs) var(--jv-font-body);
		font-variant-numeric: tabular-nums;
		text-align: center;
	}
</style>
