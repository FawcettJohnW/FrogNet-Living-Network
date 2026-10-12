#!/bin/bash
# exp-nat.sh -- FD&F LISP NAT traversal (DESIGN-NAT.md): a machine behind NAT, with NOTHING about NAT configured anywhere,
# finds out it is behind NAT ([NAT_IS_MEASURED_V1]), every member elects the RTR from capability ([RTR_IS_A_CAPABILITY_V1]),
# and the NATed machine links UP to it over TCP ([RTR_LINK_V1]); its PDUs go up that link and come down it.
# y (10.20.0.2) -- natbox (MASQUERADE, nothing new gets in) -- x (10.30.0.2, also the region host's machine).
# A normal DIF over a shim-udp4, as rlite test 0030. Run on its own (not under run.sh): its host is on x, outside.
# PASS only if: y states nat=, x states cap.rtr, both elect x; reliable flows y->x AND x->y lose nothing; and on the
# natbox's outside wire nothing is ever sent TO y's private address, and nothing reaches y but the link y opened.
set -u; H=$(cd "$(dirname "$0")/.." && pwd); B=$H/build/bin; API=/FawcettInnovations.RINA.ram_interface.php
export PATH=$B:$PATH; D=$(mktemp -d /tmp/exp-nat.XXXX); F=$D/region.perm; echo "evidence: $D"
cleanup() { for n in ny nx; do ip netns exec $n rlite-ctl terminate > /dev/null 2>&1; done; [ -n "${HP:-}" ] && kill $HP 2>/dev/null
    for n in ny nb nx; do for p in $(ip netns pids $n 2>/dev/null); do kill -9 $p 2>/dev/null; done; ip netns delete $n 2>/dev/null; done; }
cleanup; trap cleanup EXIT
for n in ny nb nx; do ip netns add $n; ip netns exec $n ip link set lo up; done
ip link add yv type veth peer name byv; ip link set yv netns ny; ip link set byv netns nb
ip link add xv type veth peer name bxv; ip link set xv netns nx; ip link set bxv netns nb
ip netns exec ny ip addr add 10.20.0.2/24 dev yv; ip netns exec ny ip link set yv up; ip netns exec ny ip route add default via 10.20.0.1
ip netns exec nb ip addr add 10.20.0.1/24 dev byv; ip netns exec nb ip link set byv up
ip netns exec nb ip addr add 10.30.0.1/24 dev bxv; ip netns exec nb ip link set bxv up
ip netns exec nx ip addr add 10.30.0.2/24 dev xv; ip netns exec nx ip link set xv up
ip netns exec nb sysctl -qw net.ipv4.ip_forward=1; ip netns exec nb iptables -t nat -A POSTROUTING -o bxv -j MASQUERADE
ip netns exec nb iptables -A FORWARD -i bxv -o byv -m state --state NEW -j DROP
echo "CONFIGURED: the NAT box (as a real one is); x and y are given the area's host (RINA_RAM) only. NOTHING about NAT, the RTR, or y's reachability"
ip netns exec nb tcpdump -i bxv -nn -s 96 -w $D/outside.pcap > /dev/null 2>&1 & TD=$!; sleep 0.5
ip netns exec nx env RINA_REGION_FILE=$F rina-host --listen 10.30.0.2:8840 --api $API > $D/host.log 2>&1 & HP=$!
for i in $(seq 100); do ip netns exec nx bash -c "(exec 3<>/dev/tcp/10.30.0.2/8840)" 2>/dev/null && break; sleep 0.05; done
X() { ip netns exec nx env RINA_RAM=10.30.0.2:8840 RINA_RAM_NETNS=/var/run/netns/nx RINA_AGENT_LOG=$D RINA_RMT_STATS=$D/rmt.txt "$@"; }
Y() { ip netns exec ny env RINA_RAM=10.30.0.2:8840 RINA_RAM_NETNS=/var/run/netns/ny RINA_AGENT_LOG=$D RINA_RMT_STATS=$D/rmt.txt "$@"; }
X rlite-uipcps -d; Y rlite-uipcps -d
for i in $(seq 200); do grep -q "linked up to the RTR" $D/agent.*.log 2>/dev/null && break; sleep 0.02; done
X rlite-ctl ipcp-create xgw shim-udp4 udptunnel.DIF; X rlite-ctl ipcp-create xnorm normal normal.DIF
X rlite-ctl ipcp-register xnorm udptunnel.DIF; X rlite-ctl ipcp-enroller-enable xnorm
Y rlite-ctl ipcp-create ygw shim-udp4 udptunnel.DIF; Y rlite-ctl ipcp-create ynorm normal normal.DIF
Y rlite-ctl ipcp-register ynorm udptunnel.DIF; Y rlite-ctl ipcp-enroll ynorm normal.DIF udptunnel.DIF xnorm
X rinaperf -lw -z on-x > /dev/null 2>&1; Y rinaperf -lw -z on-y > /dev/null 2>&1; sleep 0.3
UP=$(Y rinaperf -z on-x -p 1 -c 20 -i 10000 -g 0 2>&1); DOWN=$(X rinaperf -z on-y -p 1 -c 20 -i 10000 -g 0 2>&1)
echo "y -> x (y behind NAT):"; echo "$UP" | grep -E "transmitted|rtt min|Stopping"
echo "x -> y (only down y's own link):"; echo "$DOWN" | grep -E "transmitted|rtt min|Stopping"
ROWS=$(X peek node 2>&1)
for n in nx ny; do ip netns exec $n env RINA_RAM=10.30.0.2:8840 RINA_RAM_NETNS=/var/run/netns/$n rlite-ctl terminate > /dev/null 2>&1; done; sleep 0.5
kill $TD; wait $TD 2>/dev/null; tcpdump -nn -r $D/outside.pcap 2>/dev/null > $D/outside.txt
echo "the agents' own words:"; grep -h "NAT_IS\|RTR_IS\|RTR_LINK" $D/agent.*.log | sort | uniq -c
echo "counters:"; cat $D/rmt.txt | grep forwarded | sed 's/m:[0-9a-f]*\///'
TO_Y=$(grep -c "> 10.20.0" $D/outside.txt); LINK=$(grep -c "\.3359\b\|\.3359:" $D/outside.txt); UDP=$(grep -c " UDP" $D/outside.txt)
echo "outside wire: packets addressed to y's private net: $TO_Y; packets on the RTR link port: $LINK; UDP packets: $UDP"
NATS=$(echo "$ROWS" | grep -o 'nat=[0-9.]*' | sort -u); CAP=$(echo "$ROWS" | grep -o 'cap.rtr=[0-9.]*' | sort -u)
echo "RESULT nat=[$NATS] cap.rtr=[$CAP] up=[$(echo "$UP" | grep -o '[0-9]* packets transmitted, [0-9]* received')] down=[$(echo "$DOWN" | grep -o '[0-9]* packets transmitted, [0-9]* received')] to_y_private=$TO_Y link_packets=$LINK"
[ "$NATS" = "nat=10.30.0.1" ] && [ "$CAP" = "cap.rtr=10.30.0.2" ] && echo "$UP" | grep -q "20 packets transmitted, 20 received" && echo "$DOWN" | grep -q "20 packets transmitted, 20 received" \
  && [ "$TO_Y" = 0 ] && [ "$LINK" -gt 0 ] && { echo "RESULT PASS"; exit 0; }
echo "RESULT FAIL"; exit 1
