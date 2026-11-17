"""The config template renders to valid YAML with the keys Phase A/C settled on, and the
entrypoint's first-start / later-start rules hold (run against a temp HERMES_HOME).
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
ENTRYPOINT = ROOT / "deploy" / "entrypoint.sh"
TEMPLATE = ROOT / "deploy" / "config.template.yaml"

ENV = {
    "LLM_URL": "http://jarvis-models:8000/v1", "LLM_MODEL": "qwen3.8-27b", "LLM_CONTEXT_LENGTH": "262144",
    "GOALS_MAX_TURNS": "20", "AURA_CORS_ORIGINS": "http://console.test", "WHISPER_MODEL": "base",
    "PIPER_VOICE": "en_GB-alan-medium", "GITLAB_API_URL": "https://gitlab.test/api/v4", "GITLAB_TOKEN": "",
    "MCP_GITLAB_ENABLED": "false", "ROUTING_DELEGATION_MAX_CONCURRENT": "3",
    # The compose file's defaults for the voice (deploy/docker-compose.yml, the jarvis service).
    "TTS_PROVIDER": "openai", "TTS_OPENAI_BASE_URL": "http://jarvis-cosyvoice:8890/v1",
    "TTS_OPENAI_MODEL": "kokoro", "TTS_VOICE": "jarvis",
    # The compose file's default for the terminal backend (ADR-0045).
    "AURA_TERMINAL_BACKEND": "aura-bwrap",
    # And for the reasoning bound (`${LLM_REASONING_EFFORT:-medium}`).
    "LLM_REASONING_EFFORT": "medium",
}


def _renderer() -> str:
    """The renderer's python as `py_atomic` runs it: the shared write_atomic, then the heredoc."""
    src = ENTRYPOINT.read_text()
    body = lambda tag: src.split(f"<<'{tag}'", 1)[1].split("\n", 1)[1].split(f"\n{tag}\n", 1)[0]  # noqa: E731
    return body("PYATOMIC") + "\n" + body("PY")


def _render(dst: Path, extra: dict | None = None) -> dict:
    """Use the exact python renderer embedded in entrypoint.sh."""
    code = _renderer()
    env = {**os.environ, **ENV, **(extra or {})}
    subprocess.run([sys.executable, "-c", code, str(TEMPLATE), str(dst)], check=True, env=env)
    return yaml.safe_load(dst.read_text())


def test_template_renders_to_the_phase_a_settings(tmp_path):
    cfg = _render(tmp_path / "config.yaml")
    assert cfg["model"] == {"default": "qwen3.8-27b", "provider": "qwen-vllm", "base_url": "http://jarvis-models:8000/v1",
                            "api_key": "", "context_length": 262144, "api_mode": "chat_completions",
                            # Declared, or every look at a picture is a separate essay (2026-09-10).
                            "supports_vision": True,
                            # Capped, or a turn reasoning in circles runs for ten minutes (2026-09-10).
                            "max_tokens": 12288}
    p = cfg["providers"]["qwen-vllm"]
    assert p["request_timeout_seconds"] == 900 and p["stale_timeout_seconds"] == 900 and p["context_length"] == 262144
    assert cfg["goals"]["max_turns"] == 20 and cfg["auxiliary"]["goal_judge"]["timeout"] == 600
    assert cfg["gateway"]["api_server"]["port"] == 8642 and cfg["gateway"]["api_server"]["host"] == "0.0.0.0"
    assert cfg["stt"]["provider"] == "local" and cfg["tts"]["piper"]["voice"] == "en_GB-alan-medium"
    # CosyVoice by default (ADR-0039), through Hermes' own OpenAI-shaped TTS provider.
    assert cfg["tts"]["provider"] == "openai"
    assert cfg["tts"]["openai"] == {"base_url": "http://jarvis-cosyvoice:8890/v1", "model": "kokoro", "voice": "jarvis"}
    # The sandbox (ADR-0045): every command under bubblewrap, a scratch per task, reset per session.
    assert cfg["terminal"]["backend"] == "aura-bwrap" and "docker_image" not in cfg["terminal"]
    assert cfg["terminal"]["container_persistent"] is False
    assert cfg["terminal"]["sandbox_dir"] == "/opt/data/sandboxes"
    assert cfg["mcp_servers"]["gitlab"]["enabled"] is False
    assert set(cfg["plugins"]["enabled"]) >= {"aura-goals", "aura-personas", "llama-swap-control", "gpu-telemetry", "n8n-trigger",
                                              "proxmox-control", "comfyui-imagegen", "smart-home"}


