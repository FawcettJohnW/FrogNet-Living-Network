#!/bin/bash
# cable.sh CONTAINER DOCKER_NETWORK IFNAME -- sandbox only: plug a cable from the container into a docker network's
# bridge WITHOUT docker knowing (a veth pair, one end in the container). Docker then assigns no address: whatever the
# machine gets on that interface, it gets from the DHCP server on that LAN, as a real machine would.
set -eu
C=$1; N=$2; IFN=$3
BR="br-$(docker network inspect -f '{{.Id}}' "$N" | cut -c1-12)"
PID=$(docker inspect -f '{{.State.Pid}}' "$C")
H="v${C}${IFN}"; ip link del "$H" 2>/dev/null || true
ip link add "$H" type veth peer name "${H}p"
ip link set "$H" master "$BR" up
ip link set "${H}p" netns "$PID" name "$IFN"
nsenter -t "$PID" -n ip link set "$IFN" up
echo "cable: $C:$IFN <-> $N ($BR)"
