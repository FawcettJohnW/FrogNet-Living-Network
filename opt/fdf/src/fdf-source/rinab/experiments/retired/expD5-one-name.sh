#!/bin/bash
# expD5-one-name.sh -- FD&F D5, first part: a machine is given ONE thing about its area, the area's well-known name.
# RINA_RAM (the region's host:port) is removed from every program and agent of the test; RINA_AREA is the name, which
# resolves (here through /etc/hosts, in the network the region is reached from) to the region's host; the port is the
# area's well-known port. Then rlite's two-machine setup runs (two namespaces, one link, shim-eth, a normal DIF,
# enrollment) and a reliable ping crosses it. FAILS if anything needs more than the name.
source tests/libtest.sh
unset RINA_RAM
export RINA_AREA=rina-area.test
cp /etc/hosts /etc/hosts.save; cumulative_trap "cp /etc/hosts.save /etc/hosts" "EXIT"
echo "127.0.0.1   rina-area.test" >> /etc/hosts
echo "CONFIGURED BEFORE THE RUN: the area's name rina-area.test (one /etc/hosts line, where the region is reached from); two namespaces, one veth link, a shim-eth DIF, a normal DIF, enrollment y->x. NOT configured: the region's address or port (RINA_RAM unset)"
create_veth_pair veth x y
create_namespace x; create_namespace y
add_veth_to_namespace x veth.x; add_veth_to_namespace y veth.y
ip netns exec x rlite-ctl ipcp-create x.eth shim-eth ethdif; ip netns exec x rlite-ctl ipcp-config x.eth netdev veth.x
ip netns exec y rlite-ctl ipcp-create y.eth shim-eth ethdif; ip netns exec y rlite-ctl ipcp-config y.eth netdev veth.y
ip netns exec x rlite-ctl ipcp-create x.n normal ndif; ip netns exec x rlite-ctl ipcp-register x.n ethdif
ip netns exec y rlite-ctl ipcp-create y.n normal ndif; ip netns exec y rlite-ctl ipcp-register y.n ethdif
ip netns exec x rlite-ctl ipcp-enroller-enable x.n
ip netns exec y rlite-ctl ipcp-enroll y.n ndif ethdif x.n
start_daemon_namespace x rinaperf -lw -z rpx
OUT=$(ip netns exec y rinaperf -z rpx -p 1 -c 20 -i 10000 -g 0 2>&1); RC=$?
echo "env seen by the test: RINA_RAM=${RINA_RAM:-<unset>} RINA_AREA=$RINA_AREA"
echo "$OUT" | grep "rtt min"
echo "RESULT rc=$RC lost=$(echo "$OUT" | grep -c Timeout) of 20"
[ $RC -eq 0 ] && [ "$(echo "$OUT" | grep -c Timeout)" -eq 0 ] || exit 1
