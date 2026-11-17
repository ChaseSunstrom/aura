"""The five self-extension tool schemas.

Wording matters more than usual here: these descriptions are the only instruction
the model gets about the *order* of the loop, and a model that calls
``selfext_open_mr`` before ``selfext_verify`` produces a merge request with no
evidence in it. Each description therefore names what must have happened first.
"""
from __future__ import annotations

#: The tools array both plan and scaffold take. Declared once because a mismatch
#: between the two would let a plan validate and its scaffold fail.
_TOOLS_PROP = {
    "type": "array",
    "description": "The tools the new plugin will register. Each is "
                   "{name, description, parameters} where parameters is a JSON Schema object.",
    "items": {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "lowercase identifier, e.g. weather_now"},
            "description": {"type": "string", "description": "what the tool does, for the model"},
            "parameters": {"type": "object", "description": "JSON Schema for the arguments"},
        },
        "required": ["name", "description"],
    },
}

SELFEXT_PLAN = {
    "name": "selfext_plan",
    "description": ("Plan the capability before building it: turn a missing capability into a "
                    "written spec of the plugin name, its tools, "
                    "whether it needs an approval gate, which files will be written and which "
                    "branch will carry them. Writes nothing. Call this first so the owner can "
                    "judge the shape before any code exists."),
    "parameters": {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "plugin directory name, e.g. weather-now"},
            "why": {"type": "string", "description": "the capability that is missing today"},
            "toolset": {"type": "string", "description": "toolset name; defaults from the plugin name"},
            "tools": _TOOLS_PROP,
            "approval": {"type": "boolean", "description": "true when a tool changes the world"},
            "url": {"type": "string", "description": "the service's URL or DSN; discovery runs first and its answer joins the plan"},
            "command": {"type": "string", "description": "a CLI on this host; same"},
        },
        "required": ["name", "tools"],
    },
}

SELFEXT_SCAFFOLD = {
    "name": "selfext_scaffold",
    "description": ("Scaffold a new plugin: write the new plugin, its pytest file and (with "
                    "bench_prompt) a benchmark "
                    "task into the working checkout. Writes only under hermes/plugins/<name>/ "
                    "and bench/tasks/. Each handler comes back returning its arguments with a "
                    "TODO: fill them in before opening a merge request."),
    "parameters": {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "plugin directory name"},
            "description": {"type": "string", "description": "one sentence for plugin.yaml"},
            "toolset": {"type": "string"},
            "tools": _TOOLS_PROP,
            "approval": {"type": "boolean", "description": "register a pre_tool_call gate"},
            "bench_prompt": {"type": "string",
                             "description": "the prompt of a JCB task that proves the deployed "
                                            "capability; omit for no task"},
        },
        "required": ["name", "tools"],
    },
}

SELFEXT_VERIFY = {
    "name": "selfext_verify",
    "description": ("Run over the scaffolded plugin what CI will run over it: ruff, the "
                    "per-plugin mypy, vulture, the pinned benchmark-corpus counts, and its own "
                    "pytest — returning each verdict with the tail of its output. Needs "
                    "approval, because pytest executes the test file just written. This is the "
                    "evidence the merge request carries; run it after every edit and before "
                    "selfext_open_mr."),
    "parameters": {
        "type": "object",
        "properties": {"name": {"type": "string", "description": "plugin directory name"}},
        "required": ["name"],
    },
}

SELFEXT_OPEN_MR = {
    "name": "selfext_open_mr",
    "description": ("Create the capability's branch, commit only its own files (plus any reviewable "
                    "`paths`), push, and ask GitLab to open a merge request -- the way to propose any "
                    "change to JARVIS's own repository, a README line included. Needs approval. Nothing is merged and the "
                    "target branch is never pushed to. Refuses while the checkout carries edits "
                    "outside this capability's files. Run selfext_verify first: an unverified "
                    "merge request wastes the reviewer's time."),
    "parameters": {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "plugin directory name"},
            "title": {"type": "string", "description": "merge request title, sentence style"},
            "description": {"type": "string",
                            "description": "what was missing, what this adds, what it does not "
                                           "cover, and the test output"},
            "paths": {"type": "array", "items": {"type": "string"},
                      "description": ("Other files this merge request carries, repository-relative: "
                                      "a README or other top-level markdown, docs/, hermes/skills/, "
                                      "hermes/home/, bench/tasks/ or an ordinary plugin. For a change "
                                      "that is not a new capability, `name` is just the branch label "
                                      "(e.g. readme-note). Never CI, deploy, scripts or build files.")},
        },
        "required": ["name", "description"],
    },
}