#: What a leaked credential looks like, precise enough not to fire on prose.
#: Each prefix has to be followed by the length of key material that prefix
#: actually carries. A bare ``sk-`` substring test fired on the plugin name
#: ``disk-space`` the moment it was added to plugins.enabled, which is a false
#: positive that costs whoever hits it an afternoon and, worse, teaches the next
#: person to weaken the check rather than the pattern.
_SECRET_SHAPES: tuple[tuple[str, str], ...] = (
    ("OpenAI-style key", r"\bsk-[A-Za-z0-9_-]{20,}"),
    ("GitLab PAT", r"\bglpat-[A-Za-z0-9_-]{16,}"),
    ("GitLab runner token", r"\bglrt-[A-Za-z0-9_-]{16,}"),
    ("private key", r"BEGIN OPENSSH"),
)


def test_no_secret_placeholder_survives_and_no_secret_is_in_the_template():
    text = TEMPLATE.read_text()
    assert "${API_SERVER_KEY}" not in text and "${AURA_API_KEY}" not in text and "${HASS_TOKEN}" not in text
    for label, pattern in _SECRET_SHAPES:
        found = re.search(pattern, text)
        assert not found, f"{label} in the config template: {found.group(0)[:12]}…"


@pytest.mark.parametrize(("sample", "fires"), [
    ("  api_key: sk-proj-" + "AbCdEfGhIjKlMnOpQrStUvWxYz0123", True),
    ("  token: glpat-AbCdEfGhIjKlMnOpQr", True),
    ("  runner_token: glrt-AbCdEfGhIjKlMnOpQr", True),
    ("-----BEGIN OPENSSH PRIVATE KEY-----", True),
    # The false positive that sent this test looking for a secret in a plugin list.
    ("    - disk-space", False),
    ("    - task-scheduler", False),
    ("# the disk-space plugin reads statvfs", False),
])
def test_the_secret_shapes_fire_on_keys_and_not_on_ordinary_words(sample, fires):
    """The check above is only worth having if it is both sensitive and quiet."""
    hit = any(re.search(pattern, sample) for _, pattern in _SECRET_SHAPES)
    assert hit is fires, sample


def test_first_start_renders_and_later_starts_never_overwrite(tmp_path):
    home = tmp_path / "data"
    seed = tmp_path / "seed"
    (seed / "home").mkdir(parents=True)
    (seed / "plugins" / "demo").mkdir(parents=True)
    (seed / "agent-hooks").mkdir()
    (seed / "plugins" / "demo" / "plugin.yaml").write_text("name: demo\n")
    # `_shared/` is helper code the jarvis-* plugins import, not a plugin: it must land
    # beside them (or their imports fail) and must NOT be appended to plugins.enabled.
    (seed / "plugins" / "_shared").mkdir()
    (seed / "plugins" / "_shared" / "_approval_hook.py").write_text("SID = 1\n")
    (seed / "agent-hooks" / "h.sh").write_text("#!/bin/sh\necho\n")
    for f in ("SOUL.md", "USER.md", "MEMORY.md"):
        (seed / "home" / f).write_text(f"seed {f}\n")
    (seed / "config.template.yaml").write_text(TEMPLATE.read_text())
    script = (ENTRYPOINT.read_text().replace("SEED=/opt/jarvis/seed", f"SEED={seed}")
              .replace('exec hermes "$@"', 'echo "would exec hermes $*"')
              .replace("exec sleep infinity", 'echo "would hold the container open"'))
    ep = tmp_path / "ep.sh"
    ep.write_text(script)
    env = {**os.environ, **ENV, "HERMES_HOME": str(home), "API_SERVER_KEY": "x" * 20}

    out = subprocess.run(["bash", str(ep), "gateway", "run"], check=True, env=env, capture_output=True, text=True)
    assert "first start" in out.stderr and (home / ".jarvis-rendered").exists()
    assert yaml.safe_load((home / "config.yaml").read_text())["model"]["default"] == "qwen3.8-27b"
    assert (home / "SOUL.md").read_text() == "seed SOUL.md\n" and (home / "memories" / "USER.md").exists()
    assert (home / "plugins" / "demo" / "plugin.yaml").exists() and os.access(home / "agent-hooks" / "h.sh", os.X_OK)
    assert (home / "plugins" / "_shared" / "_approval_hook.py").exists()
    assert "would exec hermes gateway run" in out.stdout

    # the operator edits config.yaml and SOUL.md; a restart must keep both
    (home / "config.yaml").write_text("model:\n  default: edited-by-hand\n")
    (home / "SOUL.md").write_text("edited soul\n")
    (home / "memories" / "USER.md").unlink()  # a missing file is re-added
    out = subprocess.run(["bash", str(ep), "gateway", "run"], check=True, env=env, capture_output=True, text=True)
    assert "first start" not in out.stderr
    # The hand edit survives; the only change a later start may make to config.yaml is to
    # append a shipped plugin that was not yet under plugins.enabled (a newer image's
    # plugin stayed disabled on an older volume and the rollout rolled back — pipeline 59).
    after = (home / "config.yaml").read_text()
    assert after.startswith("model:\n  default: edited-by-hand\n"), after
    # "demo" is the seeded plugin; "web-searxng" is a plugin bundled with Hermes rather
    # than shipped in the seed, and it ships DISABLED — without this line an existing
    # volume would carry `web.search_backend: searxng` naming a provider that never
    # registers, and Hermes would fall back to its keyless cloud tier.
    assert yaml.safe_load(after)["plugins"]["enabled"] == ["demo", "web-searxng"], after
    assert (home / "SOUL.md").read_text() == "edited soul\n"
    assert (home / "memories" / "USER.md").read_text() == "seed USER.md\n"
    # and it is idempotent: a third start changes nothing at all
    subprocess.run(["bash", str(ep), "gateway", "run"], check=True, env=env, capture_output=True, text=True)
    assert (home / "config.yaml").read_text() == after


