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
# it is this script's own cell, held on its own session (fdf-where --hold): gone when the servers stop or the node goes. On the droplet (no .1) nothing changes: fdf-services.sh
# publishes the public address as before.
#
# [COMMS_ARE_ON_THE_SYSTEMRAM_V1] (John, 27 Nov 2026: "The communicator RAM host should be set to the same machine that
# the databasehost is. It is not first-come first-served.") On a node the two servers run on the node that is the
# SystemRam (databasehost.frognet in /etc/hosts is one of this node's addresses) and on no other: this script, started
# on any node, starts them when this node is the SystemRam, stops them when it no longer is, and otherwise waits. It
# wakes when /etc/hosts changes (inotify; the merge writes it), never on a timer. So one node says where the
# Communicator is, and every reader finds the same one. FDF_HOSTS / FDF_MY_ADDRS: the hosts file and this machine's
# addresses, for the test.
set -eu
B=${COMMS_BIN:-/opt/fdf/comms}; F=${FDF_BIN:-/opt/fdf/bin}
for p in comms-ram comms-media; do [ -x "$B/$p" ] || { echo "no $B/$p: set COMMS_BIN to the Communicator's build directory"; exit 1; }; done
FACTS=${FDF_FACTS:-/opt/fdf/var/facts.json}
DOT1=$(python3 -c 'import json,sys
try: f=json.load(open(sys.argv[1]))
except Exception: sys.exit(0)
print(next((r["ip"] for r in f.get("addrs") or [] if r.get("kind")=="own" and str(r.get("ip","")).endswith(".1")),""))' "$FACTS" 2>/dev/null || true)
HOSTS=${FDF_HOSTS:-/etc/hosts}
echo "fdf-comms: fdf-comms-2026-11-27-r2 (the Communicator's servers run on the SystemRam's node)"
if [ -z "$DOT1" ]; then                                    # the droplet: no .1, no SystemRam of its own
    "$B/comms-ram" --listen 0.0.0.0:8800 & R=$!
    trap 'kill $R 2>/dev/null' EXIT
    for i in $(seq 100); do (exec 3<>/dev/tcp/127.0.0.1/8800) 2>/dev/null && break; sleep 0.05; done
    exec "$B/comms-media" --listen 0.0.0.0:8994 --ram 127.0.0.1:8800
fi
[ -f /opt/fdf/var/join.env ] && . /opt/fdf/var/join.env
[ -n "${RINA_RAM:-}" ] || { echo "fdf-comms: this node has no RINA_RAM (/opt/fdf/var/join.env): it is not in a pond"; exit 1; }
export RINA_RAM FDF_FACTS="$FACTS"
R=; M=; W=; H=
stop_servers() { [ -n "$R$M$W" ] && kill $R $M $W 2>/dev/null; wait $R $M $W 2>/dev/null || true; R=; M=; W=; }
trap 'kill $R $M $W $H 2>/dev/null' EXIT
while :; do
    # the watch is set before the file is read, so a change between the two is not missed
    coproc WATCH { exec python3 -c 'import ctypes,os,sys
p=os.path.abspath(sys.argv[1]); d,n=os.path.split(p)
c=ctypes.CDLL(None,use_errno=True); fd=c.inotify_init()
if fd<0 or c.inotify_add_watch(fd,d.encode(),0x2|0x8|0x80|0x100)<0: sys.exit("fdf-comms: no inotify watch on "+d)
print("watching",flush=True)
while True:
    b=os.read(fd,4096); i=0
    while i<len(b):
        l=int.from_bytes(b[i+12:i+16],sys.byteorder)
        if b[i+16:i+16+l].split(b"\0")[0].decode()==n: sys.exit(0)
        i+=16+l' "$HOSTS"; }
    H=$WATCH_PID
    read -r -u "${WATCH[0]}" _ || { echo "fdf-comms: the watch on $HOSTS did not start"; exit 1; }
    DB=$(awk '{for(i=2;i<=NF;i++) if($i=="databasehost.frognet"){print $1; exit}}' "$HOSTS" 2>/dev/null || true)
    MINE=${FDF_MY_ADDRS:-$(ip -o -4 addr show 2>/dev/null | awk '{sub(/\/.*/,"",$4); print $4}')}
    if [ -n "$DB" ] && echo "$MINE" | tr ' ' '\n' | grep -qxF "$DB"; then
        if [ -z "$R" ]; then
            "$B/comms-ram" --listen 0.0.0.0:8800 & R=$!
            for i in $(seq 100); do (exec 3<>/dev/tcp/127.0.0.1/8800) 2>/dev/null && break; sleep 0.05; done
            "$B/comms-media" --listen 0.0.0.0:8994 --ram 127.0.0.1:8800 & M=$!
            # [ONE_OWNER_V1] the cell is this script's own: (pond, where, w:<guid>/comms), written own:true by fdf-where --hold
            # on its own session and held while the servers run -- gone with it, written by nobody else, swept by nobody
            for i in $(seq 100); do (exec 3<>/dev/tcp/127.0.0.1/8994) 2>/dev/null && break; sleep 0.05; done
            FDF_ADDIN=comms "$F/fdf-where" --hold comms ram=self:8800 media=self:8994 & W=$!
            echo "fdf-comms: this node is the SystemRam ($DB): the Communicator is here at $DOT1:8800 / $DOT1:8994, said in the pond at $RINA_RAM (held by pid $W)"
        fi
    else
        [ -n "$R" ] && echo "fdf-comms: this node is no longer the SystemRam: the Communicator's servers here are stopped"
        stop_servers
        echo "fdf-comms: this node is not the SystemRam (databasehost.frognet is ${DB:-not in $HOSTS}): the Communicator is not run here; waiting for $HOSTS to change"
    fi
    wait -n $R $M $W $H 2>/dev/null || true
    if kill -0 $H 2>/dev/null; then echo "fdf-comms: one of the Communicator's servers here ended: stopping"; exit 1; fi
done
