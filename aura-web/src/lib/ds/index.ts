// The JARVIS v2 design system: the one place a console screen takes its UI
// from (`scripts/verify/ds_boundary.py` holds the line). README.md is the map.

export { default as Projection } from './Projection.svelte';
export { default as Label } from './Label.svelte';
export { default as Figure } from './Figure.svelte';
export { default as LeaderLine } from './LeaderLine.svelte';
export { default as Connector } from './Connector.svelte';
export { default as PresenceStrip } from './PresenceStrip.svelte';
export { default as Subtitles } from './Subtitles.svelte';
export { default as Conversation } from './Conversation.svelte';
export { default as HoldButton } from './HoldButton.svelte';
export { default as RuleChip } from './RuleChip.svelte';
export { default as Core } from './core/Core.svelte';
export { default as Starfield } from './Starfield.svelte';
export { default as ThemeTile } from './ThemeTile.svelte';
export {
	theme,
	device,
	setTheme,
	wearTheme,
	chooseTheme,
	setDeviceTheme,
	setDeviceVoice,
	voiceFor,
	loadDraft,
	askDraft,
	DRAFT_RETRY_MS,
	VOICE_CHOICES,
	type Draft,
	type Worn
} from './theme.svelte';
export { packVars, validPack, baseOf, type Pack } from './pack';
export { default as Projected } from './gen/Projected.svelte';
export {
	PROJECTION_TYPES,
	CONTRACT as PROJECTION_CONTRACT,
	projectionFromEvent,
	validateProjection,
	isEmptyProjection,
	type Placement,
	type ProjectionFrame
} from './gen/contract';

export {
	SCENES,
	POSES,
	STAGE_TIMINGS,
	createStage,
	go,
	view,
	animationOffset,
	type Scene,
	type Stage,
	type StageView,
	type StageTimings,
	type ScenePose,
	type CorePose,
	type Phase
} from './stage';
export { MS, ms, easing, cubicOf, prefersReducedMotion, watchReducedMotion } from './motion';
export { TONES, toneColor, type Tone } from './tone';
export { connectorCurve, cleanPoints, type Point } from './lines';
export {
	CORE_STATES,
	REACTOR_TO_CORE,
	coreStateFor,
	isCoreState,
	type CoreState
} from './core/states';
