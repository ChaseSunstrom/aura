// What comes out of the core in each scene, and where it lands.
//
// The shell has no permanent panels (PROMPT.md §2): a scene projects what is
// true right now and takes it back when the scene changes. This is the table
// of which data each scene shows and the board's slot for each item
// (Core2.dc.html), kept pure so the "only when there is data" rule is pinned
// by a unit test rather than by `{#if}`s spread through a component.
//
// Does not cover: drawing (Stage.svelte), or a content panel's body: a hud_show panel
// shows its title and first line; a model's typed visual comes as a `hud.projection` frame.

import type { Point } from '../lines';
import type { Scene } from '../stage';
import type { Tone } from '../tone';
import type { ContentPanel } from '$lib/hud/data/panels';
import type { ProjectionFrame } from '../gen/contract';
import type { Goal, Job, TelemetryReading } from '$lib/glass/bodies/types';
import type { LiveStep } from '../activity/live';
import { place, type Tiles } from './tiling';

export interface Shown {
	id: string;
	/** The projection's cap: what this is. */
	label: string;
	tone: Tone;
	/** The one figure it is about, when it is about one. */
	value?: string;
	unit?: string;
	rows: string[];
	/** The turn's steps, in the order they started, when this is the steps projection (`liveSteps`). */
	steps?: readonly LiveStep[];
	/** A `hud.projection` frame to draw with `Projected`, when this is a model's visual. */
	visual?: { type: string; data: unknown };
	/** The tile it holds (`tiling.ts`): an index into the scene's slots. */
	tile: number;
	/** Its centre, in percent of the stage. */
	at: Point;
	/** The side that faces the core: where its edge line runs. */
	edge: 'left' | 'right' | 'top';
}

/** The board's slots, in percent of the stage, nearest the eye first. */
export const SLOTS: Readonly<Record<Scene, readonly Point[]>> = {
	idle: [
		[20, 24],
		[80, 24]
	],
	listening: [],
	answer: [
		[24, 26],
		[76, 22],
		[73, 62]
	],
	// The steps first, beside the core (it sits at 50, 44); a helper on the other side.
	// Centred at 76 a stack 30vw wide starts clear of the core's light at 1440 (T-0707).
	thinking: [
		[76, 44],
		[24, 30],
		[24, 64]
	],
	music: [],
	// The alert scene's one projection is the approval card; the shell places it.
	alert: [],
	// The core rides in the strip up top: the steps under it, helpers either side.
	working: [
		[50, 64],
		[18, 62],
		[82, 62]
	]
};

export interface StageData {
	sky?: readonly TelemetryReading[];
	readings?: readonly TelemetryReading[];
	jobs?: readonly Job[];
	/** The turn's steps (`liveSteps`), oldest first. */
	steps?: readonly LiveStep[];
	content?: readonly ContentPanel[];
	/** The turn's `hud.projection` frames (`TurnState.visuals`). */
	visuals?: readonly ProjectionFrame[];
	/** Running subagents, each named for the persona it wears (`companionFor`). */
	helpers?: readonly Helper[];
}

export interface Helper {
	id: string;
	who: string;
	goal: string;
}

const helper = (h: Helper): Item => ({ id: `helper:${h.id}`, label: h.who, tone: 'violet', rows: [h.goal] });

type Item = Omit<Shown, 'at' | 'edge' | 'tile'>;

const edgeOf = ([x]: Point): Shown['edge'] => (x < 45 ? 'right' : x > 55 ? 'left' : 'top');

function reading(r: TelemetryReading, label: string): Item {
	const value = r.value === null ? (r.text ?? '—') : String(Math.round(r.value * 10) / 10);
	return { id: `reading:${r.id}`, label, tone: 'accent', value, unit: r.unit, rows: r.sub ? [r.sub] : [] };
}

/**
 * Every step of the turn as one ordered list: one projection per call scattered them over
 * the stage's corners, titled by tool name with raw arguments (the owner, 2026-11-08).
 * `liveSteps` has already left out the calls that only present.
 */
