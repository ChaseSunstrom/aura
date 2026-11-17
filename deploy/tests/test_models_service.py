"""jarvis-models, the house's own LLM server (T-0278): wired the way production needs it.

The AI container that served the model was deleted on 2026-10-09 and Hermes lost its brain
for hours, so each thing that brought it back is pinned: the service is in the deploy
compose with both cards, never on the tailnet, and seeds its model files on a first start
without bringing back one the owner removed.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "deploy"
SEED = DEPLOY / "models" / "seed"


def _compose(name: str) -> dict:
    return yaml.safe_load((DEPLOY / name).read_text(encoding="utf-8"))


def test_the_models_service_is_in_the_deploy_compose_and_never_on_the_tailnet():
    svc = _compose("docker-compose.yml")["services"]["jarvis-models"]
    assert svc["image"].endswith("/jarvis-models:${MODELS_TAG:-latest}")
    assert any(v.endswith(":/models") for v in svc["volumes"])
    for port in svc["ports"]:
        host = port.rsplit(":", 2)[0]
        assert host in ("127.0.0.1", "${DOCKER_BRIDGE_IP:-172.17.0.1}"), port
    assert "TAILSCALE_IP" not in str(svc["ports"])
    # llama-swap answers nothing without the inference key, its own health probe included.
    assert "Bearer $$LLM_API_KEY" in " ".join(svc["healthcheck"]["test"])


def test_every_vllm_start_goes_through_the_prestart_and_a_failed_one_is_unhealthy():
    # T-0411: a day of failed starts behind a healthy container. The shim must sit ahead of the
    # real vllm on the image's PATH, and the healthcheck must read the mark it writes.
    dockerfile = (DEPLOY / "models" / "Dockerfile").read_text()
    assert "COPY --chmod=755 deploy/models/vllm_prestart.py /usr/local/sbin/vllm" in dockerfile
    # T-0511: llama-server (Ripple-65B) goes through the same shim; a CPU-only server names
    # /usr/local/bin/llama-server in its yaml and skips it.
    assert "COPY --chmod=755 deploy/models/vllm_prestart.py /usr/local/sbin/llama-server" in dockerfile
    assert "COPY --from=llama-cpp /llama-server /usr/local/bin/llama-server" in dockerfile
    test = " ".join(_compose("docker-compose.yml")["services"]["jarvis-models"]["healthcheck"]["test"])
    assert "test ! -e /tmp/jarvis-models-start-failed &&" in test
    assert 'MARK = Path("/tmp/jarvis-models-start-failed")' in (
        DEPLOY / "models" / "vllm_prestart.py").read_text()
    # The manager's retry (T-0412) reads the same mark; a moved mark would stop it silently.
    assert 'START_FAILED = Path("/tmp/jarvis-models-start-failed")' in (
        DEPLOY / "models" / "manager.py").read_text()
    # The container's /tmp survives `docker restart` and a reboot: a mark from before must not
    # hold a fresh start unhealthy through its whole load (start_period is 30 s, a load ~7 min).
    entry = (DEPLOY / "models" / "entrypoint.sh").read_text()
    clear = entry.index("rm -f /tmp/jarvis-models-start-failed")
    assert clear < entry.index("exec llama-swap")


def test_both_cards_reach_the_models_service_through_the_gpu_overlays():
    one = _compose("docker-compose.gpu.yml")["services"]["jarvis-models"]["devices"]
    two = _compose("docker-compose.gpu2.yml")["services"]["jarvis-models"]["devices"]
    assert "/dev/nvidia0:/dev/nvidia0" in one and "/dev/nvidia1:/dev/nvidia1" in two
    libs = " ".join(_compose("docker-compose.gpu.yml")["services"]["jarvis-models"]["volumes"])
    for lib in ("libcuda.so.1", "libnvidia-ml.so.1", "libnvidia-ptxjitcompiler.so.1", "libnvidia-nvvm.so.4"):
        assert lib in libs, lib


def test_every_seeded_model_serves_under_its_own_name_on_the_port_llama_swap_gives_it():
    aux = yaml.safe_load((SEED / "base.yaml").read_text(encoding="utf-8"))["groups"]["aux"]
    assert (aux["swap"], aux["exclusive"], aux["persistent"]) == (False, False, True)
    # A member with no file and llama-swap refuses the whole config, every chat model with it.
    for name in aux["members"]:
        assert yaml.safe_load((SEED / "models" / f"{name}.yaml").read_text())["models"][name]["unlisted"]
    for path in sorted((SEED / "models").glob("*.yaml")):
        models = yaml.safe_load(path.read_text(encoding="utf-8"))["models"]
        assert len(models) == 1, f"{path.name}: one model per file, so the console edits one file"
        (name, m), = models.items()
        assert path.stem == name
        assert "--port ${PORT}" in m["cmd"]
        if not m["cmd"].split()[0].endswith("llama-server"):
            assert f"--served-model-name {name}" in m["cmd"]
            assert "--tool-call-parser" in m["cmd"], "Hermes needs tool calls parsed"
            continue
        # T-0511: llama-server reads the one GGUF the manager fetched and never reaches the Hub.
        assert f"--alias {name}" in m["cmd"] and "--offline" in m["cmd"] and "--hf-file" in m["cmd"]
        if m.get("unlisted"):
            # The embedder and reranker: on the CPU, past the prestart shim (they need no card
            # and must not release the voice), resident beside whichever chat model is loaded.
            assert m["cmd"].startswith("/usr/local/bin/llama-server ") and m["env"] == ["CUDA_VISIBLE_DEVICES=-1"]
            assert name in aux["members"]
        else:
            assert m["cmd"].startswith("llama-server ") and "--jinja" in m["cmd"], "Hermes needs tool calls parsed"


def test_the_hermes_config_follows_llm_url_to_the_models_service():
    example = (DEPLOY / ".env.example").read_text(encoding="utf-8")
    assert "LLM_URL=http://jarvis-models:8000/v1" in example
    assert "LLAMA_SWAP_URL=http://jarvis-models:8000" in example


def test_seeding_adds_missing_models_and_never_resurrects_a_removed_one(tmp_path):
    fake = tmp_path / "bin"
    fake.mkdir()
    (fake / "llama-swap").write_text("#!/bin/sh\necho \"llama-swap $*\"\n")
    (fake / "llama-swap").chmod(0o755)
    seed = tmp_path / "seed"
    (seed / "models").mkdir(parents=True)
    for name in ("a.yaml", "b.yaml"):
        (seed / "models" / name).write_text("models: {}\n")
    conf = tmp_path / "config"
    conf.mkdir()
    (conf / ".removed").write_text("b.yaml\n")
    script = (DEPLOY / "models" / "entrypoint.sh").read_text().replace("/opt/jarvis-models/seed", str(seed))
    env = {**os.environ, "PATH": f"{fake}:{os.environ['PATH']}", "AURA_MODELS_CONFIG": str(conf),
           "HF_HOME": str(tmp_path / "hf")}
    out = subprocess.run(["sh", "-c", script], env=env, capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    assert (conf / "a.yaml").exists() and not (conf / "b.yaml").exists()
    assert f"-config-dir {conf}" in out.stdout and "-watch-config" in out.stdout


def test_the_entrypoint_starts_the_manager_online_while_llama_swap_and_its_children_stay_offline(tmp_path):
    fake = tmp_path / "bin"
    fake.mkdir()
    (fake / "llama-swap").write_text("#!/bin/sh\necho \"swap offline=$HF_HUB_OFFLINE\"\nsleep 1\n")
    (fake / "llama-swap").chmod(0o755)
    manager = tmp_path / "manager.py"
    manager.write_text("import os\nprint('manager offline=' + os.environ['HF_HUB_OFFLINE'], flush=True)\n")
    seed = tmp_path / "seed"
    (seed / "models").mkdir(parents=True)
    (seed / "models" / "a.yaml").write_text("models: {}\n")
    script = (DEPLOY / "models" / "entrypoint.sh").read_text()
    script = script.replace("/opt/jarvis-models/seed", str(seed)).replace("/opt/jarvis-models/manager.py", str(manager))
    env = {**os.environ, "PATH": f"{fake}:{os.environ['PATH']}", "AURA_MODELS_CONFIG": str(tmp_path / "config"),
           "HF_HOME": str(tmp_path / "hf")}
    out = subprocess.run(["sh", "-c", script], env=env, capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    assert "manager offline=0" in out.stdout and "swap offline=1" in out.stdout


def test_the_container_that_runs_the_models_tools_never_holds_the_managers_key():
    # jarvis loads the whole .env (env_file), so the key has to be blanked there explicitly.
    services = _compose("docker-compose.yml")["services"]
    assert services["jarvis"]["environment"]["MODELS_API_KEY"] == ""
    assert services["jarvis-adapter"]["environment"]["MODELS_API_KEY"] == "${MODELS_API_KEY:-}"
    assert "env_file" not in services["jarvis-adapter"] and "env_file" not in services["jarvis-models"]
    # The inference key is the one Hermes holds; the adapter and llama-swap's host get it explicitly.
    assert services["jarvis-adapter"]["environment"]["LLM_API_KEY"] == "${LLM_API_KEY:-}"
    assert services["jarvis-models"]["environment"]["LLM_API_KEY"] == "${LLM_API_KEY:-}"


def _run_entrypoint(tmp_path, **extra):
    """Run the entrypoint with a llama-swap that prints the base config it was handed."""
    fake = tmp_path / "bin"
    fake.mkdir()
    (fake / "llama-swap").write_text(
        "#!/bin/sh\nwhile [ $# -gt 0 ]; do [ \"$1\" = -config ] && cat \"$2\"; shift; done\n")
    (fake / "llama-swap").chmod(0o755)
    seed = tmp_path / "seed"
    (seed / "models").mkdir(parents=True)
    (seed / "base.yaml").write_text("logLevel: info\n")
    (seed / "models" / "a.yaml").write_text("models: {}\n")
    script = (DEPLOY / "models" / "entrypoint.sh").read_text().replace("/opt/jarvis-models/seed", str(seed))
    env = {k: v for k, v in os.environ.items() if k != "LLM_API_KEY"}
    env.update({"PATH": f"{fake}:{os.environ['PATH']}", "AURA_MODELS_CONFIG": str(tmp_path / "config"),
                "HF_HOME": str(tmp_path / "hf"), **extra})
    out = subprocess.run(["sh", "-c", script], env=env, capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return out, tmp_path / "config"


def test_llama_swap_requires_the_inference_key_when_one_is_set(tmp_path):
    # Any container on the host reached :9000 unauthenticated (logs with prompts, unload, /upstream).
    out, conf = _run_entrypoint(tmp_path, LLM_API_KEY='k"ey: #1')
    cfg = yaml.safe_load("\n".join(l for l in out.stdout.splitlines() if not l.startswith("jarvis-models:")))
    assert cfg["apiKeys"] == ['k"ey: #1'] and cfg["logLevel"] == "info"
    # The key lives in a runtime copy: not in the seed and not in the dir the console edits.
    assert "apiKeys" not in (tmp_path / "seed" / "base.yaml").read_text()
    assert not any("apiKeys" in p.read_text() for p in conf.glob("*") if p.is_file())


def test_llama_swap_stays_open_with_a_warning_without_the_key(tmp_path):
    out, _ = _run_entrypoint(tmp_path)
    assert "apiKeys" not in out.stdout and "LLM_API_KEY is not set" in out.stderr


def test_the_model_server_does_not_phone_home():
    """vLLM reports usage to stats.vllm.ai unless told not to: a persistent uuid, the CPU, the
    GPUs, the memory and the model's settings, on every model load. Production had sent 56
    such reports before this was pinned (T-0343, /root/.config/vllm/usage_stats.json). Not
    HF_HUB_OFFLINE here: the console's Models panel downloads weights on the owner's word."""
    env = _compose("docker-compose.yml")["services"]["jarvis-models"]["environment"]
    assert str(env.get("VLLM_NO_USAGE_STATS")) == "1"
    assert str(env.get("DO_NOT_TRACK")) == "1"


def test_llama_swap_preloads_hermes_model_when_one_is_named(tmp_path):
    """llama-swap loads a model on its first request, so after any restart of jarvis-models
    (a deploy, a reboot) Hermes's first turn waited for the whole load: 385 s on
    2026-10-09 (T-0348). PRELOAD_MODEL (compose passes LLM_MODEL) starts it at boot."""
    out, conf = _run_entrypoint(tmp_path, PRELOAD_MODEL="qwen3.8-27b", LLM_API_KEY="k")
    cfg = yaml.safe_load("\n".join(l for l in out.stdout.splitlines() if not l.startswith("jarvis-models:")))
    assert cfg["hooks"] == {"on_startup": {"preload": ["qwen3.8-27b"]}}
    assert cfg["apiKeys"] == ["k"]
    # A runtime copy only: the seed and the console's directory never carry it.
    assert "hooks" not in (tmp_path / "seed" / "base.yaml").read_text()
    # Unset: nothing preloads, as before.
    (tmp_path / "x").mkdir()
    again, _ = _run_entrypoint(tmp_path / "x", LLM_API_KEY="k")
    assert "hooks" not in again.stdout


def test_compose_names_hermes_model_as_the_one_to_preload():
    env = _compose("docker-compose.yml")["services"]["jarvis-models"]["environment"]
    assert env.get("PRELOAD_MODEL") == "${LLM_MODEL:-}"


def test_llama_swap_captures_no_request_or_reply():
    """llama-swap holds request and reply bodies in RAM for its Captures page (captureBuffer, in
    MB) unless told 0; every conversation, private chats too (T-0462), would sit there for
    anyone with the inference key to read. Does not cover vLLM's own KV cache on the GPU."""
    assert yaml.safe_load((SEED / "base.yaml").read_text())["captureBuffer"] == 0
