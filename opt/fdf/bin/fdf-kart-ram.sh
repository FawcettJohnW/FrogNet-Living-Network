#!/bin/bash
# fdf-kart-ram.sh -- the kart service: the kart memory (a rina-host, port 8799, API /ram.php) and, beside it, the kart
# service that is the lobby's intelligence (/opt/fdf/kart/kart_service.py, [KART_SERVICE_V1]). Everyone connects here
# first: players run  python3 /opt/fdf/kart/kartdemo.py --name <name> --kart <this machine>:8799  (or --fdf, which finds
# it through fdf.where "race"). The memory's file, /opt/fdf/var/kart.perm, keeps results and records across restarts
# ([PERMANENT_CELL_V1]). Needs this rina-host at fdf-2026-10-18-r22 or later (permanent tuples); the service is in fdf-2026-10-19-r23.
# KART_MIN_PLAYERS (default 2; /opt/fdf/var/kart.env under the unit): a game launches by itself when that many free
# players are all ready.
# [KART_SERVICE_UNIT_V1] run by fdf-kart.service (journalctl -u fdf-kart). It ends when either process ends, so the unit
# starts both again; a kart memory without its service, or a service without its memory, is not left running.
# Open TCP 8799 to the players.
set -u; . /opt/fdf/fdf.env; mkdir -p /opt/fdf/var
echo "the kart memory on port 8799, and the kart service beside it (players: --kart <this machine's address>:8799)"
env -u RINA_MAP_AREA RINA_REGION_FILE=/opt/fdf/var/kart.perm rina-host --listen 0.0.0.0:8799 --api /ram.php & MEM=$!
for i in $(seq 100); do (exec 3<>/dev/tcp/127.0.0.1/8799) 2>/dev/null && break; sleep 0.1; done
python3 /opt/fdf/kart/kart_service.py --memory 127.0.0.1:8799 --min-players "${KART_MIN_PLAYERS:-2}" & SVC=$!
trap 'kill $MEM $SVC 2>/dev/null' TERM INT
wait -n $MEM $SVC; RC=$?
kill -0 $MEM 2>/dev/null && echo "fdf-kart-ram: the kart service ended (exit $RC): stopping the kart memory too" || echo "fdf-kart-ram: the kart memory ended (exit $RC): stopping the kart service too"
kill $MEM $SVC 2>/dev/null; wait; exit 1
