// The data layer: the live sources the console already has — the Hermes
// link's snapshot, the house socket's registries and commands, the console's
// own endpoints — mapped onto plain shapes (`glass/bodies/types.ts`).
//
// All pure, all here, so a payload change lands in one file with one test.
// Nothing in this folder fetches, subscribes or draws: the v2 shell
// (`$lib/ds/shell/Shell.svelte`) gathers the sources and its projections and
// screens draw the results. Some mappers outlived the legacy panels that drew
// them and are reached only by their tests (T-0665 decides each).

export { approvalRequests, sourceOf as approvalSourceOf, type ApprovalSource } from './approvals';
export { auditView, type Decision } from './audit';
export { automationsView, describeTrigger, describeTriggers } from './automations';
export { chatTurns } from './chat';
export { noteDocument, noteItems, type DocumentItem, type DocumentView, type NoteRow } from './document';
export { goalView } from './goals';
export { devicesView, nameDevice, nameOffers, stateWord, SWITCHABLE, type HouseSources, type NameOffer } from './house';
export { accountsView, integrationsView, type AccountRow, type AccountState, type CatalogueRow, type ExtensionRow, type IntegrationSources } from './integrations';
export { fromCron, fromScheduled, jobSourceOf, jobsView, type JobSource } from './jobs';
export { kanbanTasks, runStateOfTask } from './kanban';
export { memoryGraph, memoryNotes, sourceWord, titleOf, type MemoryEntry } from './memory';
export { EMPTY_MODELS, gpusView, modelsView, pollMs, type ModelsData } from './models';
export { EMPTY_LISTS, modelView, plainLabel, toolsetViews, type ModelView } from './model';
export { levelOf, noticeOfPanel, noticesView } from './notifications';
export { groupCode, pairingPayload, pairingView, type PairingSources, type PairingView } from './pairing';
export { CONTENT_SUFFIX, contentIdOf, contentPanel, contentPanels, DOWNLOAD_MIMES, provenanceOf, RESERVED, servedAsDownload, type ContentBody, type ContentPanel } from './panels';
export {
	APPLIES_WORDS,
	coreSpecs,
	settingsCatalogue,
	settingsMeta,
	settingsNotice,
	settingsValues,
	settingWrite,
	SOURCE_WORDS,
	validate as validateSetting,
	withModelChoices,
	type CatalogueSpec,
	type EnvVariable,
	type SettingMeta,
	type SettingsCatalogue,
	type SettingsSources,
	type SettingWrite
} from './settings';
export { taskCalls, taskOutput, taskSteps, taskView, type TaskView } from './task';
export { runningGoal, runningTask, runningView, type RunningView } from './running';
export { chartReadings, gpuReadings, readingsView, skyView, tableReadings } from './telemetry';
export { agoOf, clockOf, secondsToMs, stampOf, untilOf } from './time';
export { timelineCalls } from './timeline';
export { tokensView, type TokenRow } from './tokens';
export { voiceStages, type Stage, type VoiceStamps } from './voice';
export { latencyRows, type LatencyRow } from './latency';
export {
	COLUMNS as TASK_COLUMNS,
	board as taskBoard,
	queue as taskQueue,
	sourceOf as taskSourceOf,
	taskStore,
	timeline as taskTimeline,
	type TaskAction,
	type TaskKind,
	type TaskRecord,
	type TaskSource
} from './taskStore';
