"""deploy/offline_selftest.py: a model service rebuilt to run with no network (T-0353)."""
from __future__ import annotations

import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location("offline_selftest", Path(__file__).resolve().parents[1] / "offline_selftest.py")
assert _spec and _spec.loader
st = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(st)

WYOMING = {
    "Config": {"Image": "rhasspy/wyoming-piper:2.3.1", "Entrypoint": ["bash", "docker_run.sh"],
               "Cmd": ["--uri", "tcp://0.0.0.0:10200", "--voice", "en_GB-alan-medium"],
               "Env": ["TZ=America/Winnipeg", "PATH=/usr/bin"]},
    "Mounts": [{"Type": "bind", "Source": "/opt/jarvis/aura-core/wyoming/piper", "Destination": "/data"}],
}
TEI = {
    "Config": {"Image": "ghcr.io/huggingface/text-embeddings-inference:cpu-1.9", "Entrypoint": ["text-embeddings-router"],
               "Cmd": ["--model-id", "BAAI/bge-small-en-v1.5", "--hostname", "127.0.0.1", "--port", "7997"],
               "Env": ["HF_HUB_OFFLINE=1"]},
    "Mounts": [{"Type": "volume", "Name": "emb", "Source": "/var/lib/docker/volumes/emb/_data", "Destination": "/data"}],
}


def test_the_run_has_no_network_and_cannot_write_the_services_data():
    argv = st.run_argv(WYOMING, "offline-wyoming-piper")
    assert argv[argv.index("--network") + 1] == "none"
    assert "/opt/jarvis/aura-core/wyoming/piper:/data:ro" in argv
    assert ["-e", "TZ=America/Winnipeg"] == argv[argv.index("-e"):argv.index("-e") + 2]
    # An entrypoint of several words: the first is --entrypoint, the rest lead the command.
    image = argv.index("rhasspy/wyoming-piper:2.3.1")
    assert argv[argv.index("--entrypoint") + 1] == "bash"
    assert argv[image + 1:] == ["docker_run.sh", "--uri", "tcp://0.0.0.0:10200", "--voice", "en_GB-alan-medium"]
    # No compose label: the health check (deploy/health_guard.py) never counts it.
    assert not any("com.docker.compose" in a for a in argv)


def test_a_named_volume_is_mounted_by_name_read_only():
    assert "emb:/data:ro" in st.run_argv(TEI, "offline-x")


def test_the_port_comes_from_the_command_it_serves_on():
    assert st.port_of(TEI["Config"]["Cmd"]) == 7997
    assert st.port_of(WYOMING["Config"]["Cmd"]) == 10200
    assert st.port_of(["--model", "x"]) is None


def test_each_kind_is_probed_from_inside_the_sealed_container():
    http = st.probe_argv("http", 7997, "offline-x")
    assert http[:3] == ["docker", "exec", "offline-x"] and "http://127.0.0.1:7997/health" in http
    wy = st.probe_argv("wyoming", 10200, "offline-y")
    assert wy[:4] == ["docker", "exec", "offline-y", "python3"] and "10200" in wy[-1] and "describe" in wy[-1]


def test_the_gpu_services_are_named_as_not_covered():
    assert set(st.NOT_COVERED) == {"jarvis-models", "jarvis-cosyvoice"}
    assert not set(st.NOT_COVERED) & set(st.SERVICES)