def test_a_later_start_adds_a_config_block_the_newer_template_introduced(tmp_path):
    """A block a newer image ships reaches a volume that already has a config.yaml.

    config.yaml is rendered on the first start only, so the routing table, the delegation
    block and the memory provider (D21/D22) would have lived in the image and never taken
    effect on the deployed volume. Only *missing* top-level keys are appended: a block the
    operator has edited is theirs, and a second pass changes nothing.
    """
    home = tmp_path / "data"
    seed = tmp_path / "seed"
    (seed / "home").mkdir(parents=True)
    (seed / "plugins").mkdir()
    (seed / "agent-hooks").mkdir()
    for f in ("SOUL.md", "USER.md", "MEMORY.md"):
        (seed / "home" / f).write_text(f"seed {f}\n")
    (seed / "config.template.yaml").write_text(TEMPLATE.read_text())
    script = (ENTRYPOINT.read_text().replace("SEED=/opt/jarvis/seed", f"SEED={seed}")
              .replace('exec hermes "$@"', 'echo "would exec hermes $*"')
              .replace("exec sleep infinity", 'echo "would hold the container open"'))
    ep = tmp_path / "ep.sh"
    ep.write_text(script)
    env = {**os.environ, **ENV, "HERMES_HOME": str(home), "API_SERVER_KEY": "x" * 20}
    subprocess.run(["bash", str(ep), "gateway", "run"], check=True, env=env, capture_output=True, text=True)

    # An older volume: the routing/delegation/memory blocks are simply not there, and the
    # operator has changed one value in a block that is.
    home.joinpath("config.yaml").write_text(
        "model:\n  default: qwen3.8-27b\n\napprovals:\n  mode: manual\n"
    )
    out = subprocess.run(["bash", str(ep), "gateway", "run"], check=True, env=env, capture_output=True, text=True)
    assert "config.yaml +=" in out.stderr
    cfg = yaml.safe_load(home.joinpath("config.yaml").read_text())
    # Empty, not "main": delegation resolves a provider name through
    # resolve_runtime_provider, which does not know the "main" alias the auxiliary
    # blocks accept, and every delegate_task returned "Cannot resolve delegation
    # provider 'main'". Empty means the child inherits the parent's.
    assert cfg["delegation"]["provider"] == ""
    assert cfg["auxiliary"]["vision"]["provider"] == "main"
    assert cfg["memory"]["provider"] == "holographic"
    # The web block is the one that must not be missed on an upgrade: without it Hermes
    # resolves no configured backend, walks its keyless tier and reads every page through
    # Exa's and Parallel's public MCP endpoints (V2 §4, D19).
    assert cfg["web"] == {"search_backend": "searxng", "extract_backend": "aura-extract",
                          "keyless_fallback": False, "keyless_rescue": False}
    assert cfg["security"]["allow_private_urls"] is True
    # `backend: "off"` is what takes Hermes off Browser Use CLI mode and back to the
    # built-in browser_* tools driving the Chrome baked into the image. Unset, the CLI mode
    # wins whenever `uvx` can run, and the model gets one browser_exec with nothing to drive.
    assert cfg["browser"]["backend"] == "off"
    assert cfg["browser"]["use_real_profile"] is False
    assert cfg["approvals"]["mode"] == "manual", "an operator's edit to an existing block wins"
    after = home.joinpath("config.yaml").read_text()
    subprocess.run(["bash", str(ep), "gateway", "run"], check=True, env=env, capture_output=True, text=True)
    assert home.joinpath("config.yaml").read_text() == after, "adding blocks is idempotent"


