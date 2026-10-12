#!/bin/bash
# exp4-diag.sh -- exp4 INSTRUMENTED for one-shot root cause of its intermittent 9-ping loss (every layer, one clock)
# Same topology and event as exp4-readdress.sh. Writes /tmp/exp4-diag.<pid>/: rinaperf lines with timestamps, tcpdump of
# both veth ends (-tt), ip monitor (address/route/neighbour) in both namespaces, each agent's RMT route log and counts.
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
D=/tmp/exp4-diag.$$; mkdir -p $D; export RINA_RMT_STATS=$D/rmt.txt; echo "diag dir: $D"
ts() { while IFS= read -r l; do echo "$(date +%s.%N) $l"; done; }
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
for n in red green; do ip netns exec $n tcpdump -i veth.$n -tt -nn -e -s 128 -w $D/$n.pcap > /dev/null 2>&1 & echo $! >> $D/pids
  ip netns exec $n ip -ts monitor address route neigh > $D/ipmon.$n.txt 2>&1 & echo $! >> $D/pids; done; sleep 0.3
ip netns exec red rlite-ctl ipcp-create xipgateway.IPCP shim-udp4 udptunnel.DIF
ip netns exec red rlite-ctl ipcp-create xnorm.IPCP normal normal.DIF
ip netns exec red rlite-ctl ipcp-register xnorm.IPCP udptunnel.DIF
ip netns exec red rlite-ctl ipcp-enroll xnorm.IPCP normal.DIF udptunnel.DIF ynorm.IPCP
( sleep 1; echo "RE-ADDRESS at $(date +%s.%N | cut -c1-14): red 10.10.10.4 -> 10.10.10.40 (DNS still says .4)";
  ip netns exec red ip addr del 10.10.10.4/24 dev veth.red; ip netns exec red ip addr add 10.10.10.40/24 dev veth.red ) & RA=$!   # [EXP4_DIAG_WAITS_FOR_ITS_EVENT_V1] the capture jobs never end: wait for the re-address only
ip netns exec red rinaperf -z rpinst1 -p 1 -c 100 -i 30000 -g 0 2>&1 | ts > $D/rinaperf.txt; RC=${PIPESTATUS[0]}; OUT=$(cat $D/rinaperf.txt)
wait $RA
LOST=$(echo "$OUT" | grep -c "Timeout")
echo "$OUT" | grep -E "rtt|Timeout" | sort | uniq -c | head -5
for p in $(cat $D/pids); do kill $p 2>/dev/null; done; sleep 0.2
for n in red green; do tcpdump -tt -nn -e -r $D/$n.pcap > $D/$n.txt 2>/dev/null; done
grep -n "Timeout" $D/rinaperf.txt | head -3; echo "first and last timeout, and the pings around them: $D/rinaperf.txt"
echo "RESULT rc=$RC pings_lost=$LOST of 100 diag=$D"
# [EXP4_COUNTS_WHAT_RINAPERF_DID_V1] rinaperf exits 0 even after it gives up ("Stopping after 9 consecutive timeouts"):
# the exit code alone passed runs whose flow had died for good. A run fails if rinaperf gave up or sent fewer than 100.
GAVEUP=$(echo "$OUT" | grep -c "Stopping after"); SENT=$(echo "$OUT" | grep -o "[0-9]* packets transmitted" | cut -d" " -f1)
echo "rinaperf gave up: $GAVEUP; transmitted: ${SENT:-?} of 100"
[ $RC -eq 0 ] && [ "$GAVEUP" -eq 0 ] && [ "${SENT:-0}" -eq 100 ] || exit 1
