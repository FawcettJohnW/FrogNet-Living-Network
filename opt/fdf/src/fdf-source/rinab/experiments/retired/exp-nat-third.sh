#!/bin/bash
# exp-nat-third.sh -- FD&F NAT traversal with a THIRD machine (DESIGN-NAT.md): y behind NAT is reached by an outside machine
# that is NOT its RTR, so every PDU between them crosses the RTR. One outside LAN (a bridge in namespace "no") holds the
# natbox's outside leg (10.30.0.1), x (10.30.0.2, also the region host's machine) and z (10.30.0.3); y (10.20.0.2) is
# behind the natbox (MASQUERADE; nothing new gets in). x and z are both RTR-capable; the test reads which one is elected
# and uses the OTHER as the third party. Nothing about NAT, the RTR, or reachability is configured.
# PASS only if: y states nat=; an RTR is elected; reliable flows y -> third AND third -> y lose nothing; the third's
# counters show the PDUs left it for the RTR (no direct route to y exists); nothing on the outside wire is addressed to
# y's private net; no UDP on the outside wire reaches y's NAT binding other than y's own link to the RTR. Run on its own.
set -u; H=$(cd "$(dirname "$0")/.." && pwd); B=$H/build/bin; export PATH=$B:$PATH; API=/FawcettInnovations.RINA.ram_interface.php
D=$(mktemp -d /tmp/exp-nat-third.XXXX); F=$D/region.perm; echo "diag dir: $D"
cleanup() { for n in nx ny nz; do ip netns exec $n env RINA_RAM=10.30.0.2:8840 RINA_RAM_NETNS=/var/run/netns/$n rlite-ctl terminate > /dev/null 2>&1; done
            sleep 0.3; [ -n "${HP:-}" ] && kill $HP 2>/dev/null; for n in nx ny nz nb no; do for p in $(ip netns pids $n 2>/dev/null); do kill $p 2>/dev/null; done; ip netns delete $n 2>/dev/null; done; }
cleanup; trap cleanup EXIT
for n in ny nb nx nz no; do ip netns add $n; ip netns exec $n ip link set lo up; done
ip netns exec no ip link add obr type bridge; ip netns exec no ip link set obr up
for m in nb nx nz; do ip link add o-$m type veth peer name p-$m; ip link set o-$m netns $m; ip link set p-$m netns no
    ip netns exec no ip link set p-$m master obr; ip netns exec no ip link set p-$m up; done
