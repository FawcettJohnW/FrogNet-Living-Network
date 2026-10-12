#!/bin/bash
# map-region-check.sh -- [AREA_MAPPING_IN_THE_REGION_V1] LISP's mapping semantics in a region, over the session. Needs
# build/. Starts its own mapping host on 127.0.0.1:4342 (port 4342 must be free). Exit 0 only if every case is right.
H=$(cd "$(dirname "$0")/.." && pwd); B=$H/build/bin; M=127.0.0.1; F=$(mktemp -u /tmp/map-region-check.XXXX.perm)
API=/FawcettInnovations.RINA.ram_interface.php; bad=0
start() { RINA_HOST_ROLE=mapping RINA_REGION_FILE=$F "$B/rina-host" --listen $M:4342 --api $API > /tmp/map-region-check.host$1.log 2>&1 & HP=$!
          for i in $(seq 100); do (exec 3<>/dev/tcp/$M/4342) 2>/dev/null && return 0; sleep 0.05; done; echo "mapping host did not start"; exit 1; }
want() { local what=$1 expect=$2; shift 2; out=$("$@" 2>&1); rc=$?
         if { [ "$expect" = ok ] && [ $rc -eq 0 ]; } || { [ "$expect" != ok ] && [ $rc -ne 0 ] && echo "$out" | grep -q "$expect"; }; then r=ok; else r=FAIL; bad=1; fi
         printf "%-52s %-4s %s\n" "$what" "$r" "$(echo "$out" | tail -1)"; }
timeout 30 tcpdump -i any -nn -c 1 udp port 4342 > /tmp/map-region-check.udp 2>&1 & TD=$!; sleep 0.5
start 1
want "site for the area name"                       ok      $B/rina-lisp map-site rina-area.test k1 $M
want "registration, right key"                      ok      $B/rina-lisp map-register rina-area.test 192.0.2.2 $M k1 3600
want "resolve: the RLOC"                            ok      $B/rina-lisp map-resolve rina-area.test $M
want "registration, WRONG key: refused"             EPERM   $B/rina-lisp map-register rina-area.test 10.9.9.9 $M wrong 3600
want "resolve after the refused one: unchanged"     ok      sh -c "$B/rina-lisp map-resolve rina-area.test $M | grep -q 192.0.2.2"
want "unknown name: negative answer"                ENOENT  $B/rina-lisp map-resolve no-such-area $M
want "short registration (ttl 1 s)"                 ok      sh -c "$B/rina-lisp map-site short.test k2 $M && $B/rina-lisp map-register short.test 192.0.2.7 $M k2 1"
sleep 1.5
want "after its ttl: expired, negative"             ENOENT  $B/rina-lisp map-resolve short.test $M
kill -9 $HP; wait $HP 2>/dev/null; start 2
want "kill -9 + restart: the registration is back"  ok      sh -c "$B/rina-lisp map-resolve rina-area.test $M | grep -q 192.0.2.2"
grep -h "REGION_FILE_V1" /tmp/map-region-check.host2.log
kill $HP; wait $HP 2>/dev/null; kill $TD 2>/dev/null; wait $TD 2>/dev/null
udp=$(grep -c "UDP\|udp" /tmp/map-region-check.udp | head -1); grep -q "^0 packets captured" /tmp/map-region-check.udp && udp=0
echo "UDP 4342 packets during the check: ${udp}"; [ "$udp" = 0 ] || bad=1
rm -f $F; echo "RESULT $([ $bad -eq 0 ] && echo PASS || echo FAIL)"; exit $bad
