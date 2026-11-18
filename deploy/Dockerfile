# JARVIS on Hermes — the production image.
#
# Base: the official Hermes image, pinned to the release validated in Phase A.
# Layered in: faster-whisper + piper-tts (CPU), the Piper voice (checksum-verified),
# the Whisper "base" weights, the JARVIS plugins/skills/hooks, SOUL/USER/MEMORY seeds
# and the config template. Secrets never enter a layer: everything secret arrives
# as environment at runtime and is rendered by deploy/entrypoint.sh on first start.
#
# Build (from the repo root):  make build   (or, by hand,
#   git archive --format=tar --prefix=repo/ -o deploy/seed-repo.tar HEAD
#   docker build -f deploy/Dockerfile -t jarvis:dev . )
# Bumping it (here and in .gitlab-ci.yml): run scripts/verify/hermes-imports.py against the new tag
# with --record first; a moved private module only fails when a goal reaches it, and
# deploy/tests/test_hermes_imports_lock.py fails CI on a tag the probe has not passed.
ARG HERMES_TAG=v0.23.2
# age, built from its module at the version scripts/install-age.sh pins (T-0758): the release
# binaries are built with a Go whose stdlib has critical fixed CVEs, which build:jarvis's grype gate
# refuses. go install checks the module against sum.golang.org; the next Go fix is this tag.
# age v1.3.2 requires golang.org/x/crypto v0.55.0 (GO-2026-6354/6355 High, GO-2026-5932), and
# `go install pkg@version` cannot raise a dependency, so it is built from a throwaway module that
# requires the commit grype names as 5932's fix (T-0766); sum.golang.org checks that one too.
# Drop the require when an age release carries it.
FROM golang:1.27.3-trixie AS age
COPY scripts/install-age.sh /tmp/install-age.sh
RUN set -eu; v="$(sed -n 's/^AGE_VERSION="\(.*\)"$/\1/p' /tmp/install-age.sh)"; test -n "$v"; \
    mkdir /build && cd /build && go mod init age-build >/dev/null 2>&1; \
    go get "filippo.io/age/cmd/age@$v" "filippo.io/age/cmd/age-keygen@$v" golang.org/x/crypto@v0.58.1-0.20261009181718-2f1f834330b2; \
    CGO_ENABLED=0 GOBIN=/out go install -trimpath filippo.io/age/cmd/age filippo.io/age/cmd/age-keygen; \
    /out/age --version

FROM nousresearch/hermes-agent:${HERMES_TAG}

ARG HERMES_TAG
ARG PIPER_VOICE=en_GB-alan-medium
ARG PIPER_VOICE_PATH=en/en_GB/alan/medium
ARG PIPER_VOICE_SHA256=0a309668932205e762801f1efc2736cd4b0120329622adf62be09e56339d3330
ARG PIPER_VOICE_JSON_SHA256=c0f0d124e5895c00e7c03b35dcc8287f319a6998a365b182deb5c8e752ee8c1e
ARG WHISPER_MODEL=small

