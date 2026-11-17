"""The model manager (deploy/models/manager.py) against tests/contracts/models-api.json.

Each contract example is what the endpoint returns for the fixture state built here; the
adapter's relay test and the console read the same file. Nothing touches the Hub, a GPU or a
running llama-swap: those are fakes. Only the llama-swap -validate tests need the real binary
and skip without it.
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import threading
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
SEED = ROOT / "deploy" / "models" / "seed"
CONTRACT = json.loads((ROOT / "tests" / "contracts" / "models-api.json").read_text(encoding="utf-8"))
EP = CONTRACT["endpoints"]

_spec = importlib.util.spec_from_file_location("models_manager", ROOT / "deploy" / "models" / "manager.py")
mm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mm)

GIB = 1024**3


class FakeHub:
    def __init__(self):
        self.release = threading.Event()
        self.planned = []
        self.plans = {"Qwen/Qwen3-8B-AWQ": (6442450944, False), "meta/gated": (1, True), "big/model": (300 * GIB, False)}

    def search(self, query):
        return [{"repo": "Qwen/Qwen3-8B-AWQ", "downloads": 412000, "likes": 120, "params_b": 8.2,
                 "size_bytes": 6442450944, "gated": False}]

    def plan(self, repo, file=None):
        self.planned.append((repo, file))
        return self.plans[repo]

    def download(self, repo, file=None):
        self.release.wait(10)


class FakeSwap:
    def __init__(self):
        self.state = {"qwen3.8-27b": "ready"}
        self.calls = []

    def running(self, strict=False):
        return dict(self.state)

    def unload(self):
        self.calls.append("unload")
        self.state.clear()

    def load(self, mid):
        self.calls.append(("load", mid))


class FakeNvml:
    def cards(self):
        out = []
        for c in EP["gpus"]["example"]["gpus"]:
            procs = [(c["index"] * 10 + i + 1, u["mib"]) for i, u in enumerate(c["users"])]
            out.append({**{k: c[k] for k in ("index", "name", "total_mib", "used_mib")}, "procs": procs})
        return out


def _blob(cache: Path, repo: str, size: int) -> None:
    d = cache / ("models--" + repo.replace("/", "--")) / "blobs"
    d.mkdir(parents=True, exist_ok=True)
    with open(d / "weights", "wb") as f:
        f.truncate(size)  # sparse: the size is what the manager reads


@pytest.fixture
def world(tmp_path, monkeypatch):
    conf, cache = tmp_path / "config", tmp_path / "hf" / "hub"
    conf.mkdir()
    # The two the contract examples describe; the llama-server seeds (T-0511) have their own tests.
    for f in ("qwen3.8-27b.yaml", "qwen3.6-35b.yaml"):
        shutil.copy(SEED / "models" / f, conf)
    (conf / ".removed").write_text("")
    _blob(cache, "cyankiwi/Qwen3.8-27B-AWQ-INT4", 21474836480)
    _blob(cache, "palmfuture/Qwen3.6-35B-A3B-GPTQ-Int4", 20401094656)
    labels = {1: "models", 2: "voice", 11: "models", 12: "hearing"}
    monkeypatch.setattr(mm, "process_label", lambda pid: labels[pid])
    du = SimpleNamespace(free=211527139328, total=423054278656)
    w = SimpleNamespace(conf=conf, cache=cache, hub=FakeHub(), swap=FakeSwap(), validated=[])
    w.m = mm.Manager(conf, cache, SEED / "base.yaml", w.hub, FakeNvml(), w.swap,
                     disk_usage=lambda _p: du, validate=lambda base, d: w.validated.append(sorted(p.name for p in d.iterdir())))
    yield w
    w.hub.release.set()


def call(w, method, path, body=None):
    url = path.split("?")
    from urllib.parse import parse_qs
    return w.m.handle(method, "/api/models" + url[0], parse_qs(url[1]) if len(url) > 1 else {}, body)


def test_list_is_the_contract_example(world):
    world.hub.release.clear()
    _blob(world.cache, "Qwen/Qwen3-8B-AWQ", 2147483648)
    assert call(world, "POST", "/downloads", EP["download"]["body"])[0] == 200
    status, out = call(world, "GET", "")
    assert status == 200 and out == EP["list"]["example"]


def test_gpus_search_download_are_the_contract_examples(world):
    assert call(world, "GET", "/gpus") == (200, EP["gpus"]["example"])
    assert call(world, "GET", "/search?q=qwen") == (200, EP["search"]["example"])
    assert call(world, "POST", "/downloads", EP["download"]["body"]) == (200, EP["download"]["example"])


def test_download_is_refused_when_gated_or_too_big_for_the_disk_minus_the_reserve(world):
    status, out = call(world, "POST", "/downloads", {"repo": "meta/gated"})
    assert status == 403 and out["ok"] is False and "gated" in out["error"]
    status, out = call(world, "POST", "/downloads", {"repo": "big/model"})
    assert status == 409 and "free" in out["error"]
    world.hub.plans["fits/exactly"] = (211527139328 - CONTRACT["limits"]["disk_reserve_bytes"], False)
    assert call(world, "POST", "/downloads", {"repo": "fits/exactly"})[0] == 200
    assert call(world, "POST", "/downloads", {"repo": "not a repo"})[0] == 400


def test_create_update_delete_load_unload_are_the_contract_examples(world):
    _blob(world.cache, "Qwen/Qwen3-8B-AWQ", 1024)
    assert call(world, "POST", "", EP["create"]["body"]) == (200, EP["create"]["example"])
    assert (world.conf / "qwen3-8b.yaml").is_file() and not list(world.conf.glob(".*.tmp"))
    assert call(world, "PATCH", "/qwen3.8-27b", EP["update"]["body"]) == (200, EP["update"]["example"])
    assert call(world, "POST", "/qwen3.6-35b/load") == (200, EP["load"]["example"])
    assert call(world, "POST", "/qwen3.6-35b/unload") == (200, EP["unload"]["example"])
    assert call(world, "DELETE", "/qwen3.6-35b?weights=1") == (200, EP["delete"]["example"])
    assert not (world.conf / "qwen3.6-35b.yaml").exists()
    assert "qwen3.6-35b.yaml" in (world.conf / ".removed").read_text().split()
    assert not (world.cache / "models--palmfuture--Qwen3.6-35B-A3B-GPTQ-Int4").exists()


def test_a_created_model_serves_under_its_id_with_the_settings_asked_for(world):
    _blob(world.cache, "Qwen/Qwen3-8B-AWQ", 1024)
    call(world, "POST", "", EP["create"]["body"])
    import yaml
    m = yaml.safe_load((world.conf / "qwen3-8b.yaml").read_text())["models"]["qwen3-8b"]
    assert "--port ${PORT}" in m["cmd"] and "--served-model-name qwen3-8b" in m["cmd"]
    assert "vllm serve Qwen/Qwen3-8B-AWQ" in m["cmd"] and "--tool-call-parser" in m["cmd"]
    assert mm.parse_settings(m) == {"engine": "vllm", "max_model_len": 32768, "gpu_memory_utilization": 0.4, "tensor_parallel_size": 1,
                                    "kv_cache_dtype": None, "quantization": None, "keep_loaded": False,
                                    "idle_unload_s": 600}


def test_update_changes_only_what_it_is_given_and_keeps_the_other_flags(world):
    call(world, "PATCH", "/qwen3.6-35b", {"settings": {"max_model_len": 4096, "quantization": "gptq", "keep_loaded": True}})
    import yaml
    m = yaml.safe_load((world.conf / "qwen3.6-35b.yaml").read_text())["models"]["qwen3.6-35b"]
    s = mm.parse_settings(m)
    assert (s["max_model_len"], s["quantization"], s["keep_loaded"]) == (4096, "gptq", True)
    assert (s["gpu_memory_utilization"], s["tensor_parallel_size"], s["kv_cache_dtype"]) == (0.75, 2, "fp8_e5m2")
    assert "--reasoning-parser qwen3" in m["cmd"] and m["env"] and m["ttl"] == 0
    call(world, "PATCH", "/qwen3.6-35b", {"settings": {"quantization": None, "keep_loaded": False, "idle_unload_s": 90}})
    m = yaml.safe_load((world.conf / "qwen3.6-35b.yaml").read_text())["models"]["qwen3.6-35b"]
    assert "--quantization" not in m["cmd"] and m["ttl"] == 90


@pytest.mark.parametrize("settings", [
    {"max_model_len": 10}, {"gpu_memory_utilization": 0.99}, {"tensor_parallel_size": 3},
    {"idle_unload_s": -1}, {"kv_cache_dtype": "int3"}, {"quantization": "magic"}, {"colour": "red"},
    {"max_model_len": True}, {"keep_loaded": "yes"}])
def test_out_of_range_settings_are_refused_and_the_file_is_untouched(world, settings):
    before = (world.conf / "qwen3.6-35b.yaml").read_text()
    status, out = call(world, "PATCH", "/qwen3.6-35b", {"settings": settings})
    assert status == 400 and out["ok"] is False and out["error"]
    assert (world.conf / "qwen3.6-35b.yaml").read_text() == before


def test_a_file_llama_swap_rejects_never_replaces_the_working_one(world):
    def reject(base, d):
        raise mm.ApiError(400, "llama-swap would not load that model: nope")
    world.m.validate = reject
    before = (world.conf / "qwen3.6-35b.yaml").read_text()
    status, out = call(world, "PATCH", "/qwen3.6-35b", {"settings": {"max_model_len": 4096}})
    assert status == 400 and "llama-swap" in out["error"]
    assert (world.conf / "qwen3.6-35b.yaml").read_text() == before and not list(world.conf.glob(".*.tmp"))


def test_validation_sees_the_whole_config_with_the_candidate_in_place_of_the_old_file(world):
    call(world, "PATCH", "/qwen3.6-35b", {"settings": {"max_model_len": 4096}})
    assert world.validated == [["qwen3.6-35b.yaml", "qwen3.8-27b.yaml"]]


def test_create_refuses_a_duplicate_a_missing_download_and_a_bad_id(world):
    body = dict(EP["create"]["body"])
    assert call(world, "POST", "", body)[0] == 409  # weights not on disk
    _blob(world.cache, "Qwen/Qwen3-8B-AWQ", 1024)
    assert call(world, "POST", "", body)[0] == 200
    assert call(world, "POST", "", body)[0] == 409
    assert call(world, "POST", "", {**body, "id": "../etc"})[0] == 400
    assert call(world, "PATCH", "/nope", {"settings": {"max_model_len": 4096}})[0] == 404


def test_a_removed_model_can_be_created_again_and_deleting_keeps_weights_another_model_uses(world):
    call(world, "DELETE", "/qwen3.6-35b")
    assert (world.cache / "models--palmfuture--Qwen3.6-35B-A3B-GPTQ-Int4").exists()  # weights=0
    _blob(world.cache, "Qwen/Qwen3-8B-AWQ", 1024)
    call(world, "POST", "", {**EP["create"]["body"], "id": "a"})
    call(world, "POST", "", {**EP["create"]["body"], "id": "b"})
    assert call(world, "DELETE", "/a?weights=1") == (200, {"ok": True, "freed_bytes": 0})
    assert (world.cache / "models--Qwen--Qwen3-8B-AWQ").exists()


def test_a_configured_model_without_weights_is_missing_and_cannot_load(world):
    shutil.rmtree(world.cache / "models--palmfuture--Qwen3.6-35B-A3B-GPTQ-Int4")
    states = {m["id"]: m["state"] for m in call(world, "GET", "")[1]["models"]}
    assert states == {"qwen3.8-27b": "loaded", "qwen3.6-35b": "missing"}
    assert call(world, "POST", "/qwen3.6-35b/load")[0] == 409


def test_unload_calls_llama_swap_only_for_a_model_that_is_running(world):
    call(world, "POST", "/qwen3.6-35b/unload")
    assert world.swap.calls == []
    call(world, "POST", "/qwen3.8-27b/unload")
    assert world.swap.calls == ["unload"]


def test_new_model_defaults_match_the_contract():
    want = {k: v for k, v in CONTRACT["new_model_defaults"].items() if k != "_comment"}
    assert mm.NEW_DEFAULTS == want


def test_limits_match_the_contract():
    lim = CONTRACT["limits"]
    for key, (lo, hi) in mm.LIMITS.items():
        assert [lo, hi] == lim[key], key
    assert mm.KV_CACHE_DTYPES == lim["kv_cache_dtype"] and mm.QUANTIZATIONS == lim["quantization"]
    assert mm.DISK_RESERVE_BYTES == lim["disk_reserve_bytes"]


def test_the_http_server_speaks_json_with_the_status_in_the_error_body(world):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), mm.make_handler(world.m))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_port}/api/models"
    try:
        assert json.load(urllib.request.urlopen(base + "/gpus")) == EP["gpus"]["example"]
        req = urllib.request.Request(base + "/nope", method="DELETE")
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(req)
        assert e.value.code == 404 and json.load(e.value)["ok"] is False
    finally:
        srv.shutdown()


# ---- the real llama-swap, when this machine has it
BIN = os.environ.get("LLAMA_SWAP_BIN") or shutil.which("llama-swap")
needs_bin = pytest.mark.skipif(not BIN, reason="llama-swap binary not available")


@needs_bin
def test_the_real_llama_swap_accepts_what_create_writes_and_refuses_what_it_cannot_load(world, monkeypatch):
    monkeypatch.setenv("LLAMA_SWAP_BIN", BIN)
    world.m.validate = mm.llama_swap_validate
    _blob(world.cache, "Qwen/Qwen3-8B-AWQ", 1024)
    assert call(world, "POST", "", EP["create"]["body"])[0] == 200
    status, out = call(world, "PATCH", "/qwen3-8b", {"settings": {"max_model_len": 2048}})
    assert status == 200
    (world.conf / "broken.yaml").write_text("models:\n  broken:\n    cmd: ''\n")
    status, out = call(world, "PATCH", "/qwen3-8b", {"settings": {"max_model_len": 4096}})
    assert status == 400 and out["error"].startswith("llama-swap would not load")
    assert "--max-model-len 2048" in (world.conf / "qwen3-8b.yaml").read_text()


def test_search_reports_no_size_for_packed_int4_rather_than_a_wrong_one(monkeypatch, tmp_path):
    # Live 2026-10-09: Qwen3-8B-AWQ (~6 GB) came back as 30 GB, its I32-packed weights counted at
    # four bytes a parameter. A dense bf16 repo still gets its size.
    huggingface_hub = pytest.importorskip("huggingface_hub")  # in the image; not in every CI venv

    rows = [SimpleNamespace(id="a/awq", downloads=1, likes=0, gated=False,
                            safetensors=SimpleNamespace(parameters={"I32": 7_000_000_000, "F16": 600_000_000}, total=8_200_000_000)),
            SimpleNamespace(id="b/dense", downloads=1, likes=0, gated=False,
                            safetensors=SimpleNamespace(parameters={"BF16": 1_000_000_000}, total=1_000_000_000))]
    monkeypatch.setattr(huggingface_hub, "HfApi", lambda: SimpleNamespace(list_models=lambda **_: rows))
    out = {r["repo"]: r["size_bytes"] for r in mm.HFHub(tmp_path).search("x")}
    assert out == {"a/awq": None, "b/dense": 2_000_000_000}


def test_a_running_download_counts_against_the_disk_for_the_next(world):
    # Commit review, 2026-10-09: two downloads started together each passed the check alone.
    world.hub.release.clear()  # downloads stay running
    half = (211527139328 - CONTRACT["limits"]["disk_reserve_bytes"]) // 2 + 1
    world.hub.plans["one/half"] = (half, False)
    world.hub.plans["two/half"] = (half, False)
    assert call(world, "POST", "/downloads", {"repo": "one/half"})[0] == 200
    status, out = call(world, "POST", "/downloads", {"repo": "two/half"})
    assert status == 409 and "free" in out["error"]


def test_the_manager_answers_only_the_adapters_key(world):
    # Commit review, 2026-10-09: anything on the compose network could drive it.
    srv = ThreadingHTTPServer(("127.0.0.1", 0), mm.make_handler(world.m, "s3cret"))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_port}/api/models/gpus"
    try:
        for headers in ({}, {"Authorization": "Bearer wrong"}):
            with pytest.raises(urllib.error.HTTPError) as e:
                urllib.request.urlopen(urllib.request.Request(base, headers=headers))
            assert e.value.code == 401
        ok = urllib.request.urlopen(urllib.request.Request(base, headers={"Authorization": "Bearer s3cret"}))
        assert json.load(ok)["ok"] is True
    finally:
        srv.shutdown()


def test_a_fresh_host_fetches_its_seeded_weights_once_and_never_a_deleted_model(world):
    # A fresh host needs no click: what the entrypoint seeded this start downloads itself.
    world.hub.release.clear()
    shutil.rmtree(world.cache / "models--palmfuture--Qwen3.6-35B-A3B-GPTQ-Int4")
    (world.conf / ".fresh").write_text("qwen3.6-35b.yaml\nqwen3.8-27b.yaml\n")
    world.hub.plans["palmfuture/Qwen3.6-35B-A3B-GPTQ-Int4"] = (1024, False)
    assert world.m.fetch_fresh() == ["palmfuture/Qwen3.6-35B-A3B-GPTQ-Int4"]  # the 27B is on disk
    assert not (world.conf / ".fresh").exists()
    assert world.m.fetch_fresh() == []  # deleted later: never fetched again


def test_a_llama_server_model_fetches_only_its_gguf_and_its_context_is_minus_c(world):
    # T-0511: Ripple-65B is one GGUF of a repo holding a dozen quants (~400 GB in all): the
    # cmd's -hf/--hf-file name what to fetch, -c is its context, and vLLM's flags mean nothing.
    import yaml
    shutil.copy(SEED / "models" / "ripple-65b.yaml", world.conf)
    repo = "AtomicChat/Ripple-65B-Preview-GGUF"
    world.hub.plans[repo] = (38 * GIB, False)
    (world.conf / ".fresh").write_text("ripple-65b.yaml\n")
    assert world.m.fetch_fresh() == [repo]
    assert world.hub.planned == [(repo, "Ripple-65B-Preview-AD-IQ4_XS.gguf")]
    assert mm.parse_settings(world.m._read("ripple-65b"))["max_model_len"] == 65536
    assert call(world, "PATCH", "/ripple-65b", {"settings": {"max_model_len": 98304}})[0] == 200
    cmd = yaml.safe_load((world.conf / "ripple-65b.yaml").read_text())["models"]["ripple-65b"]["cmd"].split()
    assert cmd[cmd.index("-c") + 1] == "98304" and "--max-model-len" not in cmd
    status, out = call(world, "PATCH", "/ripple-65b", {"settings": {"gpu_memory_utilization": 0.5}})
    assert status == 400 and "llama-server" in out["error"]


def test_the_embedder_and_reranker_are_no_chat_model_in_the_panel(world):
    # Unlisted in llama-swap (so not in /v1/models either): the console's model selects read this list.
    for f in ("qwen3-embedding-0.6b.yaml", "qwen3-reranker-0.6b.yaml"):
        shutil.copy(SEED / "models" / f, world.conf)
    assert [m["id"] for m in call(world, "GET", "")[1]["models"]] == ["qwen3.8-27b", "qwen3.6-35b"]


def test_a_host_booted_offline_keeps_its_seeded_models_for_the_next_start(world):
    shutil.rmtree(world.cache / "models--palmfuture--Qwen3.6-35B-A3B-GPTQ-Int4")
    (world.conf / ".fresh").write_text("qwen3.6-35b.yaml\n")

    def offline(repo, file=None):
        raise OSError("Network is unreachable")
    world.hub.plan = offline
    assert world.m.fetch_fresh() == []
    assert (world.conf / ".fresh").read_text().split() == ["qwen3.6-35b.yaml"]


def test_a_broken_sibling_file_does_not_half_delete_a_model_and_keeps_its_weights(world):
    (world.conf / "broken.yaml").write_text("models: [not, a, mapping\n")
    status, body = call(world, "DELETE", "/qwen3.6-35b?weights=1")
    assert (status, body) == (200, {"ok": True, "freed_bytes": 0})
    assert not (world.conf / "qwen3.6-35b.yaml").exists()
    assert (world.cache / "models--palmfuture--Qwen3.6-35B-A3B-GPTQ-Int4").exists()



def test_an_aux_model_cannot_be_deleted_because_llama_swap_would_not_start_without_it(world):
    # T-0511 review: base.yaml's aux group names the embedder; with its file gone llama-swap
    # refuses its whole config ("no model config for ...") and every chat model goes with it.
    shutil.copy(SEED / "models" / "qwen3-embedding-0.6b.yaml", world.conf)
    status, body = call(world, "DELETE", "/qwen3-embedding-0.6b?weights=1")
    assert status == 409 and "aux" in body["error"]
    assert (world.conf / "qwen3-embedding-0.6b.yaml").exists()


def test_a_delete_with_weights_frees_the_shared_blobs_its_links_point_at(world):
    # T-0514: huggingface_hub 1.33 keeps the bytes in hub/blobs/xx/ behind models--X/blobs
    # links, so removing the repo's directory alone left Ripple's 38.7 GB on the disk.
    store = world.cache / "blobs" / "46"
    store.mkdir(parents=True)
    own, both = store / "own", store / "both"
    for f, n in ((own, 3000), (both, 500)):
        with open(f, "wb") as h:
            h.truncate(n)
    links = world.cache / "models--palmfuture--Qwen3.6-35B-A3B-GPTQ-Int4" / "blobs"
    shutil.rmtree(links)
    links.mkdir()
    (links / "a").symlink_to("../../blobs/46/own")
    (links / "b").symlink_to("../../blobs/46/both")
    (world.cache / "models--cyankiwi--Qwen3.8-27B-AWQ-INT4" / "blobs" / "c").symlink_to("../../blobs/46/both")
    assert call(world, "DELETE", "/qwen3.6-35b?weights=1") == (200, {"ok": True, "freed_bytes": 3000})
    assert not own.exists() and both.exists()

# ---------------------------------------------------------------- healing a failed start (T-0412)
class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


@pytest.fixture
def heal(tmp_path):
    swap = FakeSwap()
    swap.state.clear()
    clock = Clock()
    mark = tmp_path / "start-failed"
    h = mm.Healer(swap, "qwen3.8-27b", mark=mark, clock=clock)
    return SimpleNamespace(swap=swap, clock=clock, mark=mark, h=h)


def _at(w, t):
    w.clock.t = t
    w.h.tick()
    return [c for c in w.swap.calls if c[0] == "load"]


def test_heal_retries_a_failed_start_three_times_with_a_growing_wait_then_stops(heal):
    heal.mark.write_text("vllm exited\n")
    assert _at(heal, 0) == []
    assert _at(heal, 299) == []
    assert _at(heal, 300) == [("load", "qwen3.8-27b")]
    assert len(_at(heal, 300 + 899)) == 1
    assert len(_at(heal, 300 + 900)) == 2
    assert len(_at(heal, 1200 + 2699)) == 2
    assert len(_at(heal, 1200 + 2700)) == 3
    # The mark stays, the container stays unhealthy, and health_guard pages the owner.
    assert len(_at(heal, 10**6)) == 3


def test_heal_starts_over_once_a_start_clears_the_mark(heal):
    heal.mark.write_text("vllm exited\n")
    _at(heal, 0)
    assert len(_at(heal, 300)) == 1
    heal.mark.unlink()
    assert len(_at(heal, 400)) == 1
    heal.mark.write_text("vllm exited again\n")
    assert len(_at(heal, 500)) == 1
    assert len(_at(heal, 800)) == 2  # the first wait again, not the second


def test_heal_never_touches_a_model_that_is_loading_or_loaded(heal):
    heal.mark.write_text("vllm exited\n")
    heal.swap.state["qwen3.8-27b"] = "starting"  # a request already drives the retry
    for t in (0, 300, 10_000):
        assert _at(heal, t) == []
    heal.swap.state.clear()
    assert _at(heal, 10_001) == []  # the wait starts when it stops, not before
    assert len(_at(heal, 10_301)) == 1


def test_heal_counts_a_load_that_errors_and_keeps_going(heal):
    heal.mark.write_text("vllm exited\n")

    def refused(mid):
        heal.swap.calls.append(("load", mid))
        raise OSError("connection refused")
    heal.swap.load = refused
    _at(heal, 0)
    assert len(_at(heal, 300)) == 1
    assert len(_at(heal, 1200)) == 2


def test_heal_waits_out_a_llama_swap_that_does_not_answer_instead_of_spending_a_try(heal):
    # A busy llama-swap timing out on /running is not "nothing running" (review of T-0412).
    heal.mark.write_text("vllm exited\n")

    def silent(strict=False):
        if strict:
            raise OSError("timed out")
        return {}
    heal.swap.running = silent
    assert _at(heal, 0) == [] and _at(heal, 10_000) == []
    assert heal.h.tries == 0


def test_heal_run_survives_a_tick_that_raises(heal, monkeypatch):
    # A dead thread would end the healing silently: run() logs a tripped tick and keeps going.
    ticks = []

    def boom():
        ticks.append(1)
        if len(ticks) == 3:
            raise SystemExit  # ends the loop for the test; SystemExit is not an Exception
        raise RuntimeError("tripped")
    heal.h.tick = boom
    monkeypatch.setattr(mm.time, "sleep", lambda s: None)
    with pytest.raises(SystemExit):
        heal.h.run()
    assert len(ticks) == 3


def test_a_vllm_model_that_cannot_fit_a_card_is_refused_naming_the_card_and_who_holds_it(world):
    """T-0548: 0.95 of a 24 GiB card is 22.8 GiB; card 1 frees only 21.9 GiB when the current
    model swaps out, because hearing holds 2.1 GiB of it. vLLM would die on start; say so first."""
    assert call(world, "PATCH", "/qwen3.6-35b", {"settings": {"gpu_memory_utilization": 0.95}})[0] == 200
    status, out = call(world, "POST", "/qwen3.6-35b/load")
    assert status == 409 and ("load", "qwen3.6-35b") not in world.swap.calls
    for said in ("card 1", "NVIDIA GeForce RTX 3090", "hearing", "22.8 GiB", "21.9 GiB"):
        assert said in out["error"], said


def test_a_model_already_running_is_not_measured_against_itself(world):
    status, out = call(world, "POST", "/qwen3.8-27b/load")
    assert status == 200 and out["gpus"] == []


def test_a_gguf_counts_as_downloaded_only_when_its_own_file_is_linked(world):
    """T-0517: a killed download leaves `<blob>.incomplete`, and another quant of the repo is a
    blob too; either made the panel say "unloaded" and offer Load for a file that is not there."""
    shutil.copy(SEED / "models" / "ripple-65b.yaml", world.conf)
    root = world.cache / "models--AtomicChat--Ripple-65B-Preview-GGUF"
    (root / "blobs").mkdir(parents=True)
    (root / "blobs" / "abc123.incomplete").write_bytes(b"\0" * 1024)
    (root / "blobs" / "otherquant").write_bytes(b"\0" * 1024)
    state = lambda: next(m for m in call(world, "GET", "")[1]["models"] if m["id"] == "ripple-65b")["state"]  # noqa: E731
    assert state() == "missing"
    assert call(world, "POST", "/ripple-65b/load")[0] == 409
    snap = root / "snapshots" / "rev1"
    snap.mkdir(parents=True)
    (root / "blobs" / "done").write_bytes(b"\0" * 1024)
    (snap / "Ripple-65B-Preview-AD-IQ4_XS.gguf").symlink_to(root / "blobs" / "done")
    assert state() == "unloaded"
    assert call(world, "POST", "/ripple-65b/load")[0] == 200


CREATE = CONTRACT["create_body"]
GGUF = CREATE["gguf_file"]


def _link_gguf(world, repo, file):
    root = world.cache / ("models--" + repo.replace("/", "--"))
    (root / "blobs").mkdir(parents=True, exist_ok=True)
    (root / "blobs" / "done").write_bytes(b"\0" * 1024)
    snap = root / "snapshots" / "rev1" / Path(file).parent
    snap.mkdir(parents=True, exist_ok=True)
    (snap / Path(file).name).symlink_to(root / "blobs" / "done")


def test_contract_the_create_body_fields_and_the_gguf_rule_are_the_managers(world):
    assert set(CREATE["llama_example"]) == set(CREATE["fields"])
    assert CREATE["engines"] == mm.ENGINES
    assert CREATE["llama_settings"] == mm.LLAMA_SETTINGS
    assert GGUF["pattern"] == mm.GGUF_RE.pattern and GGUF["shard_pattern"] == mm.GGUF_SHARD_RE.pattern
    for f in GGUF["ok"]:
        assert mm.check_gguf(f) == f
    for f in GGUF["bad"]:
        with pytest.raises(mm.ApiError) as e:
            mm.check_gguf(f)
        assert e.value.status == 400
    assert GGUF["shard_error"] == pytest.raises(mm.ApiError, mm.check_gguf, GGUF["bad"][-1]).value.message
    assert GGUF["missing_error"] == pytest.raises(mm.ApiError, mm.check_gguf, None).value.message


def test_create_with_a_gguf_writes_a_llama_server_entry_that_reads_back(world):
    body = CREATE["llama_example"]
    _link_gguf(world, body["repo"], body["file"])
    assert call(world, "POST", "", body) == (200, {"ok": True, "model": {"id": body["id"], "state": "unloaded"}})
    m = world.m._read(body["id"])
    assert m["cmd"].startswith("llama-server ") and "--port ${PORT}" in m["cmd"] and "vllm" not in m["cmd"]
    assert mm.repo_of(m) == body["repo"] and mm.file_of(m) == body["file"]
    s = mm.parse_settings(m)
    assert (s["engine"], s["max_model_len"], s["keep_loaded"], s["idle_unload_s"]) == ("llama.cpp", 32768, False, 600)
    assert (s["gpu_memory_utilization"], s["tensor_parallel_size"]) == (None, None)
    assert "--alias qwen3-8b-gguf" in m["cmd"] and "env" not in m


@pytest.mark.parametrize("bad", [f for f in GGUF["bad"] if f] + [None, 7])
def test_create_refuses_a_file_that_is_not_one_gguf_and_writes_nothing(world, bad):
    body = {**CREATE["llama_example"], "file": bad}
    _link_gguf(world, body["repo"], CREATE["llama_example"]["file"])
    status, out = call(world, "POST", "", body)
    assert status == 400 and out["ok"] is False and out["error"]
    assert not (world.conf / f"{body['id']}.yaml").exists()


def test_create_llama_cpp_needs_its_file_on_disk_and_a_vllm_add_takes_no_file(world):
    body = CREATE["llama_example"]
    assert call(world, "POST", "", body)[0] == 409  # the Hub's other quants are not this file
    _link_gguf(world, body["repo"], "Other-Q8_0.gguf")
    assert call(world, "POST", "", body)[0] == 409
    _blob(world.cache, "Qwen/Qwen3-8B-AWQ", 1024)
    status, out = call(world, "POST", "", {**EP["create"]["body"], "file": "x.gguf"})
    assert status == 400 and "llama.cpp" in out["error"]
    status, out = call(world, "POST", "", {**EP["create"]["body"], "engine": "ollama"})
    assert status == 400 and "engine" in out["error"]
    assert call(world, "POST", "", {**EP["create"]["body"], "engine": "vllm"})[0] == 200


def test_create_llama_cpp_refuses_vllm_only_settings(world):
    body = CREATE["llama_example"]
    _link_gguf(world, body["repo"], body["file"])
    status, out = call(world, "POST", "", {**body, "settings": {"gpu_memory_utilization": 0.4}})
    assert status == 400 and "llama-server" in out["error"]
    assert not (world.conf / f"{body['id']}.yaml").exists()
    assert call(world, "POST", "", {**body, "settings": {}})[0] == 200  # defaults carry no vLLM field


def test_download_takes_the_same_gguf_rule(world):
    world.hub.plans["Qwen/Qwen3-8B-GGUF"] = (1024, False)
    assert call(world, "POST", "/downloads", {"repo": "Qwen/Qwen3-8B-GGUF", "file": GGUF["ok"][0]})[0] == 200
    status, out = call(world, "POST", "/downloads", {"repo": "Qwen/Qwen3-8B-GGUF", "file": GGUF["bad"][-1]})
    assert status == 400 and out["error"] == GGUF["shard_error"]


REFUSALS = {k: v for k, v in CONTRACT["refusals"].items() if k != "_comment"}
RIPPLE_REPO, RIPPLE_FILE = "AtomicChat/Ripple-65B-Preview-GGUF", "Ripple-65B-Preview-AD-IQ4_XS.gguf"


def _refused(key, **slots):
    return REFUSALS[key]["status"], {"ok": False, "error": REFUSALS[key]["message"].format(**slots)}


def test_every_create_and_download_refusal_is_the_contracts_status_and_sentence(world):
    """T-1269: the manager formats these from tests/contracts/models-api.json, which the e2e mock reads too."""
    ok = EP["create"]["body"]
    gb = lambda n: f"{n / 1e9:.1f}"  # noqa: E731
    free = 211527139328 - CONTRACT["limits"]["disk_reserve_bytes"]
    seen = {
        "repo_invalid": (call(world, "POST", "/downloads", {"repo": "not a repo"}), {}),
        "gated": (call(world, "POST", "/downloads", {"repo": "meta/gated"}), {"repo": "meta/gated"}),
        "disk_full": (call(world, "POST", "/downloads", {"repo": "big/model"}),
                      {"repo": "big/model", "need_gb": gb(300 * GIB), "free_gb": gb(free),
                       "reserve_gb": CONTRACT["limits"]["disk_reserve_bytes"] // GIB}),
        "id_invalid": (call(world, "POST", "", {**ok, "id": "Bad Id"}), {}),
        "settings_not_object": (call(world, "POST", "", {**ok, "settings": "x"}), {}),
        "engine_unknown": (call(world, "POST", "", {**ok, "engine": "ollama"}), {"engines": "vllm, llama.cpp"}),
        "file_on_vllm": (call(world, "POST", "", {**ok, "file": "x.gguf"}), {}),
        "not_downloaded": (call(world, "POST", "", ok), {"what": ok["repo"]}),
    }
    _blob(world.cache, ok["repo"], 1024)
    assert call(world, "POST", "", ok)[0] == 200
    seen["model_exists"] = (call(world, "POST", "", ok), {"id": ok["id"]})
    for key, (got, slots) in seen.items():
        assert got == _refused(key, **slots), key
    assert set(seen) == set(REFUSALS)


def test_a_missing_gguf_is_refused_naming_its_file_and_the_load_says_it_the_same_way(world):
    body = CREATE["llama_example"]
    assert call(world, "POST", "", body) == _refused("not_downloaded", what=body["file"])
    shutil.copy(SEED / "models" / "ripple-65b.yaml", world.conf)
    assert call(world, "POST", "/ripple-65b/load") == _refused("not_downloaded", what=RIPPLE_FILE)


def test_the_image_carries_the_contract_the_manager_reads():
    """The manager is one file in /opt/jarvis-models; without the contract beside it, it would not start."""
    assert "COPY tests/contracts/models-api.json /opt/jarvis-models/models-api.json" in (
        ROOT / "deploy" / "models" / "Dockerfile").read_text(encoding="utf-8")
    assert "!tests/contracts/models-api.json" in (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
    assert "tests/contracts/models-api.json" in (ROOT / ".gitlab-ci.yml").read_text(encoding="utf-8")
    assert mm.REFUSALS == REFUSALS


def _ripple_gone(world):
    shutil.copy(SEED / "models" / "ripple-65b.yaml", world.conf)
    world.hub.plans[RIPPLE_REPO] = (1024, False)


def test_a_download_with_no_file_takes_it_from_the_models_own_entry_for_that_repo(world):
    """T-1269: the console's row for a llama.cpp model whose .gguf is gone posts only the repo."""
    _ripple_gone(world)
    assert call(world, "POST", "/downloads", {"repo": RIPPLE_REPO})[0] == 200
    assert world.hub.planned == [(RIPPLE_REPO, RIPPLE_FILE)]


