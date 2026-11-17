"""A setting a newer image adds *inside* a block an existing volume already has.

``config.yaml`` is rendered once, on the first start a volume ever has. Two upgrade paths
already exist for that: ``ensure_plugins_enabled`` for a plugin name, ``ensure_config_blocks``
for a whole top-level block. Neither covers a leaf added inside a block that is already
there — which is most settings changes, and which is how ``stt.local.initial_prompt`` came to
ship in the image while the deployed recogniser kept hearing "Jarvis dismissed the
notification on screen" for "Jarvis, dismiss the notification on screen".

These drive the real ``ensure_config_leaves`` out of ``deploy/entrypoint.sh`` — the embedded
python, extracted the way ``test_config_render.py`` extracts the renderer — because a copy of
the algorithm in the test would agree with itself forever.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ENTRYPOINT = ROOT / "deploy" / "entrypoint.sh"
TEMPLATE = ROOT / "deploy" / "config.template.yaml"

LIVE = """\
model:
  default: qwen3.8-27b

stt:
  enabled: true
  provider: local
  language: en
  local:
    model: "base"
    vad: true

tts:
  provider: piper
"""

NEWER = """\
model:
  default: qwen3.8-27b

stt:
  enabled: true
  provider: local
  language: en
  local:
    model: "base"
    vad: true
    # why the hint is here
    initial_prompt: "Jarvis, dismiss that. Jarvis, start a goal."

tts:
  provider: piper
"""


def _heredoc(tag: str) -> str:
    """The python a writer runs in entrypoint.sh: the shared write_atomic (PYATOMIC) in front of
    the writer's own heredoc, which is what `py_atomic` hands to python3."""
    text = ENTRYPOINT.read_text()
    body = lambda t: text.split(f"<<'{t}'", 1)[1].split("\n", 1)[1].split(f"\n{t}\n", 1)[0]  # noqa: E731
    return body("PYATOMIC") + "\n" + body(tag)


def _leaves(tmp_path: Path, live: str, template: str, *paths: str) -> tuple[str, str]:
    """Run entrypoint.sh's own ensure_config_leaves over two files. Returns (config, stderr)."""
    code = _heredoc("PYLEAVES")
    live_path, tpl_path = tmp_path / "config.yaml", tmp_path / "template.yaml"
    live_path.write_text(live, encoding="utf-8")
    tpl_path.write_text(template, encoding="utf-8")
    out = subprocess.run(["python3", "-c", code, str(live_path), str(tpl_path), *paths],
                         check=True, capture_output=True, text=True)
    return live_path.read_text(encoding="utf-8"), out.stderr


def test_a_new_leaf_lands_inside_the_block_it_belongs_to(tmp_path):
    after, stderr = _leaves(tmp_path, LIVE, NEWER, "stt.local.initial_prompt")
    assert "stt.local.initial_prompt" in stderr
    lines = after.split("\n")
    added = next(i for i, line in enumerate(lines) if "initial_prompt" in line)
    assert lines[added].startswith("    initial_prompt:"), "indented as a child of stt.local"
    assert 'Jarvis, dismiss that.' in lines[added]
    # Placed inside stt.local, not appended at the end of the file or into tts.
    assert lines.index("tts:") > added
    assert lines[added - 1].strip() == "vad: true"


def test_a_mapping_leaf_arrives_with_its_children(tmp_path):
    """Pipeline 146: `providers.qwen-vllm.extra_body` was listed, the template carried it
    with `reasoning_effort` beneath, and the live config gained `extra_body:` and nothing
    under it — one line copied, the block left behind — so the bound never reached the
    model server. A mapping leaf must land whole, re-indented as a child of its parent."""
    live = """\
providers:
  qwen-vllm:
    api: "http://jarvis-models:8000/v1"
    discover_models: true

goals:
  max_turns: 20
"""
    newer = """\
providers:
  qwen-vllm:
    api: "http://jarvis-models:8000/v1"
    discover_models: true
    # merged into every request
    extra_body:
      reasoning_effort: "medium"
      chat_template_kwargs:
        enable_thinking: true

goals:
  max_turns: 20
"""
    config, stderr = _leaves(tmp_path, live, newer, "providers.qwen-vllm.extra_body")
    assert "providers.qwen-vllm.extra_body" in stderr
    lines = config.split("\n")
    at = lines.index("    extra_body:")
    assert lines[at + 1] == '      reasoning_effort: "medium"', lines[at:at + 4]
    assert lines[at + 2] == "      chat_template_kwargs:"
    assert lines[at + 3] == "        enable_thinking: true"
    assert lines[at + 4] == "", "the blank line that ends the block stays a block boundary"
    assert lines.index("goals:") == at + 5, "nothing else moved"
    # idempotent: a second run sees the leaf and adds nothing
    again, stderr2 = _leaves(tmp_path, config, newer, "providers.qwen-vllm.extra_body")
    assert again == config and "extra_body" not in stderr2


def test_an_empty_key_left_by_the_old_copier_is_filled(tmp_path):
    """The volume upgraded by pipeline 146 has `extra_body:` with nothing beneath it. A
    copier that treats any present key as "already there" would leave the bound unsent
    forever; the children go in under the empty key, once."""
    live = """\
providers:
  qwen-vllm:
    api: "http://jarvis-models:8000/v1"
    extra_body:

goals:
  max_turns: 20
"""
    newer = """\
providers:
  qwen-vllm:
    api: "http://jarvis-models:8000/v1"
    extra_body:
      reasoning_effort: "medium"

goals:
  max_turns: 20
"""
    config, stderr = _leaves(tmp_path, live, newer, "providers.qwen-vllm.extra_body")
    assert "children of an empty key" in stderr
    lines = config.split("\n")
    at = lines.index("    extra_body:")
    assert lines[at + 1] == '      reasoning_effort: "medium"'
    assert lines[at + 2] == ""
    again, stderr2 = _leaves(tmp_path, config, newer, "providers.qwen-vllm.extra_body")
    assert again == config and "extra_body" not in stderr2


def test_everything_else_is_left_exactly_as_it_was(tmp_path):
    after, _stderr = _leaves(tmp_path, LIVE, NEWER, "stt.local.initial_prompt")
    for line in LIVE.split("\n"):
        assert line in after.split("\n"), line
    assert after.count("model:") == LIVE.count("model:")


