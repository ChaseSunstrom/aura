/**
 * An MCP server as `jarvis/mcp/list` reports it.
 *
 * Only the shape. The form that turned what someone typed into a server
 * jarvis-core would accept went with the old integrations screen; the HUD's
 * integrations panel (`hud/data/integrations.ts`) lists servers and removes
 * them, and jarvis-core (`aura/integrations/mcp/`) still runs every check on
 * anything it is sent — this was only ever a convenience, never the control.
 */

export type Transport = 'http' | 'stdio';

export interface McpTool {
	name: string;
	remote_name: string;
	description: string;
}

/** One server as `jarvis/mcp/list` reports it. Never carries the token. */
export interface McpServer {
	name: string;
	transport: Transport;
	url: string;
	command: string;
	args: string[];
	tier: number;
	enabled: boolean;
	/** False for servers written in configuration.yaml — the file owns those. */
	editable: boolean;
	has_token: boolean;
	connected: boolean;
	error: string;
	tools: McpTool[];
	tool_count: number;
	server_info?: Record<string, unknown>;
}
