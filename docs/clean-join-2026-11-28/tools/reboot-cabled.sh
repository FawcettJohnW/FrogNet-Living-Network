#!/bin/bash
# reboot-cabled.sh CONTAINER [NETWORK IFNAME ...] -- sandbox only: the October reboot.sh, then each cable plugged back
# in (a container restart makes a new network namespace, so the veth is gone, as if the cable had been pulled).
set -eu
C=$1; shift
docker restart -t 30 "$C" >/dev/null
docker exec "$C" bash -c 'for f in resolv.conf hosts hostname; do umount /etc/$f 2>/dev/null; done; hostname "$(cat /etc/hostname)"'
while [ $# -ge 2 ]; do "$(dirname "$0")/cable.sh" "$C" "$1" "$2"; shift 2; done
echo "rebooted $C at $(date -u +%H:%M:%S)"
