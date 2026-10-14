#!/bin/bash
# reboot.sh CONTAINER -- T4a test harness: reboot a node container (systemd stops everything, the container starts
# again, systemd boots). Docker re-bind-mounts its own /etc/resolv.conf, /etc/hosts and /etc/hostname at every start;
# they are unmounted again at once so the node sees the files its install and its merge wrote, as a machine would.
set -eu
C=${1:?usage: reboot.sh CONTAINER}
docker restart -t 30 "$C" >/dev/null
docker exec "$C" bash -c 'for f in resolv.conf hosts hostname; do umount /etc/$f 2>/dev/null; done; hostname "$(cat /etc/hostname)"
  sleep 10   # (frognet-discovered.service holds "starting" until the first merge; do not wait for it)
  echo "rebooted $(hostname): $(systemctl is-system-running 2>/dev/null)"'