def test_a_placeholder_secret_is_named_on_every_start():
    """Three of the host's four secrets were still the repository's dev placeholders on
    2026-09-07 — the apps' bearer was `devadapterkey0123456789`, the value the phone's
    pairing fixtures use — so anyone who had read the repository could open the console.

    The entrypoint says so on every start and never refuses to boot over it: a house that
    will not start because of a key nobody can rotate at that moment is worse than a loud
    log line. A real random key must draw no line at all, or the warning becomes noise
    and stops being read.
    """
    block = ENTRYPOINT.read_text()
    start = block.index("for _k in API_SERVER_KEY")
    end = block.index("unset _k _v", start)
    loop = block[start:end] + "unset _k _v"
    script = "log() { echo \"[jarvis-entrypoint] $*\"; }\n" + loop
    env = {
        **os.environ,
        "API_SERVER_KEY": "9f2c" * 16,          # a real 64-char random
        "HERMES_SERVE_TOKEN": "devservetoken123456",
        "AURA_API_KEY": "devadapterkey0123456789",
        "AURA_CONSOLE_PASSWORD": "change-me",   # `.env.example`'s own value counts; pragma: allowlist secret
    }
    out = subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=env, check=True).stdout
    assert "SECURITY: HERMES_SERVE_TOKEN is still a placeholder" in out
    assert "SECURITY: AURA_API_KEY is still a placeholder" in out
    assert "SECURITY: AURA_CONSOLE_PASSWORD is still a placeholder" in out
    assert "API_SERVER_KEY is still a placeholder" not in out, "a real key must be silent"
    # Never the value itself: a warning that prints the secret publishes it into the log.
    assert "devadapterkey0123456789" not in out
    assert "devservetoken123456" not in out


def _seeded_home(tmp_path):
    """A volume that has started once, and the script that seeded it."""
    home = tmp_path / "data"
    seed = tmp_path / "seed"
    (seed / "home").mkdir(parents=True)
    (seed / "plugins").mkdir()
    (seed / "agent-hooks").mkdir()
    for f in ("SOUL.md", "USER.md", "MEMORY.md"):
        (seed / "home" / f).write_text(f"seed {f}\n")
    (seed / "config.template.yaml").write_text(TEMPLATE.read_text())
    script = (ENTRYPOINT.read_text().replace("SEED=/opt/jarvis/seed", f"SEED={seed}")
              .replace('exec hermes "$@"', 'echo "would exec hermes $*"')
              .replace("exec sleep infinity", 'echo "would hold the container open"'))
    ep = tmp_path / "ep.sh"
    ep.write_text(script)
    env = {**os.environ, **ENV, "HERMES_HOME": str(home), "API_SERVER_KEY": "x" * 20}
    run = lambda: subprocess.run(["bash", str(ep), "gateway", "run"], check=True, env=env,
                                 capture_output=True, text=True)
    run()
    return home, seed, run


def test_a_corrected_soul_reaches_a_host_that_has_already_started(tmp_path):
    """B9: the fix went out in the image and the deployment kept the old text.

    `if [ ! -e ]` adds what is missing and never touches what is there, so a
    corrected SOUL.md shipped by the pipeline stopped at the first host that had
    ever booted — which is every host.
    """
    home, seed, run = _seeded_home(tmp_path)
    assert (home / "SOUL.md").read_text() == "seed SOUL.md\n"

    (seed / "home" / "SOUL.md").write_text("corrected SOUL.md\n")
    out = run()
    assert (home / "SOUL.md").read_text() == "corrected SOUL.md\n"
    assert "refreshing SOUL.md" in out.stderr
    # The one it replaced is kept beside it rather than dropped.
    assert (home / "SOUL.md.jarvis-prev").read_text() == "seed SOUL.md\n"


