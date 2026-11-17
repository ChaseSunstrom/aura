N8N_LIST = {
    "name": "n8n_list_workflows",
    "description": "Which workflows exist in n8n and which are active: id, name, active, webhook paths. Read-only.",
    "parameters": {"type": "object", "properties": {"active_only": {"type": "boolean"}}, "required": []},
}

N8N_TRIGGER = {
    "name": "n8n_trigger",
    "description": ("Fire an n8n workflow through its webhook. `webhook` is the path after /webhook/ (or a full URL); "
                    "`payload` is the JSON body. Side effects depend on the workflow, so this needs approval. "
                    "Returns the HTTP status and the workflow's response."),
    "parameters": {
        "type": "object",
        "properties": {
            "webhook": {"type": "string", "description": "Webhook path (e.g. 'lights-off') or full URL"},
            "payload": {"type": "object", "description": "JSON body to send (optional)"},
            "method": {"type": "string", "enum": ["POST", "GET"], "description": "Default POST"},
            "test": {"type": "boolean", "description": "Use the /webhook-test/ path (workflow open in the editor)"},
        },
        "required": ["webhook"],
    },
}

N8N_EXECUTIONS = {
    "name": "n8n_executions",
    "description": "Did a workflow run, when, and did it fail: recent executions of a workflow (status, start/stop times). Read-only.",
    "parameters": {
        "type": "object",
        "properties": {"workflow_id": {"type": "string"}, "limit": {"type": "integer", "description": "Default 10"}},
        "required": [],
    },
}

N8N_HEALTH = {
    "name": "n8n_health",
    "description": "Is n8n up? Calls /healthz and reports the version when the API key allows it.",
    "parameters": {"type": "object", "properties": {}, "required": []},
}

N8N_GET = {
    "name": "n8n_get_workflow",
    "description": ("One workflow's full definition by id: its nodes (name, type, parameters), "
                    "the connections between them and its settings. Read-only — use it before "
                    "n8n_update_workflow, which replaces the definition wholesale. A definition "
                    "too large for a turn comes back trimmed, and the result says what was "
                    "dropped and at which character cap."),
    "parameters": {
        "type": "object",
        "properties": {"workflow_id": {"type": "string",
                                       "description": "The id n8n_list_workflows reports"}},
        "required": ["workflow_id"],
    },
}

N8N_CREATE = {
    "name": "n8n_create_workflow",
    "description": ("Create an n8n workflow from a name, a nodes array and a connections object "
                    "(the same shapes n8n_get_workflow returns). The workflow is created "
                    "inactive; n8n_activate_workflow is what makes it fire. Needs approval."),
    "parameters": {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Workflow name as it will appear in n8n"},
            "nodes": {"type": "array", "items": {"type": "object"},
                      "description": "n8n node objects: name, type, typeVersion, position, "
                                     "parameters"},
            "connections": {"type": "object",
                            "description": "n8n connection map, keyed by source node name "
                                           "({} for one node)"},
            "settings": {"type": "object",
                         "description": "Workflow settings (optional; executionOrder v1 by "
                                        "default)"},
        },
        "required": ["name", "nodes", "connections"],
    },
}

N8N_UPDATE = {
    "name": "n8n_update_workflow",
    "description": ("Replace an existing workflow's definition. n8n's PUT overwrites the whole "
                    "workflow, so any of nodes/connections/settings left out is carried over "
                    "from the current definition — read it with n8n_get_workflow first when you "
                    "are editing rather than replacing. `name` is required because the API "
                    "requires it: pass the current name unless you mean to rename. "
                    "Needs approval."),
    "parameters": {
        "type": "object",
        "properties": {
            "workflow_id": {"type": "string", "description": "The id n8n_list_workflows reports"},
            "name": {"type": "string", "description": "The workflow's name after the update"},
            "nodes": {"type": "array", "items": {"type": "object"},
                      "description": "Replacement node array"},
            "connections": {"type": "object", "description": "Replacement connection map"},
            "settings": {"type": "object", "description": "Replacement workflow settings"},
        },
        "required": ["workflow_id", "name"],
    },
}

N8N_ACTIVATE = {
    "name": "n8n_activate_workflow",
    "description": ("Activate or deactivate a workflow. An active workflow fires on its own "
                    "trigger — a schedule, a webhook, a poll — so this starts (or stops) it "
                    "running unattended. Needs approval."),
    "parameters": {
        "type": "object",
        "properties": {
            "workflow_id": {"type": "string", "description": "The id n8n_list_workflows reports"},
            "active": {"type": "boolean", "description": "true to activate, false to deactivate"},
        },
        "required": ["workflow_id", "active"],
    },
}

#: The one list of tool names the approval hook gates. Every tool that changes
#: n8n — fires a workflow or writes one — belongs here; a tool added to the
#: plugin without an entry here reaches n8n unapproved.
N8N_EXECUTION = {
    "name": "n8n_execution",
    "description": ("One n8n execution in detail: its status, the node it stopped on and the "
                    "error n8n recorded, with each node's last output item (capped). Use it after "
                    "n8n_executions to see WHY a run failed before changing the workflow."),
    "parameters": {"type": "object",
                   "properties": {"execution_id": {"type": "string",
                                                   "description": "The id n8n_executions reports"}},
                   "required": ["execution_id"]},
}

N8N_DELETE = {
    "name": "n8n_delete_workflow",
    "description": ("Delete a workflow from n8n. Irreversible: the definition and its execution "
                    "history go with it. Prefer n8n_activate_workflow with active=false to stop "
                    "one from running; delete only when the person asks for it gone. Needs approval."),
    "parameters": {"type": "object",
                   "properties": {"workflow_id": {"type": "string",
                                                  "description": "The id n8n_list_workflows reports"}},
                   "required": ["workflow_id"]},
}

N8N_CREDENTIAL_SCHEMA = {
    "name": "n8n_credential_schema",
    "description": ("The fields a credential type takes (e.g. 'httpBasicAuth', 'slackApi', "
                    "'openAiApi'), so n8n_credential_create can be filled in correctly."),
    "parameters": {"type": "object",
                   "properties": {"type": {"type": "string", "description": "The n8n credential type name"}},
                   "required": ["type"]},
}

N8N_CREDENTIAL_CREATE = {
    "name": "n8n_credential_create",
    "description": ("Store a credential in n8n for workflows to use (name, type, and the fields "
                    "n8n_credential_schema lists). The secret values are sent to n8n and never "
                    "echoed back. Needs approval."),
    "parameters": {"type": "object",
                   "properties": {"name": {"type": "string"},
                                  "type": {"type": "string", "description": "The n8n credential type name"},
                                  "data": {"type": "object", "description": "The credential's fields"}},
                   "required": ["name", "type", "data"]},
}

N8N_CREDENTIAL_DELETE = {
    "name": "n8n_credential_delete",
    "description": "Delete a stored credential by id; workflows using it will fail until re-pointed. Needs approval.",
    "parameters": {"type": "object",
                   "properties": {"credential_id": {"type": "string"}},
                   "required": ["credential_id"]},
}

DESTRUCTIVE = {"n8n_trigger", "n8n_create_workflow", "n8n_update_workflow", "n8n_activate_workflow",
               "n8n_delete_workflow", "n8n_credential_create", "n8n_credential_delete"}
