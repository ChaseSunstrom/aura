COMFYUI_STATUS = {
    "name": "comfyui_status",
    "description": "Is ComfyUI up, can it generate now: version, GPU VRAM free/total as ComfyUI sees it, queue length and the installed checkpoints. Read-only.",
    "parameters": {"type": "object", "properties": {}, "required": []},
}

COMFYUI_SUBMIT = {
    "name": "comfyui_submit",
    "description": ("Generate an image on the local ComfyUI service (or run a full API-format workflow JSON). Occupies the GPU "
                    "for tens of seconds to minutes, so it needs approval. Waits for the result by default and returns image URLs."),
    "parameters": {
        "type": "object",
        "properties": {
            "prompt": {"type": "string", "description": "Positive prompt (text-to-image)"},
            "negative": {"type": "string"},
            "width": {"type": "integer"}, "height": {"type": "integer"},
            "seed": {"type": "integer"}, "steps": {"type": "integer"},
            "checkpoint": {"type": "string", "description": "A checkpoint name from comfyui_status"},
            "workflow": {"type": "object", "description": "A complete ComfyUI API-format workflow instead of the default text-to-image graph"},
            "wait": {"type": "boolean", "description": "Wait for completion (default true)"},
            "timeout": {"type": "integer", "description": "Seconds to wait (default 600)"},
        },
        "required": [],
    },
}

COMFYUI_RESULT = {
    "name": "comfyui_result",
    "description": "Fetch the outputs of an earlier comfyui_submit by prompt_id (status pending|completed|error, image URLs).",
    "parameters": {"type": "object", "properties": {"prompt_id": {"type": "string"}}, "required": ["prompt_id"]},
}

DESTRUCTIVE = {"comfyui_submit", "image_generate"}
