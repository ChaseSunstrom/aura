"""Write a new plugin, its test and its benchmark task into the working checkout.

The self-extension loop's second step (V2_PLAN §5): given a capability JARVIS
lacks, put a *loadable, tested* plugin on disk that a person can read in a diff.
Everything written here lands under three roots and nowhere else —
``hermes/plugins/<name>/``, ``bench/tasks/<category>/`` and, for a skill the
distillation loop approved, ``hermes/skills/<name>/`` — and
:func:`allowed_paths` is what the git layer is told to allow, so the scaffold and
the push agree on the blast radius by construction rather than by comment.

Deliberately small: one module, one toolset, a handler per tool, a pytest file
that loads the plugin the way Hermes does and asserts each handler returns JSON.
A generated plugin that splits itself over three modules is harder for a reviewer
to judge and buys nothing at this size. What this does NOT do is write the tool's
*body* — the scaffold emits a handler that returns its arguments and a `TODO` the
agent then fills in, because a generated implementation nobody wrote is exactly
the thing the merge request exists to stop.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from . import git

#: A plugin directory name: lowercase, digits and single hyphens. The same alphabet
#: Hermes' plugin loader can turn into a module slug, so a name that passes here
#: cannot produce a directory the gateway then refuses to load.
NAME_RE = re.compile(r"^[a-z][a-z0-9]*(-[a-z0-9]+)*$")

#: A tool name: a Python identifier, because it is also the handler's suffix.
TOOL_RE = re.compile(r"^[a-z][a-z0-9_]*$")

PLUGINS_SUBDIR = "hermes/plugins"
BENCH_SUBDIR = "bench/tasks"
#: Where a distilled skill's SKILL.md lands; the entrypoint seeds every directory
#: here into HERMES_HOME/skills, so this is the one place a skill ships from.
SKILLS_SUBDIR = "hermes/skills"

#: The manifest a reader trusts. ``deploy/tests/test_plugin_manifest.py`` asserts every
#: shipped plugin is named under ``plugins.enabled`` here, so a capability that writes
#: its directory and its task and stops is a capability whose own merge request is red
#: — which is exactly how merge request !2 failed. The source is the schema's template;
#: the rendered file beside it is generated, and both have to move together or the
#: schema-sync job fails instead.
#:
#: Writing this is not the privilege escalation it looks like. ``deploy/entrypoint.sh``
#: already appends every seeded plugin to ``plugins.enabled`` at runtime, so the name
#: being here changes no behaviour at all; it keeps a human-readable list honest. What
#: would be an escalation is the *model* authoring content for a deploy config, and it
#: never does: :func:`name_in_manifest` is deterministic code that inserts one list
#: entry equal to the capability's own already-validated name, and
#: :func:`git.assert_manifest_edit_is_only` refuses the push if the diff is anything else.
SCHEMA_TEMPLATE = "aura-schema/aura_schema/templates/config.template.yaml.in"
RENDERED_TEMPLATE = "deploy/config.template.yaml"
MANIFESTS: tuple[str, ...] = (SCHEMA_TEMPLATE, RENDERED_TEMPLATE)

#: The names Hermes itself bundles, recorded in the repository rather than read off
#: the filesystem so this holds wherever the loop runs. A capability may not take one:
#: Hermes loads its own plugins and ours from different directories, so a collision is
#: not an error anyone sees, it is two plugins answering to one name in a config and in
#: `hermes plugins list`. `disk-cleanup` is bundled and `disk-space` is ours, which is
#: how close that already is. Kept honest by deploy/tests/test_plugin_manifest.py.
BUNDLED_CONTRACT = "tests/contracts/hermes-bundled-plugins.json"

#: One list entry, at the indentation the block already uses.
_ENTRY_RE = re.compile(r"^ {4}- [A-Za-z0-9][A-Za-z0-9._-]*$")


class ScaffoldError(ValueError):
    """The requested scaffold is malformed, or would write outside its two roots."""


def allowed_paths(name: str, category: str = "coding-selfext") -> tuple[str, ...]:
    """Repo-relative paths this capability is allowed to touch.

    Handed to :func:`git.assert_pushable` and to :func:`git.commit`, so "what the
    scaffold may write" and "what the push may carry" are one list.
    """
    return (f"{PLUGINS_SUBDIR}/{name}", f"{BENCH_SUBDIR}/{category}",
            f"{SKILLS_SUBDIR}/{name}", *MANIFESTS)


def name_in_manifest(text: str, name: str) -> str:
    """*text* with ``name`` appended to the ``plugins.enabled`` list, or unchanged.

    Deterministic and total: it appends after the last entry already in the list, so
    the comments that group the block stay attached to the entries they describe, and
    a name already present is left alone rather than duplicated.

    The block is located by :func:`git.enabled_block`, which is also what
    :func:`git.assert_manifest_edit_is_only` uses to decide whether an added line
    landed somewhere permitted. One function, so the writer and the checker cannot
    drift apart into a hole neither reviewer would see.

    Raises rather than guessing when the block is not where it is expected. The
    alternative — writing the entry somewhere plausible — puts an unreviewed edit into
    a deploy config, which is the one thing this function must never do.
    """
    block = git.enabled_block(text)
    if block is None:
        raise ScaffoldError("no `plugins.enabled` block in the manifest; refusing to guess")
    first, last = block
    lines = text.splitlines(keepends=True)
    entry = f"    - {name}\n"
    insert_after = None
    for index in range(first, min(last, len(lines))):  # 1-based `first` skips `enabled:`
        stripped = lines[index].rstrip("\n")
        if _ENTRY_RE.match(stripped):
            if stripped == entry.rstrip("\n"):
                return text  # already named; nothing to do and nothing to duplicate
            insert_after = index
    if insert_after is None:
        raise ScaffoldError("`plugins.enabled` has no entries to append after")
    return "".join(lines[: insert_after + 1] + [entry] + lines[insert_after + 1 :])


def _name_in_manifests(root: Path, name: str) -> list[str]:
    """Add *name* to `plugins.enabled` in the schema template and the rendered file.

    Both, and identically: the rendered file is what the manifest test reads and the
    template is what regenerating it would produce, so editing one alone trades a red
    ``test:config-render`` for a red ``schema-sync``.
    """
    touched: list[str] = []
    for rel in MANIFESTS:
        path = root / rel
        if not path.is_file():
            raise ScaffoldError(f"{rel} is missing from the checkout; refusing to push a "
                                f"capability that no manifest names")
        before = path.read_text(encoding="utf-8")
        after = name_in_manifest(before, name)
        if after != before:
            path.write_text(after, encoding="utf-8")
            touched.append(rel)
    return touched


def assert_name_is_free(name: str) -> None:
    """Refuse a capability whose name already means something in the target branch.

    ``NAME_RE`` says a name is *well-formed*; this says it is *unclaimed*, and the two
    are not the same question. The name is the one part of these edits the model
    chooses, so deterministic code inserting a model-chosen string is not the same as
    the model authoring nothing — which is the argument this function exists to stop
    me making. See :func:`git.names_governed` for the two names that made the point:
    ``jarvis-guard``, whose directory ``allowed_paths`` would otherwise open, and
    ``web-exa``, which is in the manifest's *disabled* list on purpose.

    Asked of the target branch, so a capability's own scaffold does not make its own
    push illegal.
    """
    ref = git.target_ref()
    taken = git.names_governed(ref, PLUGINS_SUBDIR, MANIFESTS, BUNDLED_CONTRACT,
                               skills_subdir=SKILLS_SUBDIR)
    if name not in taken:
        return
    # The ref is in the message because of the one case where this refuses wrongly.
    # With no remote-tracking target and no local target branch, `target_ref` has
    # nothing to fall back on but HEAD, and on HEAD a capability that has already
    # committed its own scaffold reads as claiming its own name. It fails in the safe
    # direction — a legitimate push refused, never a bad one allowed — and in
    # production the remote resolves and it never fires. But "already the name of a
    # plugin" with no ref named would send whoever hits it hunting for a collision
    # that does not exist, so the message says which ref was asked and, when that is
    # the fallback, that the fallback is the likely reason.
    hint = ("" if ref != "HEAD" else
            " (asked of HEAD: neither the remote target branch nor a local one "
            "resolves here, so this capability's own committed scaffold counts "
            "against it — fetch the target branch and retry)")
    raise ScaffoldError(
        f"{name!r} is already the name of a plugin {ref} ships or a manifest there "
        f"already governs; a capability may not take an existing name{hint}")


def _validate(name: str, toolset: str, tools: list[dict[str, Any]]) -> None:
    if not NAME_RE.match(name):
        raise ScaffoldError(
            f"plugin name {name!r} must be lowercase words joined by single hyphens")
    if not TOOL_RE.match(toolset):
        raise ScaffoldError(f"toolset {toolset!r} must be a lowercase identifier")
    if not tools:
        raise ScaffoldError("a plugin with no tools is not a capability; declare at least one")
    for tool in tools:
        tool_name = str(tool.get("name", ""))
        if not TOOL_RE.match(tool_name):
            raise ScaffoldError(f"tool name {tool_name!r} must be a lowercase identifier")
        if not str(tool.get("description", "")).strip():
            raise ScaffoldError(f"tool {tool_name!r} needs a description; the model reads it")


def _schema_literal(tool: dict[str, Any]) -> str:
    params = tool.get("parameters")
    if not isinstance(params, dict) or params.get("type") != "object":
        params = {"type": "object", "properties": {}, "required": []}
    schema = {"name": tool["name"], "description": tool["description"], "parameters": params}
    return json.dumps(schema, indent=4)


def render_plugin(name: str, description: str, toolset: str,
                  tools: list[dict[str, Any]], approval: bool) -> str:
    """The ``__init__.py`` source for a generated plugin."""
    lines: list[str] = [
        f'"""{name} — {description}',
        "",
        "Generated by the JARVIS self-extension loop (V2 D24) and then edited by hand:",
        "each handler below returns its arguments until someone fills in the real call.",
        "A merge request carrying an unfilled handler is meant to be rejected.",
        '"""',
        "from __future__ import annotations",
        "",
        "import json",
        *(["import sys", "from pathlib import Path"] if approval else []),
        "",
        f'TOOLSET = "{toolset}"',
        "",
    ]
    for tool in tools:
        const = tool["name"].upper()
        lines += [f"{const} = {_schema_literal(tool)}", ""]
    for tool in tools:
        lines += [
            f'def handle_{tool["name"]}(args: dict, **kwargs) -> str:',
            f'    """{tool["description"]}"""',
            "    del kwargs",
            "    # TODO: call the real service here and return its answer.",
            '    return json.dumps({"ok": True, "args": args or {}})',
            "",
            "",
        ]
    lines += ["def register(ctx) -> None:"]
    for tool in tools:
        lines += [
            f'    ctx.register_tool(name="{tool["name"]}", toolset=TOOLSET,',
            f'                      schema={tool["name"].upper()},'
            f' handler=handle_{tool["name"]},',
            "                      check_fn=lambda: True, emoji=\"\\N{ELECTRIC PLUG}\")",
        ]
    if approval:
        gated = ", ".join(f'"{t["name"]}"' for t in tools)
        lines += [
            "",
            f"    changes_the_world = {{{gated}}}",
            "",
            "    def _gate(tool_name: str, args):",
            "        if tool_name in changes_the_world:",
            "            return {\"action\": \"approve\",",
            f'                    "message": f"{name}: {{tool_name}} will act on the world",',
            f'                    "rule_key": f"{name}:{{tool_name}}"}}',
            "        return None",
            "",
            "    # Through the shared wrapper, so a standing \"always\" runs out (T-0570).",
            "    root = str(Path(__file__).resolve().parent.parent)  # `_shared/` is a sibling dir",
            "    if root not in sys.path:",
            "        sys.path.append(root)",
            "    from _shared._approval_hook import approval_gate",
            "",
            '    ctx.register_hook("pre_tool_call", approval_gate(_gate))',
        ]
    return "\n".join(lines) + "\n"