def test_a_leaf_the_operator_already_has_is_never_replaced(tmp_path):
    """Their value wins, which is the rule the render marker enforces for the file."""
    theirs = LIVE.replace('    vad: true\n', '    vad: true\n    initial_prompt: "mine"\n')
    after, stderr = _leaves(tmp_path, theirs, NEWER, "stt.local.initial_prompt")
    assert 'initial_prompt: "mine"' in after
    assert "Jarvis, dismiss that" not in after
    assert stderr == ""


def test_it_is_idempotent(tmp_path):
    once, _ = _leaves(tmp_path, LIVE, NEWER, "stt.local.initial_prompt")
    twice, stderr = _leaves(tmp_path, once, NEWER, "stt.local.initial_prompt")
    assert twice == once and stderr == ""


def test_a_missing_parent_block_is_left_to_ensure_config_blocks(tmp_path):
    """Adding a leaf under a block that does not exist would write a broken config."""
    after, stderr = _leaves(tmp_path, "model:\n  default: x\n", NEWER, "stt.local.initial_prompt")
    assert after == "model:\n  default: x\n" and stderr == ""


def test_a_leaf_the_template_does_not_carry_is_not_invented(tmp_path):
    after, stderr = _leaves(tmp_path, LIVE, LIVE, "stt.local.initial_prompt")
    assert after == LIVE and stderr == ""


def test_the_real_template_carries_every_path_the_entrypoint_lists(tmp_path):
    """A path in CONFIG_LEAVES that the template no longer has is a silent no-op forever."""
    listed = next(line for line in ENTRYPOINT.read_text().split("\n")
                  if line.startswith("CONFIG_LEAVES="))
    paths = listed.split("=", 1)[1].strip().strip('"').split()
    assert paths, "CONFIG_LEAVES is empty"
    # An old volume that has the block but not the leaf: every listed path must be added.
    stripped = TEMPLATE.read_text(encoding="utf-8")
    for path in paths:
        leaf = path.lstrip("=").split(".")[-1]
        assert f"{leaf}:" in stripped, f"{path} is not in the rendered template"
        without = "\n".join(line for line in stripped.split("\n") if f"{leaf}:" not in line)
        after, stderr = _leaves(tmp_path, without, stripped, path)
        assert path.lstrip("=") in stderr, f"{path} was not added back"
        assert f"{leaf}:" in after


COSY = NEWER.replace("tts:\n  provider: piper\n", """\
tts:
  provider: openai
  openai:
    base_url: "http://jarvis-cosyvoice:8890/v1"
    model: "kokoro"
    voice: "jarvis"
  piper:
    voice: "en_GB-alan-medium"
""")


def test_a_pinned_leaf_takes_the_env_value_on_every_start(tmp_path):
    """`=tts.provider`: the host .env owns it, so a volume rendered when the answer was
    piper moves to CosyVoice on the next start, and back if the operator says piper."""
    after, stderr = _leaves(tmp_path, LIVE, COSY, "tts.openai", "=tts.provider")
    assert "  provider: openai\n" in after.split("stt:")[1].split("tts:")[1]
    assert "tts.provider (pinned: piper -> openai)" in stderr and "tts.openai" in stderr
    assert '    base_url: "http://jarvis-cosyvoice:8890/v1"' in after
    # Same value again: nothing to do, nothing said.
    again, stderr2 = _leaves(tmp_path, after, COSY, "tts.openai", "=tts.provider")
    assert again == after and stderr2 == ""
    # The operator turns it back: piper wins, and the openai block they might edit stays.
    back, stderr3 = _leaves(tmp_path, after, COSY.replace("provider: openai", "provider: piper"), "=tts.provider")
    assert "  provider: piper\n" in back.split("tts:")[1] and "openai -> piper" in stderr3
    assert '    base_url: "http://jarvis-cosyvoice:8890/v1"' in back


def test_an_unpinned_leaf_is_still_never_replaced(tmp_path):
    after, stderr = _leaves(tmp_path, LIVE, COSY, "tts.provider")
    assert "provider: piper" in after and "provider: openai" not in after and stderr == ""


def test_a_leaf_whose_value_is_a_swallowed_comment_takes_the_templates_line(tmp_path):
    """`model: "# delegated subagents; …"` came from a `KEY=   # note` .env line (Docker
    Compose keeps the note as the value). Fixing the .env did nothing for a config.yaml
    rendered before it, and every subagent died on "no router for requested model" a deploy
    later (2026-09-10). The template's line for the same path wins; a real value is kept."""
    live = LIVE + '\ndelegation:\n  provider: ""\n  model: "# delegated subagents; empty = inherit the parent"\n  max_concurrent_children: 5\n'
    newer = NEWER + '\ndelegation:\n  provider: ""\n  model: ""\n  max_concurrent_children: 5\n'
    after, stderr = _leaves(tmp_path, live, newer, "stt.local.initial_prompt")
    assert '  model: ""\n' in after.split("delegation:")[1]
    assert "delegation.model (was a comment" in stderr
    # A leaf with a real value is not touched, and a quoted value that merely contains a hash is not a comment.
    kept = LIVE + '\ndelegation:\n  provider: ""\n  model: "qwen#small"\n'
    again, stderr2 = _leaves(tmp_path, kept, newer)
    assert 'model: "qwen#small"' in again and stderr2 == ""


def test_the_vision_leaves_land_in_model_and_agent_and_the_shipped_template_carries_them(tmp_path):
    """2026-09-10: every `vision_analyze` on the deployed gateway went the text way -- a
    separate call writing an essay -- because Hermes cannot tell that a custom endpoint's
    model sees images. The template now declares it (`model.supports_vision`) and pins the
    attach (`agent.image_input_mode: native`); a volume rendered before that must gain both
    on its next start, inside the blocks it already has."""
    live = """\
model:
  default: qwen3.8-27b
  provider: qwen-vllm

agent:
  reasoning_effort: "medium"

tts:
  provider: piper
"""
    newer = """\
model:
  default: qwen3.8-27b
  provider: qwen-vllm
  # why
  supports_vision: true

agent:
  reasoning_effort: "medium"
  # why
  image_input_mode: native

tts:
  provider: piper
"""
    after, stderr = _leaves(tmp_path, live, newer, "model.supports_vision", "agent.image_input_mode")
    assert "model.supports_vision" in stderr and "agent.image_input_mode" in stderr
    lines = after.split("\n")
    vision = lines.index("  supports_vision: true")
    mode = lines.index("  image_input_mode: native")
    assert lines.index("model:") < vision < lines.index("agent:") < mode < lines.index("tts:")
    # Present already: left alone, whatever the operator set.
    again, stderr = _leaves(tmp_path, after.replace("native", "text"), newer, "model.supports_vision", "agent.image_input_mode")
    assert "  image_input_mode: text" in again and "image_input_mode" not in stderr
    # The shipped template carries both, and the entrypoint lists both.
    shipped = TEMPLATE.read_text(encoding="utf-8")
    assert "  supports_vision: true" in shipped and "  image_input_mode: native" in shipped
    assert "  max_tokens: 12288" in shipped
    leaves = ENTRYPOINT.read_text(encoding="utf-8").split('CONFIG_LEAVES="', 1)[1].split('"', 1)[0].split()
    assert "model.supports_vision" in leaves and "agent.image_input_mode" in leaves and "model.max_tokens" in leaves


