#!/bin/bash
# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
#
# mixed-mesh.sh -- Phase 3, the radio edge [MT_RADIO_EDGE_V1], on this host with UDP multicast as the "air":
#   S  stock meshtasticd, UDP on, not on the memory
#   F  a FrogNet node with UDP on: it hears S and bridges (D8 both ways)
#   G  a FrogNet node on the memory only (UDP off)
# Checks: G lists S; S's broadcast reaches G's client; G's broadcast reaches S's client; a DM S->G and a DM G->S.
#   mixed-mesh.sh STOCK_BIN FRONT_BIN
set -u
STOCK=${1:?stock meshtasticd}; FRONT=${2:?front meshtasticd}
EX=$(cd "$(dirname "$0")/.." && pwd); PY=/home/claude/mesh/venv-mpy/bin/python
OUT=$EX/results/mixed-$(date +%Y%m%d-%H%M%S); mkdir -p $OUT/{s,f,g}/vfs
echo "== $(cat $EX/VERSION)"; echo "== stock $STOCK"; echo "== front $FRONT"; echo "== results $OUT"
for n in s f g; do
  udp=true; [ $n = g ] && udp=false
  printf 'Lora:\n  Module: sim\nLogging:\n  LogLevel: debug\nConfig:\n  EnableUDP: %s\nGeneral:\n  MaxNodes: 200\n' $udp > $OUT/$n/config.yaml
done
ip route del blackhole 224.0.0.69/32 2>/dev/null; ip route del blackhole 239.0.0.69/32 2>/dev/null
PORT=18896
setsid "$EX/bin/meshtastic-ram" --listen 127.0.0.1:$PORT > $OUT/ram.log 2>&1 < /dev/null & RAM=$!
sleep 0.5
setsid "$STOCK" -c $OUT/s/config.yaml -h 61 -p 4581 -d $OUT/s/vfs -e > $OUT/s/node.log 2>&1 < /dev/null & S=$!
MESHTASTIC_RIBBIT=127.0.0.1:$PORT setsid "$FRONT" -c $OUT/f/config.yaml -h 62 -p 4582 -d $OUT/f/vfs -e > $OUT/f/node.log 2>&1 < /dev/null & F=$!
MESHTASTIC_RIBBIT=127.0.0.1:$PORT setsid "$FRONT" -c $OUT/g/config.yaml -h 63 -p 4583 -d $OUT/g/vfs -e > $OUT/g/node.log 2>&1 < /dev/null & G=$!
cleanup(){ kill $S $F $G $RAM 2>/dev/null; wait 2>/dev/null; }
trap cleanup EXIT
echo "== waiting 40 s: stock announces its NodeInfo over UDP ~30 s after start"
sleep 40
$PY - "$OUT" <<'EOF' | tee $OUT/mixed.log
import json, sys, time, threading
from meshtastic.tcp_interface import TCPInterface
from pubsub import pub
out = sys.argv[1]
T0 = time.time()
def log(*a): print(f"[{time.time()-T0:7.2f}] [MIXED]", *a, flush=True)
def connect(port):
    for _ in range(30):
        try: return TCPInterface("127.0.0.1", portNumber=port)
        except Exception: time.sleep(1)
    raise RuntimeError(port)
nums = {}
for name, port in (("S", 4581), ("F", 4582), ("G", 4583)):
    i = connect(port); time.sleep(1.5); nums[name] = i.myInfo.my_node_num
    seen = sorted(f"!{n['num']:08x}" for n in i.nodes.values() if n["num"] != i.myInfo.my_node_num)
    log(f"{name} !{nums[name]:08x} sees {seen}"); i.close()
res = {}
# L: G lists S
g = connect(4583); time.sleep(1.5)
res["L_G_lists_S"] = {"pass": nums["S"] in {n["num"] for n in g.nodes.values()}}
g.close(); log("L", res["L_G_lists_S"])
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
scenario("B1_S_broadcast_to_G", 4581, 4583, "MIX-S-bcast")
scenario("B2_G_broadcast_to_S", 4583, 4581, "MIX-G-bcast")
scenario("D1_S_dm_to_G", 4581, 4583, "MIX-S-dm", dest=nums["G"])
scenario("D2_G_dm_to_S", 4583, 4581, "MIX-G-dm", dest=nums["S"])
verdict = all(v["pass"] for v in res.values())
json.dump({"nums": {k: f"!{v:08x}" for k, v in nums.items()}, "results": res, "pass": verdict}, open(f"{out}/results.json", "w"), indent=1)
log("VERDICT", "PASS" if verdict else "FAIL", {k: v["pass"] for k, v in res.items()})
sys.exit(0 if verdict else 1)
EOF
RC=${PIPESTATUS[0]}
grep -E "bridge:" $OUT/f/node.log | sed 's/.*\[MT_RIBBIT\] //' | cut -c1-120 | head -12
exit $RC
