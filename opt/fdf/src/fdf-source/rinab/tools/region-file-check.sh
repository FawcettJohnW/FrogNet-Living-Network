#!/bin/bash
# region-file-check.sh -- [REGION_FILE_V1] a DIF's policy survives kill -9 of the host; an IPCP (re-asserted, not
# Permanent) does not. Needs build/ (tools/build.sh, tools/build_rlite.sh). Uses port 8851 and a fresh file.
H=$(cd "$(dirname "$0")/.." && pwd); B=$H/build/bin; PORT=8851; F=$(mktemp -u /tmp/region-file-check.XXXX.perm)
export RINA_REGION_FILE=$F RINA_RAM=127.0.0.1:$PORT RINA_RAM_NETNS=/proc/$$/ns/net
API=/FawcettInnovations.RINA.ram_interface.php
start() { "$B/rina-host" --listen 127.0.0.1:$PORT --api $API > /tmp/region-file-check.host$1.log 2>&1 & HP=$!
          for i in $(seq 100); do (exec 3<>/dev/tcp/127.0.0.1/$PORT) 2>/dev/null && return 0; sleep 0.05; done; echo "host did not start"; cat /tmp/region-file-check.host$1.log; exit 1; }
start 1
"$B/rlite-ctl" ipcp-create n1 normal d1 || exit 1
"$B/rlite-ctl" dif-policy-mod d1 addralloc static || exit 1
echo "before: policies"; "$B/rlite-ctl" dif-policy-list d1; echo "before: ipcps"; "$B/rlite-ctl" ipcps-show
echo "the file:"; cat "$F"
kill -9 $HP; wait $HP 2>/dev/null
start 2
grep REGION_FILE /tmp/region-file-check.host2.log
echo "after: ipcps (none expected: an IPCP is its owner's to re-assert)"; I=$("$B/rlite-ctl" ipcps-show); echo "$I"
"$B/rlite-ctl" ipcp-create n1 normal d1 || exit 1            # the owner re-asserts its IPCP; nobody re-writes the policy
echo "after: policies"; P=$("$B/rlite-ctl" dif-policy-list d1); echo "$P"
kill $HP; wait $HP 2>/dev/null; rm -f "$F"
echo "$P" | grep -A1 addralloc | grep -q "\[static\]" && ! echo "$I" | grep -q n1 && { echo "RESULT PASS: the policy survived kill -9, the IPCP did not"; exit 0; }
echo "RESULT FAIL"; exit 1