def test_lazy_installs_are_sealed_in_config_and_the_leaf_reaches_an_old_volume(tmp_path):
    """``HERMES_DISABLE_LAZY_INSTALLS=1`` seals the venv only: Hermes redirects an install to
    a durable target when one is configured, and ``security.allow_lazy_installs: false`` is
    the switch that wins in every mode (tools/lazy_deps.py, ``_allow_lazy_installs`` step 1).
    It was set nowhere (2026-09-18). The template carries it, the entrypoint lists it, and a
    volume rendered before it gains it inside the ``security`` block it already has."""
    live = "security:\n  allow_private_urls: true\n\nplugins:\n  enabled:\n    - jarvis-guard\n"
    after, stderr = _leaves(tmp_path, live, TEMPLATE.read_text(encoding="utf-8"),
                            "security.allow_lazy_installs")
    assert "security.allow_lazy_installs" in stderr
    lines = after.split("\n")
    at = lines.index("  allow_lazy_installs: false")
    assert lines.index("security:") < at < lines.index("plugins:")
    leaves = ENTRYPOINT.read_text(encoding="utf-8").split('CONFIG_LEAVES="', 1)[1].split('"', 1)[0].split()
    assert "security.allow_lazy_installs" in leaves
    # The operator's own answer, whichever it is, is never replaced.
    kept, stderr2 = _leaves(tmp_path, after.replace("false", "true"), TEMPLATE.read_text(encoding="utf-8"),
                            "security.allow_lazy_installs")
    assert "  allow_lazy_installs: true" in kept and stderr2 == ""


def test_the_depth_leaf_reaches_a_volume_rendered_before_it(tmp_path):
    """``delegation.max_spawn_depth`` is new (2026-09-18). Hermes reads it once at spawn time
    and defaults to 1 when it is absent, so a volume rendered before the leaf keeps every
    subagent flat however the .env is set. The template carries it, the entrypoint lists
    it, and it lands inside the ``delegation`` block an old volume already has."""
    live = 'delegation:\n  provider: ""\n  model: ""\n  max_concurrent_children: 5\n  max_iterations: 60\n\nmemory:\n  provider: holographic\n'
    newer = TEMPLATE.read_text(encoding="utf-8").replace("${ROUTING_DELEGATION_MAX_DEPTH}", "2")
    after, stderr = _leaves(tmp_path, live, newer, "delegation.max_spawn_depth")
    assert "delegation.max_spawn_depth" in stderr
    lines = after.split("\n")
    at = lines.index("  max_spawn_depth: 2")
    assert lines.index("delegation:") < at < lines.index("memory:")
    leaves = ENTRYPOINT.read_text(encoding="utf-8").split('CONFIG_LEAVES="', 1)[1].split('"', 1)[0].split()
    assert "delegation.max_spawn_depth" in leaves
    # The operator's own depth is never replaced.
    kept, stderr2 = _leaves(tmp_path, after.replace("max_spawn_depth: 2", "max_spawn_depth: 1"), newer,
                            "delegation.max_spawn_depth")
    assert "  max_spawn_depth: 1" in kept and stderr2 == ""


# --------------------------------------------------------------------------- #
# The operator override file (ADR-0044): exported over the environment at start
# --------------------------------------------------------------------------- #
def _override_env(tmp_path: Path, overrides, *, as_text: str | None = None) -> tuple[dict, str]:
    """Run entrypoint.sh's own apply_env_overrides in a bash and return (env after, stderr).

    The function is cut out of the script by its braces and sourced into a fresh bash, so
    the shell loop and the embedded python are the real ones. ``python3`` resolves to this
    interpreter (the schema is importable from it) the way the image's venv python is.
    """
    text = ENTRYPOINT.read_text(encoding="utf-8")
    start = text.index("apply_env_overrides() {")
    end = text.index("\n}\n", start) + 3
    func = text[start:end]
    file = tmp_path / ".storage" / "environment.json"
    file.parent.mkdir(exist_ok=True)
    file.write_text(as_text if as_text is not None else json.dumps(overrides), encoding="utf-8")
    script = func + f'\napply_env_overrides "{file}"\nenv -0\n'
    env = {**os.environ, "PATH": f"{Path(sys.executable).parent}:{os.environ.get('PATH', '')}",
           "LLM_URL": "http://from-compose:9000/v1"}
    out = subprocess.run(["bash", "-euo", "pipefail", "-c", script], check=True,
                         capture_output=True, env=env)
    after = dict(item.split("=", 1) for item in out.stdout.decode().split("\0") if "=" in item)
    return after, out.stderr.decode()


def test_an_override_is_exported_over_the_compose_value_and_only_its_name_is_logged(tmp_path):
    after, stderr = _override_env(tmp_path, {"LLM_URL": "http://override:9000/v1",
                                             "GOALS_MAX_TURNS": "30"})
    assert after["LLM_URL"] == "http://override:9000/v1"
    assert after["GOALS_MAX_TURNS"] == "30"
    assert "overrides: 2 applied" in stderr and "GOALS_MAX_TURNS LLM_URL" in stderr
    assert "override:9000" not in stderr and "30" not in stderr.split("applied")[0]