def test_an_explicit_file_wins_and_a_file_already_on_disk_or_a_vllm_repo_is_not_asked_for_again(world):
    _ripple_gone(world)
    assert call(world, "POST", "/downloads", {"repo": RIPPLE_REPO, "file": "Other-Q8_0.gguf"})[0] == 200
    assert world.hub.planned[-1] == (RIPPLE_REPO, "Other-Q8_0.gguf")
    _link_gguf(world, RIPPLE_REPO, RIPPLE_FILE)
    world.m.jobs.clear()
    assert call(world, "POST", "/downloads", {"repo": RIPPLE_REPO})[0] == 200
    assert world.hub.planned[-1] == (RIPPLE_REPO, None)
    assert call(world, "POST", "/downloads", EP["download"]["body"])[0] == 200
    assert world.hub.planned[-1] == (EP["download"]["body"]["repo"], None)


def test_a_broken_sibling_file_does_not_stop_the_file_being_found(world):
    _ripple_gone(world)
    (world.conf / "broken.yaml").write_text("models: [", encoding="utf-8")
    (world.conf / "empty.yaml").write_text("", encoding="utf-8")
    assert call(world, "POST", "/downloads", {"repo": RIPPLE_REPO})[0] == 200
    assert world.hub.planned == [(RIPPLE_REPO, RIPPLE_FILE)]


