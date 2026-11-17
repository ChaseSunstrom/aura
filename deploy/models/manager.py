"""The model manager inside jarvis-models (T-0278): the API the console's Models panel uses.

The shape of every endpoint is tests/contracts/models-api.json; deploy/tests/test_models_manager.py
checks each example against this code. It runs beside llama-swap (the entrypoint starts it on
:8001, compose network only, no auth of its own: the adapter's bearer check in front of
/api/models is the gate) and shares /models with it, which is why it can write a model's file
and see what is on disk. It is standard library plus huggingface_hub, PyYAML and pynvml, all of
which the vLLM image already has.

What it does not do: it does not choose flags for a model it has never seen beyond the Qwen-style
defaults in new_model_file, it does not tell a downloaded repo apart from a half-downloaded one
(on_disk_bytes is whatever is in the cache), and the GPU users it cannot attribute are "other".
"""
from __future__ import annotations

import fnmatch
import hmac
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from collections.abc import Callable
from typing import Any
from urllib.parse import parse_qs, urlsplit

import yaml

_LOGGER = logging.getLogger("jarvis-models-manager")

LIMITS = {
    "max_model_len": (1024, 1048576),
    "gpu_memory_utilization": (0.1, 0.95),
    "tensor_parallel_size": (1, 2),
    "idle_unload_s": (0, 86400),
}
KV_CACHE_DTYPES = ["auto", "fp8_e5m2", "fp8_e4m3"]
QUANTIZATIONS = [None, "awq", "awq_marlin", "gptq", "gptq_marlin", "fp8"]
DISK_RESERVE_BYTES = 10 * 1024**3
MAX_BODY = 1024 * 1024
DEFAULT_IDLE_S = 600

ID_RE = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
REPO_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*")
# What snapshot_download fetches: weights, tokenizer and config, not the .bin/.pt duplicates a
# repo often carries beside its safetensors (which would double the download and the disk check).
SKIP = ["*.bin", "*.pt", "*.pth", "*.gguf", "*.msgpack", "*.h5", "*.onnx", "original/*", ".git*"]
DTYPE_BYTES = {"F64": 8, "I64": 8, "F32": 4, "I32": 4, "U32": 4, "BF16": 2, "F16": 2, "I16": 2,
               "U16": 2, "I8": 1, "U8": 1, "BOOL": 1, "F8_E4M3": 1, "F8_E5M2": 1}
# A new model's flags: what the two seeded models run with, so Hermes gets tool calls and reasoning.
NEW_MODEL_ENV = ["NCCL_P2P_DISABLE=1", "TRITON_LIBCUDA_PATH=/usr/lib/x86_64-linux-gnu"]
NEW_MODEL_TAIL = ("--enable-auto-tool-choice --tool-call-parser qwen3_xml --reasoning-parser qwen3 "
                  "--enable-prefix-caching")
FLAGS = {
    "max_model_len": "--max-model-len",
    "gpu_memory_utilization": "--gpu-memory-utilization",
    "tensor_parallel_size": "--tensor-parallel-size",
    "kv_cache_dtype": "--kv-cache-dtype",
    "quantization": "--quantization",
}
# llama-server's spelling (T-0511); vLLM's other settings mean nothing to it and are refused.
LLAMA_FLAGS = {"max_model_len": "-c"}
GGUF_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*(/[A-Za-z0-9][A-Za-z0-9._-]*)*\.gguf")
# A split GGUF is one file per shard and llama-server reads the first with its siblings beside it:
# this manager fetches and checks one file, so a shard would "download" and then never load (T-0512).
GGUF_SHARD_RE = re.compile(r"-\d{5}-of-\d{5}\.gguf")
# Both lists, and the sentences, are tests/contracts/models-api.json "create_body" (the console reads it too).
ENGINES = ["vllm", "llama.cpp"]
LLAMA_SETTINGS = ["max_model_len", "keep_loaded", "idle_unload_s"]


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def _contract_refusals() -> dict[str, dict]:
    """The create and download refusals, from the contract the console and the e2e mock read too (T-1269).
    The image carries the file beside this one; a checkout has it under tests/contracts. Not covered: a
    reworded sentence reaches a running manager only with a rebuilt image."""
    here = Path(__file__).resolve()
    for p in (here.with_name("models-api.json"), here.parents[2] / "tests" / "contracts" / "models-api.json"):
        if p.is_file():
            table = json.loads(p.read_text(encoding="utf-8"))["refusals"]
            return {k: v for k, v in table.items() if not k.startswith("_")}
    raise FileNotFoundError("models-api.json is not beside the manager")


REFUSALS = _contract_refusals()


def refuse(key: str, **slots: Any) -> ApiError:
    """The ApiError for `key` in the contract's refusals table, its {slots} filled."""
    r = REFUSALS[key]
    return ApiError(r["status"], r["message"].format(**slots))


# ---------------------------------------------------------------- cmd <-> settings
def _flag(tokens: list[str], flag: str) -> str | None:
    return tokens[tokens.index(flag) + 1] if flag in tokens and tokens.index(flag) + 1 < len(tokens) else None


