#!/usr/bin/env bash
# scripts/setup-runner.sh — a GitLab Runner for this repo as a Docker container (no root
# needed beyond membership in the docker group), docker executor, tag `jarvis`, with the
# host's /var/run/docker.sock mounted into jobs for buildx (no privileged DinD).
#
#   GITLAB_URL=https://gitlab.home.example RUNNER_TOKEN=glrt-… scripts/setup-runner.sh
#
# Check first whether a runner already exists (Settings → CI/CD → Runners); if one with
# the `jarvis` tag and docker.sock access is online, reuse it and skip this.
# The registry (registry.home.example) is HTTPS with a valid certificate, so the
# runner's Docker daemon needs no insecure-registries entry for it.
set -euo pipefail
GITLAB_URL="${GITLAB_URL:?set GITLAB_URL to the house GitLab, e.g. https://gitlab.home.example}"
TOKEN="${RUNNER_TOKEN:?RUNNER_TOKEN (the runner authentication token from GitLab)}"
NAME="${RUNNER_NAME:-jarvis-dev-lxc}"
CONF="${RUNNER_CONFIG_DIR:-$HOME/.gitlab-runner}"
IMAGE="${RUNNER_IMAGE:-gitlab/gitlab-runner:v18.4.0}"
mkdir -p "$CONF"

if docker ps -a --format '{{.Names}}' | grep -qx gitlab-runner; then
  # A container an older copy of this script made may still carry --dns 100.100.100.100,
  # which a restart keeps and which answers nothing without Tailscale: recreate it.
  if docker inspect -f '{{.HostConfig.Dns}}' gitlab-runner | grep -q 100.100.100.100; then
    echo "gitlab-runner still pins the retired MagicDNS resolver: docker rm -f gitlab-runner and rerun" >&2; exit 1
  fi
  echo "container gitlab-runner already exists — starting it"; docker start gitlab-runner >/dev/null
else
  # No --dns pin. Until 2026-10-27 it was pinned to MagicDNS (100.100.100.100) because
  # the container copied a pre-tailscaled resolv.conf at boot and could not resolve the
  # tailnet GitLab; with Tailscale gone that address answers nothing, and the house's
  # names (gitlab.home.example) resolve through the host's own resolver.
  docker run -d --name gitlab-runner --restart unless-stopped \
    -v "$CONF":/etc/gitlab-runner -v /var/run/docker.sock:/var/run/docker.sock "$IMAGE" >/dev/null
fi

if ! grep -q "$GITLAB_URL" "$CONF/config.toml" 2>/dev/null; then
  docker exec gitlab-runner gitlab-runner register --non-interactive \
    --url "$GITLAB_URL" --token "$TOKEN" --name "$NAME" \
    --executor docker --docker-image docker:27-cli \
    --docker-volumes /var/run/docker.sock:/var/run/docker.sock \
    --docker-volumes /cache \
    --docker-network-mode host \
    --docker-pull-policy if-not-present --docker-pull-policy always
  docker exec gitlab-runner gitlab-runner verify
fi
# The config is written by the container as root, so edit it from inside: one job at a
# time on this host, and images re-pulled only when missing (the mirror is slow).
docker exec gitlab-runner sed -i -e 's/^concurrent = .*/concurrent = 1/' \
  -e 's/^    pull_policy = .*/    pull_policy = ["if-not-present"]/' /etc/gitlab-runner/config.toml
docker restart gitlab-runner >/dev/null
docker exec gitlab-runner gitlab-runner list
