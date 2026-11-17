"""jarvis-selfext — the governed loop by which JARVIS grows a capability (V2 D24).

Five tools: plan a missing capability, scaffold it into the working checkout,
lint and test it there, open a GitLab merge request for it, and report where the
checkout stands. The merge request is the boundary: everything before it happens
inside a checkout, and nothing after it happens without a person. There is no
merge tool, and adding one would defeat the design rather than complete it.

Only ``selfext_open_mr`` passes the approval gate, because it is the only tool
that reaches a remote. The gate's message names the branch and the target so the
person answering it can tell "open a merge request against main" from "push
something to main", which are not the same question.

Every tool is gated on ``selfext.repo_path`` being a git work tree, so on a
deployment where no checkout was mounted the tools do not appear at all — the
list a reader sees is the list that would work.
"""
from __future__ import annotations

import hashlib
import logging
import sys
from pathlib import Path

from aura_schema import from_env

# `_shared/` is a sibling dir, reachable only via sys.path: see _shared/_approval_hook.py.
_PLUGINS_ROOT = str(Path(__file__).resolve().parent.parent)
if _PLUGINS_ROOT not in sys.path:
    sys.path.append(_PLUGINS_ROOT)

from _shared._approval_hook import approval_gate, guarded_command  # noqa: E402
from . import git, proposals, schemas, tools  # noqa: E402

logger = logging.getLogger(__name__)

TOOLSET = "selfext"


def _available() -> bool:
    return git.available()


def _gate(tool_name: str, args: dict) -> dict | None:
    if tool_name not in schemas.DESTRUCTIVE:
        return None
    cfg = from_env()
    name = str(args.get("name") or "?")
    if tool_name == "selfext_verify":
        return {
            "action": "approve",
            "message": (f"JARVIS will run ruff, mypy, vulture and pytest over the {name} "
                        f"plugin it just wrote. pytest executes that plugin's test file, "
                        f"and any conftest.py beside it, as this user and in no sandbox."),
            "rule_key": "selfext:verify",
        }
    branch = cfg.selfext_branch_prefix + name
    raw = args.get("paths")
    extra = sorted(str(p) for p in raw) if isinstance(raw, list) else []
    carries = f" It also carries: {', '.join(extra)}." if extra else ""
    # The extra paths are in the key: a remembered "always" for the bare capability must not
    # also wave through a later request that carries files the owner never saw named.
    key = f"selfext:open_mr:{name}"
    if extra:
        key += ":" + hashlib.sha256("\n".join(extra).encode()).hexdigest()[:12]
    return {
        "action": "approve",
        "message": (f"JARVIS will push the branch '{branch}' to {cfg.selfext_remote} and open a "
                    f"merge request against '{cfg.selfext_target_branch}' for the {name} "
                    f"capability.{carries} Nothing is merged."),
        "rule_key": key,
    }


def register(ctx) -> None:
    from_env()  # fail fast: a mistyped SELFEXT_* value names itself at load, not at first use
    for name, schema, handler in (
        ("selfext_plan", schemas.SELFEXT_PLAN, tools.handle_selfext_plan),
        ("selfext_scaffold", schemas.SELFEXT_SCAFFOLD, tools.handle_selfext_scaffold),
        ("selfext_verify", schemas.SELFEXT_VERIFY, tools.handle_selfext_verify),
        ("selfext_open_mr", schemas.SELFEXT_OPEN_MR, tools.handle_selfext_open_mr),
        ("selfext_status", schemas.SELFEXT_STATUS, tools.handle_selfext_status),
        # v3 Part 3: how a service can be reached, before anything is generated.
        ("integrate_discover", schemas.INTEGRATE_DISCOVER, tools.handle_integrate_discover),
        # The distillation loop (media brief M10): a proposal is a row and a notice;
        # approving one runs plan + scaffold on a branch and stops where the loop
        # stops, at selfext_open_mr's gate.
        ("skill_propose", schemas.SKILL_PROPOSE, tools.handle_skill_propose),
        ("skill_proposals", schemas.SKILL_PROPOSALS, tools.handle_skill_proposals),
    ):
        ctx.register_tool(name=name, toolset=TOOLSET, schema=schema, handler=handler,
                          check_fn=_available, emoji="\N{SEEDLING}")
    # The queue outlives the process (a cron turn writes it, a HUD session reads it):
    # plugin state, a quota-bounded JSON file under HERMES_HOME/plugin-data.
    proposals.attach_state(getattr(ctx, "state", None))

    ctx.register_hook("pre_tool_call", approval_gate(_gate))
    ctx.register_command(
        "selfext",
        handler=guarded_command(lambda raw: tools.handle_selfext_status({}), "selfext",
                                logger=logger),
        description="Where the self-extension checkout stands (branch, head, uncommitted)",
    )
    ctx.register_command(
        "skill_proposals",
        handler=guarded_command(proposals.handle_command, "skill_proposals", logger=logger),
        description="The skill review queue: list | approve <id> | drop <id>",
        args_hint="list | approve <id> | drop <id>",
    )
