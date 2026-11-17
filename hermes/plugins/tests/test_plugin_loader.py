"""The shared test loader's FakeCtx must carry ``requires_env`` the way Hermes does.

FakeCtx stands in for Hermes' PluginContext in every plugin's tests. Hermes (tools/registry.py)
keeps ``requires_env or []`` on the ToolEntry and *iterates* it for the doctor's requirement
lists; exposure is decided by ``check_fn`` alone. The original stub accepted ``requires_env`` and
dropped it, so a test could neither see what a plugin declared nor catch the one shape that
silently corrupts the declaration — a bare string, which Hermes would list one character at a
time. These tests pin the fake to that contract so the hole cannot reopen.

Not covered: Hermes' ``override`` trust gate and toolset wiring — the fake records, it does not
police what the gateway's config would allow.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Same path shim the plugin tests use: _plugin_loader sits one level up, imported as a top-level
# module so tests load without Hermes on the path.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _plugin_loader import (  # noqa: E402
    FakeCtx,
    forget_hermes_plugins,
    load_like_hermes,
    scrub_sys_path,
)


def _register(ctx: FakeCtx, name: str, **kw) -> None:
    ctx.register_tool(name=name, toolset=kw.pop("toolset", "t"), schema={},
                      handler=lambda a: "", **kw)


def test_requires_env_is_kept_as_a_list() -> None:
    ctx = FakeCtx()
    _register(ctx, "needs_env", requires_env=["PLUGIN_LOADER_TEST_VAR"])
    assert ctx.tools["needs_env"]["requires_env"] == ["PLUGIN_LOADER_TEST_VAR"]


def test_no_requires_env_normalises_to_an_empty_list() -> None:
    # `requires_env or []` on the ToolEntry — None never reaches a caller that iterates it.
    ctx = FakeCtx()
    _register(ctx, "plain")
    assert ctx.tools["plain"]["requires_env"] == []


def test_a_bare_string_is_rejected() -> None:
    # Hermes would report requirements "F", "O", "O" for this and never notice.
    ctx = FakeCtx()
    with pytest.raises(TypeError, match="one entry per character"):
        _register(ctx, "bad", requires_env="FOO")
    assert "bad" not in ctx.tools


def test_toolset_requirements_are_the_union_in_order() -> None:
    ctx = FakeCtx()
    _register(ctx, "a", toolset="x", requires_env=["ONE", "TWO"])
    _register(ctx, "b", toolset="x", requires_env=["TWO", "THREE"])
    _register(ctx, "c", toolset="y", requires_env=["FOUR"])
    assert ctx.requirements("x") == ["ONE", "TWO", "THREE"]
    assert ctx.requirements("y") == ["FOUR"]
    assert ctx.requirements("z") == []


def test_availability_follows_check_fn_and_registration_does_not(monkeypatch) -> None:
    # Hermes registers every tool and filters when it builds definitions, so a tool whose
    # credential is absent is still in the registry — just not offered. A missing check_fn
    # means always offered, and requires_env alone never hides a tool.
    monkeypatch.delenv("PLUGIN_LOADER_TEST_VAR", raising=False)
    ctx = FakeCtx()
    _register(ctx, "gated", requires_env=["PLUGIN_LOADER_TEST_VAR"],
              check_fn=lambda: bool(__import__("os").environ.get("PLUGIN_LOADER_TEST_VAR")))
    _register(ctx, "declared_only", requires_env=["PLUGIN_LOADER_TEST_VAR"])
    _register(ctx, "open")
    assert set(ctx.tools) == {"gated", "declared_only", "open"}
    assert ctx.available("gated") is False
    assert ctx.available("declared_only") is True
    assert ctx.available("open") is True
    monkeypatch.setenv("PLUGIN_LOADER_TEST_VAR", "1")
    assert ctx.available("gated") is True


# --------------------------------------------------------------------------- #
# _shared must be reachable the way Hermes loads a plugin
# --------------------------------------------------------------------------- #
# Hermes imports a directory plugin as ``hermes_plugins.<slug>`` with the plugin
# directory as its only ``__path__`` and an empty namespace parent — there is no
# route from a plugin to a sibling directory through the package tree. The
# ``_shared`` helpers therefore depend on each consumer's own sys.path shim, and
# a plugin that forgot it works under the test loader (which happens to have the
# plugins root on sys.path already) and breaks in the gateway. This loads every
# consumer exactly as ``PluginManager._load_directory_module`` does, with the
# plugins root deliberately scrubbed from sys.path first.
import importlib
import inspect

PLUGINS_DIR = Path(__file__).resolve().parents[1]


def _shared_consumers() -> tuple[str, ...]:
    """Every plugin directory whose sources import a ``_shared`` helper.

    Discovered rather than listed. The hand-written tuple this replaces went stale
    three times in one afternoon — Phase 4 added six plugins that import ``_shared``
    and none of them reached the list, so the one test that proves the sys.path shim
    is real would have skipped exactly the new code most likely to have forgotten it.
    A plugin directory is one with a ``plugin.yaml``; ``_shared`` itself has none.
    """
    found = []
    for path in sorted(PLUGINS_DIR.iterdir()):
        if not (path / "plugin.yaml").is_file():
            continue
        for source in path.rglob("*.py"):
            if "tests" in source.parts or "__pycache__" in source.parts:
                continue
            if "_shared." in source.read_text(encoding="utf-8"):
                found.append(path.name)
                break
    return tuple(found)


SHARED_CONSUMERS = _shared_consumers()
BACKEND_ONLY = {"aura-extract"}


@pytest.fixture
def scrubbed_sys_path(monkeypatch):
    """sys.path and sys.modules as the gateway has them: no plugins root, no _shared."""
    scrub_sys_path(monkeypatch)
    yield
    forget_hermes_plugins()


@pytest.mark.parametrize("dirname", SHARED_CONSUMERS)
@pytest.mark.usefixtures("scrubbed_sys_path")
def test_a_shared_consumer_loads_the_way_hermes_loads_it(dirname: str) -> None:
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("_shared._approval_hook")  # the scrub is real
    module = load_like_hermes(dirname)
    ctx = FakeCtx()
    module.register(ctx)
    # Hooks count: aura-activity is observers only, and its `_shared` import
    # resolved or `register` would not have been reached.
    # A backend plugin registers its web provider only where Hermes' `agent` package exists, so
    # here it registers nothing; its `_shared` import resolving is what `load_like_hermes` proved.
    if dirname not in BACKEND_ONLY:
        assert ctx.tools or ctx.commands or ctx.hooks, f"{dirname} registered nothing"
    # The guarded wrapper is what _shared provides; a handler that survives a bad
    # argument without raising proves the import resolved and the wrap is on. Not
    # every consumer registers a command — aura-contacts is tools only — so the
    # check is over whatever commands exist rather than a demand that one does.
    for handler in ctx.commands.values():
        assert isinstance(handler("@s"), str)


def test_every_shared_consumer_was_discovered() -> None:
    """The discovery above must actually find the plugins, not silently find none."""
    assert len(SHARED_CONSUMERS) >= 5, f"only {SHARED_CONSUMERS} import _shared — did the scan break?"


def test_shared_is_not_a_plugin() -> None:
    """A plugin.yaml here would make Hermes load the helpers as a plugin with no register()."""
    shared = PLUGINS_DIR / "_shared"
    assert (shared / "__init__.py").is_file()
    assert not (shared / "plugin.yaml").exists() and not (shared / "plugin.yml").exists()


def _all_plugin_dirs() -> tuple[str, ...]:
    """Every plugin directory, discovered the way :func:`_shared_consumers` is."""
    return tuple(sorted(p.name for p in PLUGINS_DIR.iterdir() if (p / "plugin.yaml").is_file()))


ALL_PLUGINS = _all_plugin_dirs()


@pytest.mark.parametrize("dirname", ALL_PLUGINS)
@pytest.mark.usefixtures("scrubbed_sys_path")
def test_every_tool_handler_takes_the_kwargs_the_registry_passes(dirname: str) -> None:
    """A handler declared ``def h(args)`` is dead in the gateway and green here.

    ``ToolRegistry.dispatch`` (Hermes ``tools/registry.py``) calls a plugin's
    handler as ``entry.handler(args, **kwargs)``, and the kwargs always include
    ``task_id``. So a handler with no ``**kwargs`` raises
    ``TypeError: h() got an unexpected keyword argument 'task_id'`` the first
    time the model calls it — every time, not intermittently.

    This is not hypothetical. aura-guard's ``kill_switch`` and ``audit_query``
    shipped that way: the kill switch's own status surface and the audit log's
    query tool had never worked through a real gateway, and were found only by
    driving one. `FakeCtx` now dispatches the way the registry does, so a
    missing ``**kwargs`` fails wherever the handler is exercised — but most
    tools are not exercised by any test, so this asserts the signature directly
    for all of them.
    """
    module = load_like_hermes(dirname)
    ctx = FakeCtx()
    module.register(ctx)
    for name, entry in ctx.tools.items():
        params = inspect.signature(entry["raw_handler"]).parameters
        takes_kwargs = any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values())
        assert takes_kwargs or "task_id" in params, (
            f"{dirname}:{name} is declared "
            f"{inspect.signature(entry['raw_handler'])} — the registry will call it "
            f"with task_id= and it will raise TypeError on the first use"
        )
