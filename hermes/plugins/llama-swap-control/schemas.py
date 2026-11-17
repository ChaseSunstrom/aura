"""Model-facing schemas for the llama-swap tools."""

LLAMA_SWAP_MODELS = {
    "name": "llama_swap_models",
    "description": ("List the models llama-swap can serve on the AI machine, which one is resident, and the "
                    "routing table — which model each kind of work (chat, reasoning, coding, vision, auxiliary, "
                    "delegation, embedding) is configured to use, plus 'needs_swap': the jobs whose model is not "
                    "loaded. Use before switching models: only one model is loaded at a time and a swap takes "
                    "minutes and evicts the resident model for everyone."),
    "parameters": {"type": "object", "properties": {}, "required": []},
}

LLAMA_SWAP_STATUS = {
    "name": "llama_swap_status",
    "description": ("Which model is loaded right now and its state (starting = warming up, ready, stopping), "
                    "plus the llama-swap version. Cheap; call it to answer 'which model is running?'."),
    "parameters": {"type": "object", "properties": {}, "required": []},
}

LLAMA_SWAP_LOAD = {
    "name": "llama_swap_load",
    # Named models, not a hard-coded list: llama_swap_models is the authority on what
    # exists. The earlier wording promised qwen3.8-flash-next-79b, which llama-swap does
    # not serve on this machine (GET /v1/models lists two models), so the agent would ask
    # for a model that cannot load.
    "description": ("Make a model resident on llama-swap. Call llama_swap_models first and use an id it "
                    "lists. This EVICTS the current model and takes 1–5 minutes; every other user of the AI "
                    "machine is affected, so it needs approval. Returns the elapsed seconds."),
    "parameters": {
        "type": "object",
        "properties": {"model": {"type": "string", "description": "Model id exactly as listed by llama_swap_models"}},
        "required": ["model"],
    },
}

LLAMA_SWAP_UNLOAD = {
    "name": "llama_swap_unload",
    "description": ("Unload every model from the GPUs (frees VRAM for ComfyUI or maintenance). The next chat "
                    "cold-starts the model again (~2 minutes). Needs approval."),
    "parameters": {"type": "object", "properties": {}, "required": []},
}

_SETTINGS = {
    "type": "object",
    "description": ("Any of: max_model_len (tokens), gpu_memory_utilization (0-1, vLLM), tensor_parallel_size "
                    "(cards, vLLM), kv_cache_dtype (vLLM), quantization (vLLM), keep_loaded (true/false), "
                    "idle_unload_s (seconds). A llama.cpp model has only max_model_len, keep_loaded, "
                    "idle_unload_s. The server checks the ranges and says what is wrong."),
    "properties": {
        "max_model_len": {"type": "integer"}, "gpu_memory_utilization": {"type": "number"},
        "tensor_parallel_size": {"type": "integer"}, "kv_cache_dtype": {"type": "string"},
        "quantization": {"type": ["string", "null"]}, "keep_loaded": {"type": "boolean"},
        "idle_unload_s": {"type": "integer"},
    },
}

MODEL_DOWNLOAD = {
    "name": "model_download",
    "description": ("Start downloading a model's weights from Hugging Face onto the AI machine (the Models "
                    "screen's Download). Gigabytes: the server refuses when the disk is short or the repo is "
                    "gated. Returns at once; the download runs on and llama_swap_models shows it. For a "
                    "llama.cpp model pass the one .gguf file. Needs approval."),
    "parameters": {
        "type": "object",
        "properties": {
            "repo": {"type": "string", "description": "Hugging Face repo, owner/name (not a URL)"},
            "file": {"type": "string", "description": "One .gguf file of the repo, for llama.cpp models only"},
        },
        "required": ["repo"],
    },
}

MODEL_ADD = {
    "name": "model_add",
    "description": ("Add a model whose weights are already downloaded to the model server (the Models "
                    "screen's Add). Call model_download first; the server refuses a model not on disk. "
                    "Needs approval."),
    "parameters": {
        "type": "object",
        "properties": {
            "id": {"type": "string", "description": "The new model's name: lowercase letters, digits, dot, dash, underscore"},
            "repo": {"type": "string", "description": "Hugging Face repo, owner/name"},
            "engine": {"type": "string", "enum": ["vllm", "llama.cpp"], "description": "Default vllm"},
            "file": {"type": "string", "description": "The .gguf to serve; required for llama.cpp, never for vllm"},
            "settings": _SETTINGS,
        },
        "required": ["id", "repo"],
    },
}

MODEL_SETTINGS = {
    "name": "model_settings",
    "description": ("Change a configured model's settings (the Models screen's settings form). Send only what "
                    "changes. Takes effect the next time the model loads. Needs approval."),
    "parameters": {
        "type": "object",
        "properties": {
            "model": {"type": "string", "description": "Model id exactly as listed by llama_swap_models"},
            "settings": _SETTINGS,
        },
        "required": ["model", "settings"],
    },
}

MODEL_DELETE = {
    "name": "model_delete",
    "description": ("Remove a model from the model server; with weights=true also delete its downloaded files "
                    "(unless another model uses the same repo), which cannot be undone and means downloading "
                    "again. A loaded model is unloaded first. Needs approval."),
    "parameters": {
        "type": "object",
        "properties": {
            "model": {"type": "string", "description": "Model id exactly as listed by llama_swap_models"},
            "weights": {"type": "boolean", "description": "Also delete the downloaded files. Default false"},
        },
        "required": ["model"],
    },
}

DESTRUCTIVE = {"llama_swap_load", "llama_swap_unload"}