USER root
# node and uv are replaced whole from their official images rather than waiting for the base:
# pipeline 640's CVE gate failed on the base's node 26.5.1 (CVE-2026-111497, on CISA's KEV
# list, fixed in 26.12.1) and on the zip 8.1.0 crate compiled into its uv 0.11.6
# (GHSA-m665-v7pm-7grj; uv 0.13 no longer links zip). Only the node binary is copied: npm is
# refreshed just below, and nothing in this image builds native addons against node's headers.
COPY --from=node:26.13.0-trixie-slim /usr/local/bin/node /usr/local/bin/node
COPY --from=ghcr.io/astral-sh/uv:0.13.6 /uv /uvx /usr/local/bin/
# The CVE gate's criticals in the upstream image, and what JARVIS does about them:
# /usr/bin/docker is a Go 1.24 binary this container never uses (no socket is mounted);
# npm's bundled `tar` is refreshed with npm itself (npx serves the MCP servers); the rest
# is Debian's own security updates.
# APT_REFRESH is the day, passed by the build job, and it is read here so this layer's
# cache key changes daily: BuildKit keys a RUN by its text, and the registry cache handed
# back the apt layer from before Debian published libssh2's fix (CVE-2026-7598), so the
# CVE gate failed on an upgrade the Dockerfile already asked for. A daily key means a
# security update lands within a day without rebuilding the layers below it.
ARG APT_REFRESH=unset
RUN set -eux; echo "apt refresh $APT_REFRESH"; rm -f /usr/bin/docker; \
    apt-get update -qq && apt-get -y -qq --no-install-recommends upgrade && rm -rf /var/lib/apt/lists/*; \
    npm install -g npm@latest >/dev/null 2>&1 && npm --version

# The media toolchain, in the image because production is immutable and a runtime
# install is both lost on the next deploy and a hole in the egress allowlist
# (JARVIS_MEDIA_BRIEF Part 5). Audited 2026-09-09: ffmpeg, ffprobe, node and
# python3 were already here from the base image; these seven were not, and their
# absence is what sent the agent off to reinstall a toolchain by hand.
#
# jq is not a media tool: hermes/agent-hooks/activity-log.sh pipes every tool call through
# it into logs/activity.jsonl with stderr to /dev/null, so without it the HUD's activity
# feed and the nightly self-review read an empty file — which they did from 1 Sep to
# 18 Sep 2026 before anyone noticed the hook had never once succeeded.
#
# libreoffice is deliberately NOT installed. The brief says "if the size is
# acceptable — measure": it is roughly 500 MB against a host at 85 % disk, and
# pandoc covers the formats that actually turn up. `toolchain.py` marks it
# optional so its absence is reported rather than treated as a regression.
#
# bubblewrap is not a media tool either: it is the terminal backend (ADR-0045). With
# `terminal.backend: local` every shell command the model ran, and every execute_code,
# ran inside the gateway process with the container's whole environment — every token
# in the host .env — and /opt/data one `cat` away (found 2026-09-18). The aura-bwrap
# plugin wraps each command in `bwrap --unshare-all --clearenv`; it needs this binary
# and the seccomp profile deploy/seccomp/jarvis.json, which lets an unprivileged uid
# unshare namespaces (Docker's default refuses `unshare` outright — probed the same day).
RUN set -eux; \
    apt-get update -qq; \
    apt-get install -y -qq --no-install-recommends \
      imagemagick pandoc poppler-utils qpdf sox libsox-fmt-all jq bubblewrap; \
    rm -rf /var/lib/apt/lists/*; \
    magick -version | head -1; pandoc --version | head -1; jq --version; bwrap --version; \
    pdftoppm -v 2>&1 | head -1; qpdf --version | head -1; sox --version | head -1

# age: jarvis-backup encrypts every backup with it and the Sunday drill decrypts one (T-0568).
# Its version is the one scripts/install-age.sh pins for CI and a dev box; the age stage builds it.
COPY --from=age /out/age /out/age-keygen /usr/local/bin/
RUN age --version
# The drill's read-back (deploy/backup.sh) needs these: fail the build, not the first Sunday.
# PyYAML is installed here because Hermes stopped shipping it in v0.23 (its config moved to
# ruamel.yaml), and aura-integrations and aura-settings import `yaml` too: on a bare v0.23
# base both plugins died on import while /health stayed green (T-1519).
RUN uv pip install --python /opt/hermes/.venv/bin/python --no-cache "PyYAML>=6,<7" \
 && python3 -c "import yaml, sqlite3"

# yt-dlp through pip rather than apt: Debian's package lags by months and a video
# downloader that lags is a video downloader that does not work.
ARG YT_DLP_VERSION=2025.9.5
RUN uv pip install --python /opt/hermes/.venv/bin/python --no-cache "yt-dlp==${YT_DLP_VERSION}" \
 && ln -sf /opt/hermes/.venv/bin/yt-dlp /usr/local/bin/yt-dlp \
 && yt-dlp --version

# Voice extras into Hermes' own venv (root owns /opt/hermes; runtime never writes there).
# av<19: PyAV 19 dropped av.open(metadata_errors=), which faster-whisper 1.2.1 passes on every
# decode, so an image built after its release transcribed nothing ("open() got an unexpected
# keyword argument", a 400 from hermes serve; pipeline 642's smoke). The check decodes a
# clip through faster-whisper itself, so the next such break fails this layer, not the smoke.
RUN uv pip install --python /opt/hermes/.venv/bin/python --no-cache \
      "faster-whisper>=1.2" "av<19" "piper-tts>=1.4" "huggingface_hub>=0.34" \
 && /opt/hermes/.venv/bin/python -c "import wave, piper; from faster_whisper.audio import decode_audio; \
w = wave.open('/tmp/v.wav', 'wb'); w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); \
w.writeframes(bytes(3200)); w.close(); assert len(decode_audio('/tmp/v.wav')) == 1600; print('voice extras ok')" \
 && rm -f /tmp/v.wav

# Piper voice, downloaded at build time and checksum-verified.
RUN set -eux; mkdir -p /opt/jarvis/seed/cache/piper-voices; cd /opt/jarvis/seed/cache/piper-voices; \
    base="https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/${PIPER_VOICE_PATH}"; \
    curl -fsSL --retry 5 --retry-delay 5 -o "${PIPER_VOICE}.onnx" "${base}/${PIPER_VOICE}.onnx"; \
    curl -fsSL --retry 5 --retry-delay 5 -o "${PIPER_VOICE}.onnx.json" "${base}/${PIPER_VOICE}.onnx.json"; \
    echo "${PIPER_VOICE_SHA256}  ${PIPER_VOICE}.onnx" | sha256sum -c -; \
    echo "${PIPER_VOICE_JSON_SHA256}  ${PIPER_VOICE}.onnx.json" | sha256sum -c -

# Whisper weights (faster-whisper's CTranslate2 conversion) into a seed HF cache.
RUN set -eux; mkdir -p /opt/jarvis/seed/cache/hf; \
    HF_HOME=/opt/jarvis/seed/cache/hf /opt/hermes/.venv/bin/python -c \
      "from huggingface_hub import snapshot_download; p=snapshot_download('Systran/faster-whisper-${WHISPER_MODEL}'); print(p)"; \
    find /opt/jarvis/seed/cache/hf -name '*.lock' -delete; \
    chmod -R a+rX /opt/jarvis/seed/cache

# The one settings schema (V2 D17): the plugins read their configuration as
# attributes of aura_schema.from_env(), so the package must be importable from
# the Hermes venv — pure Python on pydantic, which Hermes already ships.
COPY aura-schema /opt/jarvis/seed/aura-schema
RUN uv pip install --python /opt/hermes/.venv/bin/python --no-cache /opt/jarvis/seed/aura-schema \
 && /opt/hermes/.venv/bin/python -c "import aura_schema; print('aura-schema ok', len(aura_schema.SETTINGS))"

# Document reading. Hermes bundles a whole `productivity/pdf` skill, and every one of its
# read scripts died in this image on `Missing dependency: install with 'python3 -m pip
# install pdfplumber'` — the base image ships the skill without the wheels it imports. The
# advice in that message cannot be followed at runtime either: root owns /opt/hermes and the
# container runs as `hermes`, so the agent is told to fix it in the one directory it may not
# write. Asked for a figure out of a PDF it therefore fell back to `terminal`, checksummed
# the bytes and answered about the checksum (bench pdf-read-004). Pinned exactly, because
# "the extractor changed under us" is the class of benchmark regression nobody looks for.
# pdfplumber pulls pdfminer.six, Pillow and pypdfium2; pypdf is the same skill's write path.
# The `docx` and `xlsx` skills were dead the same way and cost about a megabyte between them,
# so reading a document means reading a document rather than reading a PDF. python-pptx
# joined them for the making-documents skill (T-0826): python-docx, openpyxl and python-pptx
# write the three Office formats the owner asks for, and python-pptx adds about 2 MB unpacked
# (itself and XlsxWriter, its chart dependency; lxml, Pillow and typing_extensions were here
# already). Deliberately not added: reportlab, pymupdf and marker-pdf — PDF creating and OCR
# are hundreds of megabytes for capabilities nothing in this repo asks for yet, and the skill
# says plainly which half works so nobody discovers the other half at 3am.
# Pillow is named, not left to pdfplumber's dependency tree: the aura-desktop plugin draws
# the coordinate grid on every screenshot with it, and a plugin that silently saves gridless
# pictures the day pdfplumber drops the dependency is the failure this line prevents.
RUN uv pip install --python /opt/hermes/.venv/bin/python --no-cache \
      "pdfplumber==0.11.7" "pypdf==6.1.1" "python-docx==1.2.0" "openpyxl==3.1.5" "python-pptx==1.0.2" "pillow>=10.1" \
 && /opt/hermes/.venv/bin/python -c "import pdfplumber, pypdf, docx, openpyxl, pptx, PIL; print('document extras ok')"

# anyio past GHSA-82r6-8w77-94w6 (critical, fixed in 4.14.2), and pyjwt past
# GHSA-ffc3-869f-jxw9 (critical, fixed in 2.14.0), which Hermes' own release pins below:
# the CVE gate stopped pipelines 369 and 489 on them. Raised here rather than waiting for
# the upstream image, and the uv download cache an earlier layer left under /root goes in
# the same step -- the scanner reads every dist-info on the filesystem, and that cache
# carried a second copy of the vulnerable version. Pipeline 640 added pillow (five critical
# GHSAs, fixed in 13.0.0: the document extras' "pillow>=10.1" was already satisfied by the
# base's 12.3.0, so nothing raised it), certifi (GHSA-6q48-6c7w-9gw2, 2026.10.18) and
# brotlicffi (GHSA-9v8h-qqgm-4m35, 1.2.1.0).
RUN uv pip install --python /opt/hermes/.venv/bin/python --no-cache "anyio>=4.14.2" "pyjwt>=2.14.0" \
      "pillow>=13.0.0" "certifi>=2026.10.18" "brotlicffi>=1.2.1.0" \
 && rm -rf /root/.cache/uv \
 && /opt/hermes/.venv/bin/python -c "import anyio, importlib.metadata as m; v = m.version('anyio'); assert tuple(map(int, v.split('.')[:2])) >= (4, 14), v; print('anyio', v)" \
 && /opt/hermes/.venv/bin/python -c "import jwt, importlib.metadata as m; v = m.version('pyjwt'); assert tuple(map(int, v.split('.')[:2])) >= (2, 14), v; print('pyjwt', v)"

# The self-extension loop's own gate (D24). selfext_verify runs ruff, pytest, mypy (through
# scripts/mypy-plugins.sh) and vulture inside its checkout with the `python3` on PATH, which
# is this venv's, and the base image ships none of them: on production every check answered
# `No module named ruff` before reading a line of the plugin it was asked about. Pinned to
# what the pipeline pins (test:lint's ruff, py-mypy's mypy, py-dead's vulture;
# deploy/tests/test_selfext_seed.py holds the two equal), because a self-check on another
# checker version than the merge request's gate is what let !2 be pronounced ready and then
# fail py-mypy. pytest is pinned to the dev venv's versions; the pipeline leaves it floating.
RUN uv pip install --python /opt/hermes/.venv/bin/python --no-cache \
      "ruff==0.16.3" "mypy==2.3.1" "vulture==2.16" \
      "pytest==9.1.1" "pytest-asyncio==1.4.0" "pytest-timeout==2.4.0" \
 && /opt/hermes/.venv/bin/python -m ruff --version && /opt/hermes/.venv/bin/mypy --version \
 && /opt/hermes/.venv/bin/vulture --version && /opt/hermes/.venv/bin/python -m pytest --version

# JARVIS assets: plugins, skills, hooks, memory seeds, config template, scripts.
COPY hermes/plugins /opt/jarvis/seed/plugins
# Home Assistant left Hermes core in v0.23 for the official catalog plugin `homeassistant` (the
# same ha_* tools and HASS_TOKEN; T-1519). Hermes would install it on first start, but
# security.allow_lazy_installs is off here, so without this AURA loses its house tools. Installed
# at the catalog's reviewed pin for this Hermes, into a scratch home, and seeded like ours: the
# entrypoint copies it into the volume and enables it (it has a plugin.yaml).
RUN set -eux; home="$(mktemp -d)"; \
    HERMES_HOME="$home" /opt/hermes/.venv/bin/hermes plugins install homeassistant --yes-deps --no-enable; \
    test -f "$home/plugins/homeassistant/plugin.yaml"; \
    rm -rf "$home/plugins/homeassistant/tests" "$home/plugins/homeassistant/.git"; \
    cp -r "$home/plugins/homeassistant" /opt/jarvis/seed/plugins/homeassistant; rm -rf "$home"
COPY hermes/skills /opt/jarvis/seed/skills
COPY hermes/agent-hooks /opt/jarvis/seed/agent-hooks
COPY hermes/home /opt/jarvis/seed/home
COPY hermes/cron /opt/jarvis/seed/cron
COPY deploy/config.template.yaml /opt/jarvis/seed/config.template.yaml
COPY deploy/entrypoint.sh /opt/jarvis/bin/jarvis-entrypoint
COPY deploy/memory_health.py /opt/jarvis/bin/memory_health.py
# Same script as an s6 cont-init step: seeds the volume before any service starts.
COPY deploy/entrypoint.sh /etc/cont-init.d/03-jarvis-seed
COPY deploy/backup.sh /opt/jarvis/bin/jarvis-backup
# The scripts library (JARVIS_MEDIA_BRIEF Part 5): on PATH by name, because the
# media-fetch skill said `scripts/media/fetch-video.sh` and nothing had ever
# copied it into the image -- JARVIS answered "fetch-video.sh is missing" to a
# request to play a video (the owner's transcript, 2026-09-09). One directory per
# area, every `*.sh` in them linked by name; each area's test pins its own COPY line.
COPY scripts/media /opt/jarvis/bin/media
COPY scripts/document /opt/jarvis/bin/document
COPY scripts/image /opt/jarvis/bin/image
COPY scripts/audio /opt/jarvis/bin/audio
# The ownership pass skips seed/cache: chown and chmod copy every file they touch into this
# layer, and the Whisper and Piper weights (~550 MB) were stored twice. The cache is made
# readable in the RUN that downloads it.
RUN rm -rf /opt/jarvis/seed/plugins/*/tests /opt/jarvis/seed/plugins/_plugin_loader.py \
 && find /opt/jarvis/seed -name __pycache__ -type d -prune -exec rm -rf {} + \
 && chmod 0755 /opt/jarvis/bin/* /opt/jarvis/seed/agent-hooks/* /etc/cont-init.d/03-jarvis-seed \
 && find /opt/jarvis -path /opt/jarvis/seed/cache -prune -o -exec chown root:root {} + -exec chmod a+rX {} + \
 && ln -s /opt/jarvis/bin/jarvis-entrypoint /usr/local/bin/jarvis-entrypoint \
 && ln -s /opt/jarvis/bin/jarvis-backup /usr/local/bin/jarvis-backup \
 && chmod 0755 /opt/jarvis/bin/*/*.sh \
 && for f in /opt/jarvis/bin/*/*.sh; do ln -s "$f" "/usr/local/bin/$(basename "$f")"; done

# The repository itself, for the self-extension loop (D24): jarvis-entrypoint copies
# /opt/jarvis/seed/repo into the volume on the first start that finds no checkout, and the
# selfext_* tools stay hidden without one. It is a `git archive` of the commit being built:
# the build context carries no .git (.dockerignore keeps it to the seed assets), so the
# build:jarvis job and `make build` write deploy/seed-repo.tar right before the build and
# ADD unpacks it (the tar's own `repo/` prefix makes the directory). A build without the tar
# fails here, on purpose, rather than shipping a gateway whose loop is silently invisible.
# After the chown pass above and last of the seed steps: the tree changes with every commit,
# and everything above it is cache. Not chowned or chmodded: git archive writes root-owned
# 644/755 entries, and the entrypoint gives the copy to hermes.
ADD deploy/seed-repo.tar /opt/jarvis/seed/

# GET /v1/skills was a 500 on every call: the pinned api_server passes include_editorial=
# to tools.skills_tool.find_all_skills (_find_all_skills before v0.23), which takes only
# skip_disabled (the console's skills list polled it all day; still so in v0.23.2). Dropped
# only while the signature lacks the keyword, so a base that fixes either side leaves this a
# no-op; a base that renames the function again fails the build here.
# HERMES_HOME is a scratch directory: importing tools.skills_tool creates Hermes' home dirs
# (hooks, sessions, caches) as root 0700, and in /opt/data they were baked into the image,
# copied into every NEW volume, and the gateway (uid hermes) died on PermissionError at
# /opt/data/hooks before the API server came up (integration:smoke red from pipeline 543;
# production's older volume never saw them).
RUN cd /opt/hermes && export HERMES_HOME="$(mktemp -d)" && /opt/hermes/.venv/bin/python -c "\
import inspect, pathlib; \
from tools.skills_tool import find_all_skills; \
api = pathlib.Path('gateway/platforms/api_server.py'); \
'include_editorial' in inspect.signature(find_all_skills).parameters or \
api.write_text(api.read_text().replace(', include_editorial=True', '')); \
assert 'include_editorial' in inspect.signature(find_all_skills).parameters or 'include_editorial=True' not in api.read_text(), 'the /v1/skills patch no longer applies'" \
 && rm -rf "$HERMES_HOME"

# OpenRouter's public model list stays out of the house (T-0839). Hermes reads it for
# reasoning limits whenever a model picker is built, OpenRouter key or not: the console's
# chat asks for its model list on load, and the T-0767 capture saw openrouter.ai from this
# container. The URL is a constant with no config key, and this house runs no OpenRouter
# model, so it goes to a closed loopback port: the fetch fails at once, and Hermes treats
# that as "unknown" and uses its built-in list. A base whose line moved fails the build
# here instead of calling out again.
RUN f=/opt/hermes/hermes_cli/models_reasoning_caps.py \
 && grep -q '^_OPENROUTER_CATALOG_URL = "https://openrouter.ai/api/v1/models"$' "$f" \
 && sed -i 's#^_OPENROUTER_CATALOG_URL = .*#_OPENROUTER_CATALOG_URL = "http://127.0.0.1:9/openrouter-is-off"#' "$f" \
 && /opt/hermes/.venv/bin/python -c "import sys; sys.path.insert(0, '/opt/hermes'); from hermes_cli import models_reasoning_caps as m; assert m._OPENROUTER_CAPS.url() == 'http://127.0.0.1:9/openrouter-is-off', m._OPENROUTER_CAPS"

# A browser that is actually here (V2 §4, D18). Since v0.23 the model's one browser tool,
# browser_exec, drives the Chromium the Hermes image bakes (its path is in
# /etc/hermes/chromium-executable-path), so this image installs none of its own: the
# agent-browser CLI, its Chrome and the per-session profile shim (T-0795) went with the ten
# browser_* tools they served. Hermes attaches a second session to the Chromium already on
# the shared profile, which is what the shim did by hand.
#
# Prove it launches, as the user that will launch it: a missing shared library is otherwise
# invisible until an agent asks for a page in production. --no-sandbox because Chrome's
# sandbox cannot start in this container (Hermes adds the same flags to its packaged
# Chromium, tools/browser_launcher.py CHROMIUM_SANDBOX_BYPASS_ARGS).
#
# XDG_RUNTIME_DIR=/tmp/hermes-runtime ships root:root 0700 in the base, and a runtime socket
# made there as `hermes` fails "Permission denied"; it is handed to hermes here.
RUN install -d -o hermes -g hermes -m 0700 /tmp/hermes-runtime
RUN set -eux; \
    chrome="$(cat /etc/hermes/chromium-executable-path)"; \
    test -x "$chrome"; \
    su -s /bin/sh -c "HOME=/tmp '$chrome' --headless=new --no-sandbox --disable-dev-shm-usage \
      --user-data-dir=/tmp/build-check-profile --dump-dom 'data:text/html,<p>ok</p>'" hermes \
      | grep -q '<p>ok</p>'; \
    rm -rf /tmp/build-check-profile

# The Whisper weights are baked (seeded into the volume on first start); faster-whisper
# must not consult huggingface.co at load time — inside the container that call hangs
# behind the exit node. Another WHISPER_MODEL therefore needs a rebuild with the ARG.
ENV HERMES_DISABLE_LAZY_INSTALLS=1 \
    HF_HOME=/opt/data/cache/hf \
    HF_HUB_OFFLINE=1

LABEL org.opencontainers.image.title="jarvis" \
      org.opencontainers.image.description="JARVIS on Hermes Agent ${HERMES_TAG}: local voice, homelab plugins, HUD backend"

EXPOSE 8642 9119
# Root here on purpose: s6-overlay's /init needs PID 1 as root to chown the volume,
# then drops every service AND the main program to uid 10000 (hermes).
CMD ["jarvis-entrypoint", "gateway", "run"]