def test_a_name_that_is_not_an_overridable_setting_never_reaches_the_environment(tmp_path):
    """This runs as root in the container and the file is the gateway uid's to write."""
    after, stderr = _override_env(tmp_path, {
        "LD_PRELOAD": "/opt/data/evil.so",            # not a setting
        "SELFEXT_REPO_PATH": "/etc",                  # root chowns it later in this script
        "TAILSCALE_IP": "10.0.0.1",                   # a compose port bind
        "HERMES_API_KEY": "stolen",                   # renamed by compose on its way in
        "LLM_URL": "http://override:9000/v1",
    })
    for name in ("LD_PRELOAD", "SELFEXT_REPO_PATH", "TAILSCALE_IP", "HERMES_API_KEY"):
        assert name not in after, name
    assert after["LLM_URL"] == "http://override:9000/v1"
    assert "overrides: 1 applied" in stderr


def test_a_value_with_a_newline_survives_and_an_empty_string_unsets_nothing(tmp_path):
    after, _ = _override_env(tmp_path, {"LLM_URL": "http://a\nb", "GOALS_MAX_TURNS": ""})
    assert after["LLM_URL"] == "http://a\nb"
    assert after["GOALS_MAX_TURNS"] == ""  # exported empty: from_env reads an empty variable as unset


def test_a_missing_or_broken_file_applies_nothing_and_says_so(tmp_path):
    after, stderr = _override_env(tmp_path, None, as_text="{not json")
    assert after["LLM_URL"] == "http://from-compose:9000/v1"
    assert "unreadable" in stderr and "none applied" in stderr
    after, stderr = _override_env(tmp_path, None, as_text="[1, 2]")
    assert after["LLM_URL"] == "http://from-compose:9000/v1" and "none applied" in stderr
    after, stderr = _override_env(tmp_path, {})
    assert "overrides: none in" in stderr
    # no file at all: silent, nothing exported
    text = ENTRYPOINT.read_text(encoding="utf-8")
    func = text[text.index("apply_env_overrides() {"):text.index("\n}\n", text.index("apply_env_overrides() {")) + 3]
    out = subprocess.run(["bash", "-euo", "pipefail", "-c", func + f'\napply_env_overrides "{tmp_path}/absent.json"\n'],
                         check=True, capture_output=True, text=True)
    assert out.stderr == ""


def test_the_block_runs_before_the_first_render_and_the_upgrade_render():
    """Both renders read the environment; an override exported after either is a no-op."""
    text = ENTRYPOINT.read_text(encoding="utf-8")
    applied = text.index('apply_env_overrides "$HERMES_HOME/.storage/environment.json"')
    assert applied < text.index('render "$SEED/config.template.yaml" "$HERMES_HOME/config.yaml"')
    assert applied < text.index('render "$SEED/config.template.yaml" "$rendered_template"')



def test_the_concurrency_and_model_leaves_follow_the_env_on_every_start(tmp_path):
    """The twenty-agent proof (P1.4, 2026-09-18) was refused at its first delegate_task:
    "max_concurrent_children is 5". The host's .env said 20 and the template renders it,
    but the volume had been rendered when the answer was 5 and only the depth leaf beside
    it was pinned. Both of delegation's env-fed scalars are pinned now. Not covered: the
    value the env carries being sane -- that is the schema's bounds check.
    """
    live = "delegation:\n  provider: \"\"\n  model: \"\"\n  max_concurrent_children: 5\n  max_spawn_depth: 2\n  max_iterations: 60\n"
    tpl = "delegation:\n  provider: \"\"\n  model: \"qwen3.8-27b\"\n  max_concurrent_children: 20\n  max_spawn_depth: 2\n  max_iterations: 60\n"
    after, err = _leaves(tmp_path, live, tpl, "=delegation.max_concurrent_children", "=delegation.model")
    assert "  max_concurrent_children: 20\n" in after
    assert '  model: "qwen3.8-27b"\n' in after
    assert "pinned: 5 -> 20" in err
    leaves = ENTRYPOINT.read_text().split('CONFIG_LEAVES="', 1)[1].split('"', 1)[0].split()
    assert "=delegation.max_concurrent_children" in leaves and "=delegation.model" in leaves


# --- prune_config_list: the other direction ---------------------------------------------

def _prune(tmp_path: Path, live: str, path: str, *items: str, only_if: str = "") -> tuple[str, str]:
    """Run entrypoint.sh's own prune_config_list over a file. Returns (config, stderr)."""
    code = _heredoc("PYPRUNE")
    live_path = tmp_path / "config.yaml"
    live_path.write_text(live, encoding="utf-8")
    env = {**os.environ, "PRUNE_ONLY_IF": only_if}
    out = subprocess.run([sys.executable, "-c", code, str(live_path), path, *items],
                         check=True, capture_output=True, text=True, env=env)
    return live_path.read_text(encoding="utf-8"), out.stderr


TOOLS = """\
model:
  default: qwen3.8-27b
tools:
  enabled:
    - web
    - context_engine   # left by the 09-02 template
    - a2a
    - vision
  disabled:
    - computer_use
plugins:
  enabled:
    - a2a-lookalike
"""


def test_a_retired_toolset_is_pruned_from_the_list_it_is_named_in(tmp_path):
    """Production kept `context_engine` and `a2a` in tools.enabled a week after the template
    dropped them (audit §10, 2026-09-18). Only the named items go, only from the named list:
    the `plugins.enabled` entry that merely looks like one stays. Not covered: an item the
    template still names — the entrypoint's RETIRED_TOOLSETS is the only source of names.
    """
    after, err = _prune(tmp_path, TOOLS, "tools.enabled", "context_engine", "a2a")
    assert "context_engine" not in after.split("plugins:")[0]
    assert "- a2a\n" not in after.split("plugins:")[0]
    assert "- web\n" in after and "- vision\n" in after and "- computer_use\n" in after
    assert "- a2a-lookalike" in after
    assert "config.yaml -= tools.enabled: context_engine a2a" in err


def test_pruning_is_idempotent_and_silent_when_nothing_is_listed(tmp_path):
    once, _ = _prune(tmp_path, TOOLS, "tools.enabled", "context_engine", "a2a")
    twice, err = _prune(tmp_path, once, "tools.enabled", "context_engine", "a2a")
    assert twice == once and err == ""
    untouched, err = _prune(tmp_path, TOOLS, "tools.nothing", "context_engine")
    assert untouched == TOOLS and err == ""


