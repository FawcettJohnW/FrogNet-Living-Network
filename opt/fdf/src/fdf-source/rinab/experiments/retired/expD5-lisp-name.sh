#!/bin/bash
# SUPERSEDED by expD5-region-mapping.sh ([AREA_MAPPING_IN_THE_REGION_V1], 2026-10-06): the client no longer resolves over
# lispers.net's UDP wire. Kept as the record of the lispers.net (edge / oracle) path; it is not run in the regression.
# expD5-lisp-name.sh -- FD&F D5 through LISP ([AREA_NAME_IS_A_LISP_EID_V1]).
# The area's one well-known name is a LISP distinguished-name EID. The region host registered it (name -> its RLOC)
# with the mapping system when it started (run.sh, LISP_MS); here nothing about the region's address exists anywhere
# else: no RINA_RAM, no /etc/hosts line, no DNS. x (a member) is told the area's name and the Map-Resolver; y (new) is
# told NOTHING, joins over the link (LAN join, broadcast enrollment as rlite 0025), and its agent and programs resolve
# the name the member gave them through LISP. A reliable ping then crosses x-y.
# FAILS if the name resolves any other way, if anything on y is configured, or if the mapping system is not asked.
# Needs: lispers.net running as Map-Server/Map-Resolver (lisp-up.sh), run.sh with HOST_LISTEN, LISP_MS, LISP_AREA, LISP_KEY.
source tests/libtest.sh
: "${LISP_MR:?LISP_MR (the Map-Resolver)}" "${LISP_AREA:?LISP_AREA}" "${LISP_LOG:?LISP_LOG (lispers.net lisp-ms.log)}"
unset RINA_RAM
export RINA_AREA=$LISP_AREA RINA_MAP_RESOLVER=$LISP_MR
# the name must NOT be resolvable except through LISP
if getent hosts "$LISP_AREA" > /dev/null; then echo "FAIL: '$LISP_AREA' resolves through the system resolver: $(getent hosts "$LISP_AREA")"; exit 1; fi
echo "the system resolver does not know '$LISP_AREA' (getent hosts: not found)"
echo "CONFIGURED BEFORE THE RUN: the host registered '$LISP_AREA' -> its RLOC with the Map-Server (run.sh); x: RINA_AREA=$LISP_AREA and RINA_MAP_RESOLVER=$LISP_MR; y: NOTHING (RINA_RAM, RINA_AREA, RINA_MAP_RESOLVER removed from its agent and every program); one veth link x-y. NOT configured anywhere: the region's address or port"
asked_before=$(sed 's/\x1b\[[0-9;]*m//g' "$LISP_LOG" | grep -c "Proxy-replying for EID \[0\]'$LISP_AREA'")
Y() { env -u RINA_RAM -u RINA_AREA -u RINA_MAP_RESOLVER ip netns exec y "$@"; }
create_veth_pair veth x y
create_namespace x
ip netns add y; cumulative_trap "ip netns delete y" "EXIT"; ip netns exec y ip link set lo up
cumulative_trap "ip netns exec y rlite-ctl terminate || true" "EXIT"
add_veth_to_namespace x veth.x; add_veth_to_namespace y veth.y
ip netns exec x timeout 5 tcpdump -i veth.x -nn -e -A -c 2 ether proto 0xd1f0 > /tmp/expD5-lisp-link.txt 2>&1 &   # the ask and the answer, as evidence
CAP=$!; sleep 0.5
Y rlite-uipcps -d
ip netns exec x rlite-ctl ipcp-create x.eth shim-eth ethdif; ip netns exec x rlite-ctl ipcp-config x.eth netdev veth.x
ip netns exec x rlite-ctl ipcp-create x.n normal ndif; ip netns exec x rlite-ctl ipcp-register x.n ethdif
ip netns exec x rlite-ctl ipcp-enroller-enable x.n
Y rlite-ctl ipcp-create y.eth shim-eth ethdif; Y rlite-ctl ipcp-config y.eth netdev veth.y
Y rlite-ctl ipcp-create y.n normal ndif; Y rlite-ctl ipcp-register y.n ethdif
Y rlite-ctl ipcp-enroll y.n ndif ethdif                  # broadcast enrollment: no neighbour named
start_daemon_namespace x rinaperf -lw -z rpx
OUT=$(Y rinaperf -z rpx -p 1 -c 20 -i 10000 -g 0 2>&1); RC=$?
asked=$(( $(sed 's/\x1b\[[0-9;]*m//g' "$LISP_LOG" | grep -c "Proxy-replying for EID \[0\]'$LISP_AREA'") - asked_before ))
echo "$OUT" | grep "rtt min"
wait $CAP 2>/dev/null; echo "on the link (y asks, x answers):"; grep -a -E "^[0-9]|rina-area" /tmp/expD5-lisp-link.txt
echo "LISP lookups answered by the Map-Server during the run: $asked"
echo "RESULT rc=$RC lost=$(echo "$OUT" | grep -c Timeout) of 20 lisp_lookups=$asked"
[ $RC -eq 0 ] && [ "$(echo "$OUT" | grep -c Timeout)" -eq 0 ] && [ "$asked" -gt 0 ] || exit 1
