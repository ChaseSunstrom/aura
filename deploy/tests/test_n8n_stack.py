"""n8n and its sandbox ride the Hermes stack (T-0630) without widening what the host exposes.

Pinned: n8n's port binds the stack's bind address (and loopback for jarvis-core on the host),
the sandbox services publish nothing, every image is pinned by digest, secrets arrive through
n8n.env (env_file) and never through interpolation of the owner's .env, and the deploy scripts
run the generator before any `compose` call.

Not covered: that the sandbox actually boots (needs the images, a privileged daemon and the
production host; the runbook's first-run section is the check).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = yaml.safe_load((ROOT / "deploy" / "docker-compose.yml").read_text(encoding="utf-8"))
SERVICES = COMPOSE["services"]
N8N_SERVICES = ("sandbox-certs", "sandbox-api", "sandbox-runner-1", "n8n")


def test_the_four_services_exist() -> None:
    for name in N8N_SERVICES:
        assert name in SERVICES, name
    assert "sandbox-tls" in COMPOSE["volumes"]


def test_only_n8n_publishes_and_only_on_the_bind_address_or_loopback() -> None:
    for name in N8N_SERVICES:
        ports = SERVICES[name].get("ports", [])
        if name != "n8n":
            assert not ports, f"{name} must publish nothing"
            continue
        assert ports
        for p in ports:
            assert re.match(r"^(\$\{TAILSCALE_IP\}|127\.0\.0\.1):\$\{N8N_PORT:-5678\}:5678$", p), p


def test_images_are_pinned_by_digest() -> None:
    for name in N8N_SERVICES:
        if name == "n8n" or name.startswith("sandbox"):
            assert re.search(r"@sha256:[0-9a-f]{64}$", SERVICES[name]["image"]), name
    # the sandbox image the runner starts for n8n is named in env, and pinned too
    for name in ("sandbox-runner-1", "n8n"):
        env = SERVICES[name]["environment"]
        value = env.get("SANDBOX_RUNNER_DOCKER_SANDBOX_IMAGE") or env.get("N8N_INSTANCE_AI_SANDBOX_IMAGE")
        assert value and "@sha256:" in value, name


def test_secrets_come_from_n8n_env_not_from_the_owners_env() -> None:
    for name in N8N_SERVICES[1:]:
        files = SERVICES[name]["env_file"]
        paths = [f["path"] if isinstance(f, dict) else f for f in files]
        assert "n8n.env" in paths, name
        assert ".env" not in paths, name
    secrets = ("SANDBOX_API_KEYS", "SANDBOX_N8N_API_KEY", "N8N_ENCRYPTION_KEY",
               "N8N_INSTANCE_AI_SANDBOX_API_KEY", "SANDBOX_API_RUNNER_API_KEY",
               "SANDBOX_API_RUNNER_REGISTRATION_TOKEN")
    for name in N8N_SERVICES:
        env = SERVICES[name].get("environment") or {}
        for key in secrets:
            assert key not in env, f"{name} sets {key} inline"


def test_the_runner_is_privileged_and_the_rest_are_not() -> None:
    assert SERVICES["sandbox-runner-1"]["privileged"] is True
    for name in ("sandbox-certs", "sandbox-api", "n8n"):
        assert not SERVICES[name].get("privileged"), name


def test_start_order_and_health() -> None:
    assert SERVICES["sandbox-api"]["depends_on"]["sandbox-certs"]["condition"] == "service_completed_successfully"
    assert SERVICES["sandbox-runner-1"]["depends_on"]["sandbox-api"]["condition"] == "service_healthy"
    assert SERVICES["n8n"]["depends_on"]["sandbox-api"]["condition"] == "service_healthy"
    assert "healthz" in " ".join(SERVICES["sandbox-api"]["healthcheck"]["test"])


def test_the_model_is_the_houses_own_endpoint() -> None:
    env = SERVICES["n8n"]["environment"]
    assert env["N8N_INSTANCE_AI_MODEL_URL"] == "http://jarvis-models:8000/v1"
    assert env["N8N_INSTANCE_AI_MODEL_API_KEY"] == "${LLM_API_KEY:-}"
    assert env["N8N_INSTANCE_AI_MODEL"] == "${LLM_MODEL}"
    assert env["N8N_TELEMETRY_ENABLED"] == "false"


def test_dind_daemon_json_ships_and_is_valid_json() -> None:
    import json
    cfg = json.loads((ROOT / "deploy" / "dind-daemon.json").read_text(encoding="utf-8"))
    assert "log-opts" in cfg
    mounts = SERVICES["sandbox-runner-1"]["volumes"]
    assert "./dind-daemon.json:/etc/docker/daemon.json:ro" in mounts


def test_the_env_files_and_data_are_ignored_and_the_example_names_nothing_secret() -> None:
    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert re.search(r"^n8n\.env$", ignore, re.M) or "deploy/n8n.env" in ignore
    example = (ROOT / "deploy" / ".env.example").read_text(encoding="utf-8")
    assert "N8N_URL=http://n8n:5678" in example


@pytest.mark.parametrize("script", ["scripts/deploy.sh", "scripts/deploy-here.sh"])
def test_deploy_scripts_generate_n8n_env_before_compose_and_keep_it(script: str) -> None:
    text = (ROOT / script).read_text(encoding="utf-8")
    assert "n8n-env.sh" in text
    first_up = min(i for i in (text.find("$COMPOSE pull"), text.find("docker compose $files up")) if i >= 0)
    assert text.index("n8n-env.sh") < first_up
    assert "--exclude" in text and "n8n.env" in text  # kept out of the sync, so --delete cannot take it
