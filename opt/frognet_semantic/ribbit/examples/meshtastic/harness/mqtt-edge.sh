#!/bin/bash
# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
#
# mqtt-edge.sh -- D6, MQTT as the optional edge [MT_MQTT_EDGE_V1], on this host, a broker as the only link off the memory:
#   S  stock meshtasticd, UDP off, MQTT on (uplink + downlink on channel 0)
#   M  a FrogNet node, UDP off, MQTT on: the memory's MQTT edge
#   G  a FrogNet node on the memory only
# Checks: G lists S; S lists G; S's broadcast reaches G's client; G's reaches S's client; a DM each way.
#   mqtt-edge.sh STOCK_BIN FRONT_BIN
set -u
STOCK=${1:?stock meshtasticd}; FRONT=${2:?front meshtasticd}
EX=$(cd "$(dirname "$0")/.." && pwd); PY=/home/claude/mesh/venv-mpy/bin/python
OUT=$EX/results/mqtt-$(date +%Y%m%d-%H%M%S); mkdir -p $OUT/{s,m,g}/vfs
echo "== $(cat $EX/VERSION)"; echo "== stock $STOCK"; echo "== front $FRONT"; echo "== results $OUT"
for n in s m g; do
  printf 'Lora:\n  Module: sim\nLogging:\n  LogLevel: debug\nConfig:\n  EnableUDP: false\nGeneral:\n  MaxNodes: 200\n' > $OUT/$n/config.yaml
done
BPORT=18831; PORT=18897
cat > $OUT/broker.yaml <<Y
listeners:
  default:
    type: tcp
    bind: 127.0.0.1:$BPORT
    max_connections: 50
auth:
  allow-anonymous: true
plugins:
  - amqtt.plugins.authentication.AnonymousAuthPlugin
Y
/home/claude/mesh/venv-mpy/bin/amqtt -c $OUT/broker.yaml > $OUT/broker.log 2>&1 < /dev/null & B=$!
"$EX/bin/meshtastic-ram" --listen 127.0.0.1:$PORT > $OUT/ram.log 2>&1 < /dev/null & RAM=$!
sleep 1
"$STOCK" -c $OUT/s/config.yaml -h 71 -p 4591 -d $OUT/s/vfs -e > $OUT/s/node.log 2>&1 < /dev/null & S=$!
MESHTASTIC_RIBBIT=127.0.0.1:$PORT "$FRONT" -c $OUT/m/config.yaml -h 72 -p 4592 -d $OUT/m/vfs -e > $OUT/m/node.log 2>&1 < /dev/null & M=$!
MESHTASTIC_RIBBIT=127.0.0.1:$PORT "$FRONT" -c $OUT/g/config.yaml -h 73 -p 4593 -d $OUT/g/vfs -e > $OUT/g/node.log 2>&1 < /dev/null & G=$!
cleanup(){ kill $S $M $G $RAM $B 2>/dev/null; wait 2>/dev/null; }
trap cleanup EXIT
sleep 6
mt(){ $PY -m meshtastic --host 127.0.0.1:$1 "${@:2}" 2>&1 | grep -v "^$" | tail -1; }
for p in 4591 4592 4593; do mt $p --set lora.region US; done; sleep 6
for p in 4591 4592; do mt $p --set mqtt.enabled true --set mqtt.address 127.0.0.1:$BPORT --set mqtt.encryption_enabled true; done; sleep 6
for p in 4591 4592; do mt $p --ch-index 0 --ch-set uplink_enabled true --ch-set downlink_enabled true; done
echo "== waiting 45 s: the nodes reboot on the channel change; stock announces its NodeInfo ~30 s after"
sleep 45
$PY - "$OUT" <<'EOF2' | tee $OUT/mqtt.log
import json, sys, time, threading
from meshtastic.tcp_interface import TCPInterface
from pubsub import pub
out = sys.argv[1]
T0 = time.time()
def log(*a): print(f"[{time.time()-T0:7.2f}] [MQTT-EDGE]", *a, flush=True)
def connect(port):
    for _ in range(30):
        try: return TCPInterface("127.0.0.1", portNumber=port)
        except Exception: time.sleep(1)
    raise RuntimeError(port)
nums, seen = {}, {}
for name, port in (("S", 4591), ("M", 4592), ("G", 4593)):
    i = connect(port); time.sleep(1.5); nums[name] = i.myInfo.my_node_num
    seen[name] = {n["num"] for n in i.nodes.values() if n["num"] != i.myInfo.my_node_num}
    log(f"{name} !{nums[name]:08x} sees {sorted(f'!{n:08x}' for n in seen[name])}"); i.close()
res = {"L_G_lists_S": {"pass": nums["S"] in seen["G"]}, "L_S_lists_G": {"pass": nums["G"] in seen["S"]}}
log("L_G_lists_S", res["L_G_lists_S"]); log("L_S_lists_G", res["L_S_lists_G"])
got, lock = [], threading.Lock()
def on_text(packet=None, interface=None, **_):
    with lock: got.append((interface.hostname + ":" + str(interface.portNumber), f"!{packet.get('from',0):08x}", packet.get("decoded", {}).get("text")))
pub.subscribe(on_text, "meshtastic.receive.text")
def wait_for(pred, secs):
    end = time.time() + secs
    while time.time() < end:
        with lock:
            if pred(got): return True
        time.sleep(0.2)
    return False
def scenario(key, src_port, dst_port, text, dest=None):
    with lock: got.clear()
    dst = connect(dst_port); time.sleep(1.5)
    src = connect(src_port); time.sleep(1.5)
    t = time.time()
    if dest is None: src.sendText(text, wantAck=False)
    else: src.sendText(text, destinationId=dest, wantAck=False)
    ok = wait_for(lambda g: any(p.endswith(str(dst_port)) and tx == text for p, _, tx in g), 20)
    res[key] = {"pass": ok, "seconds": round(time.time() - t, 2)}
    log(key, res[key]); src.close(); dst.close(); time.sleep(2)
scenario("B1_S_broadcast_to_G", 4591, 4593, "MQ-S-bcast")
scenario("B2_G_broadcast_to_S", 4593, 4591, "MQ-G-bcast")
scenario("D1_S_dm_to_G", 4591, 4593, "MQ-S-dm", dest=nums["G"])
scenario("D2_G_dm_to_S", 4593, 4591, "MQ-G-dm", dest=nums["S"])
verdict = all(v["pass"] for v in res.values())
json.dump({"nums": {k: f"!{v:08x}" for k, v in nums.items()}, "results": res, "pass": verdict}, open(f"{out}/results.json", "w"), indent=1)
log("VERDICT", "PASS" if verdict else "FAIL", {k: v["pass"] for k, v in res.items()})
sys.exit(0 if verdict else 1)
EOF2
RC=${PIPESTATUS[0]}
grep -E "bridge:|MQTT Publish|Received MQTT" $OUT/m/node.log | sed 's/.*\] //' | cut -c1-120 | head -12
exit $RC
