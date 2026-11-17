import { describe, expect, it } from 'vitest';

import { STATES } from '$lib/reactor';
import { SCENES } from '../stage';
import { SCENE_OF, demoScene, sceneFor } from './scene';

describe('sceneFor', () => {
	it('every reactor state has a scene', () => {
		for (const s of STATES) expect(SCENES).toContain(SCENE_OF[s]);
	});

	it('a reply is the answer scene with a speaking core', () => {
		expect(sceneFor({ reactor: 'speaking' })).toEqual({ scene: 'answer', core: 'speaking' });
	});

	it('a tool call stays beside the core; a goal lifts it into the working scene', () => {
		expect(sceneFor({ reactor: 'tool' })).toEqual({ scene: 'thinking', core: 'acting' });
		expect(sceneFor({ reactor: 'goal' })).toEqual({ scene: 'working', core: 'working' });
	});

	it('a live goal keeps the strip up through its own turns; the person or a question takes the stage', () => {
		expect(sceneFor({ reactor: 'idle', goal: true })).toEqual({ scene: 'working', core: 'working' });
		for (const reactor of ['thinking', 'tool', 'speaking', 'warming'] as const) expect(sceneFor({ reactor, goal: true })).toEqual({ scene: 'working', core: 'working' });
		expect(sceneFor({ reactor: 'listening', goal: true }).scene).toBe('listening');
		expect(sceneFor({ reactor: 'attention', goal: true }).scene).toBe('alert');
		expect(sceneFor({ reactor: 'offline', goal: true }).scene).toBe('idle');
	});

	it('offline shows nothing, and the core does not claim to be ready', () => {
		expect(sceneFor({ reactor: 'offline' })).toEqual({ scene: 'idle', core: 'alert' });
	});

	it('an approval is the alert scene', () => {
		expect(sceneFor({ reactor: 'attention' }).scene).toBe('alert');
	});

	it('music shows only at rest', () => {
		expect(sceneFor({ reactor: 'idle', music: true }).scene).toBe('music');
		expect(sceneFor({ reactor: 'thinking', music: true }).scene).toBe('thinking');
	});

	// T-1472: with speech off there was no answer scene at all, so a list asked for in a typed
	// turn was out only while the model worked, and a projection another session sent to a
	// resting console was never drawn.
	it('what was drawn stays out at rest: the answer scene, with a core that is not speaking', () => {
		expect(sceneFor({ reactor: 'idle', visual: true })).toEqual({ scene: 'answer', core: 'idle' });
		// Music waits for it to go back.
		expect(sceneFor({ reactor: 'idle', visual: true, music: true }).scene).toBe('answer');
		expect(sceneFor({ reactor: 'idle' }).scene).toBe('idle');
	});

	it('a held visual takes the stage from nothing that is going on', () => {
		expect(sceneFor({ reactor: 'speaking', visual: true })).toEqual({ scene: 'answer', core: 'speaking' });
		expect(sceneFor({ reactor: 'thinking', visual: true }).scene).toBe('thinking');
		expect(sceneFor({ reactor: 'listening', visual: true }).scene).toBe('listening');
		expect(sceneFor({ reactor: 'attention', visual: true }).scene).toBe('alert');
		expect(sceneFor({ reactor: 'idle', visual: true, goal: true }).scene).toBe('working');
		// Offline, the core must not look ready behind a chart: nothing is out.
		expect(sceneFor({ reactor: 'offline', visual: true })).toEqual({ scene: 'idle', core: 'alert' });
	});
});

describe('demoScene', () => {
	it('takes a reactor state, a scene, error, or clears', () => {
		expect(demoScene('thinking')).toBe('thinking');
		expect(demoScene('working')).toBe('working');
		expect(demoScene('error')).toBe('alert');
		expect(demoScene('')).toBeNull();
		expect(demoScene('constructor')).toBeNull();
		expect(demoScene(4)).toBeNull();
	});
});
