#!/bin/bash
# [MAP_OWNER_IS_NOT_TRANSFERRED_V1] two live clients register the same EID with own:true: the second is REFUSED with the
# holder named; when the first leaves, the second's periodic assertion (FDF_ASSERT_SEC) registers it within seconds.
cd "$(dirname "$0")"; B=${B:-../build}; T=../templates/fdf.prime; D=$(mktemp -d)
PH=""; PA=""; PB=""; trap 'kill $PB $PA $PH 2>/dev/null' EXIT          # [KILL_BY_PID_V1] only what this oracle started
KEY=0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef
RINA_REGION_FILE=$D/area.perm RIBBIT_TEMPLATES=$T $B/rina-host --listen 127.0.0.1:51997 --api /FawcettInnovations.RINA.ram_interface.php >$D/host.log 2>&1 & PH=$!; sleep 1
API="http://127.0.0.1:51997/FawcettInnovations.RINA.ram_interface.php?op=rina"
curl -s -X POST -d "{\"op\":\"map_site\",\"name\":\"t\",\"key\":\"$KEY\"}" "$API" >/dev/null
mk() { echo "{\"role\":\"node\",\"name\":\"$1\",\"guid\":\"g-$1\",\"subnet\":\"$2\",\"pubkey\":\"P$1\",\"maps\":[{\"eid\":\"10.9.9.0/24\",\"rlocs\":\"hubA:1:50\"}]}" > $D/$1.json; }
mk a 10.1.1.0/24; mk b 10.2.2.0/24
export RINA_AREA=t RINA_SITE_KEY=$KEY RIBBIT_TEMPLATES=$T
FDF_FACTS=$D/a.json FDF_NAME=a $B/bin/fdf-client 127.0.0.1:51997 > $D/a.log 2>&1 & PA=$!; sleep 1.5
FDF_FACTS=$D/b.json FDF_NAME=b FDF_ASSERT_SEC=2 $B/bin/fdf-client 127.0.0.1:51997 > $D/b.log 2>&1 & PB=$!; sleep 2
fail=0; chk() { if grep -q "$2" $1; then echo "PASS  $3"; else echo "FAIL  $3"; fail=1; fi; }
chk $D/a.log "mapping 10.9.9.0/24 -> hubA:1:50 registered" "a registers 10.9.9.0/24"
chk $D/b.log "REFUSED by the region: HTTP 200: EEXIST" "b, alive beside a, is refused"
chk $D/b.log "held by a live session (node:w:g-a)" "the holder is named"
LIST() { curl -s -X POST -d '{"op":"map_list","site":"t"}' "$API"; }
echo "   region: $(LIST)"
kill $PA; sleep 6
chk $D/b.log "mapping 10.9.9.0/24 -> hubA:1:50 registered" "after a left: b's assertion registers it"
echo "   region: $(LIST)"
echo "--- b.log:"; grep -v PRIMED $D/b.log | head -6; kill $PB $PH; rm -rf $D; echo "RESULT $([ $fail = 0 ] && echo PASS || echo FAIL)"; exit $fail
