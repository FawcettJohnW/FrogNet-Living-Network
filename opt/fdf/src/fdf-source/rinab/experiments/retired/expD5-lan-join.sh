#!/bin/bash
# expD5-lan-join.sh -- FD&F D5, second part: on a LAN, a machine with NOTHING configured joins its area.
# x is an existing member (its agent knows the area). y is new: its agent and every program on it run with RINA_RAM
# and RINA_AREA removed. y's agent asks its links (an Ethernet broadcast); x's agent answers with the area; y joins,
# and y's programs ask y's agent. Then y enrolls in x's DIF over the link and a reliable ping crosses it.
# FAILS if anything on y needs to be told where the area is.
source tests/libtest.sh
# [LAN_JOIN_TEST_MEMBER_KNOWS_NAME_V1] a member answers with its area's NAME, never a loopback locator, so x (an existing
# member) is told the name, as in expD5-one-name.sh. y is told nothing: its agent and programs run with both removed.
unset RINA_RAM
export RINA_AREA=rina-area.test
cp /etc/hosts /etc/hosts.save; cumulative_trap "cp /etc/hosts.save /etc/hosts" "EXIT"
echo "127.0.0.1   rina-area.test" >> /etc/hosts
Y() { env -u RINA_RAM -u RINA_AREA ip netns exec y "$@"; }   # everything on y: nothing about the area
create_veth_pair veth x y
create_namespace x
ip netns add y; cumulative_trap "ip netns delete y" "EXIT"; ip netns exec y ip link set lo up
cumulative_trap "ip netns exec y rlite-ctl terminate || true" "EXIT"
add_veth_to_namespace x veth.x; add_veth_to_namespace y veth.y
echo "CONFIGURED BEFORE THE RUN: x is an existing member (told the area name, as in expD5-one-name); y: NOTHING about the area (RINA_RAM and RINA_AREA removed from its agent and every program); one veth link x-y"
# [LAN_JOIN_PROOF_V1] the pass needs the ask (type 4, broadcast from y) and the answer (type 5, from x) on the wire
ip netns exec y tcpdump -i veth.y -nn -e -XX -U -w /tmp/lanjoin.pcap ether proto 0xd1f0 2>/tmp/lanjoin.tcpdump.err & TD=$!
cumulative_trap "kill $TD 2>/dev/null || true" "EXIT"; sleep 0.5
Y rlite-uipcps -d
ip netns exec x rlite-ctl ipcp-create x.eth shim-eth ethdif; ip netns exec x rlite-ctl ipcp-config x.eth netdev veth.x
ip netns exec x rlite-ctl ipcp-create x.n normal ndif; ip netns exec x rlite-ctl ipcp-register x.n ethdif
ip netns exec x rlite-ctl ipcp-enroller-enable x.n
Y rlite-ctl ipcp-create y.eth shim-eth ethdif; Y rlite-ctl ipcp-config y.eth netdev veth.y
Y rlite-ctl ipcp-create y.n normal ndif; Y rlite-ctl ipcp-register y.n ethdif
Y rlite-ctl ipcp-enroll y.n ndif ethdif        # [LAN_JOIN_BROADCAST_ENROLL_V1] no neighbour named: broadcast enrollment, as rlite 0025
start_daemon_namespace x rinaperf -lw -z rpx
OUT=$(Y rinaperf -z rpx -p 1 -c 20 -i 10000 -g 0 2>&1); RC=$?
echo "$OUT" | grep "rtt min"
echo "RESULT rc=$RC lost=$(echo "$OUT" | grep -c Timeout) of 20"
sleep 0.3; kill $TD; wait $TD 2>/dev/null
FR=$(tcpdump -r /tmp/lanjoin.pcap -nn -e -XX 2>/dev/null)
ASK=$(echo "$FR" | grep -A2 "ff:ff:ff:ff:ff:ff" | grep -c "0x0000:.*d1f0 04")
ANS=$(echo "$FR" | grep -c "d1f0 05")
echo "$FR" | grep -B1 -A3 "d1f0 0[45]" | head -20
echo "LAN_JOIN asks=$ASK answers=$ANS"
[ $RC -eq 0 ] && [ "$(echo "$OUT" | grep -c Timeout)" -eq 0 ] && [ "$ASK" -ge 1 ] && [ "$ANS" -ge 1 ] || exit 1
