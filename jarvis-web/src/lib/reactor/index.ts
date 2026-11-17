// The reactor's state table: what the house and the voice mean as one state,
// and its word. The v2 core (`$lib/ds/core`) draws it; the flat instrument
// that used to went with the v2 shell (T-0611).

export {
	IGNITION_LAYERS,
	MARGIN,
	PRECEDENCE,
	RINGS,
	STATES,
	WORDS,
	arcValue,
	bladeTone,
	colorOf,
	haloOf,
	haloPeakOf,
	ignitionLayer,
	isPlan,
	isStill,
	wornState,
	type BladeTone,
	type ReactorStateName
} from './spec';
export {
	REST,
	derive,
	inputsFrom,
	rankOf,
	segmentsOf,
	stateFrom,
	warmingProgress,
	wordOf,
	type Derived,
	type ReactorInputs,
	type Sources
} from './reactorState';