def _set_flag(tokens: list[str], flag: str, value: Any) -> None:
    if value is None:
        if flag in tokens:
            i = tokens.index(flag)
            del tokens[i:i + 2]
    elif flag in tokens:
        tokens[tokens.index(flag) + 1] = str(value)
    else:
        tokens += [flag, str(value)]


def _flags(tokens: list[str]) -> dict[str, str]:
    return LLAMA_FLAGS if tokens[:1] and tokens[0].endswith("llama-server") else FLAGS


def parse_settings(model: dict) -> dict[str, Any]:
    """The panel's settings. `engine` says which: vLLM's own fields are None for llama.cpp
    (T-0518), where vLLM's defaults used to stand in for settings llama-server does not have."""
    tokens = str(model.get("cmd", "")).split()
    ttl = int(model.get("ttl", 0) or 0)
    flags = _flags(tokens)
    vllm = flags is FLAGS
    return {
        "engine": "vllm" if vllm else "llama.cpp",
        "max_model_len": int(_flag(tokens, flags["max_model_len"]) or 0) or None,
        "gpu_memory_utilization": float(_flag(tokens, FLAGS["gpu_memory_utilization"]) or 0.9) if vllm else None,
        "tensor_parallel_size": int(_flag(tokens, FLAGS["tensor_parallel_size"]) or 1) if vllm else None,
        "kv_cache_dtype": _flag(tokens, flags.get("kv_cache_dtype", "")),
        "quantization": _flag(tokens, flags.get("quantization", "")),
        "keep_loaded": ttl == 0,
        "idle_unload_s": ttl,
    }


def repo_of(model: dict) -> str:
    tokens = str(model.get("cmd", "")).split()
    if "serve" in tokens:
        return tokens[tokens.index("serve") + 1]
    # llama-server's -hf owner/name[:quant]
    return (_flag(tokens, "-hf") or _flag(tokens, "--hf-repo") or "").split(":")[0]


def file_of(model: dict) -> str | None:
    """The one GGUF a llama-server model reads; None for a vLLM model (the whole snapshot)."""
    tokens = str(model.get("cmd", "")).split()
    return _flag(tokens, "--hf-file") or _flag(tokens, "-hff")


def validate_settings(s: dict[str, Any]) -> None:
    def bad(msg: str):
        raise ApiError(400, msg)

    unknown = set(s) - set(FLAGS) - {"keep_loaded", "idle_unload_s"}
    if unknown:
        bad(f"Unknown setting: {', '.join(sorted(unknown))}.")
    for key, (lo, hi) in LIMITS.items():
        if key in s:
            v = s[key]
            isnum = isinstance(v, (int, float)) and not isinstance(v, bool)
            if not isnum or (key != "gpu_memory_utilization" and int(v) != v) or not lo <= v <= hi:
                bad(f"{key} must be a number from {lo} to {hi}.")
    if "kv_cache_dtype" in s and s["kv_cache_dtype"] not in KV_CACHE_DTYPES:
        bad(f"kv_cache_dtype must be one of {', '.join(KV_CACHE_DTYPES)}.")
    if "quantization" in s and s["quantization"] not in QUANTIZATIONS:
        bad("quantization must be empty or one of " + ", ".join(q for q in QUANTIZATIONS if q) + ".")
    if "keep_loaded" in s and not isinstance(s["keep_loaded"], bool):
        bad("keep_loaded must be true or false.")


def apply_settings(model: dict, changes: dict[str, Any]) -> dict:
    """The model with `changes` applied to its cmd and ttl; everything else in it untouched."""
    validate_settings(changes)
    merged = {**parse_settings(model), **changes}
    tokens = str(model["cmd"]).split()
    flags = _flags(tokens)
    if other := sorted(set(changes) & set(FLAGS) - set(flags)):
        raise ApiError(400, f"A llama-server model has no {', '.join(other)}: set it in its cmd.")
    for key, flag in flags.items():
        if key in changes:
            value = merged[key]
            _set_flag(tokens, flag, int(value) if key in ("max_model_len", "tensor_parallel_size") else value)
    if merged["keep_loaded"]:
        ttl = 0
    else:
        ttl = merged["idle_unload_s"] or DEFAULT_IDLE_S
    out = {**model, "cmd": " ".join(tokens), "ttl": ttl}
    return out


# A new model's settings when the request leaves one out; the console's form starts from the same
# values (both read tests/contracts/models-api.json "new_model_defaults").
NEW_DEFAULTS = {"max_model_len": 32768, "gpu_memory_utilization": 0.4, "tensor_parallel_size": 1,
                "keep_loaded": False, "idle_unload_s": DEFAULT_IDLE_S}