function steps(list: readonly LiveStep[]): Item | null {
	if (!list.length) return null;
	const tone: Tone = list.some((s) => s.state === 'failed') ? 'danger' : list.some((s) => s.state === 'waiting') ? 'warn' : 'accent';
	return { id: 'steps', label: 'Steps', tone, rows: [], steps: list };
}

function visual(f: ProjectionFrame): Item {
	const title = (f.data as { title?: unknown } | null)?.title;
	return { id: `visual:${f.id}`, label: typeof title === 'string' && title ? title : f.type, tone: 'accent', rows: [], visual: { type: f.type, data: f.data } };
}

/** The one line a content panel reads as where it cannot draw its body (the stage, the car's card). */
export function contentLine(p: ContentPanel): string | undefined {
	const b = p.body;
	const line =
		b.type === 'browser' ? b.summary || b.url : b.type === 'document' && b.kind !== 'file' ? b.text.split('\n').find((l) => l.trim()) : undefined;
	return line?.slice(0, 140);
}

function content(p: ContentPanel): Item {
	const line = contentLine(p);
	return { id: `content:${p.spec.id}`, label: p.title || p.body.type, tone: 'accent', rows: line ? [line] : [] };
}

/**
 * The items a scene shows, before placing. Nothing to say → nothing projected.
 * A running helper comes first in idle: it is someone, and a reading can wait a
 * slot. Where work is shown the steps come first and keep the slot beside the core,
 * then what the turn drew, then the helpers.
 */
function itemsFor(scene: Scene, d: StageData): Item[] {
	const helpers = (d.helpers ?? []).map(helper);
	const visuals = (d.visuals ?? []).map(visual);
	switch (scene) {
		case 'idle': {
			// A panel another session pulled up (hud.panel.show) leads: it stays until
			// dismissed, and only the answer scene drew it, so an idle stage hid it.
			const out: Item[] = [...(d.content ?? []).map(content), ...helpers];
			// The sky's first row is the moon or the next pass (`skyView`), not the
			// weather: labelled "Outside", the moon's 98 % lit read as a humidity.
			const sky = d.sky?.[0];
			if (sky) out.push(reading(sky, sky.label));
			const next = d.jobs?.find((j) => j.state !== 'paused' && j.next);
			if (next) out.push({ id: `job:${next.id}`, label: 'Next', tone: 'accent', rows: [next.title, next.next ?? ''] });
			else if (d.readings?.[0]) out.push(reading(d.readings[0], d.readings[0].label));
			return out;
		}
		case 'thinking':
		case 'working': {
			const work = steps(d.steps ?? []);
			return work ? [work, ...visuals, ...helpers] : [...visuals, ...helpers];
		}
		// What the reply drew leads; a hud_show panel's title follows.
		case 'answer':
			return [...visuals, ...(d.content ?? []).map(content)];
		default:
			return [];
	}
}

/**
 * The projections for one scene, each in its tile (the scene's slots, [kept] as the owner
 * arranged them, `tiling.ts`), in tile order. Extra items past the scene's slots are dropped:
 * a fourth projection in a scene drawn for three lands on top of one of them.
 */
export function projectionsFor(scene: Scene, data: StageData, kept?: Tiles): Shown[] {
	const slots = SLOTS[scene];
	const items = itemsFor(scene, data).slice(0, slots.length);
	const tiles = place(items.map((item) => item.id), slots.length, kept);
	return items
		.map((item, i) => ({ ...item, tile: tiles[i], at: slots[tiles[i]], edge: edgeOf(slots[tiles[i]]) }))
		.sort((a, b) => a.tile - b.tile);
}

/** The presence strip's steps for a goal: its subgoals, the current one the first not done. */
export function stripFor(goal: Goal | null | undefined): { task: string; steps: { label: string }[]; current: number } | null {
	if (!goal) return null;
	const subs = goal.subgoals ?? [];
	const steps = subs.length ? subs.map((s) => ({ label: s.label })) : [{ label: goal.turn ? `Turn ${goal.turn}` : 'Working' }];
	const current = subs.findIndex((s) => s.state !== 'done');
	return { task: goal.title, steps, current: current < 0 ? Math.max(0, steps.length - 1) : current };
}
