# jarvis-models: the house's own LLM server (T-0278). The AI container that served the model is
# gone; this is its replacement on the JARVIS host: llama-swap in front, vLLM as the child
# process it starts per model, so one OpenAI-compatible endpoint swaps models on demand and
# the adapter's /running and the llama-swap-control plugin keep the API they were written for.
#
# One image, not llama-swap spawning `docker run vllm…`: that would need the docker socket
# inside a container and a second way of wiring the GPUs. GPU access is the house's usual
# kind (deploy/docker-compose.gpu.yml): device nodes and the host's driver libs, no toolkit.
ARG VLLM_TAG=v0.31.0

# llama-swap, built from its tagged source (T-0415). The v268 release binary still links
# golang.org/x/net v0.59.0, and grype fails the image's critical gate on it (pipelines 642/644);
# the same source with x/net raised passes. The checked commit pins the source the way the old
# checksum pinned the binary (the Go toolchain is a tag, like the other bases). No embed_ui tag: nothing opens llama-swap's own web UI (the console has
# a models page), and building it would need node.
FROM golang:1.27.3-bookworm AS llama-swap
ARG LLAMA_SWAP_VERSION=268
ARG LLAMA_SWAP_COMMIT=2576af3ac6aaec841706c212186f0475b086bb5b
ARG X_NET_VERSION=v0.62.0
RUN set -eux; \
    git clone --depth 1 --branch "v${LLAMA_SWAP_VERSION}" https://github.com/mostlygeek/llama-swap /src; \
    cd /src; \
    test "$(git rev-parse HEAD)" = "${LLAMA_SWAP_COMMIT}"; \
    go get "golang.org/x/net@${X_NET_VERSION}"; \
    go mod tidy; \
    CGO_ENABLED=0 go build -trimpath -ldflags "-s -w -X main.version=${LLAMA_SWAP_VERSION} -X main.commit=${LLAMA_SWAP_COMMIT}" -o /llama-swap .; \
    /llama-swap -version

# llama.cpp's server (T-0511), for what vLLM cannot load: GGUF quants, and architectures only
# llama.cpp has (Ripple-65B's `ricursive`, from b12167). Built on the vLLM base so it links the
# CUDA 13 runtime the final image already ships; sm_86 only (the host's two 3090s). The checksum
# pins the source. No OpenSSL: models run `--offline` from the Hub cache the manager fills.
FROM vllm/vllm-openai:${VLLM_TAG} AS llama-cpp
ARG LLAMA_CPP_TAG=b12192
ARG LLAMA_CPP_SHA256=8863ba89ecc37ce76a18bb9c397455f0ee2931ef0814179c56f0700924f939fc
RUN set -eux; \
    pip install --no-cache-dir "cmake==4.1.2"; \
    curl -sSfL -o /tmp/src.tgz "https://github.com/ggml-org/llama.cpp/archive/refs/tags/${LLAMA_CPP_TAG}.tar.gz"; \
    echo "${LLAMA_CPP_SHA256}  /tmp/src.tgz" | sha256sum -c -; \
    tar xzf /tmp/src.tgz -C /tmp; \
    cd "/tmp/llama.cpp-${LLAMA_CPP_TAG}"; \
    cmake -B build -DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=86 -DBUILD_SHARED_LIBS=OFF \
        -DLLAMA_OPENSSL=OFF -DLLAMA_BUILD_TESTS=OFF -DLLAMA_BUILD_EXAMPLES=OFF; \
    cmake --build build --target llama-server -j"$(nproc)"; \
    cp build/bin/llama-server /llama-server

FROM vllm/vllm-openai:${VLLM_TAG}

# The vLLM base ships Python packages grype fails the critical gate on (pipelines 642/644):
# Pillow, LMCache, certifi and uv below their fixed releases, and Mooncake's transfer engine,
# whose bundled Go library carries an old Go runtime. Mooncake is vLLM's transport for moving
# the KV cache between hosts, which one host swapping one model at a time never uses, so it goes;
# the rest move to the versions proven against vLLM 0.31.0. The import line fails the build if a
# bump breaks vLLM, instead of the first model start minutes into a deploy; the freeze comparison
# fails it if a new dependency drags torch or vllm to another version (their CUDA builds must match).
RUN set -eux; \
    core="$(pip freeze | grep -E '^(torch|vllm)==')"; \
    pip uninstall -y mooncake-transfer-engine-cuda13; \
    pip install --no-cache-dir "pillow==13.0.0" "lmcache==0.5.6" "certifi==2026.10.23" "uv==0.13.6"; \
    test "$(pip freeze | grep -E '^(torch|vllm)==')" = "$core"; \
    python3 -c "import vllm, PIL.Image, lmcache; from vllm.entrypoints.openai import api_server"

COPY --from=llama-swap /llama-swap /usr/local/bin/llama-swap
COPY --from=llama-cpp /llama-server /usr/local/bin/llama-server

COPY deploy/models/seed /opt/jarvis-models/seed
COPY deploy/models/entrypoint.sh /opt/jarvis-models/entrypoint.sh
COPY deploy/models/manager.py /opt/jarvis-models/manager.py
# The manager's refusal sentences (T-1269): it reads this file beside itself, and the console and the e2e mock read the same one.
COPY tests/contracts/models-api.json /opt/jarvis-models/models-api.json
# Ahead of /usr/local/bin/vllm on PATH: every `vllm serve` first frees the cards (T-0411).
COPY --chmod=755 deploy/models/vllm_prestart.py /usr/local/sbin/vllm
COPY --chmod=755 deploy/models/vllm_prestart.py /usr/local/sbin/llama-server

# The model store: Hugging Face cache, one llama-swap file per model, and the manager's state.
# A bind mount in production (AURA_MODELS_DIR); a fresh host gets ./models beside the compose file.
# The compile caches too: vLLM's torch.compile, Triton and FlashInfer kernels take minutes per model on a
# cold start (measured 2026-10-09), and in the container's own filesystem they are lost on
# every recreate.
ENV HF_HOME=/models/hf \
    AURA_MODELS_CONFIG=/models/config \
    XDG_CACHE_HOME=/models/cache \
    VLLM_CACHE_ROOT=/models/cache/vllm \
    TRITON_CACHE_DIR=/models/cache/triton
VOLUME /models
EXPOSE 8000 8001
ENTRYPOINT ["/bin/sh", "/opt/jarvis-models/entrypoint.sh"]