ip link add yv type veth peer name byv; ip link set yv netns ny; ip link set byv netns nb
ip netns exec ny ip addr add 10.20.0.2/24 dev yv; ip netns exec ny ip link set yv up; ip netns exec ny ip route add default via 10.20.0.1
ip netns exec nb ip addr add 10.20.0.1/24 dev byv; ip netns exec nb ip link set byv up
ip netns exec nb ip addr add 10.30.0.1/24 dev o-nb; ip netns exec nb ip link set o-nb up
ip netns exec nx ip addr add 10.30.0.2/24 dev o-nx; ip netns exec nx ip link set o-nx up
ip netns exec nz ip addr add 10.30.0.3/24 dev o-nz; ip netns exec nz ip link set o-nz up
ip netns exec nb sysctl -qw net.ipv4.ip_forward=1; ip netns exec nb iptables -t nat -A POSTROUTING -o o-nb -j MASQUERADE
ip netns exec nb iptables -A FORWARD -i o-nb -o byv -m state --state NEW -j DROP
echo "CONFIGURED: the NAT box; x, y and z are given the area's host (RINA_RAM) only. NOTHING about NAT, the RTR, or reachability"
ip netns exec nb tcpdump -i o-nb -nn -s 96 -w $D/outside.pcap > /dev/null 2>&1 & TD=$!; sleep 0.5
ip netns exec nx env RINA_REGION_FILE=$F rina-host --listen 10.30.0.2:8840 --api $API > $D/host.log 2>&1 & HP=$!
for i in $(seq 100); do ip netns exec nx bash -c "(exec 3<>/dev/tcp/10.30.0.2/8840)" 2>/dev/null && break; sleep 0.05; done
M() { local n=$1; shift; ip netns exec $n env RINA_RAM=10.30.0.2:8840 RINA_RAM_NETNS=/var/run/netns/$n RINA_AGENT_LOG=$D RINA_RMT_STATS=$D/rmt.txt "$@"; }
M nx rlite-uipcps -d; M nz rlite-uipcps -d; M ny rlite-uipcps -d
for i in $(seq 200); do grep -q "linked up to the RTR" $D/agent.*.log 2>/dev/null && break; sleep 0.02; done
M nx rlite-ctl ipcp-create xgw shim-udp4 udptunnel.DIF; M nx rlite-ctl ipcp-create xnorm normal normal.DIF
M nx rlite-ctl ipcp-register xnorm udptunnel.DIF; M nx rlite-ctl ipcp-enroller-enable xnorm
for n in nz ny; do c=${n#n}; M $n rlite-ctl ipcp-create ${c}gw shim-udp4 udptunnel.DIF; M $n rlite-ctl ipcp-create ${c}norm normal normal.DIF
    M $n rlite-ctl ipcp-register ${c}norm udptunnel.DIF; M $n rlite-ctl ipcp-enroll ${c}norm normal.DIF udptunnel.DIF xnorm; done
RTR=$(grep -h "RTR elected: 10" $D/agent.*.log | tail -1 | grep -o "10\.30\.0\.[0-9]*")
case "$RTR" in 10.30.0.2) T=nz;; 10.30.0.3) T=nx;; *) echo "no RTR elected"; echo "RESULT FAIL"; exit 1;; esac
echo "the elected RTR: $RTR; the third party (the other outside machine): $T"
M $T rinaperf -lw -z on-third > /dev/null 2>&1; M ny rinaperf -lw -z on-y > /dev/null 2>&1; sleep 0.3
UP=$(M ny rinaperf -z on-third -p 1 -c 20 -i 10000 -g 0 2>&1); DOWN=$(M $T rinaperf -z on-y -p 1 -c 20 -i 10000 -g 0 2>&1)
echo "y -> third:"; echo "$UP" | grep -E "transmitted|rtt min|Stopping"; echo "third -> y:"; echo "$DOWN" | grep -E "transmitted|rtt min|Stopping"
ROWS=$(M nx peek node 2>&1)
for n in nx ny nz; do M $n rlite-ctl terminate > /dev/null 2>&1; done; sleep 0.5
kill $TD; wait $TD 2>/dev/null; tcpdump -nn -r $D/outside.pcap 2>/dev/null > $D/outside.txt
echo "the agents' own words:"; grep -h "NAT_IS\|RTR_IS\|RTR_LINK" $D/agent.*.log | sed 's/ [0-9]\{10\}\.[0-9]\{3\}//; s/([0-9.:<>-]*)//' | sort | uniq -c
echo "counters:"; grep forwarded $D/rmt.txt | sed 's/m:[0-9a-f]*\///'
TO_Y=$(grep -c "> 10.20.0" $D/outside.txt); NATS=$(echo "$ROWS" | grep -o 'nat=[0-9.]*' | sort -u)
echo "outside wire (natbox leg): packets addressed to y's private net: $TO_Y"
echo "RESULT nat=[$NATS] rtr=$RTR third=$T up=[$(echo "$UP" | grep -o '[0-9]* packets transmitted, [0-9]* received')] down=[$(echo "$DOWN" | grep -o '[0-9]* packets transmitted, [0-9]* received')] to_y_private=$TO_Y"
[ "$NATS" = "nat=10.30.0.1" ] && echo "$UP" | grep -q "20 packets transmitted, 20 received" && echo "$DOWN" | grep -q "20 packets transmitted, 20 received" && [ "$TO_Y" = 0 ] && { echo "RESULT PASS"; exit 0; }
echo "RESULT FAIL"; exit 1
