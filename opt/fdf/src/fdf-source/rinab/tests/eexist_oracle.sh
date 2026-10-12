#!/bin/bash
cd "$(dirname "$0")"; B=../../src/rinab/build/static; T=../../src/rinab/templates/fdf.prime
pkill -x rina-host; pkill -x fdf-client; sleep 0.5; rm -f area2.perm
RINA_REGION_FILE=$PWD/area2.perm RIBBIT_TEMPLATES=$T $B/rina-host --listen 127.0.0.1:51998 --api /FawcettInnovations.RINA.ram_interface.php >host2.log 2>&1 & sleep 1
FDF_FACTS=$PWD/fa.json RIBBIT_TEMPLATES=$T $B/fdf-client 127.0.0.1:51998 > a.log 2>&1 & PA=$!; sleep 1
FDF_FACTS=$PWD/fb.json RIBBIT_TEMPLATES=$T $B/fdf-client 127.0.0.1:51998 > b.log 2>&1 & PB=$!; sleep 2
echo "--- with A alive:"; grep -v PRIMED b.log
kill $PA; sleep 3
echo "--- after A left:"; grep -v PRIMED b.log
curl -s -X POST -d '{"op":"participants"}' "http://127.0.0.1:51998/FawcettInnovations.RINA.ram_interface.php?op=rina" | python3 -c 'import sys,json; print([(p["name"],p["alive"]) for p in json.load(sys.stdin)["participants"]])'
kill $PB; pkill -x rina-host; exit 0
