#!/bin/bash
# net.sh CONTAINER NETWORK IP IFNAME -- T4a test harness: attach a container to a docker network with a FIXED interface
# name. Docker does not keep interface order across a restart (seen in T4a: a node's eth0 and eth1 swapped on its
# "reboot" and its LAN profile landed on the uplink), so every endpoint is named explicitly.
set -eu
docker network connect --ip "$3" --driver-opt "com.docker.network.endpoint.ifname=$4" "$2" "$1"
