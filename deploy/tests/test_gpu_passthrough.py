"""The GPU must reach the TTS container without making a GPU-less host unbootable.

ADR-0039 rule 4: a host that has lost its GPU still has to start the stack and
speak, badly, on the CPU. The failure mode this guards is specific and easy to
reintroduce -- Docker refuses to *create* a container whose `devices:` entry
names a node that does not exist, and a container that cannot be created is
restarted forever by `restart: unless-stopped`. So "add GPU access" and "keep the
CPU path working" pull in opposite directions, and the split between the base
compose file and the overlay is what holds them apart.

These are structural assertions, not a substitute for the real proof:
scripts/verify/gpu-allocation.py runs a genuine cuMemAlloc inside the container.
"""

from __future__ import annotations

import pathlib

import pytest

yaml = pytest.importorskip("yaml")

ROOT = pathlib.Path(__file__).resolve().parents[2]
#: CosyVoice is defined once, in the deploy stack (the house compose's copy went with
#: T-0289); the fixtures stay parametrized so a second stack is one row here.
PAIRS = {
    "deploy": (ROOT / "deploy" / "docker-compose.yml", ROOT / "deploy" / "docker-compose.gpu.yml"),
}


@pytest.fixture(params=sorted(PAIRS), ids=sorted(PAIRS))
def BASE(request):
    return PAIRS[request.param][0]


@pytest.fixture(params=sorted(PAIRS), ids=sorted(PAIRS))
def OVERLAY(request):
    return PAIRS[request.param][1]

#: The device nodes a CUDA process needs. /dev/nvidia-uvm is the one that is
#: quietly forgotten: nvidia-smi works without it and the first allocation does not.
REQUIRED_DEVICES = {"/dev/nvidiactl", "/dev/nvidia-uvm", "/dev/nvidia-uvm-tools", "/dev/nvidia0"}


def _service(path: pathlib.Path, name: str) -> dict:
    return yaml.safe_load(path.read_text())["services"][name]


def test_base_compose_requests_no_gpu_device(BASE) -> None:
    """The base file must start on a host with no GPU at all."""
    svc = _service(BASE, "jarvis-cosyvoice")
    assert "devices" not in svc, (
        "jarvis-cosyvoice declares devices in the BASE compose file. A missing "
        "/dev/nvidia0 then stops the container being created and it restart-loops "
        "forever. GPU access belongs in docker-compose.gpu.yml."
    )
    assert "runtime" not in svc, "no nvidia container runtime exists on this host (ADR-0039)"
    # A memory limit is fine; a device reservation would hard-require a GPU too.
    assert "devices" not in ((svc.get("deploy") or {}).get("resources") or {}).get("reservations", {}), (
        "deploy.resources.reservations.devices would also hard-require a GPU"
    )


def test_overlay_passes_the_devices_a_cuda_allocation_needs(OVERLAY) -> None:
    svc = _service(OVERLAY, "jarvis-cosyvoice")
    got = {d.split(":")[0] for d in svc["devices"]}
    assert REQUIRED_DEVICES <= got, f"overlay is missing {REQUIRED_DEVICES - got}"


def test_overlay_mounts_the_driver_rather_than_baking_it(OVERLAY) -> None:
    """The kernel module and libcuda must be the same build.

    An image carrying its own libcuda drifts on the next host driver update, and
    the symptom is a working nvidia-smi with a failing first allocation. Mounting
    from the host makes the constraint physical.
    """
    svc = _service(OVERLAY, "jarvis-cosyvoice")
    mounts = " ".join(svc["volumes"])
    assert "libcuda.so" in mounts, "libcuda must be bind-mounted from the host"
    assert "libnvidia-ml.so" in mounts, "libnvidia-ml must be bind-mounted from the host"
    assert "libnvidia-ptxjitcompiler.so" in mounts, "cuFFT needs the driver's PTX compiler (CUFFT_INTERNAL_ERROR)"
    # By the host's .so.1 link, not a versioned file: a pinned version went missing
    # on the next driver update and Docker mounted an empty directory instead.
    sources = [v.split(":", 1)[0] for v in svc["volumes"] if ".so" in v]
    assert all(src.endswith(".so.1") for src in sources), sources


def test_tts_is_pinned_to_one_card_and_capped(OVERLAY) -> None:
    """ADR-0039 rules 2 and 3.

    Only card 0 is passed through, so ComfyUI keeps card 1's headroom; and the cap
    stays under the 3072 MiB ceiling measured beside vLLM, so a growing allocation
    fails our own check instead of starving the model server's next cold start.
    """
    svc = _service(OVERLAY, "jarvis-cosyvoice")
    env = svc["environment"]
    env = env if isinstance(env, dict) else dict(e.split("=", 1) for e in env)

    passed = {d.split(":")[0] for d in svc["devices"]}
    assert "/dev/nvidia1" not in passed, "card 1 is ComfyUI's; the TTS is pinned to card 0"
    assert env["CUDA_VISIBLE_DEVICES"] == "0"

    cap = int(env["COSYVOICE_VRAM_CAP_MIB"].split(":-")[-1].rstrip("}"))
    assert cap < 3072, f"cap {cap} MiB is at or above the measured ceiling beside vLLM"


def test_device_selection_degrades_rather_than_demanding_cuda(BASE, OVERLAY) -> None:
    """`auto` means "CUDA if a device really allocates, else CPU"."""
    base = _service(BASE, "jarvis-cosyvoice")["environment"]
    base = base if isinstance(base, dict) else dict(e.split("=", 1) for e in base)
    assert "auto" in base["COSYVOICE_DEVICE"], "the base path must not demand cuda"

    overlay = _service(OVERLAY, "jarvis-cosyvoice").get("environment") or {}
    overlay = overlay if isinstance(overlay, dict) else dict(e.split("=", 1) for e in overlay)
    assert "COSYVOICE_DEVICE" not in overlay or "auto" in overlay["COSYVOICE_DEVICE"], (
        "the overlay must not pin device=cuda; degrading on a GPU that vanished is the point"
    )


def test_deploy_adds_the_overlay_only_when_the_host_has_the_node() -> None:
    deploy = (ROOT / "scripts" / "deploy.sh").read_text()
    assert "test -e /dev/nvidia0" in deploy, "deploy must probe the host before adding the overlay"
    assert "docker-compose.gpu.yml" in deploy
    assert "docker compose $COMPOSE_FILES" in deploy, "all compose calls must go through one selection"


def test_the_second_card_is_whispers_and_the_tts_keeps_cuda_0() -> None:
    """docker-compose.gpu2.yml (the owner, 2026-10-08: "put it on the other card"):
    the second node, ordered so cuda:0 is still the TTS card, Whisper on cuda:1."""
    svc = _service(ROOT / "deploy" / "docker-compose.gpu2.yml", "jarvis-cosyvoice")
    env = svc["environment"]
    assert [d.split(":")[0] for d in svc["devices"]] == ["/dev/nvidia1"]
    assert env["CUDA_DEVICE_ORDER"] == "PCI_BUS_ID"
    assert env["WHISPER_DEVICE"] == "cuda:1"
    assert "1,0" in env["CUDA_VISIBLE_DEVICES"], "cuda:0 must stay the card /dev/nvidia0 is"


def test_deploy_adds_the_second_card_only_when_the_host_has_it() -> None:
    for script in (ROOT / "scripts" / "deploy.sh", ROOT / "deploy" / "boot-recover.sh"):
        text = script.read_text()
        assert "/dev/nvidia1" in text and "docker-compose.gpu2.yml" in text, script
