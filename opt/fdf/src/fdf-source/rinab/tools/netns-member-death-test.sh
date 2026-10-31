#!/bin/bash
# netns-member-death-test.sh -- [FDF_MEMBER_KT_V1] the Kotlin member's liveness, measured the way
# rinab/tools/netns-latency-test.sh measures fdf-client's, side by side with fdf-client in the same topology:
#   (1) clean death: the member's process killed (-9); its sockets close   -> first event on a watcher (fdf-events)
#   (2) silent death: the member's link goes down (no FIN)                 -> first event on the watcher (keepalive)
#   (3) silent region: packets from the region dropped at the member        -> the MEMBER says its session ended
#       (proves the member's own keepalive 5+3x5 s: without it the OS default is 2 h)
# usage: bash netns-member-death-test.sh <dir with rina-host, fdf-client, fdf-events> <fdf-member-kt launcher>
set -u
B=$1; K=$2; T=$(mktemp -d); API=/FawcettInnovations.RINA.ram_interface.php
ms() { date +%s%3N; }
cleanup() { for n in hub cpp kt w; do ip netns pids $n 2>/dev/null | xargs -r kill -9; ip netns del $n 2>/dev/null; done; ip link del br-m 2>/dev/null; }
trap cleanup EXIT; cleanup
ip link add br-m type bridge && ip link set br-m up
i=1; for n in hub cpp kt w; do ip netns add $n; ip netns exec $n ip link set lo up
  ip link add v-$n type veth peer name v-$n-b; ip link set v-$n netns $n; ip link set v-$n-b master br-m up
  ip netns exec $n ip addr add 10.77.0.$i/24 dev v-$n; ip netns exec $n ip link set v-$n up; i=$((i+2)); done
ip netns exec hub env RINA_REGION_FILE=$T/area.perm $B/rina-host --listen 10.77.0.1:8840 --api $API > $T/host.log 2>&1 &
sleep 1
echo '{"role":"leaf","name":"cpp-leaf","guid":"cpp-leaf-1","pubkey":"PUBcpp"}' > $T/cpp.json
echo '{"role":"leaf","name":"kt-leaf","guid":"kt-leaf-1","pubkey":"PUBkt"}' > $T/kt.json
start() {   # start <ns> <log>: the member of that ns
  if [ $1 = cpp ]; then ip netns exec cpp env RINA_RAM=10.77.0.1:8840 FDF_FACTS=$T/cpp.json $B/fdf-client > $T/$2 2>&1 &
  else ip netns exec kt env RINA_RAM=10.77.0.1:8840 FDF_FACTS=$T/kt.json $K > $T/$2 2>&1 & fi
  for j in $(seq 100); do grep -q "joined the region" $T/$2 2>/dev/null && return 0; sleep 0.1; done; echo "  $1 did not join"; cat $T/$2; return 1; }
watcher() { ip netns exec w env RINA_RAM=10.77.0.1:8840 $B/fdf-events 2>/dev/null | while IFS= read -r l; do echo "$(ms) $l"; done > $T/$1 &
  for j in $(seq 50); do grep -q ready $T/$1 2>/dev/null && return; sleep 0.1; done; }
first_event() { for j in $(seq 1200); do l=$(grep -m1 -P "^[0-9]+ (p|m)(\t|$)" $T/$1 2>/dev/null); [ -n "$l" ] && { echo $l; return; }; sleep 0.05; done; echo "none"; }
pid_in() { ip netns pids $1 | while read p; do c=$(cat /proc/$p/comm 2>/dev/null); [ "$c" = fdf-client ] || [ "$c" = java ] && echo $p; done | head -1; }
for who in cpp kt; do
  echo "=== $who"
  # (1) clean
  start $who c1.$who.log || exit 1; sleep 1; watcher d1.$who.log
  t0=$(ms); kill -9 $(pid_in $who); e=$(first_event d1.$who.log)
  echo "  (1) clean death  -> '$(echo $e | awk '{print $2}')' on the watcher after $(( $(echo $e | awk '{print $1}') - t0 )) ms"
  ip netns pids w | xargs -r kill 2>/dev/null; sleep 0.5
  # (2) silent: the member's link down
  start $who c2.$who.log || exit 1; sleep 1; watcher d2.$who.log
  t0=$(ms); ip netns exec $who ip link set v-$who down; e=$(first_event d2.$who.log)
  echo "  (2) silent death -> '$(echo $e | awk '{print $2}')' on the watcher after $(( $(echo $e | awk '{print $1}') - t0 )) ms"
  ip netns pids w | xargs -r kill 2>/dev/null; ip netns pids $who | xargs -r kill -9; ip netns exec $who ip link set v-$who up; sleep 0.5
  # (3) silent region, seen by the member: drop everything from the region at the member
  start $who c3.$who.log || exit 1; sleep 1
  t0=$(ms); ip netns exec $who iptables -A INPUT -s 10.77.0.1 -j DROP
  for j in $(seq 1200); do grep -q -E "session to .* ended|session ended|ending this session" $T/c3.$who.log && break; sleep 0.05; done; t1=$(ms)
  echo "  (3) silent region -> the member says its session ended after $((t1 - t0)) ms: $(grep -h -E "ended|ending this session" $T/c3.$who.log | head -1 | cut -c1-160)"
  ip netns exec $who iptables -F; ip netns pids $who | xargs -r kill -9; sleep 0.5
done
echo "--- host.log (sessions)"; grep -E "session (up|down)" $T/host.log | sed 's/^/    /'
echo "RESULT DONE ($T)"
