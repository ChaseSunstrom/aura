# The clean machine of scripts/verify/install-clean.sh (T-0864): Ubuntu as it ships, plus what
# the one-line install asks a machine to have: Docker Engine with compose and buildx, curl, git.
# No python3, no node, no source: whatever else the install needs it must bring itself.
#
# It is run privileged with its own Docker daemon and its own /var/lib/docker volume, so nothing
# it builds or starts is on the host's daemon. Not a hardened image and never a deployed one.
FROM ubuntu:24.04

RUN apt-get update \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      ca-certificates curl git iptables docker.io docker-compose-v2 docker-buildx \
 && rm -rf /var/lib/apt/lists/*

COPY machine-init.sh /usr/local/bin/machine-init
RUN chmod 0755 /usr/local/bin/machine-init

ENTRYPOINT ["/usr/local/bin/machine-init"]
