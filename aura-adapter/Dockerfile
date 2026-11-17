# jarvis-adapter — one process, no secrets in layers (they arrive as env at runtime).
FROM python:3.12.16-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
# Debian's security updates at build time: the CI CVE gate (grype, --fail-on critical
# --only-fixed) found OpenSSL and GnuTLS criticals in the base image that bookworm had
# already fixed (pipeline 14).
# APT_REFRESH is the day, passed by the build job, and it is read here so this layer's
# cache key changes daily: BuildKit keys a RUN by its text, and the registry cache handed
# back the apt layer from before Debian published libssh2's fix (CVE-2026-7598), so the
# CVE gate failed on an upgrade the Dockerfile already asked for. A daily key means a
# security update lands within a day without rebuilding the layers below it.
ARG APT_REFRESH=unset
RUN echo "apt refresh $APT_REFRESH" && apt-get update -qq && apt-get -y -qq --no-install-recommends upgrade && rm -rf /var/lib/apt/lists/*
# gid 10000 is the gateway's group (hermes): the settings overrides file on the shared volume
# is written 0640 by that group so this uid can read it and no other can (ADR-0044).
RUN groupadd -g 10001 adapter && groupadd -g 10000 hermes \
 && useradd -u 10001 -g adapter -G hermes -m -s /usr/sbin/nologin adapter
WORKDIR /app
# Built from the repo root (.gitlab-ci.yml build:adapter CONTEXT: ., Makefile `build`) so
# the one settings schema the adapter reads its configuration from is installed too.
COPY aura-adapter/requirements.txt ./
COPY aura-schema ./aura-schema
RUN pip install --no-cache-dir -r requirements.txt ./aura-schema && rm -rf ./aura-schema
COPY aura-adapter/aura_adapter ./aura_adapter
# The artifact store's mountpoint, owned by the adapter: a fresh named volume
# takes the image directory's owner, and without this it is root's and every
# publish is an EACCES 507 from uid 10001.
RUN mkdir -p /opt/artifacts /opt/adapter-state && chown adapter:adapter /opt/artifacts /opt/adapter-state
USER adapter
EXPOSE 8643
CMD ["python", "-m", "aura_adapter"]
