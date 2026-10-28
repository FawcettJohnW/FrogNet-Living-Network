#!/bin/bash
# map-oracle-check.sh LISPERS_LISP_DIR -- [MAPPING_MATCHES_LISPERS_V1] the mapping region's answers against the incumbent's.
# The SAME registrations and lookups go to unmodified lispers.net (its own UDP wire, as the oracle only) and to FD&F's
# mapping region (over the session). Each case prints both answers; exit 0 only if every case agrees.
# Needs build/ and lispers.net (lisp-mapping/lisp-up.sh starts it with site 'rina-area.test', key fdf-area-key).
L=${1:?usage: map-oracle-check.sh /path/to/lispers.net/lisp}; H=$(cd "$(dirname "$0")/.." && pwd); B=$H/build/bin
IP=$(ip -4 -o addr show eth0 | awk '{print $4}' | cut -d/ -f1); API=/FawcettInnovations.RINA.ram_interface.php; bad=0
K=$(sed -n "s/^ *authentication-key = *//p" $H/lisp-mapping/lisp.config)   # the site key lispers.net is given (lisp-up.sh copies this config)
F=$(mktemp -u /tmp/map-oracle.XXXX.perm)
ss -ulnH | grep -q ":4342 " || { echo "lispers.net is not running (lisp-mapping/lisp-up.sh $L)"; exit 2; }
RINA_HOST_ROLE=mapping RINA_REGION_FILE=$F "$B/rina-host" --listen $IP:4342 --api $API > /tmp/map-oracle.host.log 2>&1 & HP=$!
for i in $(seq 100); do (exec 3<>/dev/tcp/$IP/4342) 2>/dev/null && break; sleep 0.05; done
"$B/rina-lisp" map-site rina-area.test $K $IP > /dev/null || { echo "no mapping region"; kill $HP; exit 2; }
# the answer each gives, reduced to the same words: an RLOC, "negative", or "refused"
lisp_res() { local o; o=$(timeout 3 "$B/rina-lisp" resolve "$1" $IP 2>&1); echo "$o" | grep -o -- "-> [0-9.]*" | cut -c4- || true; echo "$o" | grep -q "no RLOC\|negative" && echo negative; [ -z "$o" ] && echo "no answer"; }
regn_res() { local o; o=$("$B/rina-lisp" map-resolve "$1" $IP 2>&1); echo "$o" | grep -o -- "-> [0-9.]*" | cut -c4- || true; echo "$o" | grep -q "ENOENT" && echo negative; }
lisp_reg() { timeout 3 "$B/rina-lisp" register "$1" "$2" $IP "$3" "$4" > /dev/null 2>&1 && echo registered || echo refused; }
regn_reg() { "$B/rina-lisp" map-register "$1" "$2" $IP "$3" "$4" > /dev/null 2>&1 && echo registered || echo refused; }
case_() { local what=$1 a=$2 b=$3; local r=ok; [ "$a" = "$b" ] || { r=DIFFER; bad=1; }; printf "%-46s lispers.net: %-12s region: %-12s %s\n" "$what" "$a" "$b" "$r"; }
case_ "register the name, right key"                "$(lisp_reg rina-area.test 192.0.2.40 $K 3600)" "$(regn_reg rina-area.test 192.0.2.40 $K 3600)"
case_ "resolve it"                                  "$(lisp_res rina-area.test)" "$(regn_res rina-area.test)"
case_ "register with a WRONG key"                   "$(lisp_reg rina-area.test 10.9.9.9 wrong 3600)" "$(regn_reg rina-area.test 10.9.9.9 wrong 3600)"
case_ "resolve after the wrong key"                 "$(lisp_res rina-area.test)" "$(regn_res rina-area.test)"
case_ "re-register at a new RLOC (a move)"          "$(lisp_reg rina-area.test 192.0.2.41 $K 3600)" "$(regn_reg rina-area.test 192.0.2.41 $K 3600)"
case_ "resolve after the move"                      "$(lisp_res rina-area.test)" "$(regn_res rina-area.test)"
case_ "resolve a name nobody registered"            "$(lisp_res no-such-area.test)" "$(regn_res no-such-area.test)"
kill $HP; wait $HP 2>/dev/null; rm -f $F
echo "RESULT $([ $bad -eq 0 ] && echo PASS || echo FAIL)"; exit $bad
