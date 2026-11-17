"""jarvis-files — the household file share over WebDAV.

Registers the ``files_share`` toolset (``fileshare_list`` / ``fileshare_read`` /
``files_search`` read-only, ``fileshare_write`` / ``fileshare_delete`` behind the approval gate)
and the ``/files`` slash command. The toolset is deliberately **not** called
``file``: Hermes already owns a ``file`` toolset for the local filesystem, and
registering a second one under that name would shadow it, leaving the model to
choose between two different meanings of "read a file".

The HTTP and XML work is in ``_shared._dav``, shared with jarvis-calendar and
jarvis-contacts; nothing here touches the network at import time, so the package
loads with no DAV server anywhere.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

# `_shared/` is a sibling dir, reachable only via sys.path: see _shared/_dav.py.
_PLUGINS_ROOT = str(Path(__file__).resolve().parent.parent)
if _PLUGINS_ROOT not in sys.path:
    sys.path.append(_PLUGINS_ROOT)

from aura_schema import from_env  # noqa: E402

from _shared._approval_hook import approval_gate, guarded_command  # noqa: E402
from _shared import _dav  # noqa: E402
from . import schemas, tools  # noqa: E402

logger = logging.getLogger(__name__)

#: Hermes toolset key — not `file`, which the built-in local-filesystem toolset owns.
TOOLSET = "files_share"


def register(ctx) -> None:
    # Read the settings once here, where Hermes reports which plugin failed to
    # load: a mistyped DAV_VERIFY_SSL otherwise first surfaces inside a tool
    # call, looking like a server fault.
    from_env()
    ctx.register_tool(name="fileshare_list", toolset=TOOLSET, schema=schemas.FILESHARE_LIST,
                      handler=tools.handle_list, check_fn=_dav.available, emoji="📁")
    ctx.register_tool(name="fileshare_read", toolset=TOOLSET, schema=schemas.FILESHARE_READ,
                      handler=tools.handle_read, check_fn=_dav.available, emoji="📁")
    ctx.register_tool(name="files_search", toolset=TOOLSET, schema=schemas.FILES_SEARCH,
                      handler=tools.handle_search, check_fn=_dav.available, emoji="📁")
    ctx.register_tool(name="fileshare_write", toolset=TOOLSET, schema=schemas.FILESHARE_WRITE,
                      handler=tools.handle_write, check_fn=_dav.available, emoji="📁")
    ctx.register_tool(name="fileshare_delete", toolset=TOOLSET, schema=schemas.FILESHARE_DELETE,
                      handler=tools.handle_delete, check_fn=_dav.available, emoji="📁")

    ctx.register_hook("pre_tool_call", approval_gate(tools.build_approval))
    ctx.register_command(
        "files",
        handler=guarded_command(tools.handle_command, "files", logger=logger),
        description="List the DAV file share: the root, or one directory under it",
        args_hint="[path]",
    )