SELFEXT_STATUS = {
    "name": "selfext_status",
    "description": ("Where the self-extension checkout stands: branch, head commit, target "
                    "branch, and anything uncommitted. Read-only."),
    "parameters": {"type": "object", "properties": {}, "required": []},
}

INTEGRATE_DISCOVER = {
    "name": "integrate_discover",
    "description": (
        "Work out how a service can be reached, before anything is generated: OpenAPI, GraphQL, "
        "MCP, a CLI, a database, a webhook, a web UI to drive in a browser, or a GUI that only "
        "computer_use can touch. Give it a URL or a command. Read-only — it fetches at most a "
        "handful of well-known paths, or runs `<command> --help`, and decides nothing: the "
        "answer is an input to selfext_plan."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "The service's URL, or a database DSN."},
            "command": {"type": "string", "description": "A CLI on this host, e.g. `ffmpeg`."},
        },
        "required": [],
    },
}

SKILL_PROPOSE = {
    "name": "skill_propose",
    "description": (
        "Propose a skill after a task that took a long chain of tool calls and succeeded: "
        "what was done by hand, the tools in the order that worked, the dead ends, what "
        "should vary, the plugin name it would become and the request that would prove it "
        "as a benchmark task. Writes one row to the review queue and shows it on the HUD; "
        "nothing is built until a person approves it with skill_proposals. Call it after "
        "any task of more than a few tool calls you would not want to repeat by hand."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "task": {"type": "string", "description": "what was done, in one sentence"},
            "chain": {"type": "array", "items": {"type": "string"},
                      "description": "the tool names in the order that worked"},
            "name": {"type": "string",
                     "description": "the plugin/skill directory name it would become, e.g. weekly-backup-report"},
            "tool": {"type": "string",
                     "description": "the tool name, e.g. weekly_backup_report; defaults from name"},
            "bench_prompt": {"type": "string",
                             "description": "the request a JCB task would send to prove the skill"},
            "dead_ends": {"type": "array", "items": {"type": "string"},
                          "description": "what was tried and did not work"},
            "parameters": {"type": "array", "items": {"type": "string"},
                           "description": "what should vary between runs"},
            "against": {"type": "string",
                        "description": "the strongest case for NOT building it: an existing "
                                       "skill or tool that covers it, how rarely it recurs, "
                                       "what it costs to keep; required, and not the task "
                                       "restated"},
        },
        "required": ["task", "chain", "name", "bench_prompt", "against"],
    },
}

SKILL_PROPOSALS = {
    "name": "skill_proposals",
    "description": (
        "The skill review queue. list: the open proposals. approve <id>: run selfext_plan "
        "and selfext_scaffold for it on its branch — the plugin, its test, a SKILL.md under "
        "hermes/skills/<name>/ and a benchmark task, uncommitted — then fill in the handler, "
        "selfext_verify and selfext_open_mr as for any capability. drop <id>: dismiss it; "
        "the row stays as dropped."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["list", "approve", "drop"]},
            "id": {"type": "string", "description": "the proposal id, for approve and drop"},
            "all": {"type": "boolean", "description": "list approved and dropped rows too"},
        },
        "required": ["action"],
    },
}

#: The tools that ask before they act. `selfext_open_mr` because it leaves the
#: checkout; `selfext_verify` because it runs `python3 -m pytest` over a plugin the
#: model finished writing moments ago, as the gateway user and in no sandbox, and
#: pytest collects a `conftest.py` before the first test runs.
#:
#: Gated here as well as in the approval matrix, not only there. The matrix is the
#: governor and answers first, but it is registered by `aura-guard`, and an image
#: shipped without that plugin has no matrix at all — which was true of the running
#: stack on 2026-09-03. A control that exists only in the table is a control that
#: is one deploy away from not existing.
DESTRUCTIVE = {"selfext_open_mr", "selfext_verify"}