def check_gguf(file: Any) -> str:
    """`file` when it is one .gguf of a repo; raises ApiError(400) with the contract's sentence otherwise.
    Not covered: whether the repo has it (the download and the disk check say that)."""
    if file is None or file == "":
        raise ApiError(400, "Pick the .gguf file to serve: llama.cpp reads one file of the repo.")
    if not isinstance(file, str) or not GGUF_RE.fullmatch(file):
        raise ApiError(400, "file is one .gguf in the repo.")
    if GGUF_SHARD_RE.search(file):
        raise ApiError(400, "That file is one shard of a split GGUF; only a single .gguf file is supported.")
    return file


def new_model_file(mid: str, repo: str, settings: dict[str, Any], file: str | None = None) -> dict:
    if file:
        # What ripple-65b.yaml (T-0511) runs, less its one-off tuning: --offline reads the file
        # from the cache the download filled, one slot, every layer on the cards.
        s = {k: v for k, v in {**NEW_DEFAULTS, **settings}.items() if k in LLAMA_SETTINGS or k in settings}
        base = {"name": repo.split("/")[-1], "ttl": 0,
                "cmd": f"llama-server -hf {repo} --hf-file {file} --offline --no-mmproj --port ${{PORT}} "
                       f"--alias {mid} --jinja -np 1 -ngl 99 -fa on"}
        return {"models": {mid: apply_settings(base, s)}}
    s = {**NEW_DEFAULTS, **settings}
    base = {"name": repo.split("/")[-1], "ttl": 0, "env": list(NEW_MODEL_ENV),
            "cmd": f"vllm serve {repo} --port ${{PORT}} --served-model-name {mid} {NEW_MODEL_TAIL}"}
    return {"models": {mid: apply_settings(base, s)}}


# ---------------------------------------------------------------- the outside world
class HFHub:
    """huggingface_hub, behind the few calls the manager makes (tests substitute a fake)."""

    def __init__(self, cache_dir: Path):
        self.cache_dir = cache_dir

    def search(self, query: str) -> list[dict]:
        from huggingface_hub import HfApi

        out = []
        for m in HfApi().list_models(search=query, pipeline_tag="text-generation", sort="downloads",
                                     limit=20, expand=["downloads", "likes", "gated", "safetensors"]):
            st = getattr(m, "safetensors", None)
            params = getattr(st, "parameters", None) or {}
            size = sum(n * DTYPE_BYTES[d] for d, n in params.items() if d in DTYPE_BYTES)
            total = getattr(st, "total", None)
            out.append({"repo": m.id, "downloads": m.downloads or 0, "likes": m.likes or 0,
                        "params_b": round(total / 1e9, 1) if total else None,
                        # Packed int4 (AWQ/GPTQ store eight weights per I32) makes the per-dtype count
                        # a ~5x overestimate (Qwen3-8B-AWQ read as 30 GB, live 2026-10-09); null then,
                        # and the download's own check (plan) sums the real files.
                        "size_bytes": size if size and len(params) and all(d in DTYPE_BYTES for d in params)
                        and not ({"I32", "U32"} & set(params)) else None,
                        "gated": bool(m.gated)})
        return out

    def plan(self, repo: str, file: str | None = None) -> tuple[int, bool]:
        """(bytes download would fetch, gated)."""
        from huggingface_hub import HfApi

        info = HfApi().model_info(repo, files_metadata=True)
        total = sum(f.size or 0 for f in info.siblings or []
                    if (f.rfilename == file if file else not any(fnmatch.fnmatch(f.rfilename, p) for p in SKIP)))
        return total, bool(info.gated)

    def download(self, repo: str, file: str | None = None) -> None:
        """The repo's snapshot, or with `file` only that file (a GGUF repo holds every quant)."""
        from huggingface_hub import hf_hub_download, snapshot_download

        if file:
            hf_hub_download(repo, file, cache_dir=str(self.cache_dir))
        else:
            snapshot_download(repo, cache_dir=str(self.cache_dir), ignore_patterns=SKIP)


