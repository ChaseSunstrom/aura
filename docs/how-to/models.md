# Run and manage the house's models

Since 2026-10-09 the house serves its own LLMs: the `jarvis-models` service in
`deploy/docker-compose.yml` replaced the separate AI container. llama-swap sits in front and
starts whichever model a request names, one chat model on both GPUs at a time. Each model's
file names its engine: vLLM (`vllm serve`) or llama.cpp's `llama-server` (since T-0511, for a GGUF
or an architecture only llama.cpp loads, such as `ripple-65b`), so switching engines at runtime
is picking another model. Two more run on the CPU beside any chat model, in the `aux` group of
`deploy/models/seed/base.yaml`: `qwen3-embedding-0.6b` (`/v1/embeddings`) and
`qwen3-reranker-0.6b` (`/v1/rerank`), both reached on the console's origin at `/llm/v1` too.
They are `unlisted`: no chat model, so not in `/v1/models` or the picker. Ollama is not an
engine here: it is llama.cpp underneath, with a second weight store.

## What runs where

| Piece | Where | Reached by |
| --- | --- | --- |
| llama-swap (OpenAI API, swaps models) | `jarvis-models:8000`; host `127.0.0.1:9000` and the docker bridge `172.17.0.1:9000` | Hermes (`LLM_URL`), the adapter's status (`LLAMA_SWAP_URL`), the gateway, CI's smoke |
| the model manager (the Models panel's API) | `jarvis-models:8001`, compose network only | the adapter's `/api/models/*` |
| weights (Hugging Face cache) | `$AURA_MODELS_DIR/hf` (production `/srv/jarvis-models`) | |
| one llama-swap file per model | `$AURA_MODELS_DIR/config/*.yaml`, hot-reloaded | the manager writes them |
| compile caches | `$AURA_MODELS_DIR/cache` | vLLM, Triton, FlashInfer |

Two keys, one per trust boundary, both in the host's `.env`:

- `LLM_API_KEY` — llama-swap answers nothing without it. Hermes, the adapter, the plugins,
  the gateway (`aura-core/.env`) and CI (`CI_LLM_API_KEY`) send it.
- `MODELS_API_KEY` — the manager answers nothing without it; only the adapter and
  `jarvis-models` hold it. The `jarvis` container, where the model's tools run, has it blanked.

## Manage models

Open **Models** in the console's launcher (`/models`; `/settings/models` opens it too): every
model with its state, size and engine, and each card's memory by who holds it. Load says which
cards the model lands on; unload and delete take a hold (unloading stops that model's replies
until its next request loads it again; delete optionally takes its weights). Edit changes
context length, GPU share, one or two cards, idle unload and keep-loaded (a llama-server model
takes only context length, idle unload and keep-loaded; the rest is in its cmd); cache type and
quantisation are not on the screen. Search Hugging Face to download a new one, then add it. Downloads are the
only traffic that leaves the house; each is checked against the free disk minus a 10 GB reserve,
and a gated model says which licence to accept first. A change applies without a restart.

To serve a GGUF (llama.cpp) instead of a vLLM snapshot, the manager's create takes
`engine: "llama.cpp"` and `file`, the one `.gguf` of the repo (download it first with
`POST /api/models/downloads {repo, file}`): it writes a `llama-server` entry that reads back as
engine llama.cpp with that file. A file that is not one `.gguf`, or one shard of a split GGUF, is
refused; so is a vLLM add that names a file. The field names and the file rule are
`create_body` in `tests/contracts/models-api.json`. The Models screen's add form carries it
(T-1254): choose llama.cpp, type the repo and its one `.gguf`, download the file, then add.
`addBody` in `jarvis-web/src/lib/hud/data/models.ts` builds and checks the body, so a file that
is not one `.gguf` is said beside the box and nothing is sent. A llama.cpp model whose file has
gone missing is downloaded again from its row (T-1269): "Download again" posts only the repo, and
the manager takes the file from its own entry for that repo (the llama.cpp one whose `.gguf` is not
on disk), so the list needs no `file` field. The button is off while that repo is downloading (the
manager answers a repeat download of a running repo with the running job, not a refusal). Every
create and download refusal is a keyed `refusals` entry (`status`, `message` with `{slot}`s) in the
same contract: the manager, the e2e mock and the tests read it, the answer is still
`{ok: false, error}`, and the screen shows the sentence without branching on it. The manager reads
the contract from `models-api.json` beside `manager.py`, which the models image copies: change the
table and the image is rebuilt.

## First start on a new host

`docker compose up -d` with the gpu overlays. On an empty models directory the service seeds
`qwen3.8-27b`, `qwen3.6-35b`, `ripple-65b` and the two CPU models and downloads their weights
by itself (an existing host gets the same on its next start for any seed it lacks and never
deleted: the T-0511 image fetched Ripple's 38.7 GB unasked) (about 85 GB; a llama-server model fetches only the one GGUF its `--hf-file` names). The first
load of a model compiles its kernels (about seven minutes on the 3090s); later starts reuse the
cache. Set both keys in `.env` first; without `LLM_API_KEY` llama-swap runs open and logs a
warning, without `MODELS_API_KEY` the Models panel stays off.

## Check it

```bash
python3 scripts/verify/models-tool-call.py qwen3.8-27b   # a tool call, end to end (key from the host .env)
```

Deploy a new image from `/opt/jarvis-host` with the GPU overlays (without them the service
comes back with no cards): `docker compose -f docker-compose.yml -f docker-compose.gpu.yml -f
docker-compose.gpu2.yml up -d --no-deps jarvis-models`
(CI's `build:models` builds it when `deploy/models/` changes; `scripts/deploy-here.sh REV jarvis-models`
builds and recreates it here, with the overlays, as a local `:latest`).
Swapping models unloads the other one: while a model loads, Hermes waits for it.