def render_manifest(name: str, description: str, tools: list[dict[str, Any]],
                    approval: bool) -> str:
    lines = [
        f"name: {name}",
        "version: 0.1.0",
        f'description: "{description}"',
        'author: "JARVIS self-extension"',
        "provides_tools:",
    ]
    lines += [f"  - {t['name']}" for t in tools]
    if approval:
        lines += ["provides_hooks:", "  - pre_tool_call"]
    return "\n".join(lines) + "\n"


def render_test(name: str, tools: list[dict[str, Any]], approval: bool) -> str:
    """A pytest file that loads the plugin the way Hermes does and exercises it."""
    slug = name.replace("-", "_")
    first = tools[0]["name"]
    lines = [
        f'"""{name}: the generated plugin loads, registers, and answers with JSON."""',
        "from __future__ import annotations",
        "",
        "import json",
        "import sys",
        "from pathlib import Path",
        "",
        "sys.path.insert(0, str(Path(__file__).resolve().parents[2]))",
        "from _plugin_loader import load_plugin  # noqa: E402",
        "",
        f'plugin = load_plugin("{name}")',
        "",
        "",
        f"def test_{slug}_registers_its_tools() -> None:",
        "    ctx = plugin.FakeCtx()",
        "    plugin.register(ctx)",
        f"    assert set(ctx.tools) == {{{', '.join(repr(t['name']) for t in tools)}}}",
        "",
        "",
        f"def test_{slug}_handlers_return_json() -> None:",
        "    ctx = plugin.FakeCtx()",
        "    plugin.register(ctx)",
        "    for name, entry in ctx.tools.items():",
        '        parsed = json.loads(entry["handler"]({}))',
        '        assert isinstance(parsed, dict), f"{name} did not answer with a JSON object"',
    ]
    if approval:
        lines += [
            "",
            "",
            f"def test_{slug}_gates_what_changes_the_world() -> None:",
            "    ctx = plugin.FakeCtx()",
            "    plugin.register(ctx)",
            f'    directive = ctx.hooks["pre_tool_call"]("{first}", {{}}, "")',
            '    assert directive["action"] == "approve"',
            '    assert ctx.hooks["pre_tool_call"]("not_a_tool_of_this_plugin", {}, "") is None',
        ]
    return "\n".join(lines) + "\n"


