#!/bin/bash
# SUPERSEDED by expD5-region-mapping.sh ([AREA_MAPPING_IN_THE_REGION_V1], 2026-10-06): the client no longer resolves over
# lispers.net's UDP wire. Kept as the record of the lispers.net (edge / oracle) path; it is not run in the regression.
# expD5-mr-capability.sh -- FD&F D5: the Map-Resolver comes from a capability probe ([MR_IS_A_CAPABILITY_V1],
# [MR_ELECTED_FROM_CAPABILITY_V1]), not from configuration.
# A chain root -- x -- y. root is the run's own agent, where the host and the mapping system (lispers.net) are; it is
# told ONLY the area's name (run.sh, LISP_AREA). It probes itself for the map-resolver role, states cap.mr in its own
# record, and every member elects the resolver from those records. x and y are told NOTHING (no RINA_RAM, RINA_AREA or
# RINA_MAP_RESOLVER anywhere): x learns name:port@resolver from root over their link, y learns it from x over theirs,
# each resolves the name through LISP, and y enrolls by broadcast and pings x.
# The x-y capture takes 6 frames: x asks its own links too, so the answer is not the second frame.
# FAILS if a Map-Resolver address is configured anywhere, if the name resolves other than through LISP, if root's probe
# did not pass, or if the answer on the x-y link does not carry the elected resolver.
source tests/libtest.sh
: "${LISP_AREA:?LISP_AREA}" "${LISP_LOG:?LISP_LOG (lispers.net lisp-ms.log)}" "${RINA_AGENT_LOG:?RINA_AGENT_LOG (run.sh)}"
[ -n "${RINA_MAP_RESOLVER:-}" ] && { echo "FAIL: RINA_MAP_RESOLVER is set in the run ($RINA_MAP_RESOLVER)"; exit 1; }
if getent hosts "$LISP_AREA" > /dev/null; then echo "FAIL: '$LISP_AREA' resolves through the system resolver"; exit 1; fi
echo "CONFIGURED BEFORE THE RUN: the host registered '$LISP_AREA' with the Map-Server; root (the run's own agent, where the host and lispers.net are) is told RINA_AREA=$LISP_AREA only. x and y: NOTHING. Links root-x and x-y. NOT configured anywhere: the region's address or port, the Map-Resolver"
asked_before=$(sed 's/\x1b\[[0-9;]*m//g' "$LISP_LOG" | grep -c "Proxy-replying for EID \[0\]'$LISP_AREA'")
N() { local n=$1; shift; env -u RINA_RAM -u RINA_AREA -u RINA_MAP_RESOLVER ip netns exec $n "$@"; }   # nothing about the area
create_veth_pair vr r x                                        # root -- x  (veth.r stays here, in root's network)
create_veth_pair vy x2 y                                       # x -- y
for n in x y; do ip netns add $n; cumulative_trap "ip netns delete $n" "EXIT"; ip netns exec $n ip link set lo up
    cumulative_trap "ip netns exec $n rlite-ctl terminate || true" "EXIT"; done
ip link set vr.r up
ip link set vr.x netns x; ip netns exec x ip link set vr.x up
ip link set vy.x2 netns x; ip netns exec x ip link set vy.x2 up
ip link set vy.y netns y; ip netns exec y ip link set vy.y up
ip netns exec x timeout 4 tcpdump -i vy.x2 -nn -e -A -c 6 ether proto 0xd1f0 > /tmp/expD5-mr-link.txt 2>&1 & CAP=$!; sleep 0.5
N x rlite-uipcps -d
N x rlite-ctl ipcp-create x.eth shim-eth ethdif; N x rlite-ctl ipcp-config x.eth netdev vy.x2
N x rlite-ctl ipcp-create x.n normal ndif; N x rlite-ctl ipcp-register x.n ethdif; N x rlite-ctl ipcp-enroller-enable x.n
N y rlite-uipcps -d
N y rlite-ctl ipcp-create y.eth shim-eth ethdif; N y rlite-ctl ipcp-config y.eth netdev vy.y
N y rlite-ctl ipcp-create y.n normal ndif; N y rlite-ctl ipcp-register y.n ethdif
N y rlite-ctl ipcp-enroll y.n ndif ethdif                      # broadcast enrollment: no neighbour named
N x rinaperf -lw -z rpx > /dev/null 2>&1 &
sleep 0.3
OUT=$(N y rinaperf -z rpx -p 1 -c 20 -i 10000 -g 0 2>&1); RC=$?
asked=$(( $(sed 's/\x1b\[[0-9;]*m//g' "$LISP_LOG" | grep -c "Proxy-replying for EID \[0\]'$LISP_AREA'") - asked_before ))
echo "$OUT" | grep "rtt min"
wait $CAP 2>/dev/null; echo "on the x-y link (y asks, x answers):"; grep -a -o "$LISP_AREA[^ ]*" /tmp/expD5-mr-link.txt | head -1
echo "agents' own words:"; grep -h "MR_IS_A_CAPABILITY_V1\|MR_ELECTED\|joined the area" "$RINA_AGENT_LOG"/agent.*.log
probe_ok=$(grep -h "map-resolver probe" "$RINA_AGENT_LOG"/agent.*.log | grep -c PASS)
link_mr=$(grep -a -o "$LISP_AREA:[0-9]*@[0-9.]*" /tmp/expD5-mr-link.txt | head -1 | sed 's/.*@//')
echo "LISP lookups answered by the Map-Server during the run (probe included): $asked"
echo "RESULT rc=$RC lost=$(echo "$OUT" | grep -c Timeout) of 20 probe_pass=$probe_ok resolver_on_link=${link_mr:-none} lisp_lookups=$asked"
[ $RC -eq 0 ] && [ "$(echo "$OUT" | grep -c Timeout)" -eq 0 ] && [ "$probe_ok" -ge 1 ] && [ -n "$link_mr" ] && [ "$asked" -gt 0 ] || exit 1
