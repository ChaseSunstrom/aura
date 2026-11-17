/**
 * What one turn did, and what each step of it cost.
 *
 * jarvis-core groups every tool call, model call, approval and subagent into a
 * trace keyed on the context id the bus already carried. This is the shape
 * `jarvis/trace` answers, and only the shape: the readings that once sat beside
 * it (time split, token totals) went with the old task screen, and the
 * `JarvisClient.getTrace` that fetches one keeps the type so a caller reading
 * a span cannot make one up.
 */

export interface Span {
	kind: string;
	name: string;
	started: number;
	ms: number | null;
	ok: boolean;
	error: string | null;
	data: Record<string, unknown>;
}

export interface Trace {
	id: string;
	origin: string;
	label: string;
	task_id: string | null;
	started: number;
	ms: number | null;
	truncated: number;
	spans: number;
	tools: number;
	model_calls: number;
	prompt_tokens: number;
	completion_tokens: number;
	model_ms: number;
	tool_ms: number;
	errors: number;
	spans_detail?: Span[];
}
