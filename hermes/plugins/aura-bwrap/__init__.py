"""aura-bwrap — the terminal backend: every shell command in a bubblewrap sandbox.

``terminal.backend: local`` runs the model's shell and ``execute_code`` inside the
gateway process, with every credential of the container in its environment and the
whole Hermes home — config.yaml, the memories, the audit log — one ``cat`` away. The
disposable CT of ADR-0025 is owner-blocked, and the docker backend needs a socket this
image refuses to mount. This is the interim (ADR-0045): a pluggable backend, registered
through ``PluginContext.register_terminal_environment_provider``, whose environment
wraps each command in ``bwrap --unshare-all --clearenv`` with the toolchain read-only,
one writable scratch per task, and nothing of ``/opt/data`` but that scratch.

It registers no tool, so it has no row in the approval matrix and no gate of its own:
the matrix keeps deciding whether a command *runs*; this decides *where*. If bwrap is
missing the provider refuses to create an environment and Hermes reports the terminal
as unavailable — it never hands the command to the local backend instead.
"""
from __future__ import annotations

import logging

from . import sandbox

_LOGGER = logging.getLogger(__name__)

NAME = "aura-bwrap"


def provider_class() -> type:
    """Build the provider lazily: its ABC lives in Hermes, like the extract provider's."""
    from agent.terminal_env_provider import TerminalEnvironmentProvider

    class JarvisBwrapProvider(TerminalEnvironmentProvider):
        """The classification flags say what Hermes may assume about the fence.

        ``is_container``: the filesystem the model sees is not the host's, so host
        cwds are sanitised and file tools resolve paths through the environment.
        ``is_remote`` False: it is the same kernel and the same machine, so the
        host's platform facts still describe where a command runs. The guards are
        skipped because nothing of the host is writable but the scratch. Each
        non-persistent session gets its own scratch (``session_isolated_when_
        nonpersistent``), or a cleanup in one session would empty another's cwd.
        """

        is_remote = False
        is_container = True
        session_isolated_when_nonpersistent = True

        @property
        def name(self) -> str:
            return NAME

        @property
        def display_name(self) -> str:
            return "JARVIS bubblewrap sandbox"

        @property
        def description(self) -> str:
            return ("Run commands under bwrap in this container: no network, no environment, "
                    "the toolchain read-only, one scratch directory per task.")

        @property
        def skip_container_guards(self) -> bool:
            return True

        @property
        def env_description(self) -> str:
            return "a bubblewrap sandbox inside the JARVIS container (Linux, no network)"

        def is_available(self) -> bool:
            return sandbox.bwrap_path() is not None

        def setup_instructions(self) -> list[str]:
            return ["bubblewrap is installed by deploy/Dockerfile; the container needs "
                    "deploy/seccomp/jarvis.json (docker-compose.yml, security_opt)."]

        def create_environment(self, *, cwd: str, timeout: int, task_id: str = "default",
                               image=None, container_config=None, **kwargs):
            del image, container_config, kwargs  # no image, no resource knobs: it is a process
            return sandbox.environment_class()(cwd=cwd, timeout=timeout, task_id=task_id)

    return JarvisBwrapProvider


def register(ctx) -> None:
    try:
        provider = provider_class()()
    except ImportError as exc:
        # The docs generator and the shared plugin tests load every plugin outside the
        # Hermes runtime; there is no backend to register there, and saying so is the
        # truth (aura-extract does the same for its ABC).
        _LOGGER.info("aura-bwrap: no Hermes provider ABC here, registering nothing (%s)", exc)
        return
    register_provider = getattr(ctx, "register_terminal_environment_provider", None)
    if register_provider is None:
        # A Hermes without the extension point cannot run this backend at all, and
        # `terminal.backend: aura-bwrap` then names nothing: Hermes raises
        # "Unknown environment type" on the first command rather than running it
        # locally, which is the failure this plugin wants over the silent one.
        _LOGGER.error("aura-bwrap: this Hermes has no register_terminal_environment_provider; "
                      "the terminal will refuse every command until the config names a backend "
                      "that exists")
        return
    register_provider(provider)
