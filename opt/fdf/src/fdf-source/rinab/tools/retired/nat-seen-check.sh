#!/bin/bash
# nat-seen-check.sh -- [SESSION_PEER_V1] a host tells a machine where its session arrives from. Behind NAT that is NOT
# one of the machine's own addresses (DESIGN-NAT.md step 1). y (10.20.0.2) -- natbox (MASQUERADE) -- x (10.30.0.2, host).
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
IN=$(ip netns exec ny $B/rina-lisp seen 10.30.0.2 8840 2>&1); echo "from y, behind NAT: $IN"
OUT=$(ip netns exec nx $B/rina-lisp seen 10.30.0.2 8840 2>&1); echo "from x, outside:    $OUT"
echo "y's own addresses: $(ip netns exec ny ip -4 -o addr show | awk '{print $4}' | tr '\n' ' ')"
echo "$IN" | grep -q "at 10.30.0.1$" && echo "$OUT" | grep -q "at 10.30.0.2$" && { echo "RESULT PASS: y is seen at an address it does not hold"; exit 0; }
echo "RESULT FAIL"; exit 1