def test_a_retired_toolset_in_a_list_written_at_its_keys_own_indent_is_pruned(tmp_path):
    """PyYAML writes `enabled:` and its `- web` items at the same column. The block of
    `enabled` ended at its first such item, so the prune found nothing and the retired names
    stayed on production. The sibling `disabled` list is the next key, so its `- a2a` must
    survive a prune of `enabled`, and so must every other `enabled` item."""
    live = ("tools:\n  enabled:\n  - web\n  - context_engine\n  - a2a\n  - vision\n"
            "  disabled:\n  - a2a\n  - computer_use\n")
    after, err = _prune(tmp_path, live, "tools.enabled", "context_engine", "a2a")
    assert after == ("tools:\n  enabled:\n  - web\n  - vision\n"
                     "  disabled:\n  - a2a\n  - computer_use\n")
    assert "config.yaml -= tools.enabled: context_engine a2a" in err


def test_a_top_level_indentless_list_is_pruned_too(tmp_path):
    """The entrypoint's second caller prunes `toolsets: kanban` from a top-level list written
    at column 0 with its key. Same fault, same fix: the list is the key's block."""
    live = "toolsets:\n- web\n- kanban\nplatform_toolsets:\n  tui: x\n"
    after, err = _prune(tmp_path, live, "toolsets", "kanban")
    assert after == "toolsets:\n- web\nplatform_toolsets:\n  tui: x\n"
    assert "config.yaml -= toolsets: kanban" in err


EXTRA = """\
providers:
  qwen-vllm:
    extra_body:
      reasoning_effort: "medium"   # the old bound
      chat_template_kwargs:
        reasoning_effort: "medium"
      note: |
        reasoning_effort: kept, it is text
      flow: {reasoning_effort: low}
    api_key: "x"
"""


def test_a_retired_leaf_goes_and_only_the_direct_child_of_that_name(tmp_path):
    """T-0814 retires `extra_body.reasoning_effort`. A one-line leaf with an inline comment
    goes; the nested `chat_template_kwargs.reasoning_effort`, a block scalar's text and a
    flow mapping's key that merely carry the name stay, and so does the next sibling."""
    after, err = _prune(tmp_path, EXTRA, "providers.qwen-vllm.extra_body", "reasoning_effort")
    assert "the old bound" not in after
    assert '      chat_template_kwargs:\n        reasoning_effort: "medium"\n' in after
    assert "reasoning_effort: kept, it is text" in after and "flow: {reasoning_effort: low}" in after
    assert '    api_key: "x"' in after
    assert "config.yaml -= providers.qwen-vllm.extra_body: reasoning_effort" in err


def test_a_block_valued_key_of_the_retired_name_is_never_cut_in_half(tmp_path):
    """Removing only the key line of `reasoning_effort: |` would orphan its text and leave
    a config Hermes cannot parse; a value that is not on the line is not a one-line leaf."""
    live = "a:\n  reasoning_effort: |\n    medium\n  b: 1\n"
    after, err = _prune(tmp_path, live, "a", "reasoning_effort")
    assert after == live and err == ""


def test_the_old_bound_stays_until_its_replacement_is_there(tmp_path):
    """The T-0814 review: a volume whose operator wrote their own `chat_template_kwargs`
    (the old template suggested `{enable_thinking: false}` there) does not get the new
    default copied in, and pruning the old one then would leave every request that carries
    no level unbounded -- the 4000-token alert turns of pipeline 145. It stays, and says
    why on every start until someone moves it."""
    live = ('providers:\n  qwen-vllm:\n    extra_body:\n      reasoning_effort: "medium"\n'
            "      chat_template_kwargs: {enable_thinking: false}\n")
    need = "providers.qwen-vllm.extra_body.chat_template_kwargs.reasoning_effort"
    after, err = _prune(tmp_path, live, "providers.qwen-vllm.extra_body", "reasoning_effort", only_if=need)
    assert after == live
    assert "keeps providers.qwen-vllm.extra_body: reasoning_effort" in err and need in err
    moved = live.replace("{enable_thinking: false}", '\n        reasoning_effort: "medium"')
    after, err = _prune(tmp_path, moved, "providers.qwen-vllm.extra_body", "reasoning_effort", only_if=need)
    assert '      reasoning_effort: "medium"\n      chat_template_kwargs' not in after
    assert "-= providers.qwen-vllm.extra_body: reasoning_effort" in err


def test_the_entrypoint_names_the_two_retired_toolsets():
    """The names live in one place, and the audit's two are the ones."""
    text = ENTRYPOINT.read_text()
    assert 'RETIRED_TOOLSETS="context_engine a2a"' in text
    assert 'prune_config_list "$HERMES_HOME/config.yaml" tools.enabled $RETIRED_TOOLSETS' in text


def test_the_model_endpoint_follows_the_env_on_every_start(tmp_path):
    # 2026-10-09: LLM_URL moved off the deleted AI container, and Hermes kept calling it,
    # because config.yaml in the volume held the URL rendered on the first start.
    live = ('model:\n  default: "qwen3.8-27b"\n  base_url: "http://old-ai-host:9000/v1"\n'
            'providers:\n  qwen-vllm:\n    api: "http://old-ai-host:9000/v1"\n')
    template = live.replace("http://old-ai-host:9000/v1", "http://jarvis-models:8000/v1")
    after, _ = _leaves(tmp_path, live, template, "=model.base_url", "=providers.qwen-vllm.api")
    assert "old-ai-host" not in after and after.count("http://jarvis-models:8000/v1") == 2
    leaves = ENTRYPOINT.read_text(encoding="utf-8").split('CONFIG_LEAVES="', 1)[1].split('"', 1)[0].split()
    assert {"=model.base_url", "=providers.qwen-vllm.api"} <= set(leaves)


def test_the_context_window_follows_the_env_on_every_start(tmp_path):
    # 2026-10-09: the volume kept the first start's 262144 while jarvis-models serves
    # --max-model-len 256000, so Hermes would have sent up to 6k tokens past the window
    # before compressing; LLM_CONTEXT_LENGTH in .env could not correct it.
    live = ('model:\n  default: "qwen3.8-27b"\n  context_length: 262144\n'
            'providers:\n  qwen-vllm:\n    context_length: 262144\n')
    template = live.replace("262144", "256000")
    after, _ = _leaves(tmp_path, live, template, "=model.context_length", "=providers.qwen-vllm.context_length")
    assert "262144" not in after and after.count("context_length: 256000") == 2
    leaves = ENTRYPOINT.read_text(encoding="utf-8").split('CONFIG_LEAVES="', 1)[1].split('"', 1)[0].split()
    assert {"=model.context_length", "=providers.qwen-vllm.context_length"} <= set(leaves)


