"""jarvis-artifacts — the agent's way into the artifact store.

Registers the ``artifacts`` toolset so a tool that produces media has somewhere
to put it that every surface can reach, which is what the media brief asks for
when it says to retire "here's a path on the container".

The store itself is the adapter's; this is a client. One implementation of the
retention rules and the size caps, in the one service that already terminates
the bearer key and mounts the volume.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

_PLUGINS_ROOT = str(Path(__file__).resolve().parent.parent)
if _PLUGINS_ROOT not in sys.path:
    sys.path.append(_PLUGINS_ROOT)

from _shared._approval_hook import approval_gate  # noqa: E402

# `surfaces` is imported for its side effect on the package namespace: the
# plugin loader and the routing rule both reach it as `plugin.surfaces`.
from . import deliver, recording, schemas, surfaces, toolchain, tools  # noqa: E402,F401

logger = logging.getLogger(__name__)

TOOLSET = "artifacts"


def _available() -> bool:
    """The store is reachable only when the adapter's key is in the environment."""
    return bool(tools._key())


def register(ctx) -> None:
    ctx.register_tool(name="artifact_publish", toolset=TOOLSET, schema=schemas.ARTIFACT_PUBLISH,
                      handler=tools.handle_publish, check_fn=_available, emoji="📦")
    ctx.register_tool(name="artifact_list", toolset=TOOLSET, schema=schemas.ARTIFACT_LIST,
                      handler=tools.handle_list, check_fn=_available, emoji="📦")
    ctx.register_tool(name="artifact_get", toolset=TOOLSET, schema=schemas.ARTIFACT_GET,
                      handler=tools.handle_get, check_fn=_available, emoji="📦")
    ctx.register_tool(name="jarvis_health", toolset=TOOLSET, schema=schemas.AURA_HEALTH,
                      handler=tools.handle_health, check_fn=_available, emoji="🩺")
    ctx.register_tool(name="artifact_pin", toolset=TOOLSET, schema=schemas.ARTIFACT_PIN,
                      handler=tools.handle_pin, check_fn=_available, emoji="📦")
    ctx.register_tool(name="artifact_delete", toolset=TOOLSET, schema=schemas.ARTIFACT_DELETE,
                      handler=tools.handle_delete, check_fn=_available, emoji="📦")
    # Gated by the matrix (`media:fetch`): the one artifact verb that reaches the
    # public internet from the gateway.
    ctx.register_tool(name="media_fetch", toolset=TOOLSET, schema=schemas.MEDIA_FETCH,
                      handler=tools.handle_media_fetch, check_fn=_available, emoji="📦")

    ctx.register_tool(name="surface_capabilities", toolset=TOOLSET,
                      schema=schemas.SURFACE_CAPABILITIES,
                      handler=tools.handle_surface_capabilities, emoji="📦")
    # The handoff: what the routing rule (`surfaces.route`) calls for when the
    # surface someone is on cannot show a thing. Free in the matrix — it moves
    # something the house already produced between the person's own screens.
    ctx.register_tool(name="artifact_send", toolset=TOOLSET, schema=schemas.ARTIFACT_SEND,
                      handler=tools.handle_send, check_fn=_available, emoji="📦")

    # A recording to words, on the house's own transcriber (T-0825). Free in the matrix for
    # the owner and never a guest's; the transcript is outside text, so the turn is marked
    # (jarvis-hud's later.note_tool marks every tool it does not list as quiet).
    ctx.register_tool(name="recording_transcribe", toolset=TOOLSET, schema=schemas.RECORDING_TRANSCRIBE,
                      handler=recording.handle_recording_transcribe, check_fn=_available, emoji="🎙️")

    ctx.register_tool(name="toolchain_check", toolset=TOOLSET, schema=schemas.TOOLCHAIN_CHECK,
                      handler=tools.handle_toolchain_check, emoji="📦")

    ctx.register_hook("pre_tool_call", approval_gate(tools.build_approval))
    # browser_vision's screenshot stays on the HUD as the task's receipt (T-0428).
    ctx.register_hook("post_tool_call", tools.browser_receipt)
    # One line in every new session's prompt saying what the screen it is on
    # can show, so planning starts from a fact rather than a deduction (the
    # brief: "the reasoning budget spent deciding that is itself the bug").
    # Optional on a Hermes without sections, so the tools still register.
    if hasattr(ctx, "register_system_prompt_section"):
        ctx.register_system_prompt_section("jarvis-surface", tools.render_surface_section,
                                           position="after_memory")
