#!/bin/bash
# fdf-comms.sh -- [KART_CALLS_V1] the real Communicator's shared memory (comms-ram, port 8800) and media server
# (comms-media, port 8994), on the same machine as the race. The kart demo's Call buttons open comms-app against these.
# COMMS_BIN: the directory with comms-ram and comms-media (the Communicator's build/; default below).
# Foreground; both print to this terminal; Ctrl-C stops both. Open TCP 8800 and 8994 to the players.
#
# [COMMS_ON_A_NODE_V1] On a FrogNet node (its facts.json has its own .1), the two servers are the network's own: no cloud
# service. The node says where they are in its pond's memory -- (pond, where, w:<guid>) {"comms":{"ram":"w:<guid>:8800",
# "media":"w:<guid>:8994"}} (fdf-where self:<port>; IDENTITY_IN_TUPLES_V1), resolved by each reader to this node's address
# in the reader's scope -- so the phone's Communicator and every member find them with nothing typed. The cell is the node's:
# the region removes it when the node's session ends, and this script writes it again on the event that shows the node
# back (fdf-events), and clears it when the servers stop. On the droplet (no .1) nothing changes: fdf-services.sh
# publishes the public address as before.
set -eu
B=${COMMS_BIN:-/opt/fdf/comms}; F=${FDF_BIN:-/opt/fdf/bin}
for p in comms-ram comms-media; do [ -x "$B/$p" ] || { echo "no $B/$p: set COMMS_BIN to the Communicator's build directory"; exit 1; }; done
FACTS=${FDF_FACTS:-/opt/fdf/var/facts.json}
DOT1=$(python3 -c 'import json,sys
try: f=json.load(open(sys.argv[1]))
except Exception: sys.exit(0)
print(next((r["ip"] for r in f.get("addrs") or [] if r.get("kind")=="own" and str(r.get("ip","")).endswith(".1")),""))' "$FACTS" 2>/dev/null || true)
"$B/comms-ram" --listen 0.0.0.0:8800 & R=$!
trap 'kill $R 2>/dev/null' EXIT
for i in $(seq 100); do (exec 3<>/dev/tcp/127.0.0.1/8800) 2>/dev/null && break; sleep 0.05; done
if [ -z "$DOT1" ]; then
    exec "$B/comms-media" --listen 0.0.0.0:8994 --ram 127.0.0.1:8800
fi
# ---- a node: the servers, then the cell that says where they are, kept while they run
[ -f /opt/fdf/var/join.env ] && . /opt/fdf/var/join.env
[ -n "${RINA_RAM:-}" ] || { echo "fdf-comms: this node has no RINA_RAM (/opt/fdf/var/join.env): it is not in a pond"; exit 1; }
export RINA_RAM FDF_FACTS="$FACTS"
"$B/comms-media" --listen 0.0.0.0:8994 --ram 127.0.0.1:8800 & M=$!
trap 'kill $R $M 2>/dev/null; "$F/fdf-where" comms --clear || true' EXIT
for i in $(seq 100); do (exec 3<>/dev/tcp/127.0.0.1/8994) 2>/dev/null && break; sleep 0.05; done
# [IDENTITY_IN_TUPLES_V1] the cell names this node by identity (w:<guid>:<port>); each reader resolves it in its own scope
say() { "$F/fdf-where" comms ram=self:8800 media=self:8994; }
say || echo "fdf-comms: could not write where the Communicator is (the next region event retries)"
echo "fdf-comms: the Communicator on this node at $DOT1:8800 / $DOT1:8994, said in the pond at $RINA_RAM"
# the region removes the cell with this node's session; on each event, if it is gone and the servers run, write it again
"$F/fdf-events" | while IFS= read -r ev; do
    kill -0 $M 2>/dev/null || break
    "$F/fdf-where" | grep -q '^  comms ' || { echo "fdf-comms: the cell is gone ($ev); writing it again"; say || true; }
done
wait $M