def _blocks(tmp_path: Path, live: str, template: str) -> tuple[str, str]:
    """Run entrypoint.sh's own ensure_config_blocks over two files. Returns (config, stderr)."""
    code = _heredoc("PYBLOCKS")
    live_path, tpl_path = tmp_path / "config.yaml", tmp_path / "template.yaml"
    live_path.write_text(live, encoding="utf-8")
    tpl_path.write_text(template, encoding="utf-8")
    out = subprocess.run(["python3", "-c", code, str(live_path), str(tpl_path)],
                         check=True, capture_output=True, text=True)
    return live_path.read_text(encoding="utf-8"), out.stderr


def test_the_catalogue_switches_reach_an_old_volume_and_keep_hermes_in_the_house(tmp_path):
    """T-0839: Hermes asked hermes-agent.nousresearch.com for its model catalogue every 20
    minutes and models.dev every 4 hours, with nobody asking (the T-0767 capture). The
    template switches the catalogue off and points models.dev at a closed loopback port, and
    a volume rendered before that gains the three blocks on its next start. Not covered: the
    OpenRouter caps URL, which has no config key (deploy/Dockerfile patches it)."""
    import yaml
    from urllib.parse import urlsplit

    live = "model:\n  default: x\n\nsecurity:\n  allow_private_urls: true\n"
    after, stderr = _blocks(tmp_path, live, TEMPLATE.read_text(encoding="utf-8"))
    for name in ("model_catalog", "models_dev", "updates"):
        assert name in stderr
    cfg = yaml.safe_load(after)
    assert cfg["model_catalog"]["enabled"] is False
    assert urlsplit(cfg["models_dev"]["url"]).hostname == "127.0.0.1"
    assert cfg["updates"]["check"] is False
    assert cfg["model"] == {"default": "x"}
    # An operator who turned the catalogue back on keeps their answer.
    mine = live + "\nmodel_catalog:\n  enabled: true\n"
    kept, _ = _blocks(tmp_path, mine, TEMPLATE.read_text(encoding="utf-8"))
    assert yaml.safe_load(kept)["model_catalog"]["enabled"] is True


def test_the_kanban_board_reaches_an_old_volume_with_its_dispatcher_off(tmp_path):
    """T-0909: asked for something no tool reaches, AURA follows the capability-gap skill to
    `kanban_create`, which was never among her tools: the kanban toolset was off, so she went
    round later_add, a read-only notes dir, memory and the shell. The template opts the
    owner's surfaces into kanban with a saved tui list and turns the in-gateway dispatcher off,
    so a filed card is a record on the Board and never a worker run. T-0937: T-0935 used Hermes'
    legacy top-level `toolsets: [kanban]`, which reaches every platform with no saved list
    (webhook, telegram, discord, cron); a volume that took it loses it here. Not covered: what
    Hermes resolves from these lists (probe_tui_scope.sh checks that on the deployed box)."""
    import yaml

    # A volume as T-0935's deploy left it: the legacy opt-in, no tui list.
    live = ("model:\n  default: x\n\nplatform_toolsets:\n  cli:\n    - hermes-cli\n"
            "  api_server:\n    - hermes-api-server\n\ntoolsets:\n  - kanban\n")
    template = TEMPLATE.read_text(encoding="utf-8")
    after, stderr = _blocks(tmp_path, live, template)
    assert "kanban" in stderr
    leaves = ENTRYPOINT.read_text(encoding="utf-8").split('CONFIG_LEAVES="', 1)[1].split('"', 1)[0].split()
    assert "platform_toolsets.tui" in leaves
    after, _ = _leaves(tmp_path, after, template, *leaves)
    assert 'prune_config_list "$HERMES_HOME/config.yaml" toolsets kanban' in ENTRYPOINT.read_text(encoding="utf-8")
    after, _ = _prune(tmp_path, after, "toolsets", "kanban")
    cfg = yaml.safe_load(after)
    assert not cfg.get("toolsets")
    assert cfg["platform_toolsets"]["tui"] == ["hermes-tui", "kanban"]
    assert cfg["kanban"]["dispatch_in_gateway"] is False and cfg["kanban"]["review_dispatch"] is False
    # Only tui is granted kanban: the shipped template has no legacy opt-in and no other list
    # names it, so n8n's webhook events, telegram, discord, cron and the API server file no cards.
    shipped = yaml.safe_load(template)
    assert "toolsets" not in shipped
    assert [p for p, names in shipped["platform_toolsets"].items() if "kanban" in names] == ["tui"]


# --- T-1206: a list written at its key's own indent, and an atomic write ------------------

def test_a_leaf_with_an_indentless_list_arrives_whole_and_lands_after_a_list_sibling(tmp_path):
    """PyYAML writes `cli:` and its `- item` lines in one column. The block of `cli` ended at its
    first item, so `tui` was copied as a bare `tui:` (its list left behind) and, after a pass
    through the volume, read as an empty key. Not covered: a flow list (`[a, b]`), which is one
    line and never had the fault."""
    import yaml

    live = "platform_toolsets:\n  cli:\n  - hermes-cli\nkanban:\n  dispatch_in_gateway: false\n"
    template = ("platform_toolsets:\n  cli:\n  - hermes-cli\n  tui:\n  - hermes-tui\n  - kanban\n"
                "kanban:\n  dispatch_in_gateway: false\n")
    after, stderr = _leaves(tmp_path, live, template, "platform_toolsets.tui")
    assert "platform_toolsets.tui" in stderr
    assert after == ("platform_toolsets:\n  cli:\n  - hermes-cli\n  tui:\n  - hermes-tui\n"
                     "  - kanban\nkanban:\n  dispatch_in_gateway: false\n")
    assert yaml.safe_load(after)["platform_toolsets"]["tui"] == ["hermes-tui", "kanban"]
    again, stderr = _leaves(tmp_path, after, template, "platform_toolsets.tui")
    assert again == after and stderr == ""


def test_a_key_whose_value_is_an_indentless_list_is_not_an_empty_key(tmp_path):
    """The empty-key repair inserts the template's children under a bare `key:`. A key with a
    list at its own indent is not bare, so nothing may be inserted beneath it."""
    live = "tts:\n  openai:\n  - legacy\n  provider: piper\n"
    template = "tts:\n  openai:\n    voice: nova\n  provider: piper\n"
    after, stderr = _leaves(tmp_path, live, template, "tts.openai")
    assert after == live and stderr == ""


