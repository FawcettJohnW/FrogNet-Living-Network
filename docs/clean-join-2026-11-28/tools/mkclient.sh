#!/bin/bash
# mkclient.sh CONTAINER HOSTNAME OWN_NET:OWN_IP:OWN_IF PARENT_NET PARENT_IF PARENT_DOT1 TEMP_IP -- sandbox only: a clean
# machine whose ONLY way out is a cable into a FrogNet node's LAN. It is given a DHCP client the way a stock machine
# has one (NetworkManager managing its ethernet, as Raspberry Pi OS does), then takes its address from the node's DHCP.
set -eu
C=$1; HN=$2; IFS=: read -r ON OIP OIF <<<"$3"; PN=$4; PIF=$5; P1=$6; TMP=$7
T=/home/claude/clean/docs/cleanup-2026-10/t4a-tools
FN_HOSTNAME=$HN bash $T/boot.sh "$C" --network "name=$ON,ip=$OIP,driver-opt=com.docker.network.endpoint.ifname=$OIF" | tail -1
"$(dirname "$0")/cable.sh" "$C" "$PN" "$PIF"
docker cp /root/.ccr/ca-bundle.crt "$C":/etc/ssl/ccr-bundle.crt
docker exec "$C" bash -c "
ip addr add $TMP/24 dev $PIF; ip route add default via $P1; echo 'nameserver $P1' > /etc/resolv.conf
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq >/dev/null 2>&1; apt-get install -y -qq ca-certificates network-manager >/dev/null 2>&1; echo \"apt rc=\$?\"
update-ca-certificates --fresh >/dev/null 2>&1
printf '[global]\ncert = /etc/ssl/ccr-bundle.crt\nproxy = http://172.30.0.1:41789\n' > /etc/pip.conf
: > /etc/NetworkManager/conf.d/10-globally-managed-devices.conf
ip route del default; ip addr flush dev $PIF
systemctl restart NetworkManager; sleep 3
nmcli con add type ethernet ifname $PIF con-name uplink ipv4.method auto ipv6.method disabled >/dev/null; nmcli con up uplink >/dev/null 2>&1
sleep 4; ip -4 -o addr show dev $PIF | awk '{print \$2, \$4}'; ip -4 route show default"
bash $T/put-tree.sh "$C" | tail -1; docker cp $T/inst.sh "$C":/root/inst.sh