class Nvml:
    """Per-card memory and the processes on it, through pynvml (the driver lib is mounted in)."""

    def cards(self) -> list[dict]:
        import pynvml

        pynvml.nvmlInit()
        try:
            out = []
            for i in range(pynvml.nvmlDeviceGetCount()):
                h = pynvml.nvmlDeviceGetHandleByIndex(i)
                mem = pynvml.nvmlDeviceGetMemoryInfo(h)
                name = pynvml.nvmlDeviceGetName(h)
                procs = [(p.pid, (p.usedGpuMemory or 0) // 2**20)
                         for p in pynvml.nvmlDeviceGetComputeRunningProcesses(h)]
                out.append({"index": i, "name": name.decode() if isinstance(name, bytes) else name,
                            "total_mib": mem.total // 2**20, "used_mib": mem.used // 2**20, "procs": procs})
            return out
        finally:
            pynvml.nvmlShutdown()


def process_label(pid: int) -> str:
    # ponytail: NVML reports host pids; a pid that also exists here is almost always vLLM, since
    # this container's pid space is tiny. Voice and hearing live in other containers and cannot
    # be told from each other by pid, so they read as "other" until the stack labels them.
    return "models" if Path(f"/proc/{pid}").exists() else "other"


class Swap:
    """llama-swap's HTTP API on loopback."""

    def __init__(self, url: str):
        self.url = url.rstrip("/")

    def _call(self, path: str, body: dict | None = None, timeout: float = 5) -> bytes:
        headers = {"content-type": "application/json"}
        if os.environ.get("LLM_API_KEY"):  # llama-swap's apiKeys; unset, it is open
            headers["authorization"] = "Bearer " + os.environ["LLM_API_KEY"]
        req = urllib.request.Request(self.url + path, data=None if body is None else json.dumps(body).encode(),
                                     headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()

    def running(self, strict: bool = False) -> dict[str, str]:
        """{} when llama-swap does not answer, unless `strict`, which raises instead."""
        try:
            data = json.loads(self._call("/running"))
        except (OSError, ValueError):
            if strict:
                raise
            return {}
        return {r["model"]: r.get("state", "") for r in data.get("running", [])}

    def unload(self) -> None:
        self._call("/unload", timeout=120)

    def load(self, mid: str) -> None:
        # llama-swap loads on the first request for a model; a one-token completion is that request.
        self._call("/v1/chat/completions", {"model": mid, "max_tokens": 1,
                                            "messages": [{"role": "user", "content": "hi"}]}, timeout=1800)


def llama_swap_validate(base: Path, conf_dir: Path) -> None:
    """Raises ApiError(400) with llama-swap's own complaint when the config would not load."""
    binary = os.environ.get("LLAMA_SWAP_BIN", "llama-swap")
    if not shutil.which(binary):
        raise ApiError(500, "llama-swap is not installed here, so the model file cannot be checked.")
    r = subprocess.run([binary, "-validate", "-config", str(base), "-config-dir", str(conf_dir)],
                       capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        why = (r.stderr or r.stdout).strip().splitlines()
        raise ApiError(400, "llama-swap would not load that model: " + (why[-1] if why else "invalid config"))


# ---------------------------------------------------------------- healing a failed start
START_FAILED = Path("/tmp/jarvis-models-start-failed")  # vllm_prestart.py's mark (T-0411)
HEAL_WAITS = (300, 900, 2700)


class Healer:
    """Retries Hermes's model after a vLLM start died, so an outage heals before a request (T-0412).

    vllm_prestart.py writes the mark when a start dies and clears it when one answers /health.
    While the mark is there and llama-swap runs nothing, this loads `model` after each wait in
    turn, counted from the last try; a start something else drives (a request, the console's
    Load) restarts the wait instead of using a try. After the last try it stops, and the mark
    keeps the container unhealthy, so health_guard pages the owner. A llama-swap that does not
    answer /running skips the minute rather than counting as idle. Not covered: any model but
    `model`, a cause a retry cannot fix (the cap ends that), and a manager restart while the mark
    stands (the count is in memory, so the cap starts over; a container restart clears the mark).
    """

    def __init__(self, swap: Any, model: str, mark: Path = START_FAILED,
                 waits: tuple[float, ...] = HEAL_WAITS,
                 clock: Callable[[], float] = time.monotonic):
        self.swap, self.model, self.mark, self.waits, self.clock = swap, model, mark, waits, clock
        self.tries = 0
        self.due: float | None = None

    def tick(self) -> None:
        if not self.mark.exists():
            self.tries, self.due = 0, None
            return
        if self.tries >= len(self.waits):
            return
        try:
            busy = self.swap.running(strict=True)
        except (OSError, ValueError):
            return  # busy or down: a timeout is not "nothing running", so decide next minute
        if busy:
            self.due = None
            return
        now = self.clock()
        if self.due is None:
            self.due = now + self.waits[self.tries]
        if now < self.due:
            return
        self.tries += 1
        _LOGGER.warning("vLLM's last start died; loading %s again (try %d of %d)",
                        self.model, self.tries, len(self.waits))
        try:
            self.swap.load(self.model)  # blocks until the start answers or dies
        except OSError as exc:
            _LOGGER.warning("loading %s again failed: %s", self.model, exc)
        self.due = self.clock() + self.waits[self.tries] if self.tries < len(self.waits) else None

    def run(self, every: float = 60) -> None:
        while True:
            time.sleep(every)
            try:
                self.tick()
            except Exception:  # noqa: BLE001 - a dead thread would end the healing silently
                _LOGGER.exception("the failed-start healer tripped")


# ---------------------------------------------------------------- the manager
class Manager:
    def __init__(self, conf: Path, hub_cache: Path, base_yaml: Path, hub: Any, nvml: Any, swap: Any,
                 disk_usage=shutil.disk_usage, validate=llama_swap_validate):
        self.conf, self.hub_cache, self.base_yaml = conf, hub_cache, base_yaml
        self.hub, self.nvml, self.swap = hub, nvml, swap
        self.disk_usage, self.validate = disk_usage, validate
        self.jobs: dict[str, dict] = {}
        self.lock = threading.Lock()
        # Separate from self.lock, which a download holds across a Hub request: two PATCHes of
        # one model must not share the temp file or race the replace.
        self.write_lock = threading.Lock()

    # -- disk
    def _cache_dir(self, repo: str) -> Path:
        return self.hub_cache / ("models--" + repo.replace("/", "--"))

    def on_disk(self, repo: str) -> int:
        """Bytes under the repo's blobs, an unfinished download included: the progress counter."""
        blobs = self._cache_dir(repo) / "blobs"
        return sum(f.stat().st_size for f in blobs.iterdir() if f.is_file()) if blobs.is_dir() else 0

    def has_weights(self, model: dict) -> bool:
        """Whether `model` can load. A llama-server model reads one GGUF, which counts only once it
        is linked in a snapshot (huggingface_hub links a blob when it is finished): a killed
        download's `.incomplete` and another quant of the same repo are blobs too (T-0517).
        Not covered: a vLLM snapshot is taken as whole once any blob is there."""
        repo, file = repo_of(model), file_of(model)
        if file:
            return any((snap / file).is_file() for snap in (self._cache_dir(repo) / "snapshots").glob("*"))
        return self.on_disk(repo) > 0

    # -- model files
    def _path(self, mid: str) -> Path:
        if not ID_RE.fullmatch(mid):
            raise ApiError(400, "That is not a model id.")
        return self.conf / f"{mid}.yaml"

    def _read(self, mid: str) -> dict:
        p = self._path(mid)
        if not p.is_file():
            raise ApiError(404, f"There is no model called {mid}.")
        return yaml.safe_load(p.read_text(encoding="utf-8"))["models"][mid]

    def _write(self, mid: str, doc: dict) -> None:
        """Validate with llama-swap against the whole config, then replace the file in one step."""
        final = self._path(mid)
        tmp = self.conf / f".{mid}.yaml.tmp"  # not *.yaml: llama-swap's watcher must not see it
        text = yaml.safe_dump(doc, sort_keys=False, width=10**6)
        with self.write_lock:
            self._validate_and_replace(tmp, final, text)

    def _validate_and_replace(self, tmp: Path, final: Path, text: str) -> None:
        tmp.write_text(text, encoding="utf-8")
        try:
            with tempfile.TemporaryDirectory() as d:
                for f in self.conf.glob("*.yaml"):
                    if f != final:
                        shutil.copy(f, d)
                shutil.copy(tmp, Path(d) / final.name)
                self.validate(self.base_yaml, Path(d))
            os.replace(tmp, final)
        finally:
            tmp.unlink(missing_ok=True)

    def fetch_fresh(self) -> list[str]:
        """Download the weights of the models the entrypoint seeded on this start (``.fresh``).

        So a fresh host needs no click: its seeded models fetch themselves, each through the
        same disk check as a download from the panel. Only freshly seeded files: a model whose
        weights the owner deleted later is never fetched again behind their back. Not covered:
        a download that fails is reported in the panel and not retried.
        """
        fresh = self.conf / ".fresh"
        if not fresh.exists():
            return []
        started, retry = [], []
        for name in fresh.read_text(encoding="utf-8").split():
            mid = name.removesuffix(".yaml")
            try:
                m = self._read(mid)
                repo = repo_of(m)
                if repo and not self.has_weights(m):
                    self.download(repo, file_of(m))
                    started.append(repo)
            except OSError as exc:
                # No network yet (a host booted offline): try again on the next start.
                _LOGGER.warning("could not reach the Hub for the seeded model %s: %s", mid, exc)
                retry.append(name)
            except (ApiError, KeyError, ValueError) as exc:
                _LOGGER.warning("could not fetch the seeded model %s: %s", mid, exc)
        if retry:
            fresh.write_text("".join(f"{n}\n" for n in retry), encoding="utf-8")
        else:
            fresh.unlink(missing_ok=True)
        return started

    # -- views
    def _state(self, mid: str, repo: str, running: dict[str, str], model: dict) -> str:
        job = self.jobs.get(repo)
        if job and job["state"] == "running":
            return "downloading"
        live = running.get(mid)
        if live:
            return "starting" if live != "ready" else "loaded"
        return "unloaded" if self.has_weights(model) else "missing"

    def _model_view(self, mid: str, running: dict[str, str]) -> dict:
        m = self._read(mid)
        repo = repo_of(m)
        return {"id": mid, "name": m.get("name", mid), "repo": repo, "state": self._state(mid, repo, running, m),
                "on_disk_bytes": self.on_disk(repo), "settings": parse_settings(m)}

    def _job_view(self, job: dict) -> dict:
        return {"repo": job["repo"], "state": job["state"], "done_bytes": self.on_disk(job["repo"]),
                "total_bytes": job["total"], "error": job["error"]}

    def list(self) -> dict:
        running = self.swap.running()
        models = []
        for f in sorted(self.conf.glob("*.yaml")):
            try:
                if self._read(f.stem).get("unlisted"):
                    continue  # llama-swap's own flag: the embedder and reranker are no chat model (T-0511)
                models.append(self._model_view(f.stem, running))
            except Exception:  # noqa: BLE001 - one broken file must not hide the others
                _LOGGER.exception("unreadable model file %s", f)
        du = self.disk_usage(self.hub_cache)
        # What is running first, then the models kept loaded, so the house's main model leads.
        models.sort(key=lambda m: (m["state"] not in ("loaded", "starting"), not m["settings"]["keep_loaded"], m["id"]))
        return {"ok": True, "models": models, "downloads": [self._job_view(j) for j in self.jobs.values()],
                "disk": {"free_bytes": du.free, "total_bytes": du.total}}

    def gpus(self) -> dict:
        out = []
        for c in self.nvml.cards():
            users: dict[str, int] = {}
            for pid, mib in c["procs"]:
                who = process_label(pid)
                users[who] = users.get(who, 0) + mib
            out.append({"index": c["index"], "name": c["name"], "total_mib": c["total_mib"],
                        "used_mib": c["used_mib"], "users": [{"who": w, "mib": n} for w, n in users.items()]})
        return {"ok": True, "gpus": out}

    def search(self, query: str) -> dict:
        if not query.strip():
            raise ApiError(400, "Type something to search for.")
        results = []
        for r in self.hub.search(query.strip()):
            name = r["repo"].lower()
            quant = next((q for q in ("awq", "gptq", "fp8") if q in name), None)
            results.append({**r, "quantization": quant, "local": self.on_disk(r["repo"]) > 0})
        return {"ok": True, "results": results}

    # -- actions
    def _missing_file(self, repo: str) -> str | None:
        """The .gguf a llama.cpp entry for `repo` reads and the cache lacks: what a row whose file is gone
        needs fetched, so the console posts the repo alone (T-1269). Not covered: with two such entries
        (two quants of one repo) it is the first by file name, and the other follows on its own click."""
        for f in sorted(self.conf.glob("*.yaml")):
            try:
                model = self._read(f.stem)
                if repo_of(model) == repo and file_of(model) and not self.has_weights(model):
                    return file_of(model)
            except (ApiError, OSError, KeyError, TypeError, AttributeError, yaml.YAMLError):
                continue  # a broken sibling file is not this repo's business
        return None

    def download(self, repo: str, file: str | None = None) -> dict:
        if not isinstance(repo, str) or not REPO_RE.fullmatch(repo):
            raise refuse("repo_invalid")
        if file is not None:
            check_gguf(file)
        with self.lock:
            job = self.jobs.get(repo)
            if job and job["state"] == "running":
                return {"ok": True, "download": self._job_view(job)}
            if file is None:
                file = self._missing_file(repo)
            total, gated = self.hub.plan(repo, file)
            if gated:
                raise refuse("gated", repo=repo)
            # Downloads still running will take their remaining bytes too: two started together
            # must not each pass the check against the same free space.
            pending = sum(max(j["total"] - self.on_disk(j["repo"]), 0)
                          for j in self.jobs.values() if j["state"] == "running")
            free = self.disk_usage(self.hub_cache).free - pending
            if total > free - DISK_RESERVE_BYTES:
                raise refuse("disk_full", repo=repo, need_gb=f"{total / 1e9:.1f}",
                             free_gb=f"{max(free - DISK_RESERVE_BYTES, 0) / 1e9:.1f}",
                             reserve_gb=DISK_RESERVE_BYTES // 1024**3)
            job = self.jobs[repo] = {"repo": repo, "file": file, "state": "running", "total": total, "error": None}
        threading.Thread(target=self._run_download, args=(job,), daemon=True).start()
        return {"ok": True, "download": self._job_view(job)}

    def _run_download(self, job: dict) -> None:
        try:
            self.hub.download(job["repo"], job["file"])
            job["state"] = "done"
        except Exception as exc:  # noqa: BLE001 - the reason goes to the panel
            _LOGGER.exception("download of %s failed", job["repo"])
            job["error"], job["state"] = str(exc)[:300] or exc.__class__.__name__, "error"

    def create(self, body: dict) -> dict:
        mid, repo = body.get("id"), body.get("repo")
        if not isinstance(mid, str) or not ID_RE.fullmatch(mid):
            raise refuse("id_invalid")
        if not isinstance(repo, str) or not REPO_RE.fullmatch(repo):
            raise refuse("repo_invalid")
        settings = body.get("settings") or {}
        if not isinstance(settings, dict):
            raise refuse("settings_not_object")
        engine = body.get("engine", "vllm")
        if engine not in ENGINES:
            raise refuse("engine_unknown", engines=", ".join(ENGINES))
        file = check_gguf(body.get("file")) if engine == "llama.cpp" else None
        if engine == "vllm" and body.get("file") is not None:
            raise refuse("file_on_vllm")
        if self._path(mid).exists():
            raise refuse("model_exists", id=mid)
        doc = new_model_file(mid, repo, settings, file)  # refuses a setting the engine lacks, before any disk check
        if not (self.has_weights(doc["models"][mid]) if file else self.on_disk(repo)):
            raise refuse("not_downloaded", what=file or repo)
        self._write(mid, doc)
        removed = self.conf / ".removed"
        if removed.exists():
            keep = [x for x in removed.read_text().splitlines() if x != f"{mid}.yaml"]
            removed.write_text("".join(x + "\n" for x in keep))
        return {"ok": True, "model": {"id": mid, "state": self._state(mid, repo, self.swap.running(), self._read(mid))}}

    def update(self, mid: str, body: dict) -> dict:
        changes = body.get("settings")
        if not isinstance(changes, dict) or not changes:
            raise ApiError(400, "Send the settings to change.")
        m = apply_settings(self._read(mid), changes)
        self._write(mid, {"models": {mid: m}})
        return {"ok": True, "model": {"id": mid, "state": self._state(mid, repo_of(m), self.swap.running(), m)}}

    def delete(self, mid: str, weights: bool) -> dict:
        model = self._read(mid)
        if model.get("unlisted"):
            # base.yaml's aux group names it: without its file llama-swap refuses the whole config.
            raise ApiError(409, f"{mid} is in the aux group (embeddings, rerank) and stays.")
        repo = repo_of(model)
        # Before the unlink, and tolerant of a broken sibling file: a raise after it would leave
        # the model gone, the weights kept and the panel told it failed.
        shared = False
        for f in self.conf.glob("*.yaml"):
            if f.stem == mid:
                continue
            try:
                shared = shared or repo_of(self._read(f.stem)) == repo
            except (ApiError, OSError, KeyError, TypeError, yaml.YAMLError):
                shared = True  # unknown: keep the weights rather than delete someone's
        if self.swap.running().get(mid):
            self.swap.unload()
        self._path(mid).unlink()
        removed = self.conf / ".removed"
        with removed.open("a") as f:
            f.write(f"{mid}.yaml\n")
        freed = 0
        if weights and not shared:
            blobs = list((self._cache_dir(repo) / "blobs").glob("*"))
            freed = sum(f.stat().st_size for f in blobs if f.is_file() and not f.is_symlink())
            targets = {f.resolve() for f in blobs if f.is_symlink()}
            shutil.rmtree(self._cache_dir(repo), ignore_errors=True)
            # huggingface_hub 1.33 keeps the bytes in hub/blobs/xx/ behind those links, and
            # another repo may link the same blob: free only what nothing links any more.
            hub = self.hub_cache.resolve()
            kept = {f.resolve() for f in self.hub_cache.glob("models--*/blobs/*") if f.is_symlink()}
            for t in targets - kept:
                if t.is_file() and t.is_relative_to(hub):
                    freed += t.stat().st_size
                    t.unlink()
        return {"ok": True, "freed_bytes": freed}

    def load(self, mid: str) -> dict:
        model = self._read(mid)
        repo = repo_of(model)
        if not self.has_weights(model):
            raise refuse("not_downloaded", what=file_of(model) or repo)
        gpus = [] if self.swap.running().get(mid) else self._fit(mid, model)
        threading.Thread(target=self._safe, args=(self.swap.load, mid), daemon=True).start()
        return {"ok": True, "model": {"id": mid, "state": "starting"}, "gpus": gpus}

    def _fit(self, mid: str, model: dict) -> list[dict]:
        """The cards `model` lands on, and for vLLM whether it fits; ApiError(409) when it cannot.

        vLLM takes gpu_memory_utilization of each card's total on its first tensor_parallel_size
        visible cards (CUDA_VISIBLE_DEVICES in the model's env, else every card) and dies on start
        when that is not free; llama.cpp spreads its weights, so its need is not estimated.
        """
        env = dict(str(e).partition("=")[::2] for e in model.get("env") or [])
        visible = env.get("CUDA_VISIBLE_DEVICES")
        if visible is not None and visible.strip() in ("", "-1"):
            return []  # on the CPU
        try:
            cards = self.nvml.cards()
        except Exception:  # noqa: BLE001 - no driver here: load as before, unmeasured
            _LOGGER.warning("could not read the cards; loading %s unmeasured", mid, exc_info=True)
            return []
        if visible:
            order = [int(x) for x in visible.split(",") if x.strip().isdigit()]
            cards = [c for i in order for c in cards if c["index"] == i]
        settings = parse_settings(model)
        vllm = "serve" in str(model.get("cmd", "")).split()
        rows = []
        for c in cards[: settings["tensor_parallel_size"]] if vllm else cards:
            users: dict[str, int] = {}
            for pid, mib in c["procs"]:
                users[process_label(pid)] = users.get(process_label(pid), 0) + mib
            # ponytail: everything labelled "models" is taken to swap out with the current model;
            # a model kept beside it (a llama-swap group) would be counted free. Read groups if one is.
            free = c["total_mib"] - c["used_mib"] + users.get("models", 0)
            need = int(settings["gpu_memory_utilization"] * c["total_mib"]) if vllm else None
            if need is not None and need > free:
                held = ", ".join(f"{who} holds {n / 1024:.1f} GiB" for who, n in users.items() if who != "models")
                raise ApiError(409, f"{mid} needs {need / 1024:.1f} GiB on card {c['index']} ({c['name']}), and "
                                    f"{free / 1024:.1f} GiB is free there once the current model is unloaded"
                                    f"{' (' + held + ')' if held else ''}. Lower its memory share, or free the card.")
            rows.append({"index": c["index"], "name": c["name"], "need_mib": need, "free_mib": free})
        return rows

    @staticmethod
    def _safe(fn, *args) -> None:
        try:
            fn(*args)
        except Exception:  # noqa: BLE001 - the panel sees the state fall back to unloaded
            _LOGGER.exception("background call failed")

    def unload(self, mid: str) -> dict:
        self._read(mid)
        if self.swap.running().get(mid):
            self.swap.unload()
        return {"ok": True, "model": {"id": mid, "state": "unloaded"}}

    # -- routing
    def handle(self, method: str, path: str, query: dict[str, list[str]], body: Any) -> tuple[int, dict]:
        try:
            parts = [p for p in path.removeprefix("/api/models").split("/") if p]
            if not path.startswith("/api/models"):
                raise ApiError(404, "No such endpoint.")
            if method == "GET" and parts == []:
                return 200, self.list()
            if method == "GET" and parts == ["gpus"]:
                return 200, self.gpus()
            if method == "GET" and parts == ["search"]:
                return 200, self.search((query.get("q") or [""])[0])
            if method == "POST" and parts == ["downloads"]:
                return 200, self.download((body or {}).get("repo"), (body or {}).get("file"))
            if method == "POST" and parts == []:
                return 200, self.create(body or {})
            if len(parts) == 1 and method == "PATCH":
                return 200, self.update(parts[0], body or {})
            if len(parts) == 1 and method == "DELETE":
                return 200, self.delete(parts[0], (query.get("weights") or ["0"])[0] in ("1", "true"))
            if len(parts) == 2 and method == "POST" and parts[1] == "load":
                return 200, self.load(parts[0])
            if len(parts) == 2 and method == "POST" and parts[1] == "unload":
                return 200, self.unload(parts[0])
            raise ApiError(404, "No such endpoint.")
        except ApiError as e:
            return e.status, {"ok": False, "error": e.message}
        except Exception as exc:  # noqa: BLE001
            _LOGGER.exception("model manager failed on %s %s", method, path)
            return 500, {"ok": False, "error": f"The model manager failed: {exc.__class__.__name__}."}


def make_handler(manager: Manager, key: str = ""):
    class Handler(BaseHTTPRequestHandler):
        def _go(self):
            # Only the adapter holds the key. Without it, anything on the compose network (the
            # model's own tools run in `jarvis`) could delete weights or rewrite a model's flags.
            if key and not hmac.compare_digest(self.headers.get("authorization", ""), f"Bearer {key}"):
                return self._send(401, {"ok": False, "error": "This needs the model manager's key."})
            url = urlsplit(self.path)
            body = None
            n = int(self.headers.get("content-length") or 0)
            if n > MAX_BODY:
                return self._send(413, {"ok": False, "error": "That request is too large."})
            if n:
                try:
                    body = json.loads(self.rfile.read(n))
                except ValueError:
                    status, out = 400, {"ok": False, "error": "The body is not JSON."}
                    return self._send(status, out)
            self._send(*manager.handle(self.command, url.path, parse_qs(url.query), body))

        def _send(self, status: int, out: dict):
            raw = json.dumps(out).encode()
            self.send_response(status)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        do_GET = do_POST = do_PATCH = do_DELETE = _go

        def log_message(self, fmt, *args):
            _LOGGER.info(fmt, *args)

    return Handler


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    hf_home = Path(os.environ.get("HF_HOME", "/models/hf"))
    cache = hf_home / "hub"
    cache.mkdir(parents=True, exist_ok=True)
    manager = Manager(
        conf=Path(os.environ.get("AURA_MODELS_CONFIG", "/models/config")),
        hub_cache=cache,
        base_yaml=Path(os.environ.get("AURA_MODELS_BASE", "/opt/jarvis-models/seed/base.yaml")),
        hub=HFHub(cache), nvml=Nvml(), swap=Swap(f"http://127.0.0.1:{os.environ.get('PORT', '8000')}"))
    threading.Thread(target=manager.fetch_fresh, daemon=True).start()
    if os.environ.get("PRELOAD_MODEL"):  # the model the entrypoint loads at boot: Hermes's
        threading.Thread(target=Healer(manager.swap, os.environ["PRELOAD_MODEL"]).run, daemon=True).start()
    key = os.environ.get("MODELS_API_KEY", "")
    if not key:
        _LOGGER.warning("MODELS_API_KEY is not set: listening on loopback only; the Models panel is off")
    host = "0.0.0.0" if key else "127.0.0.1"
    ThreadingHTTPServer((host, int(os.environ.get("MANAGER_PORT", "8001"))), make_handler(manager, key)).serve_forever()


if __name__ == "__main__":
    main()