def _killed_before_replace(code: str, argv: list[str]) -> subprocess.CompletedProcess:
    """Run an entrypoint heredoc with os.replace made to kill the process the way a container
    recreate does: after the new text was written somewhere, before it took the old file's name."""
    prelude = "import os\nos.replace = lambda *a, **k: os._exit(137)\n"
    return subprocess.run([sys.executable, "-c", prelude + code, *argv], capture_output=True, text=True)


def test_a_write_killed_midway_leaves_the_old_config_whole_in_both_upgrades(tmp_path):
    """The gateway cannot start from a truncated config.yaml. Each writer writes a temp file and
    renames it, so a kill leaves the old file; asserted on the file, not on how it was written."""
    live_path, tpl_path = tmp_path / "config.yaml", tmp_path / "template.yaml"
    live_path.write_text(LIVE, encoding="utf-8")
    tpl_path.write_text(NEWER, encoding="utf-8")
    done = _killed_before_replace(_heredoc("PYLEAVES"),
                                  [str(live_path), str(tpl_path), "stt.local.initial_prompt"])
    assert done.returncode == 137
    assert live_path.read_text(encoding="utf-8") == LIVE

    live_path.write_text(TOOLS, encoding="utf-8")
    done = _killed_before_replace(_heredoc("PYPRUNE"), [str(live_path), "tools.enabled", "a2a"])
    assert done.returncode == 137
    assert live_path.read_text(encoding="utf-8") == TOOLS


def test_a_rewritten_config_keeps_its_mode_and_leaves_no_temp_file_behind(tmp_path):
    """The entrypoint starts as root and drops to uid 10000 later: the replacement must carry the
    old file's owner and mode (a fresh temp file is root's, 0600). Owner is checked as far as a
    non-root test can: the uid/gid are the file's own after the rewrite."""
    live_path, tpl_path = tmp_path / "config.yaml", tmp_path / "template.yaml"
    tpl_path.write_text(NEWER, encoding="utf-8")
    for tag, argv, text in (
        ("PYLEAVES", [str(tpl_path), "stt.local.initial_prompt"], LIVE),
        ("PYPRUNE", ["tools.enabled", "a2a"], TOOLS),
    ):
        live_path.write_text(text, encoding="utf-8")
        live_path.chmod(0o640)
        before = live_path.stat()
        subprocess.run([sys.executable, "-c", _heredoc(tag), str(live_path), *argv],
                       check=True, capture_output=True, text=True,
                       env={**os.environ, "PRUNE_ONLY_IF": ""})
        after = live_path.stat()
        assert live_path.read_text(encoding="utf-8") != text
        assert after.st_mode == before.st_mode and (after.st_uid, after.st_gid) == (before.st_uid, before.st_gid)
        assert sorted(p.name for p in tmp_path.iterdir()) == ["config.yaml", "template.yaml"]


# --- T-1245: every other writer in the entrypoint goes through the same write_atomic ------

BLOCK_TEMPLATE = "model:\n  default: x\n\nupdates:\n  check: false\n"


def _writers(tmp_path: Path):
    """(tag, argv, the file it rewrites, that file's text before, its parent) for the writers
    that were plain `open(..., "w")` until T-1245: the plugin list, the missing blocks, the
    cron seed record and the render."""
    tpl = tmp_path / "template.yaml"
    tpl.write_text(BLOCK_TEMPLATE, encoding="utf-8")
    cfg, seeded = tmp_path / "config.yaml", tmp_path / "cron" / ".seeded.json"
    seeded.parent.mkdir()
    out = tmp_path / "rendered.yaml"
    return [
        ("PYPLUGINS", [str(cfg), "web-searxng"], cfg, "model:\n  default: x\n"),
        ("PYBLOCKS", [str(cfg), str(tpl)], cfg, "model:\n  default: x\n"),
        ("PYCRONREC", [str(tmp_path), "brief", "d1"], seeded, '{\n "old": "d0"\n}'),
        ("PY", [str(tpl), str(out)], out, "# the last render\n"),
    ]


def test_every_atomic_writer_killed_midway_leaves_its_old_file_whole(tmp_path):
    """Plain `open(path, "w")` truncates first: a kill between that and the last byte left a
    half config.yaml the gateway cannot start from, or a half .seeded.json that re-seeds every
    cron row. Asserted on the file after a kill just before the rename."""
    for tag, argv, target, before in _writers(tmp_path):
        target.write_text(before, encoding="utf-8")
        done = _killed_before_replace(_heredoc(tag), argv)
        assert done.returncode == 137, (tag, done.stderr)
        assert target.read_text(encoding="utf-8") == before, tag


def test_every_atomic_writer_keeps_the_mode_and_leaves_no_temp_file(tmp_path):
    for tag, argv, target, before in _writers(tmp_path):
        target.write_text(before, encoding="utf-8")
        target.chmod(0o640)
        st = target.stat()
        siblings = sorted(p.name for p in target.parent.iterdir())
        subprocess.run([sys.executable, "-c", _heredoc(tag), *argv], check=True, capture_output=True,
                       text=True, env={**os.environ, "PRUNE_ONLY_IF": ""})
        after = target.stat()
        assert target.read_text(encoding="utf-8") != before, tag
        assert after.st_mode == st.st_mode and (after.st_uid, after.st_gid) == (st.st_uid, st.st_gid), tag
        assert sorted(p.name for p in target.parent.iterdir()) == siblings, tag


def test_a_target_that_does_not_exist_yet_is_created_by_the_same_writer(tmp_path):
    """The first render and the first cron record have no file to copy an owner and a mode from:
    the file is created with a plain write's mode (umask), not mkstemp's 0600."""
    out, home = tmp_path / "first.yaml", tmp_path / "home"
    home.mkdir()
    tpl = tmp_path / "template.yaml"
    tpl.write_text("a: 1\n", encoding="utf-8")
    subprocess.run([sys.executable, "-c", _heredoc("PY"), str(tpl), str(out)], check=True,
                   capture_output=True, text=True)
    subprocess.run([sys.executable, "-c", _heredoc("PYCRONREC"), str(home), "brief", "d1"], check=True)
    assert out.read_text(encoding="utf-8") == "a: 1\n"
    assert json.loads((home / "cron" / ".seeded.json").read_text()) == {"brief": "d1"}
    umask = os.umask(0)
    os.umask(umask)
    assert out.stat().st_mode & 0o777 == 0o666 & ~umask
    assert sorted(p.name for p in tmp_path.iterdir()) == ["first.yaml", "home", "template.yaml"]


