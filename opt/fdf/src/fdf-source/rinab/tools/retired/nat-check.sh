#!/bin/bash
# nat-check.sh -- does this kernel give real NAT between namespaces? y (10.20.0.2, private) -- natbox -- x (10.30.0.2).
# PASS only if: y reaches x; x sees y's connection come from the natbox (10.30.0.1); x cannot reach y's own address.
set -u; cleanup() { for n in ny nb nx; do ip netns delete $n 2>/dev/null; done; }; cleanup; trap cleanup EXIT
for n in ny nb nx; do ip netns add $n; ip netns exec $n ip link set lo up; done
ip link add yv type veth peer name byv; ip link set yv netns ny; ip link set byv netns nb
ip link add xv type veth peer name bxv; ip link set xv netns nx; ip link set bxv netns nb
ip netns exec ny ip addr add 10.20.0.2/24 dev yv; ip netns exec ny ip link set yv up; ip netns exec ny ip route add default via 10.20.0.1
ip netns exec nb ip addr add 10.20.0.1/24 dev byv; ip netns exec nb ip link set byv up
ip netns exec nb ip addr add 10.30.0.1/24 dev bxv; ip netns exec nb ip link set bxv up
ip netns exec nx ip addr add 10.30.0.2/24 dev xv; ip netns exec nx ip link set xv up
ip netns exec nb sysctl -qw net.ipv4.ip_forward=1
ip netns exec nb iptables -t nat -A POSTROUTING -o bxv -j MASQUERADE
ip netns exec nb iptables -A FORWARD -i bxv -o byv -m state --state NEW -j DROP      # nothing new from outside gets in
ip netns exec nx python3 -c "
import socket; s=socket.socket(); s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1); s.bind(('10.30.0.2',9999)); s.listen(1)
c,a=s.accept(); print('x saw the connection from', a[0]); c.close()" > /tmp/nat-check.x 2>&1 &
sleep 0.5
ip netns exec ny python3 -c "import socket; socket.create_connection(('10.30.0.2',9999),3).close(); print('y reached x')" 2>&1
sleep 0.3; cat /tmp/nat-check.x
IN=$(ip netns exec nx python3 -c "
import socket
try: socket.create_connection(('10.20.0.2',9999),2); print('x reached y')
except Exception as e: print('x cannot reach y:', type(e).__name__)" 2>&1); echo "$IN"
grep -q "from 10.30.0.1" /tmp/nat-check.x && echo "$IN" | grep -q "cannot" && { echo "RESULT PASS: real NAT"; exit 0; }; echo "RESULT FAIL"; exit 1
