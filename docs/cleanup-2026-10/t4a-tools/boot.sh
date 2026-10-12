#!/bin/bash
# boot.sh NAME [extra docker run args...] -- T4a test harness (sandbox only, not shipped).
# Interfaces: give the uplink as  --network name=fn-wan,ip=X,driver-opt=com.docker.network.endpoint.ifname=eth0  and
# attach LANs with net.sh (fixed names: docker reorders unnamed interfaces on restart).
# FN_HOSTNAME=... sets the container's hostname (docker restores it at every start, so a node is given the name
# its install will write, FrogNetHost.<NAME>, or every "reboot" would rename it).
# Boots a clean Ubuntu 24.04 systemd container from frognet-clean:noble with the repo at /repo (read-only), then makes
# it look like a machine rather than a container:
#   - the sandbox's HTTPS proxy CA is trusted (pip goes through a TLS-intercepting proxy here; a real machine does not
#     need this, and the installer is not changed for it);
#   - docker's bind-mounted /etc/resolv.conf, /etc/hosts, /etc/hostname are replaced by ordinary files with the same
#     content (a machine's are files; the installer and the reset rewrite them).
set -eu
N=${1:?usage: boot.sh NAME [docker run args]}; shift
docker rm -f "$N" >/dev/null 2>&1 || true
docker run -d --name "$N" --hostname "${FN_HOSTNAME:-$N}" --privileged --cgroupns=host -v /sys/fs/cgroup:/sys/fs/cgroup:rw \
  --tmpfs /run --tmpfs /run/lock -e container=docker -v /home/claude/clean:/repo:ro "$@" frognet-clean:noble /sbin/init >/dev/null
for c in /usr/local/share/ca-certificates/*.crt; do docker cp "$c" "$N":/usr/local/share/ca-certificates/; done
docker exec "$N" bash -c '
  for f in resolv.conf hosts hostname; do cp /etc/$f /tmp/$f.keep; umount /etc/$f; cat /tmp/$f.keep > /etc/$f; done
  # the image ships an older ca-certificates; the installer'"'"'s apt run upgrades it and the upgrade drops the sandbox
  # CAs from the bundle (seen in T4a), so upgrade it here first, then add them
  DEBIAN_FRONTEND=noninteractive apt-get update -qq >/dev/null 2>&1; DEBIAN_FRONTEND=noninteractive apt-get install -y -qq ca-certificates >/dev/null 2>&1
  update-ca-certificates --fresh >/dev/null 2>&1
  printf "[global]\ncert = /etc/ssl/certs/ca-certificates.crt\n" > /etc/pip.conf
  for i in $(seq 1 20); do systemctl is-system-running 2>/dev/null | grep -qE "running|degraded" && break; sleep 0.5; done
  echo "booted $(hostname): $(systemctl is-system-running 2>/dev/null); $(ip -o -4 addr | awk "{print \$2\"=\"\$4}" | tr "\n" " ")"'