def test_atomic_is_the_only_way_the_entrypoints_python_writes_a_file():
    """The scan behind the criterion: one write_atomic, and every `open(` in an entrypoint
    heredoc that writes is that function's own mkstemp descriptor."""
    import re

    text = ENTRYPOINT.read_text(encoding="utf-8")
    assert text.count("def write_atomic") == 1
    for m in re.finditer(r"<<'(PY\w*)'\n(.*?)\n\1\n", text, re.S):
        tag, code = m.groups()
        if tag == "PYATOMIC":
            continue
        assert not re.search(r"open\([^)]*[\"'][wax+]", code), f"{tag} opens a file for writing"
        assert ".write_text(" not in code and ".write(" not in code.replace("sys.stdout.write(", ""), tag


# --- T-1265: the shell's own writes (seeded files, their digests, the marker) are atomic ---

def _seed_run(tmp_path: Path, src_text: str, *, target: str | None, stamp: str | None,
              kill_rename: bool = False, mode: int | None = None, src_mode: int | None = None):
    """Run entrypoint.sh's own seed_file (and its helper) in bash over a scratch home. With
    kill_rename a `mv` that dies stands in for a start killed after the temp file was written
    and before the rename, which is the only window a plain `cp`/`echo >` left open."""
    import re

    text = ENTRYPOINT.read_text(encoding="utf-8")
    funcs = "\n".join(re.search(rf"^{n}\(\) \{{.*?^\}}", text, re.S | re.M).group(0)
                      for n in ("atomic_write", "seed_file"))
    home, seed, state, bin_ = (tmp_path / d for d in ("home", "seed", "state", "bin"))
    for d in (home / "memories", seed / "home", state, bin_):
        d.mkdir(parents=True, exist_ok=True)
    (seed / "home" / "SOUL.md").write_text(src_text, encoding="utf-8")
    if src_mode is not None:
        (seed / "home" / "SOUL.md").chmod(src_mode)
    tgt, stp = home / "SOUL.md", state / "SOUL.md.sha256"
    if target is not None:
        tgt.write_text(target, encoding="utf-8")
        if mode is not None:
            tgt.chmod(mode)
    if stamp is not None:
        stp.write_text(stamp, encoding="utf-8")
    if kill_rename:
        (bin_ / "mv").write_text("#!/bin/sh\nkill -9 $$\n", encoding="utf-8")
        (bin_ / "mv").chmod(0o755)
    script = (f'set -euo pipefail\nHERMES_HOME={home}\nSEED={seed}\nSEED_STATE={state}\n'
              'log() { echo "$*" >&2; }\ndigest() { sha256sum "$1" | cut -d" " -f1; }\n'
              f'{funcs}\nseed_file home/SOUL.md SOUL.md\n')
    done = subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                          env={**os.environ, "PATH": f"{bin_}:{os.environ['PATH']}"})
    return done, tgt, stp


def _digest(text: str) -> str:
    import hashlib
    return hashlib.sha256(text.encode()).hexdigest()


def test_entrypoint_atomic_seed_file_killed_before_the_rename_leaves_target_and_stamp_whole(tmp_path):
    """`cp -f src target` and `echo > stamp` truncate first: a start killed between that and the
    last byte left a half SOUL.md or an empty stamp, which reads as "this host has local edits"
    and the file is never refreshed again. Each of the three stamp writes and both target
    copies is run with the rename killed; the old file (or none) must be what is left."""
    cases = {
        "first add": dict(target=None, stamp=None),
        "refresh": dict(target="old\n", stamp=_digest("old\n") + "\n"),
        "adopt": dict(target="new\n", stamp=None),
    }
    for name, kw in cases.items():
        work = tmp_path / name.replace(" ", "_")
        done, tgt, stp = _seed_run(work, "new\n", kill_rename=True, **kw)
        assert done.returncode != 0, (name, done.stderr)
        got = lambda p: p.read_text(encoding="utf-8") if p.exists() else None  # noqa: E731
        assert got(tgt) == kw["target"], name
        assert got(stp) == kw["stamp"], name


def test_entrypoint_seed_file_atomic_write_keeps_mode_and_leaves_no_temp_file(tmp_path):
    done, tgt, stp = _seed_run(tmp_path / "refresh", "new\n", target="old\n",
                               stamp=_digest("old\n") + "\n", mode=0o640)
    assert done.returncode == 0, done.stderr
    assert tgt.read_text(encoding="utf-8") == "new\n" and tgt.stat().st_mode & 0o777 == 0o640
    assert stp.read_text(encoding="utf-8").strip() == _digest("new\n")
    assert [p.name for p in tgt.parent.iterdir() if p.name.startswith(".")] == []
    assert [p.name for p in stp.parent.iterdir()] == [stp.name]
    done, tgt, stp = _seed_run(tmp_path / "first", "new\n", target=None, stamp=None)
    assert done.returncode == 0, done.stderr
    umask = os.umask(0)
    os.umask(umask)
    assert tgt.stat().st_mode & 0o777 == 0o666 & ~umask == stp.stat().st_mode & 0o777


def test_entrypoint_atomic_first_add_takes_the_source_s_mode_as_cp_did(tmp_path):
    """A rendered seed (MEMORY.md) comes from a mktemp file, 0600, and `cp` carried that to a
    new target. Created at the umask's mode instead, the owner's memory file on a new volume
    would be readable by every user in the container."""
    done, tgt, _stp = _seed_run(tmp_path, "new\n", target=None, stamp=None, src_mode=0o600)
    assert done.returncode == 0, done.stderr
    assert tgt.stat().st_mode & 0o777 == 0o600


def test_entrypoint_atomic_scan_no_bare_redirect_or_cp_onto_a_seeded_file_stamp_or_marker():
    """The scan behind T-1265: the seeded target, its stamp and the .jarvis-rendered marker are
    written only through atomic_write."""
    import re

    text = ENTRYPOINT.read_text(encoding="utf-8")
    assert text.count("atomic_write() {") == 1
    assert not re.search(r'>\s*"\$(stamp|MARKER)"', text)
    assert not re.search(r'\bcp\b[^\n|;]*"\$target"\s*$', text, re.M)
    assert re.search(r'atomic_write "\$MARKER"', text)
