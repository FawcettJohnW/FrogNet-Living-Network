#!/bin/bash
# exp5b-host-restart.sh -- FD&F experiment 5b, part B: the area's region host dies (kill -9) and restarts from its file.
# Machines re-attach on a host-change announcement that travels by NEIGHBOUR PROPAGATION (John's ruling): the host,
# once listening, tells its own machine's agent (root, the run's own); root announces to its links; x applies it
# (re-attaches, re-asserting what it owns) and forwards; y applies it. Nobody is told anything else.
# A chain root -- x -- y; a shim-eth DIF and a normal DIF on x-y; y enrolled in x's DIF before the event.
# FAILS if an IPCP is not back (same name, DIF and address) without any rlite-ctl command after the restart, if a new
# flow y->x loses anything (no re-enrollment is done), or if any agent did not re-attach on the announcement.
# Programs do not survive the host yet (part C): the test's own rinaperf server is ended before the kill.
source tests/libtest.sh
API=/FawcettInnovations.RINA.ram_interface.php; LOG=${RINA_AGENT_LOG:?RINA_AGENT_LOG (run.sh)}
echo "CONFIGURED BEFORE THE RUN: links root-x and x-y; on x and y: their IPCPs, x enroller, y enrolled (as rlite 0025). AFTER THE RESTART: nothing"
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
N x rinaperf -lw -z before > /dev/null 2>&1 &
sleep 0.3; B4=$(N y rinaperf -z before -p 1 -c 5 -i 10000 -g 0 2>&1); echo "$B4" | grep "rtt min"
show() { N $1 rlite-ctl ipcps-show | grep -o "name='[^']*', dif_type='[^']*', dif_name='[^']*', address=[0-9*]*" | sort; }
BX=$(show x); BY=$(show y); echo "before, x:"; echo "$BX"; echo "before, y:"; echo "$BY"
for p in $(ip netns pids x); do [ "$(cat /proc/$p/comm)" = rinaperf ] && kill $p; done; sleep 0.2   # part C: programs do not survive yet
HP=$(pgrep -nx rina-host); echo "THE EVENT: kill -9 the host (pid $HP)"; T0=$(date +%s.%N)
kill -9 $HP; sleep 0.5
grep -h "AGENT_SURVIVES_THE_REGION_V1" $LOG/agent.*.log | sed 's/ (.*)//' | sort | uniq -c
echo "restarting the host on the same file ($RINA_REGION_FILE)"; T1=$(date +%s.%N)
unshare --pid --fork --mount --mount-proc bash -c "mount -t tmpfs none /var/run/netns && mount -t tmpfs none /sys/class/net && exec rina-host --listen $RINA_RAM --api $API" > $LOG/host-restarted.log 2>&1 &
for i in $(seq 500); do [ "$(grep -l 're-attached' $LOG/agent.*.log 2>/dev/null | wc -l)" -ge 3 ] && break; sleep 0.01; done
T2=$(date +%s.%N); echo "all three agents re-attached $(echo "($T2 - $T1) * 1000" | bc | cut -d. -f1) ms after the host was started"
grep -h "HOST_ANNOUNCES\|HOST_CHANGE_BY\|AGENT_REASSERTS" $LOG/agent.*.log $LOG/host-restarted.log
AX=$(show x); AY=$(show y); echo "after, x:"; echo "$AX"; echo "after, y:"; echo "$AY"
N x rinaperf -lw -z after > /dev/null 2>&1 &
sleep 0.3; OUT=$(N y rinaperf -z after -p 1 -c 20 -i 10000 -g 0 2>&1); RC=$?; echo "$OUT" | grep "rtt min"
re=$(grep -l "re-attached" $LOG/agent.*.log | wc -l); fwd=$(grep -h "applied, forwarded" $LOG/agent.*.log | wc -l)
echo "RESULT rc=$RC lost=$(echo "$OUT" | grep -c Timeout) of 20 ipcps_x_same=$([ "$BX" = "$AX" ] && echo yes || echo no) ipcps_y_same=$([ "$BY" = "$AY" ] && echo yes || echo no) agents_reattached=$re forwarded=$fwd"
[ $RC -eq 0 ] && [ "$(echo "$OUT" | grep -c Timeout)" -eq 0 ] && [ "$BX" = "$AX" ] && [ "$BY" = "$AY" ] && [ -n "$BX" ] && [ "$re" -ge 3 ] || exit 1