def test_a_second_download_of_a_repo_already_downloading_answers_the_running_job(world):
    """Not a refusal (T-1269): the console's button is disabled meanwhile, and a second click anyway
    gets the same job, ok true, without another plan or a second thread."""
    _ripple_gone(world)
    world.hub.release.clear()
    first = call(world, "POST", "/downloads", {"repo": RIPPLE_REPO})
    again = call(world, "POST", "/downloads", {"repo": RIPPLE_REPO})
    assert first[0] == again[0] == 200 and first[1]["download"]["state"] == "running" and again == first
    assert world.hub.planned == [(RIPPLE_REPO, RIPPLE_FILE)]
    row = next(m for m in call(world, "GET", "")[1]["models"] if m["id"] == "ripple-65b")
    assert row["state"] == "downloading"


def test_a_llama_server_model_says_its_engine_and_has_no_vllm_settings(world):
    """T-0518: it read gpu_memory_utilization 0.9 and tensor_parallel_size 1, vLLM's defaults,
    and the console offered (and validated) four fields llama-server does not have."""
    shutil.copy(SEED / "models" / "ripple-65b.yaml", world.conf)
    s = mm.parse_settings(world.m._read("ripple-65b"))
    assert s["engine"] == "llama.cpp"
    assert (s["gpu_memory_utilization"], s["tensor_parallel_size"], s["kv_cache_dtype"], s["quantization"]) == (None,) * 4
    assert mm.parse_settings(world.m._read("qwen3.6-35b"))["engine"] == "vllm"
