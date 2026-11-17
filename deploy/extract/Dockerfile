# jarvis-extract — local article extraction for the JARVIS stack (V2 §4, D19).
#
# One stdlib HTTP server and Trafilatura. No browser, no model, no GPU: the whole
# image is the Python base plus lxml and a handful of pure-Python packages, which
# is the reason this backend was chosen over self-hosted Firecrawl (four services
# and a Playwright browser) and Crawl4AI (4.23 GB).
#
# Build (from the repo root):  docker build -f deploy/extract/Dockerfile -t jarvis-extract:dev .
#
# The base is pinned by digest, not by tag: `python:3.12-slim-bookworm` moves
# every few weeks and a benchmark number is only comparable against a fixed image.
# (python:3.12.16-slim-bookworm; a Critical CVE in 3.12.15 failed the CI image scan.)
FROM python@sha256:e697c81897302a4c4333bade000d98038fbb21eb064f8ee1fd42d02ebcd9d337

# curl for the compose healthcheck only — the slim base has neither curl nor wget,
# and a healthcheck that shells out to python pays an interpreter start every 30s.
# APT_REFRESH is the day, passed by the build job, and it is read here so this layer's
# cache key changes daily: BuildKit keys a RUN by its text, and the registry cache handed
# back the apt layer from before Debian published libssh2's fix (CVE-2026-7598), so the
# CVE gate failed on an upgrade the Dockerfile already asked for. A daily key means a
# security update lands within a day without rebuilding the layers below it.
ARG APT_REFRESH=unset
RUN set -eux; echo "apt refresh $APT_REFRESH"; \
    apt-get update -qq; \
    apt-get -y -qq --no-install-recommends upgrade; \
    apt-get install -y -qq --no-install-recommends curl; \
    rm -rf /var/lib/apt/lists/*

WORKDIR /opt/extract
COPY deploy/extract/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt \
 && python -c "import trafilatura; print('trafilatura', trafilatura.__version__)"

COPY deploy/extract/service.py ./service.py

# Unprivileged, and nothing in the image is writable by it: this process parses
# hostile HTML for a living, so a bug in lxml must not land as root with a
# writable filesystem. (The compose file adds no-new-privileges and drops caps.)
RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin extract \
 && chown -R root:root /opt/extract && chmod -R a+rX /opt/extract
USER 10001

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    EXTRACT_BIND=0.0.0.0 \
    EXTRACT_PORT=8896

EXPOSE 8896
LABEL org.opencontainers.image.title="jarvis-extract" \
      org.opencontainers.image.description="Local URL/HTML → article text for JARVIS (Trafilatura)"

CMD ["python", "service.py"]
