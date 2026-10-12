#!/bin/bash
# exp6-split-merge.sh -- FD&F experiment 6: a DIF splits and reconnects; nothing is merged.
# Topology: four machines in a line a-b-c-d (veth links ab, bc, cd; a shim-eth DIF on each; one normal DIF over them).
# Two reliable pings run at once (rinaperf -g 0, 30 ms apart): a->d (across the split) and a->b (inside one half).
# At about 1 s the b-c link goes down: the DIF is two islands. At about 2 s it comes back up. Nothing else is done:
# no step reconciles anything; each agent re-derives its routes from the truths available, and reliable flows re-send
# what their receivers have not stated they took.
# FAILS if either application sees an error or loses a ping, or the in-island ping stalls during the split.
source tests/libtest.sh
for m in a b c d; do create_namespace $m; ip netns exec $m rlite-ctl ipcp-create $m.n normal sdif; done
for li in ab bc cd; do
  l=${li:0:1}; r=${li:1:1}
  create_veth_pair veth ${li}l ${li}r
  add_veth_to_namespace $l veth.${li}l; add_veth_to_namespace $r veth.${li}r
  ip netns exec $l rlite-ctl ipcp-create ${li}l.eth shim-eth ${li}dif; ip netns exec $l rlite-ctl ipcp-config ${li}l.eth netdev veth.${li}l
  ip netns exec $r rlite-ctl ipcp-create ${li}r.eth shim-eth ${li}dif; ip netns exec $r rlite-ctl ipcp-config ${li}r.eth netdev veth.${li}r
  ip netns exec $l rlite-ctl ipcp-register $l.n ${li}dif; ip netns exec $r rlite-ctl ipcp-register $r.n ${li}dif
done
echo "CONFIGURED BEFORE THE RUN: four namespaces in a line, veth links ab bc cd with a shim-eth DIF each, one normal DIF; enrollments b->a, c->b, d->c; nothing about routes"
ip netns exec a rlite-ctl ipcp-enroller-enable a.n
ip netns exec b rlite-ctl ipcp-enroll b.n sdif abdif a.n
ip netns exec c rlite-ctl ipcp-enroll c.n sdif bcdif b.n
ip netns exec d rlite-ctl ipcp-enroll d.n sdif cddif c.n
start_daemon_namespace d rinaperf -lw -z rpd
start_daemon_namespace b rinaperf -lw -z rpb
( sleep 1; echo "SPLIT at $(date +%s.%N | cut -c1-14)"; ip netns exec b ip link set veth.bcl down; ip netns exec c ip link set veth.bcr down
  sleep 1; echo "MERGE at $(date +%s.%N | cut -c1-14)"; ip netns exec b ip link set veth.bcl up; ip netns exec c ip link set veth.bcr up ) &
ip netns exec a rinaperf -z rpb -p 1 -c 100 -i 30000 -g 0 > /tmp/exp6-ab.txt 2>&1 & AB=$!
OUT=$(ip netns exec a rinaperf -z rpd -p 1 -c 100 -i 30000 -g 0 2>&1); RC=$?
wait $AB; RCB=$?
OUTB=$(cat /tmp/exp6-ab.txt)
wait
echo "RESULT across the split a->d: rc=$RC lost=$(echo "$OUT" | grep -c Timeout) of 100"
echo "RESULT inside one island a->b: rc=$RCB lost=$(echo "$OUTB" | grep -c Timeout) of 100"
echo "$OUT" | grep -E "rtt min" ; echo "$OUTB" | grep -E "rtt min"
[ $RC -eq 0 ] && [ $RCB -eq 0 ] && [ "$(echo "$OUT" | grep -c Timeout)" -eq 0 ] && [ "$(echo "$OUTB" | grep -c Timeout)" -eq 0 ] || exit 1
