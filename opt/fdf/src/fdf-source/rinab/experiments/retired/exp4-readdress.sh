#!/bin/bash
# exp4-readdress.sh -- FD&F experiment 4: a machine changes its locator (RLOC) mid-flow; its identity does not change.
# Topology: rlite's 0030 (two machines red and green on one link with IPv4 addresses, a shim-udp4 DIF over IP, a
# normal DIF over it). A rinaperf ping runs from red to green (100 pings, 30 ms apart: rinaperf -i is in MICROseconds, so -i 30000); at about 1 s, red's address
# moves from 10.10.10.4 to 10.10.10.40. /etc/hosts keeps the OLD address on purpose: if anything still resolved by DNS,
# the flow would break. FAILS if the application sees an error, or the flow needs re-enrollment, or pings do not resume.
# Recorded: pings lost (the interruption), and what was configured before the run (the harness rule).
# [EXP4_RELIABLE_FLOW_V1] The ping flow is RELIABLE (-g 0), as in experiments 5a and 6. With an unreliable flow the one
# PDU dropped while the machine has no address (about 7 ms) is lost for good, and rinaperf then ends the test itself: its
# server stops 10 s after the last ping it received, before the client's 10 s timeout on the lost one (rinaperf.c,
# RP_DATA_WAIT_MSECS). That measured rinaperf, not the claim; evidence/exp4-loss/ has the run that showed it.
source tests/libtest.sh
# [EXP4_SHOWS_THE_GAP_WAS_HIT_V1] each agent's counters, so a run shows whether a PDU fell into the no-address gap
export RINA_RMT_STATS=$(mktemp /tmp/exp4-rmt.XXXX)
create_veth_pair veth red green
create_namespace green
create_namespace red
add_veth_to_namespace green veth.green
ip netns exec green ip addr add 10.10.10.52/24 dev veth.green
add_veth_to_namespace red veth.red
ip netns exec red ip addr add 10.10.10.4/24 dev veth.red
cp /etc/hosts /etc/hosts.save
cumulative_trap "cp /etc/hosts.save /etc/hosts" "EXIT"
echo "10.10.10.4      xnorm.IPCP" >> /etc/hosts
echo "10.10.10.52     ynorm.IPCP" >> /etc/hosts
echo "CONFIGURED BEFORE THE RUN: two namespaces, one veth link, addresses 10.10.10.4 (red) and 10.10.10.52 (green); /etc/hosts names for the two normal IPCPs (used by rlite's shim-udp4 registration); a shim-udp4 DIF and a normal DIF, red enrolled through green"
ip netns exec green rlite-ctl ipcp-create yipgateway.IPCP shim-udp4 udptunnel.DIF
ip netns exec green rlite-ctl ipcp-create ynorm.IPCP normal normal.DIF
ip netns exec green rlite-ctl ipcp-register ynorm.IPCP udptunnel.DIF
ip netns exec green rlite-ctl ipcp-enroller-enable ynorm.IPCP
start_daemon_namespace green rinaperf -lw -z rpinst1
ip netns exec red rlite-ctl ipcp-create xipgateway.IPCP shim-udp4 udptunnel.DIF
ip netns exec red rlite-ctl ipcp-create xnorm.IPCP normal normal.DIF
ip netns exec red rlite-ctl ipcp-register xnorm.IPCP udptunnel.DIF
ip netns exec red rlite-ctl ipcp-enroll xnorm.IPCP normal.DIF udptunnel.DIF ynorm.IPCP
( sleep 1; echo "RE-ADDRESS at $(date +%s.%N | cut -c1-14): red 10.10.10.4 -> 10.10.10.40 (DNS still says .4)";
  ip netns exec red ip addr del 10.10.10.4/24 dev veth.red; ip netns exec red ip addr add 10.10.10.40/24 dev veth.red ) &
OUT=$(ip netns exec red rinaperf -z rpinst1 -p 1 -c 100 -i 30000 -g 0 2>&1); RC=$?
wait
LOST=$(echo "$OUT" | grep -c "Timeout")
echo "$OUT" | grep -E "rtt|Timeout" | sort | uniq -c | head -5
echo "$OUT" | grep "rtt min"
ip netns exec red rlite-ctl terminate > /dev/null 2>&1; ip netns exec green rlite-ctl terminate > /dev/null 2>&1; sleep 0.3   # the agents write their counters as they end
NR=$(grep -o "no_route=[0-9]*" $RINA_RMT_STATS | cut -d= -f2 | paste -sd+ | bc); echo "PDUs dropped for lack of a route (the gap): ${NR:-?}"
echo "RESULT rc=$RC pings_lost=$LOST of 100 gap_drops=${NR:-?}"
# [EXP4_COUNTS_WHAT_RINAPERF_DID_V1] rinaperf exits 0 even after it gives up ("Stopping after 9 consecutive timeouts"):
# the exit code alone passed runs whose flow had died for good. A run fails if rinaperf gave up or sent fewer than 100.
GAVEUP=$(echo "$OUT" | grep -c "Stopping after"); SENT=$(echo "$OUT" | grep -o "[0-9]* packets transmitted" | cut -d" " -f1)
echo "rinaperf gave up: $GAVEUP; transmitted: ${SENT:-?} of 100"
[ $RC -eq 0 ] && [ "$GAVEUP" -eq 0 ] && [ "${SENT:-0}" -eq 100 ] || exit 1
