#!/bin/bash
# discovery-check.sh -- [FDF_DISCOVERY_V1] three machines announce; the participants query lists them; one leaves.
# Each machine: its own network namespace, its own hostname (UTS namespace) and address, its own agent, told only where
# the RAM manager is (name-only joining is plan item 3), and reaching it from ITS OWN network (RINA_RAM_NETNS=/proc/self/ns/net),
# so every session crosses the shared network the capture watches. PASS only if all three are listed with the right name, IP and a
# capability, all alive; after one leaves, it is listed not alive and the other two still alive; and NO packet passed between two machines.
set -u; H=$(cd "$(dirname "$0")/.." && pwd); B=$H/build/bin; API=/FawcettInnovations.RINA.ram_interface.php; PORT=8871
PROBE=${RINA_CAPABILITY_PROBE:-/tmp/frognet-src/usr/local/bin/frognet_capability_probe.sh}
D=$(mktemp -d /tmp/discovery-check.XXXX); echo "diag dir: $D"
export RINA_RAM=10.60.0.1:$PORT RINA_RAM_NETNS=/proc/self/ns/net RINA_AGENT_LOG=$D RINA_CAPABILITY_PROBE=$PROBE RINA_REGION_FILE=$D/region.perm PATH=$B:$PATH
cleanup() { for i in 1 2 3; do ip netns exec dm$i rlite-ctl terminate > /dev/null 2>&1; done; sleep 0.3; kill ${HP:-} 2>/dev/null
            for i in 1 2 3; do for p in $(ip netns pids dm$i 2>/dev/null); do kill -9 $p; done; ip netns delete dm$i 2>/dev/null; done; ip link delete dmbr 2>/dev/null; }
cleanup; trap cleanup EXIT
ip link add dmbr type bridge; ip addr add 10.60.0.1/24 dev dmbr; ip link set dmbr up       # the network the RAM manager is on
for i in 1 2 3; do ip netns add dm$i; ip netns exec dm$i ip link set lo up
    ip link add v$i type veth peer name b$i; ip link set b$i master dmbr; ip link set b$i up; ip link set v$i netns dm$i
    ip netns exec dm$i ip addr add 10.60.0.1$i/24 dev v$i; ip netns exec dm$i ip link set v$i up; done
# [FDF_NO_MEMBER_TO_MEMBER_V1] capture the shared network for the whole run: no packet may pass between two machines
tcpdump -i dmbr -nn -U -w $D/wire.pcap > /dev/null 2>&1 & TD=$!; sleep 0.5
rina-host --listen 10.60.0.1:$PORT --api $API > $D/host.log 2>&1 & HP=$!
for i in $(seq 100); do (exec 3<>/dev/tcp/10.60.0.1/$PORT) 2>/dev/null && break; sleep 0.05; done
for i in 1 2 3; do ip netns exec dm$i unshare --uts bash -c "hostname machine-$i; exec rlite-uipcps -d" || echo "machine-$i: agent failed"; done
for i in $(seq 100); do [ "$(grep -h 'FDF_DISCOVERY_V1\] announced' $D/agent.*.log 2>/dev/null | wc -l)" -ge 3 ] && break; sleep 0.05; done
echo "== participants, all three up"; P1=$(fdf-participants); echo "$P1" | tee $D/participants-1.json
ip netns exec dm2 rlite-ctl terminate > /dev/null 2>&1; sleep 0.5
echo "== participants, machine-2 gone"; P2=$(fdf-participants); echo "$P2" | tee $D/participants-2.json
kill $TD 2>/dev/null; wait $TD 2>/dev/null
M2M=$(tcpdump -nn -r $D/wire.pcap 'src net 10.60.0.0/24 and dst net 10.60.0.0/24 and not host 10.60.0.1' 2>/dev/null | wc -l)
ALL=$(tcpdump -nn -r $D/wire.pcap 2>/dev/null | wc -l)
echo "wire: $ALL packets on the shared network; $M2M between two machines"; echo "$M2M" > $D/m2m.count
python3 - "$D" <<'PY'
import json, sys
D = sys.argv[1]; p1 = {p["hostname"]: p for p in json.load(open(D + "/participants-1.json"))}; p2 = {p["hostname"]: p for p in json.load(open(D + "/participants-2.json"))}
ok = sorted(p1) == ["machine-1", "machine-2", "machine-3"] and all(p1[f"machine-{i}"]["ip"] == f"10.60.0.1{i}" and p1[f"machine-{i}"]["alive"] and p1[f"machine-{i}"]["cores"] > 0 for i in (1, 2, 3))
ok = ok and int(open(D + "/m2m.count").read()) == 0 and p2["machine-2"]["alive"] is False and p2["machine-1"]["alive"] and p2["machine-3"]["alive"]
print("RESULT", "PASS" if ok else "FAIL"); sys.exit(0 if ok else 1)
PY
