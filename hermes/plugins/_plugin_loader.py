"""Test helper: load a JARVIS plugin directory (hyphenated name) as a package.

Hermes loads each plugin directory as a package at runtime; tests need the
same so that `from . import tools` inside `__init__.py` works. Also provides
`FakeCtx`, a stand-in for Hermes' PluginContext that records registrations.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

PLUGINS_DIR = Path(__file__).resolve().parent

# The plugins read their configuration from aura-schema (V2 D17), which the Hermes
# image installs into the Hermes venv; the repo suite has it as a sibling tree, so put
# it on the path here — before any plugin is loaded — instead of in every test file.
_SCHEMA_DIR = PLUGINS_DIR.parents[1] / "aura-schema"
if _SCHEMA_DIR.is_dir() and str(_SCHEMA_DIR) not in sys.path:
    sys.path.insert(0, str(_SCHEMA_DIR))


def _as_hermes_dispatches(handler):
    """Call a tool handler the way the gateway calls it, not the way it is easiest to.

    ``ToolRegistry.dispatch`` (Hermes ``tools/registry.py``) invokes a plugin's
    handler as ``entry.handler(args, **kwargs)``, and the kwargs it is given
    always include ``task_id``. A handler declared ``def h(args)`` therefore
    dies with ``TypeError: h() got an unexpected keyword argument 'task_id'``
    the first time the model calls it.

    The fake used to call ``handler(args)``. That is the shape that is
    convenient to write a test against and it is not the shape the runtime
    uses, so a plugin whose handler omitted ``**kwargs`` passed every test in
    this repo and was dead in the gateway. Found live: jarvis-guard's
    ``kill_switch`` and ``audit_query`` — the two tools the owner reaches for
    when something is already wrong — had never once worked, and 51 green tests
    said otherwise.

    Handlers keep being callable with a single positional argument from a test;
    the wrapper supplies the kwargs. ``raw_handler`` is kept beside it for the
    rare test that wants to inspect the undecorated function.
    """

    def _dispatch(args=None, **kwargs):
        kwargs.setdefault("task_id", "fake-task")
        return handler(args if args is not None else {}, **kwargs)

    return _dispatch


class FakeCtx:
    def __init__(self):
        self.tools: dict[str, dict] = {}
        self.hooks: dict[str, object] = {}
        self.commands: dict[str, object] = {}
        self.image_gen_providers: list = []
        self.web_search_providers: list = []

    def register_tool(self, name, toolset, schema, handler, check_fn=None, requires_env=None,
                      is_async=False, description="", emoji="", override=False):
        # Hermes (tools/registry.py) stores `requires_env or []` on the ToolEntry and reads it
        # back by iterating it — doctor/banner requirements, `check_tool_availability`. A bare
        # string would therefore surface as one requirement per character ("F", "O", "O") and
        # never as FOO; the fake rejects it so a plugin cannot ship that shape. The original stub
        # accepted `requires_env` and dropped it, so a test could not see what a plugin declared.
        if isinstance(requires_env, str):
            raise TypeError(
                f"{name}: requires_env must be a list of env var names, not the string "
                f"{requires_env!r} (Hermes iterates it — a str becomes one entry per character)"
            )
        self.tools[name] = {"toolset": toolset, "schema": schema,
                            "handler": _as_hermes_dispatches(handler),
                            "raw_handler": handler,
                            "check_fn": check_fn, "requires_env": list(requires_env or []),
                            "is_async": is_async, "emoji": emoji}

    def available(self, name: str) -> bool:
        """Whether Hermes would expose ``name`` — its ``check_fn`` (or none) says yes.

        Mirrors ``ToolRegistry._toolset_has_exposable_tools``: registration always succeeds
        (the runtime registers first and filters when building tool definitions), so a plugin
        test asserting on ``ctx.tools`` sees every declared tool, and asks here for the gate.
        ``requires_env`` does not gate exposure in Hermes either — it is what the doctor reports
        — so it is not consulted here.
        """
        check_fn = self.tools[name]["check_fn"]
        return True if check_fn is None else bool(check_fn())

    def requirements(self, toolset: str) -> list[str]:
        """The env var names Hermes' doctor lists for ``toolset`` (union over its tools, in
        registration order, deduplicated) — ``get_toolset_requirements()['env_vars']``."""
        out: list[str] = []
        for entry in self.tools.values():
            if entry["toolset"] != toolset:
                continue
            for env in entry["requires_env"]:
                if env not in out:
                    out.append(env)
        return out

    def register_hook(self, event, callback):
        self.hooks[event] = callback

    def register_command(self, name, handler, description="", args_hint=""):
        self.commands[name] = handler

    def register_image_gen_provider(self, provider):
        self.image_gen_providers.append(provider)

    def register_web_search_provider(self, provider):
        # Hermes refuses a provider that does not subclass its WebSearchProvider ABC
        # (hermes_cli/plugins.py) — a plugin that registers a duck-typed object would
        # pass here and be silently ignored in the gateway, so the fake records the
        # instance and the plugin's own test asserts the base class.
        self.web_search_providers.append(provider)


def load_plugin(dirname: str) -> ModuleType:
    modname = dirname.replace("-", "_")
    for key in list(sys.modules):
        if key == modname or key.startswith(modname + "."):
            del sys.modules[key]
    path = PLUGINS_DIR / dirname
    spec = importlib.util.spec_from_file_location(modname, path / "__init__.py",
                                                  submodule_search_locations=[str(path)])
    mod = importlib.util.module_from_spec(spec)
    sys.modules[modname] = mod
    spec.loader.exec_module(mod)
    mod.FakeCtx = FakeCtx
    return mod


def load_like_hermes(dirname: str) -> ModuleType:
    """Load a plugin exactly as ``PluginManager._load_directory_module`` does.

    Different from :func:`load_plugin` in the one way that matters for the
    ``_shared`` helpers: Hermes imports a directory plugin as
    ``hermes_plugins.<slug>`` under an empty namespace parent, so there is no route
    from a plugin to a sibling directory through the package tree. A plugin that
    forgot its ``sys.path`` shim loads fine under :func:`load_plugin` (the test
    process has the plugins root on the path already) and breaks in the gateway;
    loaded through here, with :func:`scrub_sys_path` first, it fails in the test.
    """
    slug = dirname.replace("-", "_")
    module_name = f"hermes_plugins.{slug}"
    if "hermes_plugins" not in sys.modules:
        namespace = ModuleType("hermes_plugins")
        namespace.__path__ = []  # type: ignore[attr-defined]
        namespace.__package__ = "hermes_plugins"
        sys.modules["hermes_plugins"] = namespace
    for name in [n for n in sys.modules if n == module_name or n.startswith(module_name + ".")]:
        del sys.modules[name]
    plugin_dir = PLUGINS_DIR / dirname
    spec = importlib.util.spec_from_file_location(
        module_name, plugin_dir / "__init__.py", submodule_search_locations=[str(plugin_dir)]
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    module.__package__ = module_name
    module.__path__ = [str(plugin_dir)]  # type: ignore[attr-defined]
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def scrub_sys_path(monkeypatch) -> None:
    """Put sys.path and sys.modules in the state the gateway has them: no plugins root.

    Call from a fixture, then :func:`load_like_hermes`. The caller is responsible for
    dropping ``hermes_plugins`` modules afterwards if it loads more than one plugin.
    """
    root = str(PLUGINS_DIR)
    monkeypatch.setattr(sys, "path", [p for p in sys.path if p != root])
    for name in [n for n in sys.modules if n == "_shared" or n.startswith("_shared.")]:
        monkeypatch.delitem(sys.modules, name)


def forget_hermes_plugins() -> None:
    """Drop every ``hermes_plugins.*`` module a :func:`load_like_hermes` call left behind."""
    for name in [n for n in sys.modules if n.startswith("hermes_plugins")]:
        sys.modules.pop(name, None)


#: What the DAV plugins' tests point at. One place, so a change to the fake server's
#: identity does not have to be made in four files and get missed in one.
DAV_ENV = {
    "DAV_URL": "https://cloud.test/remote.php/dav",
    "DAV_USERNAME": "alice",
    "DAV_PASSWORD": "s3cr3t-app-password",
}


def set_dav_env(monkeypatch) -> None:
    """Point the DAV settings at the fake server every DAV plugin test uses."""
    for name, value in DAV_ENV.items():
        monkeypatch.setenv(name, value)


def serve_dav(monkeypatch, handler) -> None:
    """Replace ``httpx.Client`` with one whose transport is *handler*.

    Captures the real class first: a second call in the same test would otherwise
    wrap the replacement and recurse. ``auth`` is forwarded because the DAV layer
    passes basic credentials there, and a fake that dropped them would not notice a
    plugin that stopped sending them.
    """
    import httpx  # noqa: PLC0415 - a test-only dependency, not imported at plugin load

    real = getattr(serve_dav, "_real_client", None) or httpx.Client
    serve_dav._real_client = real  # type: ignore[attr-defined]
    monkeypatch.setattr(httpx, "Client", lambda **kw: real(
        base_url=kw.get("base_url", ""), auth=kw.get("auth"),
        transport=httpx.MockTransport(handler)))
