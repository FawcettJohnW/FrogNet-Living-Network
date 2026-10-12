#!/bin/bash
# dashboard-check.sh -- [FDF_DASHBOARD_V1] the FD&F dashboard's server side, as a browser uses it: the page is served;
# getParticipants.php returns the participants; waitParticipants.php?gen=0 is HELD until the set changes and then returns
# it. PASS only if all three, and the held request did not return before the change.
set -u; H=$(cd "$(dirname "$0")/.." && pwd); B=$H/build/bin; D=$(mktemp -d /tmp/dashboard-check.XXXX); echo "diag dir: $D"
export RINA_RAM=127.0.0.1:8876 RINA_RAM_NETNS=/proc/self/ns/net FDF_BIN=$B RINA_REGION_FILE=$D/region.perm
cleanup() { kill ${HP:-} ${SP:-} ${WP:-} 2>/dev/null; }; trap cleanup EXIT
$B/rina-host --listen 127.0.0.1:8876 --api /FawcettInnovations.RINA.ram_interface.php > $D/host.log 2>&1 & HP=$!
for i in $(seq 100); do (exec 3<>/dev/tcp/127.0.0.1/8876) 2>/dev/null && break; sleep 0.05; done
PHP_CLI_SERVER_WORKERS=4 php -S 127.0.0.1:8091 -t $H/www > $D/php.log 2>&1 & SP=$!; sleep 1
PAGE=$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8091/fdf/fdf_dashboard.html); echo "page: HTTP $PAGE"
FIRST=$(curl -s http://127.0.0.1:8091/fdf/getParticipants.php); echo "first view: $FIRST" | head -c 200; echo
curl -s "http://127.0.0.1:8091/fdf/waitParticipants.php?gen=0" > $D/wait.json & WP=$!; sleep 1
kill -0 $WP 2>/dev/null && HELD=yes || HELD=no; echo "long-poll held before any change: $HELD"
$B/transport-conformance > /dev/null 2>&1                     # a participant announces: the set changes
for i in $(seq 100); do kill -0 $WP 2>/dev/null || break; sleep 0.05; done
echo "long-poll returned: $(head -c 200 $D/wait.json)"
[ "$PAGE" = 200 ] && [ "$FIRST" = "[" ] || echo "$FIRST" | grep -q '^\['
[ "$PAGE" = 200 ] && [ "$HELD" = yes ] && grep -q '"gen":' $D/wait.json && grep -q '"hostname":"conformance"' $D/wait.json && { echo "RESULT PASS"; exit 0; }
echo "RESULT FAIL"; exit 1