def render_bench_task(task_id: str, name: str, tool: str, prompt: str) -> str:
    """A JCB task file that proves the deployed capability answers."""
    return (
        f"id: {task_id}\n"
        "category: coding-selfext\n"
        f"prompt: {json.dumps(prompt)}\n"
        "setup: {}\n"
        "checker:\n"
        "  type: tool_used\n"
        f"  tool: {tool}\n"
        "timeout_s: 180\n"
        "needs:\n"
        f"- plugin:{name}\n"
        "safety_expectation: comply\n"
        "fast: false\n"
        "tags:\n"
        "- self-extension\n"
    )


def scaffold(name: str, description: str, toolset: str, tools: list[dict[str, Any]],
             *, approval: bool = False, bench_prompt: str = "",
             skill_md: str = "") -> dict[str, Any]:
    """Write the plugin, its test and (when asked) its benchmark task and SKILL.md.

    Returns the paths. Both directories are checked before either is written, so a
    refusal leaves nothing half-made.
    """
    _validate(name, toolset, tools)
    assert_name_is_free(name)
    root = git.repo_path()
    plugin_dir = root / PLUGINS_SUBDIR / name
    skill_dir = root / SKILLS_SUBDIR / name
    if plugin_dir.exists():
        raise ScaffoldError(f"{PLUGINS_SUBDIR}/{name} already exists; pick another name")
    if skill_md.strip() and skill_dir.exists():
        raise ScaffoldError(f"{SKILLS_SUBDIR}/{name} already exists; pick another name")
    (plugin_dir / "tests").mkdir(parents=True)
    written: list[str] = []

    def _write(path: Path, body: str) -> None:
        path.write_text(body, encoding="utf-8")
        written.append(str(path.relative_to(root)))

    _write(plugin_dir / "plugin.yaml", render_manifest(name, description, tools, approval))
    _write(plugin_dir / "__init__.py",
           render_plugin(name, description, toolset, tools, approval))
    _write(plugin_dir / "tests" / f"test_{name.replace('-', '_')}.py",
           render_test(name, tools, approval))

    task_id = ""
    if bench_prompt.strip():
        task_id = f"selfext-{name}-001"
        task_dir = root / BENCH_SUBDIR / "coding-selfext"
        task_dir.mkdir(parents=True, exist_ok=True)
        _write(task_dir / f"{task_id}.yaml",
               render_bench_task(task_id, name, tools[0]["name"], bench_prompt))

    if skill_md.strip():
        skill_dir.mkdir(parents=True)
        _write(skill_dir / "SKILL.md", skill_md)

    written.extend(_name_in_manifests(root, name))

    return {"ok": True, "plugin": name, "files": written, "bench_task": task_id,
            "allowed_paths": list(allowed_paths(name))}
