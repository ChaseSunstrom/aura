#!/bin/sh
# PID 1 of the proof's clean machine (scripts/verify/install-clean.sh): have Docker Engine
# running, as a booted machine with Docker installed does. A container has no init system to
# start it, so this does.
set -eu

# cgroup v2 lets a group hold either processes or child groups with controllers, not both. This
# container's own processes move into a child group so the daemon can make groups beside it
# (the same step Docker's own docker-in-docker image takes).
if [ -f /sys/fs/cgroup/cgroup.controllers ]; then
  mkdir -p /sys/fs/cgroup/init
  xargs -rn1 < /sys/fs/cgroup/cgroup.procs > /sys/fs/cgroup/init/cgroup.procs 2>/dev/null || :
  sed -e 's/ / +/g' -e 's/^/+/' < /sys/fs/cgroup/cgroup.controllers > /sys/fs/cgroup/cgroup.subtree_control
fi

# A container's resolver is a loopback address only this container can reach, which its own
# containers cannot use. A real machine's resolver is on its network; the proof hands one in.
if [ -n "${MACHINE_DNS:-}" ]; then
  mkdir -p /etc/docker
  printf '{"dns": ["%s"]}\n' "$MACHINE_DNS" > /etc/docker/daemon.json
fi

exec dockerd > /var/log/dockerd.log 2>&1
