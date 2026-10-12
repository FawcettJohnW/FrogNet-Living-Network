#!/bin/bash
# exp5a-member-dies.sh -- FD&F experiment 5 (first half): a member dies mid-flow; the DIF heals around it by itself.
# Topology: three machines a, b, c; links ab, bc, ac (veth, shim-eth); one normal DIF over them, enrolled through a.
# The ac link starts DOWN, so a's flow to c goes through b. At about 1 s, b dies (its agent is killed and its links go
# dark) and ac comes up. Nothing is reconfigured: every agent learns of the change from the region (b's session ends;
# a's and c's devices change), re-derives its routes, and the flow continues a -> c directly.
# FAILS if the application sees an error, or the pings do not resume. Recorded: pings lost; each agent's route log.
source tests/libtest.sh
for m in a b c; do create_namespace $m; ip netns exec $m rlite-ctl ipcp-create $m.n normal hdif; done
for li in ab bc ac; do
  l=${li:0:1}; r=${li:1:1}
  create_veth_pair veth ${li}l ${li}r
  add_veth_to_namespace $l veth.${li}l; add_veth_to_namespace $r veth.${li}r
  ip netns exec $l rlite-ctl ipcp-create ${li}l.eth shim-eth ${li}dif; ip netns exec $l rlite-ctl ipcp-config ${li}l.eth netdev veth.${li}l
  ip netns exec $r rlite-ctl ipcp-create ${li}r.eth shim-eth ${li}dif; ip netns exec $r rlite-ctl ipcp-config ${li}r.eth netdev veth.${li}r
  ip netns exec $l rlite-ctl ipcp-register $l.n ${li}dif; ip netns exec $r rlite-ctl ipcp-register $r.n ${li}dif
done
echo "CONFIGURED BEFORE THE RUN: three namespaces; veth links ab, bc, ac with a shim-eth DIF each; one normal DIF hdif; enrollment b->a over ab, c->b over bc, c->a over ac (lower flow); nothing about routes"
ip netns exec a rlite-ctl ipcp-enroller-enable a.n
ip netns exec b rlite-ctl ipcp-enroll b.n hdif abdif a.n
ip netns exec c rlite-ctl ipcp-enroll c.n hdif bcdif b.n
ip netns exec c rlite-ctl ipcp-lower-flow-alloc c.n hdif acdif a.n
echo "links in a at start: $(ip netns exec a ip -br link | awk '{print $1"="$2}' | tr "\n" " ")"
ip netns exec a ip link set veth.acl down; ip netns exec c ip link set veth.acr down
ip netns exec c env RINA_WIRE_STATS=1 rinaperf -l -z rpc > /tmp/exp5a-server.log 2>&1 &
sleep 0.3
( sleep 1; echo "B DIES at $(date +%s.%N | cut -c1-14); ac comes up";
  for pid in $(ip netns pids b); do [ "$(cat /proc/$pid/comm 2>/dev/null)" = rlite-uipcps ] && kill -9 $pid && echo "killed b's agent pid $pid"; done
  ip netns exec b ip link set veth.abr down; ip netns exec b ip link set veth.bcl down
  for m in a b c; do n=0; for pid in $(ip netns pids $m); do [ "$(cat /proc/$pid/comm 2>/dev/null)" = rlite-uipcps ] && n=$((n+1)); done; echo "agents alive in $m: $n"; done
  echo "links in a before heal: $(ip netns exec a ip -br link | awk '{print $1"="$2}' | tr "\n" " ")"
  ip netns exec a ip link set veth.acl up; ip netns exec c ip link set veth.acr up
  ip netns exec c timeout 4 tcpdump -i veth.acr -nn -e -c 40 ether proto 0xd1f0 > /tmp/tcpd-c.txt 2>&1 &
  ip netns exec a timeout 4 tcpdump -i veth.acl -nn -e -c 40 ether proto 0xd1f0 > /tmp/tcpd-a.txt 2>&1 &
  echo "links in a after heal: $(ip netns exec a ip -br link | awk '{print $1"="$2}' | tr "\n" " ")" ) &
( sleep 12; P=$(pgrep -f "^rinaperf -z rpc -p 1" | head -1); echo "CLIENT DUMP pid $P"; gdb -p $P -batch -ex "thread apply all bt 10" 2>&1 | grep -E "^#|^Thread" | grep -v "^\[New" > /tmp/exp5a-client.dump ) &
OUT=$(ip netns exec a env RINA_WIRE_STATS=1 rinaperf -z rpc -p 1 -c 100 -i 30000 -g 0 2>&1); RC=$?
wait
echo "$OUT" | grep -E "rtt =" | head -2
echo "RESULT rc=$RC pings_lost=$(echo "$OUT" | grep -c Timeout) of 100"
# pass: the application saw no error, and the flow carried on: at most the ping in flight when b died and one sent
# before the heal are lost (rinaperf exits 0 even when it gives up after 9 consecutive timeouts, so rc alone proves nothing)
[ $RC -eq 0 ] && [ "$(echo "$OUT" | grep -c Timeout)" -le 2 ] && ! echo "$OUT" | grep -q "Stopping after" || exit 1
