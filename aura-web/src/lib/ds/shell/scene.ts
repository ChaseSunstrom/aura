// What the stage shows for what JARVIS is doing: the reactor's eleven states
// (derived from the socket, the voice and the house) land on the stage's seven
// scenes, and the core wears its own state inside that scene.
//
// Pure, so the shell renders `sceneFor(...)` and the mapping is pinned by a
// unit test instead of by a switch buried in a component.

import { coreStateFor, type CoreState } from '../core/states';
import type { Scene } from '../stage';
import type { ReactorStateName } from '$lib/reactor';

/**
 * Reactor state → scene.
 *
 * - `speaking` → answer: the reply's projections are out while it is said.
 * - `warming` → thinking: the model loading reads as "give me a second"; the
 *   core says embedding (violet) inside the thinking scene.
 * - `tool` → thinking, with an acting core: one tool call inside a turn shows as
 *   a projection beside the core. Lifting the core into the strip for each call
 *   would bounce it up and down a turn that calls three tools.
 * - `goal` → working: the core rises into the presence strip for the long job.
 * - `attention`, `alert`, `kill` → alert: something waits on the user.
 * - `offline` → idle, with an alert core: nothing is out, and the core must
 *   not claim to be ready (`REACTOR_TO_CORE`).
 */
export const SCENE_OF: Readonly<Record<ReactorStateName, Scene>> = {
	idle: 'idle',
	listening: 'listening',
	thinking: 'thinking',
	speaking: 'answer',
	tool: 'thinking',
	goal: 'working',
	attention: 'alert',
	warming: 'thinking',
	alert: 'alert',
	kill: 'alert',
	offline: 'idle'
};

const GOAL_TURNS: ReadonlySet<ReactorStateName> = new Set(['idle', 'thinking', 'tool', 'speaking', 'warming', 'goal']);

export interface SceneInput {
	reactor: ReactorStateName;
	/** A /goal is live (set, continuing or parked). */
	goal?: boolean;
	/** Music is playing and nothing else is going on. */
	music?: boolean;
	/** Something a turn drew is still held out (`visualsHeld`, and not sent back). */
	visual?: boolean;
}

/** The scene and the core's state for one moment. */
export function sceneFor({ reactor, goal = false, music = false, visual = false }: SceneInput): { scene: Scene; core: CoreState } {
	// A live goal is a job under way: the strip stays up at rest and through the
	// goal's own turns (its thinking, its tools, its spoken updates), so the core
	// does not drop out of the strip and back for every turn. The person talking
	// or a question waiting still takes the stage.
	if (goal && GOAL_TURNS.has(reactor)) return { scene: 'working', core: 'working' };
	// What was drawn stays where the answer put it once the last word is said, with the core
	// at rest. With speech off there was no answer scene at all, so a list was out only while
	// the model worked, and a frame another session shared with a resting console was never
	// drawn (T-1472). Only at rest: listening, a turn, a question and offline keep their own
	// scenes, and music waits until this goes back.
	if (reactor === 'idle' && visual) return { scene: 'answer', core: coreStateFor(reactor) };
	if (reactor === 'idle' && music) return { scene: 'music', core: 'music' };
	const scene = SCENE_OF[reactor];
	// The working scene's core is the lifted one whatever the reactor says.
	const core = scene === 'working' ? 'working' : coreStateFor(reactor);
	return { scene, core };
}

/** The demo override (`jarvis:orb-demo`): a scene name, a reactor state, or '' to clear. */
export function demoScene(raw: unknown): Scene | ReactorStateName | null {
	if (typeof raw !== 'string' || !raw) return null;
	if (raw === 'error') return 'alert';
	// Own keys only: `constructor` is "in" every object literal.
	if (Object.hasOwn(SCENE_OF, raw)) return raw as ReactorStateName;
	if (raw === 'answer' || raw === 'music' || raw === 'working') return raw;
	return null;
}
