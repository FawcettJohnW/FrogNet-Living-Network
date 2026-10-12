#!/bin/bash
# nat-agent-check.sh -- [NAT_IS_MEASURED_V1] (DESIGN-NAT.md step 2) an agent behind NAT finds out and states "nat=<seen>"
# in its own record; an agent outside states nothing of the kind. Same topology as nat-seen-check.sh.
set -u; H=$(cd "$(dirname "$0")/.." && pwd); B=$H/build/bin; API=/FawcettInnovations.RINA.ram_interface.php
cleanup() { [ -n "${HP:-}" ] && kill $HP 2>/dev/null; for n in ny nb nx; do ip netns delete $n 2>/dev/null; done; rm -f ${F:-}; }; cleanup; trap cleanup EXIT
for n in ny nb nx; do ip netns add $n; ip netns exec $n ip link set lo up; done
ip link add yv type veth peer name byv; ip link set yv netns ny; ip link set byv netns nb
ip link add xv type veth peer name bxv; ip link set xv netns nx; ip link set bxv netns nb
ip netns exec ny ip addr add 10.20.0.2/24 dev yv; ip netns exec ny ip link set yv up; ip netns exec ny ip route add default via 10.20.0.1
ip netns exec nb ip addr add 10.20.0.1/24 dev byv; ip netns exec nb ip link set byv up
ip netns exec nb ip addr add 10.30.0.1/24 dev bxv; ip netns exec nb ip link set bxv up
ip netns exec nx ip addr add 10.30.0.2/24 dev xv; ip netns exec nx ip link set xv up
ip netns exec nb sysctl -qw net.ipv4.ip_forward=1; ip netns exec nb iptables -t nat -A POSTROUTING -o bxv -j MASQUERADE
F=$(mktemp -u /tmp/nat-seen.XXXX.perm)
ip netns exec nx env RINA_REGION_FILE=$F $B/rina-host --listen 10.30.0.2:8840 --api $API > /tmp/nat-seen.host.log 2>&1 & HP=$!
for i in $(seq 100); do ip netns exec nx bash -c "(exec 3<>/dev/tcp/10.30.0.2/8840)" 2>/dev/null && break; sleep 0.05; done
for n in ny nx; do ip netns exec $n env RINA_RAM=10.30.0.2:8840 RINA_RAM_NETNS=/var/run/netns/$n RINA_AGENT_LOG=/tmp RINA_REGION_FILE=$F $B/rlite-uipcps -d || echo "agent in $n failed"; done
sleep 0.5
ROWS=$(ip netns exec nx env RINA_RAM=10.30.0.2:8840 RINA_RAM_NETNS=/var/run/netns/nx $B/peek node 2>&1)
echo "the node records (as the region holds them):"; echo "$ROWS" | grep -o '"[^"]*devs[^]]*' | head; echo "$ROWS" | grep -o 'nat=[^;]*' | sort | uniq -c
for n in ny nx; do ip netns exec $n env RINA_RAM=10.30.0.2:8840 RINA_RAM_NETNS=/var/run/netns/$n $B/rlite-ctl terminate > /dev/null 2>&1; done
NATS=$(echo "$ROWS" | grep -o 'nat=[0-9.]*' | sort -u)
[ "$NATS" = "nat=10.30.0.1" ] && [ "$(echo "$ROWS" | grep -o 'nat=' | wc -l)" = 1 ] && { echo "RESULT PASS: only the machine behind NAT states nat=10.30.0.1"; exit 0; }
echo "RESULT FAIL"; echo "$ROWS" | head -5; exit 1
