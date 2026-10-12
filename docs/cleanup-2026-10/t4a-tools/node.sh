#!/bin/bash
# node.sh CONTAINER HOSTNAME WAN_IP [NETWORK:IP:IFNAME ...] -- T4a harness: boot a node container with its uplink on
# fn-wan as eth0 and each LAN given, all interface names fixed; copy the working tree to /root/FrogNet and inst.sh.
set -eu
C=$1; HN=$2; W=$3; shift 3
FN_HOSTNAME=$HN bash "$(dirname "$0")/boot.sh" "$C" --network "name=fn-wan,ip=$W,driver-opt=com.docker.network.endpoint.ifname=eth0"
for l in "$@"; do IFS=: read -r n ip ifn <<<"$l"; bash "$(dirname "$0")/net.sh" "$C" "$n" "$ip" "$ifn"; done
bash "$(dirname "$0")/put-tree.sh" "$C"
docker cp "$(dirname "$0")/inst.sh" "$C":/root/inst.sh
docker exec "$C" bash -c 'ip -o -4 addr | awk "{print \$2\"=\"\$4}" | tr "\n" " "; echo'
