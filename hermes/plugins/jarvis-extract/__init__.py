"""jarvis-extract — the local backend Hermes' web_extract reads pages through.

A ``kind: backend`` plugin, so it registers no tool and no command of its own:
only the web provider that ``web.extract_backend: jarvis-extract`` names, which
calls the stack's own extraction service (``deploy/extract``). Loaded by Hermes
from ``$HERMES_HOME/plugins`` on the volume; listed in ``plugins.enabled`` in
``deploy/config.template.yaml``.
"""
from __future__ import annotations

import logging

from aura_schema import from_env

from . import provider

_LOGGER = logging.getLogger(__name__)


def register(ctx) -> None:
    """Register the provider. ``from_env()`` first so a mistyped ``EXTRACT_*``
    value names itself at load rather than at the first page the agent reads.

    The provider subclasses an ABC that lives inside Hermes, so building it
    imports ``agent``. The docs generator loads every plugin outside the Hermes
    runtime to list what it registers, and an unguarded import there failed the
    whole `make docs` run (pipeline 96's docs-sync). Under Hermes the import
    always succeeds; anywhere else this plugin simply registers nothing and says
    why, which is the truth for a backend that cannot exist off its runtime."""
    from_env()
    try:
        cls = provider.provider_class()
    except ImportError as exc:
        _LOGGER.info("jarvis-extract: no Hermes provider ABC here, registering nothing (%s)", exc)
        return
    ctx.register_web_search_provider(cls())