def test_the_profile_of_a_person_is_written_once_and_never_refreshed(tmp_path):
    """USER.md says who the house's person is; the image ships a blank one.

    Until the release the image's copy was the first owner's own profile, and every host that
    had never edited it held exactly those bytes. Refreshing it like SOUL.md would have swapped
    a profile in use for the blank one on the next deploy. It is written where there is none
    and left alone ever after, edited or not; SOUL.md beside it still takes its correction.

    Not covered: a profile the image *should* correct. Nothing in it is the product's to say.
    """
    home, seed, run = _seeded_home(tmp_path)
    assert (home / "memories" / "USER.md").read_text() == "seed USER.md\n"

    (seed / "home" / "USER.md").write_text("blank profile\n")
    (seed / "home" / "SOUL.md").write_text("corrected SOUL.md\n")
    out = run()
    assert (home / "memories" / "USER.md").read_text() == "seed USER.md\n"
    assert "USER.md" not in out.stderr
    assert (home / "SOUL.md").read_text() == "corrected SOUL.md\n"

    (home / "memories" / "USER.md").unlink()             # no profile at all: the blank one is written
    run()
    assert (home / "memories" / "USER.md").read_text() == "blank profile\n"


def test_a_hand_edited_soul_is_never_overwritten_by_a_newer_image(tmp_path):
    """The other half, and the reason this is a digest and not a copy."""
    home, seed, run = _seeded_home(tmp_path)
    (home / "SOUL.md").write_text("the operator's own words\n")
    (seed / "home" / "SOUL.md").write_text("a newer image's SOUL.md\n")

    out = run()
    assert (home / "SOUL.md").read_text() == "the operator's own words\n"
    assert "not refreshing SOUL.md" in out.stderr
    # And it stays refused, however many times the container restarts.
    run()
    assert (home / "SOUL.md").read_text() == "the operator's own words\n"


def test_a_volume_seeded_before_the_digests_existed_is_left_alone(tmp_path):
    """An upgrade cannot tell an old seed from an edit, so it must not guess."""
    home, seed, run = _seeded_home(tmp_path)
    # Wind it back to how such a volume looks: a file, and nothing recording it.
    (home / "SOUL.md").write_text("whatever this host had\n")
    for stamp in (home / ".seed-digests").iterdir():
        stamp.unlink()

    (seed / "home" / "SOUL.md").write_text("a newer image's SOUL.md\n")
    out = run()
    assert (home / "SOUL.md").read_text() == "whatever this host had\n"
    assert "no seed digest" in out.stderr


def test_an_untouched_file_is_adopted_so_the_next_correction_lands(tmp_path):
    """The same volume, when its file *does* match what the image ships."""
    home, seed, run = _seeded_home(tmp_path)
    for stamp in (home / ".seed-digests").iterdir():
        stamp.unlink()

    run()  # adopts: the file is byte-identical to the seed
    assert (home / ".seed-digests" / "SOUL.md.sha256").exists()

    (seed / "home" / "SOUL.md").write_text("corrected later\n")
    run()
    assert (home / "SOUL.md").read_text() == "corrected later\n"


def test_a_value_that_is_a_swallowed_comment_is_treated_as_unset(tmp_path: Path) -> None:
    """Compose keeps `KEY=   # note` as the string "# note" when the value is empty. The
    delegated subagents' model was that string in production and every subagent died
    on a 404 from llama-swap. The entrypoint drops such values before rendering."""
    home = tmp_path / "home"
    seed = tmp_path / "seed"
    for d in ("plugins", "agent-hooks", "home", "skills"):
        (seed / d).mkdir(parents=True)
    for f in ("SOUL.md", "USER.md", "MEMORY.md"):
        (seed / "home" / f).write_text(f"seed {f}\n")
    (seed / "config.template.yaml").write_text(TEMPLATE.read_text())
    script = (ENTRYPOINT.read_text().replace("SEED=/opt/jarvis/seed", f"SEED={seed}")
              .replace('exec hermes "$@"', 'echo "would exec hermes $*"')
              .replace("exec sleep infinity", 'echo "would hold the container open"'))
    ep = tmp_path / "ep.sh"
    ep.write_text(script)
    env = {**os.environ, **ENV, "HERMES_HOME": str(home), "API_SERVER_KEY": "x" * 20,
           "ROUTING_DELEGATION_MODEL": "# delegated subagents; empty = inherit the parent"}
    out = subprocess.run(["bash", str(ep), "gateway", "run"], check=True, env=env, capture_output=True, text=True)
    assert "ROUTING_DELEGATION_MODEL is a comment, not a value" in out.stderr
    rendered = (home / "config.yaml").read_text()
    assert "# delegated subagents" not in rendered.replace("#", "", 0) or "model: \"# delegated" not in rendered
    assert 'model: "# delegated subagents' not in rendered


