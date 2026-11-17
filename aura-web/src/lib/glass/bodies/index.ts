// The panel data's shapes. The legacy bodies that drew them went with the v2
// shell (T-0611); `$lib/hud/data` still maps the feeds into these shapes, and
// the v2 projections and screens read them.

export type {
	ApprovalRequest,
	AuditEntry,
	Automation,
	CatalogueProblem,
	ChatTurn,
	Device,
	Goal,
	GoalGate,
	Integration,
	Job,
	KanbanTask,
	MemoryNote,
	Notice,
	RunState,
	SettingGroup,
	SettingSpec,
	TaskStep,
	TelemetryReading,
	TimelineCall,
	Toolset
} from './types';
