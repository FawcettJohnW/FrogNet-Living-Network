#!/bin/bash
# exp5b-host-moves.sh -- FD&F 5b with a MOVE: the area's region host dies (kill -9) and comes back at a DIFFERENT RLOC,
# while a reliable flow runs. Its machine's agent registers the area's name -> the new RLOC in the mapping region of the
# elected map-resolver, over the session ([AREA_HOST_REGISTERS_ITSELF_V1]), then re-attaches BY NAME and propagates the
# announcement; x and y re-attach by name and find the host where it registered. Mapping mode (run.sh MAP_AREA).
# A chain root -- x -- y; DIFs on x-y; x runs a server; y runs a reliable ping across the move. Programs are given NO
# RINA_RAM: they ask their agent, as a member's programs do.
# FAILS if the mapping does not answer the new RLOC; if the ping loses anything or gives up; if a new client cannot reach
# the same server by name; if a program or agent did not re-attach; or if the host's agent did not register it.
source tests/libtest.sh
: "${MAP_AREA:?MAP_AREA (run.sh mapping mode)}"; API=/FawcettInnovations.RINA.ram_interface.php; LOG=${RINA_AGENT_LOG:?RINA_AGENT_LOG}
OLD=${RINA_RAM%:*}; NEW=192.0.2.3; PORT=${RINA_RAM##*:}
echo "CONFIGURED BEFORE THE RUN: links root-x and x-y; IPCPs on x and y; x's server registered. THE MOVE: the host returns at $NEW (was $OLD)"
ip addr add $NEW/24 dev eth0; cumulative_trap "ip addr del $NEW/24 dev eth0" "EXIT"
N() { local n=$1; shift; env -u RINA_RAM -u RINA_AREA -u RINA_MAP_RESOLVER ip netns exec $n "$@"; }
create_veth_pair vr r x; create_veth_pair vy x2 y
for n in x y; do ip netns add $n; cumulative_trap "ip netns delete $n" "EXIT"; ip netns exec $n ip link set lo up
    cumulative_trap "ip netns exec $n rlite-ctl terminate > /dev/null 2>&1 || true" "EXIT"; done   # agents started below, told NOTHING
ip link set vr.r up; ip link set vr.x netns x; ip netns exec x ip link set vr.x up
ip link set vy.x2 netns x; ip netns exec x ip link set vy.x2 up; ip link set vy.y netns y; ip netns exec y ip link set vy.y up
N x rlite-uipcps -d; N y rlite-uipcps -d                         # they learn the area from their links (name:port@resolver)
N x rlite-ctl ipcp-create x.eth shim-eth ethdif; N x rlite-ctl ipcp-config x.eth netdev vy.x2
N x rlite-ctl ipcp-create x.n normal ndif; N x rlite-ctl ipcp-register x.n ethdif; N x rlite-ctl ipcp-enroller-enable x.n
N y rlite-ctl ipcp-create y.eth shim-eth ethdif; N y rlite-ctl ipcp-config y.eth netdev vy.y
N y rlite-ctl ipcp-create y.n normal ndif; N y rlite-ctl ipcp-register y.n ethdif
N y rlite-ctl ipcp-enroll y.n ndif ethdif
echo "the mapping, before: $(rina-lisp map-resolve $MAP_AREA $OLD 2>&1)"
N x rinaperf -lw -z srvmv > $LOG/server.out 2> $LOG/server.err &
sleep 0.3; SRV=$(for p in $(ip netns pids x); do [ "$(cat /proc/$p/comm)" = rinaperf ] && echo $p; done | head -1)
N y rinaperf -z srvmv -p 1 -c 200 -i 30000 -g 0 > $LOG/ping.out 2> $LOG/ping.err &
PING=$!; sleep 1
HP=$(pgrep -nx rina-host -f -- "--listen $OLD:$PORT" 2>/dev/null || true); [ -z "$HP" ] && HP=$(for p in $(pgrep -x rina-host); do grep -qa -- "$OLD:$PORT" /proc/$p/cmdline && echo $p; done | tail -1)
echo "THE EVENT: kill -9 the area host (pid $HP, $OLD:$PORT), restart it at $NEW:$PORT on the same file"; kill -9 $HP; sleep 0.5
unshare --pid --fork --mount --mount-proc bash -c "mount -t tmpfs none /var/run/netns && mount -t tmpfs none /sys/class/net && exec rina-host --listen $NEW:$PORT --api $API" > $LOG/host-moved.log 2>&1 &
wait $PING
for i in $(seq 300); do [ "$(cat $LOG/server.err $LOG/ping.err 2>/dev/null | grep -c 're-attached')" -ge 2 ] && break; sleep 0.01; done
echo "the mapping, after: $(rina-lisp map-resolve $MAP_AREA $OLD 2>&1)"; MAPPED=$(rina-lisp map-resolve $MAP_AREA $OLD 2>&1 | grep -o -- "-> [0-9.]*" | cut -c4-)
echo "the running ping:"; grep -E "packets transmitted|rtt min|Stopping" $LOG/ping.out
echo "agents' and programs' own words:"; grep -h "AREA_HOST_REGISTERS\|re-attached" $LOG/agent.*.log $LOG/server.err $LOG/ping.err | sed 's/ (pid [0-9]*)//' | sort | uniq -c
OUT=$(N y rinaperf -z srvmv -p 1 -c 20 -i 10000 -g 0 2>&1); RC=$?
echo "a new client, to the same server:"; echo "$OUT" | grep -E "packets transmitted|Stopping"
reg=$(grep -h "AREA_HOST_REGISTERS_ITSELF_V1\] registered '$MAP_AREA' -> $NEW" $LOG/agent.*.log | wc -l)
agents=$(grep -l "re-attached" $LOG/agent.*.log | wc -l); progs=$(grep -h "LIBRARY_REASSERTS_V1\] re-attached" $LOG/server.err $LOG/ping.err | wc -l)
SENT=$(grep -o "[0-9]* packets transmitted, [0-9]* received" $LOG/ping.out); NEWC=$(echo "$OUT" | grep -o "[0-9]* packets transmitted, [0-9]* received")
N x rlite-ctl terminate > /dev/null 2>&1; N y rlite-ctl terminate > /dev/null 2>&1; for n in x y; do for p in $(ip netns pids $n); do kill $p; done; done; sleep 0.5
MV=$(for p in $(pgrep -x rina-host); do grep -qa -- "$NEW:$PORT" /proc/$p/cmdline && echo $p; done | tail -1)   # [EXP5B_MOVE_SHOWS_HOST_FDS_V1]
echo "the moved host (pid $MV) after x and y are gone: $(ls /proc/$MV/fd | wc -l) descriptors; its TCP peers:"; ss -tnp 2>/dev/null | grep "pid=$MV," | awk '{print $1, $4, $5}' | sort | uniq -c
echo "RESULT mapped=${MAPPED:-none} registered_by_agent=$reg ping=[$SENT] new=[$NEWC] agents_reattached=$agents programs_reattached=$progs server_alive=$(kill -0 $SRV 2>/dev/null && echo yes || echo no)"
[ "$MAPPED" = "$NEW" ] && [ "$reg" -ge 1 ] && [ "$SENT" = "200 packets transmitted, 200 received" ] && [ "$NEWC" = "20 packets transmitted, 20 received" ] && [ $RC -eq 0 ] && [ "$agents" -ge 3 ] && [ "$progs" -ge 2 ] && kill -0 $SRV 2>/dev/null || exit 1