def test_the_env_example_never_puts_a_comment_after_an_empty_value() -> None:
    """The shape Compose mis-parses. Comments on an empty value go on the line above."""
    example = (ROOT / "deploy" / ".env.example").read_text().splitlines()
    bad = [line for line in example if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=\s*#", line)]
    assert bad == [], "docker compose keeps the comment as the value on these lines"


def test_every_directory_the_entrypoint_makes_as_root_is_handed_to_hermes():
    """The mkdir near the top runs as root (cont-init) and the chown loop at the bottom is a
    hand-kept mirror of it. `files` and `browser` were missing from the mirror: root 0755 on
    the volume, so the gateway (uid hermes) could not save a download or a delivered file, and
    Chrome could not write the profile meant to keep a login across restarts (2026-09-18,
    bench browser-download-003). Every top-level name the mkdir makes must be in the loop."""
    text = ENTRYPOINT.read_text(encoding="utf-8")
    made = re.search(r'mkdir -p "\$HERMES_HOME"/\{([^}]*)\}', text).group(1).split(",")
    loop = re.search(r'for f in ([^;]*); do\n\s+\[ -e "\$HERMES_HOME/\$f" \] && chown -R hermes:hermes',
                     text).group(1).split()
    assert {"files", "browser"} <= set(loop)
    for d in made:
        assert d.split("/")[0] in loop, f"{d} is created as root and never handed to hermes"


RETIRED = ("productivity/calendar-lookup", "email/outbound-email", "email/himalaya", "gitlab")


def test_retired_skills_are_removed_on_every_start_and_kept_out_of_the_sync(tmp_path):
    """Four skills on the volume told the model to reach a service by shell instead of
    through the typed, gated tool (2026-09-18). The image retires them the way it owns
    plugins: gone on every start, logged, and the name in Hermes' own `.curator_suppressed`
    so its bundled-skill sync — which runs after the entrypoint and seeds `himalaya` onto a
    fresh volume — does not put one back. A sibling skill is untouched, and a second start
    has nothing to say and does not duplicate the marker."""
    home, _seed, run = _seeded_home(tmp_path)
    for rel in RETIRED + ("productivity/pdf",):
        (home / "skills" / rel).mkdir(parents=True)
        (home / "skills" / rel / "SKILL.md").write_text(f"---\nname: {rel.rsplit('/', 1)[-1]}\n---\n")
    out = run()
    for rel in RETIRED:
        assert not (home / "skills" / rel).exists(), rel
        assert f"skills: removing '{rel}' (retired by the image)" in out.stderr
    assert (home / "skills" / "productivity" / "pdf" / "SKILL.md").exists()
    marker = home / "skills" / ".curator_suppressed"
    assert marker.read_text().split() == ["calendar-lookup", "outbound-email", "himalaya", "gitlab"]
    out = run()
    assert "skills: removing" not in out.stderr
    assert marker.read_text().split() == ["calendar-lookup", "outbound-email", "himalaya", "gitlab"]


def test_the_selfext_checkout_is_provisioned_from_a_baked_tree_or_declared_absent(tmp_path):
    """aura-selfext hides every selfext_* tool until $SELFEXT_REPO_PATH is a git work tree,
    and nothing ever made one (2026-09-18). With no tree in the image the start says so and
    creates nothing: an empty `git init` would show tools that fail on first use, the case
    the plugin's `available()` exists to prevent. With a tree baked at $SEED/repo the
    checkout is made once, on the target branch, the tree committed and clean, and a later
    start leaves it alone."""
    home, seed, run = _seeded_home(tmp_path)
    checkout = home / "work" / "jarvis"
    out = run()
    assert "selfext: no checkout at" in out.stderr and str(seed / "repo") in out.stderr
    assert not checkout.exists()

    (seed / "repo" / "hermes" / "plugins").mkdir(parents=True)
    (seed / "repo" / "hermes" / "plugins" / "README.md").write_text("baked\n")
    out = run()
    assert "selfext: checkout provisioned" in out.stderr
    git = lambda *a: subprocess.run(["git", "-C", str(checkout), *a], check=True,
                                    capture_output=True, text=True).stdout.strip()
    assert (checkout / ".git").is_dir()
    assert (checkout / "hermes" / "plugins" / "README.md").read_text() == "baked\n"
    assert git("rev-parse", "--abbrev-ref", "HEAD") == "main"
    assert git("status", "--porcelain") == "", "the baked tree is committed, not left staged"
    head = git("rev-parse", "HEAD")
    out = run()
    assert git("rev-parse", "HEAD") == head and "selfext:" not in out.stderr


def test_the_browser_profiles_singleton_links_are_removed_on_every_start():
    """A deploy that replaces the container while Chrome holds the profile leaves
    SingletonLock/Cookie/Socket pointing at the old container, and Chrome then refuses
    the profile as in use elsewhere (exit 21) — every browser tool failed on production
    after the deploy of 2026-09-18 until they were removed by hand. A fresh container
    has no Chrome, so on start they are always stale."""
    src = (ROOT / "deploy" / "entrypoint.sh").read_text()
    line = next(l for l in src.splitlines() if "Singleton{Lock,Cookie,Socket}" in l)
    assert line.startswith('rm -f "$HERMES_HOME"/browser/profile/Singleton')
    assert src.index("Singleton{Lock,Cookie,Socket}") < src.index("starting the s6-supervised gateway")


def test_a_setting_newer_than_the_hosts_env_renders_its_default_not_empty(tmp_path):
    """ACTIVITY_ENABLED was added after production's .env was written; unset, it rendered
    `enabled:` and the activity view (which fails closed) captured nothing. The schema's
    default is what an unset variable means."""
    env = {k: v for k, v in os.environ.items() if k != "ACTIVITY_ENABLED"}
    code = _renderer()
    dst = tmp_path / "config.yaml"
    out = subprocess.run([sys.executable, "-c", code, str(TEMPLATE), str(dst)], check=True,
                         env={**env, **ENV}, capture_output=True, text=True)
    assert yaml.safe_load(dst.read_text())["activity"]["enabled"] is True
    assert "took the schema's default:" in out.stderr and "'ACTIVITY_ENABLED'" in out.stderr
    # Set, it is the host's word, not the default's.
    assert _render(tmp_path / "off.yaml", {"ACTIVITY_ENABLED": "false"})["activity"]["enabled"] is False


def test_the_static_reasoning_level_yields_to_the_sessions_own(tmp_path):
    """T-0814: `extra_body` is merged over the top level of every request, so a static
    `extra_body.reasoning_effort: medium` overwrote the level Hermes put there for the
    session, and a voice turn at low or a `none` turn reasoned at medium (T-0723,
    bench/results/reasoning-effort.md). Under `chat_template_kwargs` the default is what
    vLLM uses only when the request carries no level of its own. A volume rendered before
    the move has the old leaf: a later start adds the new one and retires the old.
    Not covered: whether vLLM honours it -- that is the probe in the bench note."""
    home, _seed, run = _seeded_home(tmp_path)
    cfg = yaml.safe_load(home.joinpath("config.yaml").read_text())
    assert cfg["providers"]["qwen-vllm"]["extra_body"] == {"chat_template_kwargs": {"reasoning_effort": "medium"}}

    # The shape production's volume has: the old leaf, rendered on its first start.
    text = home.joinpath("config.yaml").read_text()
    moved = '    extra_body:\n      chat_template_kwargs:\n        reasoning_effort: "medium"\n'
    assert moved in text
    home.joinpath("config.yaml").write_text(
        text.replace(moved, '    extra_body:\n      reasoning_effort: "medium"\n'))
    out = run()
    cfg = yaml.safe_load(home.joinpath("config.yaml").read_text())
    assert cfg["providers"]["qwen-vllm"]["extra_body"] == {"chat_template_kwargs": {"reasoning_effort": "medium"}}
    assert "config.yaml -= providers.qwen-vllm.extra_body: reasoning_effort" in out.stderr
    after = home.joinpath("config.yaml").read_text()
    assert "extra_body" not in run().stderr
    assert home.joinpath("config.yaml").read_text() == after, "the move is idempotent"
