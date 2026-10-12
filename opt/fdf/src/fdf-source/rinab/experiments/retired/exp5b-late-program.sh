#!/bin/bash
# exp5b-late-program.sh -- exp5b-full with FAULT INJECTION: both programs re-attach 500 ms late ([FAULT_REATTACH_DELAY_V1]),
# so the routes change while they are detached and the route events are missed. Proves [RESEND_ON_HOST_CHANGE_V1].
# exp5b-full.sh -- FD&F experiment 5b: the area's region host dies (kill -9) and restarts from its file WHILE A FLOW IS
# RUNNING, and programs live through it. Agents re-attach on the host-change announcement (neighbour propagation, part B);
# programs re-attach when their agent tells them ([LIBRARY_REASSERTS_V1], part C) and write back what they own.
# A chain root -- x -- y; shim-eth and normal DIFs on x-y; x runs a rinaperf server (registered BEFORE the kill); y runs a
# reliable ping across the kill and the restart.
# FAILS if the running ping loses anything or gives up; if a NEW client on y cannot reach the SAME server by name after
# the restart (its registration must have been re-asserted); or if a program or agent did not re-attach.
source tests/libtest.sh
export RINA_RMT_STATS=$(mktemp /tmp/exp5b-rmt.XXXX)   # [EXP5B_SHOWS_ITS_ROUTES_V1] each agent's route derivations and counters
API=/FawcettInnovations.RINA.ram_interface.php; LOG=${RINA_AGENT_LOG:?RINA_AGENT_LOG (run.sh)}
echo "CONFIGURED BEFORE THE RUN: links root-x and x-y; on x and y: their IPCPs, x enroller, y enrolled; x's server registered. AFTER THE RESTART: nothing"
N() { local n=$1; shift; ip netns exec $n "$@"; }
create_veth_pair vr r x; create_veth_pair vy x2 y
create_namespace x; create_namespace y
ip link set vr.r up; ip link set vr.x netns x; N x ip link set vr.x up
add_veth_to_namespace x vy.x2; add_veth_to_namespace y vy.y
N x rlite-ctl ipcp-create x.eth shim-eth ethdif; N x rlite-ctl ipcp-config x.eth netdev vy.x2
N x rlite-ctl ipcp-create x.n normal ndif; N x rlite-ctl ipcp-register x.n ethdif; N x rlite-ctl ipcp-enroller-enable x.n
N y rlite-ctl ipcp-create y.eth shim-eth ethdif; N y rlite-ctl ipcp-config y.eth netdev vy.y
N y rlite-ctl ipcp-create y.n normal ndif; N y rlite-ctl ipcp-register y.n ethdif
N y rlite-ctl ipcp-enroll y.n ndif ethdif
RINA_FAULT_REATTACH_DELAY_MS=500 N x rinaperf -lw -z srv5b > $LOG/server.out 2> $LOG/server.err &
sleep 0.3; SRV=$(for p in $(ip netns pids x); do [ "$(cat /proc/$p/comm)" = rinaperf ] && echo $p; done | head -1)   # rinaperf -w daemonizes: the server is the process in x, not $!
echo "the server: rinaperf pid $SRV in x"
RINA_FAULT_REATTACH_DELAY_MS=500 N y rinaperf -z srv5b -p 1 -c 3000 -i 2000 -g 0 > $LOG/ping.out 2> $LOG/ping.err &
PING=$!; sleep 1
HP=$(pgrep -nx rina-host); echo "THE EVENT: kill -9 the host (pid $HP) with the ping running"; kill -9 $HP; sleep 0.5
echo "restarting the host on the same file"; T1=$(date +%s.%N)
unshare --pid --fork --mount --mount-proc bash -c "mount -t tmpfs none /var/run/netns && mount -t tmpfs none /sys/class/net && exec rina-host --listen $RINA_RAM --api $API" > $LOG/host-restarted.log 2>&1 &
wait $PING; PRC=$?
for i in $(seq 300); do [ "$(cat $LOG/server.err $LOG/ping.err 2>/dev/null | grep -c 're-attached')" -ge 2 ] && break; sleep 0.01; done
echo "the running ping:"; grep -E "packets transmitted|rtt min|Stopping" $LOG/ping.out
echo "programs' own words:"; grep -h "LIBRARY_REASSERTS" $LOG/server.err $LOG/ping.err | sed 's/ (pid [0-9]*)//' | sort | uniq -c
OUT=$(N y rinaperf -z srv5b -p 1 -c 20 -i 10000 -g 0 2>&1); RC=$?
echo "a new client after the restart, to the same server:"; echo "$OUT" | grep -E "packets transmitted|rtt min|Stopping"
kill -0 $SRV 2>/dev/null && echo "the server process ($SRV) is the one started before the kill: still running" || echo "the server process is gone"
agents=$(grep -l "re-attached" $LOG/agent.*.log | wc -l); progs=$(grep -h "LIBRARY_REASSERTS_V1\] re-attached" $LOG/server.err $LOG/ping.err | wc -l)
SENT=$(grep -o "[0-9]* packets transmitted, [0-9]* received" $LOG/ping.out)
NEW=$(echo "$OUT" | grep -o "[0-9]* packets transmitted, [0-9]* received")
N x rlite-ctl terminate > /dev/null 2>&1; N y rlite-ctl terminate > /dev/null 2>&1; sleep 0.3   # x and y write their counters
echo "agents' counters:"; grep "forwarded=" $RINA_RMT_STATS | sed 's/m:[0-9a-f]*\///'
echo "route derivations around the event:"; grep "derived" $RINA_RMT_STATS | sed 's/m:[0-9a-f]*\///' | uniq -c | tail -12
echo "RESULT ping=[$SENT] gaveup=$(grep -c Stopping $LOG/ping.out) new=[$NEW] new_rc=$RC agents_reattached=$agents programs_reattached=$progs"
[ "$SENT" = "3000 packets transmitted, 3000 received" ] && [ "$NEW" = "20 packets transmitted, 20 received" ] && [ $RC -eq 0 ] && [ "$agents" -ge 3 ] && [ "$progs" -ge 2 ] && kill -0 $SRV 2>/dev/null || exit 1
