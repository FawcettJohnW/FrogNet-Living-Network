#!/bin/bash
# map-expiry-compare.sh LISPERS_LISP_DIR -- [MAPPING_MATCHES_LISPERS_V1] when does an expired registration stop being
# answered? The same name, TTL 5 s, registered at the same moment in lispers.net (oracle) and the mapping region; both
# asked once a second until both answer negative (at most 80 s). Prints the seconds after registration for each.
# lispers.net expires sites from a 60 s timer (LISP_SITE_TIMEOUT_CHECK_INTERVAL); the region checks the TTL when read.
L=${1:?LISPERS_LISP_DIR}; H=$(cd "$(dirname "$0")/.." && pwd); B=$H/build/bin; K=fdf-area-password; N=rina-area.test
IP=$(ip -4 -o addr show eth0 | awk '{print $4}' | cut -d/ -f1); API=/FawcettInnovations.RINA.ram_interface.php; F=$(mktemp -u /tmp/map-exp.XXXX.perm)
ss -ulnH | grep -q ":4342 " || { echo "lispers.net is not running"; exit 2; }
RINA_HOST_ROLE=mapping RINA_REGION_FILE=$F "$B/rina-host" --listen $IP:4342 --api $API > /dev/null 2>&1 & HP=$!
for i in $(seq 100); do (exec 3<>/dev/tcp/$IP/4342) 2>/dev/null && break; sleep 0.05; done
"$B/rina-lisp" map-site $N $K $IP > /dev/null
T0=$(date +%s.%N)
timeout 3 "$B/rina-lisp" register $N 192.0.2.50 $IP $K 5 > /dev/null 2>&1; "$B/rina-lisp" map-register $N 192.0.2.50 $IP $K 5 > /dev/null 2>&1
LE=""; RE=""
while [ -z "$LE" ] || [ -z "$RE" ]; do
    t=$(echo "$(date +%s.%N) - $T0" | bc); [ "$(echo "$t > 80" | bc)" = 1 ] && break
    [ -z "$LE" ] && ! timeout 3 "$B/rina-lisp" resolve $N $IP 2>&1 | grep -q -- "-> 192.0.2.50" && LE=$(printf "%.1f" $t)
    [ -z "$RE" ] && ! "$B/rina-lisp" map-resolve $N $IP 2>&1 | grep -q -- "-> 192.0.2.50" && RE=$(printf "%.1f" $t)
    sleep 1
done
kill $HP; wait $HP 2>/dev/null; rm -f $F
printf "%-30s %s\n" "registration TTL" "5 s" "lispers.net first negative at" "${LE:-not within 80} s" "mapping region first negative at" "${RE:-not within 80} s"
