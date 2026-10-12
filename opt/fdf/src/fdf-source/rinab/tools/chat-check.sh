#!/bin/bash
# chat-check.sh -- [FDF_CHAT_V1] page input as <App>.<QueueName>.<submitter>, a service, and a page reading the room,
# all through the shared space. PASS only if: a held room read waits with no message; alice's message (posted through
# chat_send.php into chat.inbox.alice) reaches it via the service as #1 from alice; then bob's makes the room #1, #2 in order.
set -u; H=$(cd "$(dirname "$0")/.." && pwd); B=$H/build/bin; D=$(mktemp -d /tmp/chat-check.XXXX); echo "diag dir: $D"
export RINA_RAM=127.0.0.1:8878 RINA_RAM_NETNS=/proc/self/ns/net FDF_BIN=$B RINA_REGION_FILE=$D/region.perm
cleanup() { kill ${HP:-} ${SP:-} ${CS:-} ${WP:-} 2>/dev/null; }; trap cleanup EXIT
$B/rina-host --listen 127.0.0.1:8878 --api /FawcettInnovations.RINA.ram_interface.php > $D/host.log 2>&1 & HP=$!
for i in $(seq 100); do (exec 3<>/dev/tcp/127.0.0.1/8878) 2>/dev/null && break; sleep 0.05; done
python3 $H/tools/chat-service.py > $D/service.log 2>&1 & CS=$!
PHP_CLI_SERVER_WORKERS=4 php -S 127.0.0.1:8092 -t $H/www > $D/php.log 2>&1 & SP=$!; sleep 1
echo "page: HTTP $(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8092/fdf/chat.html)"
curl -s "http://127.0.0.1:8092/fdf/chat_wait.php?after=0" > $D/wait1.json & WP=$!; sleep 1
kill -0 $WP 2>/dev/null && HELD=yes || HELD=no; echo "room read held with no message: $HELD"
echo "alice posts: $(curl -s -d from=alice -d 'text=hello from alice' http://127.0.0.1:8092/fdf/chat_send.php)"
for i in $(seq 100); do kill -0 $WP 2>/dev/null || break; sleep 0.05; done
echo "the held room read returned: $(cat $D/wait1.json)"
echo "bob posts: $(curl -s -d from=bob -d 'text=hi alice' http://127.0.0.1:8092/fdf/chat_send.php)"; sleep 0.5
ROOM=$(curl -s "http://127.0.0.1:8092/fdf/chat_wait.php?after=0"); echo "the room: $ROOM"
echo "service: $(tr '\n' ' ' < $D/service.log)"
python3 - "$D/wait1.json" "$ROOM" "$HELD" <<'PY'
import json, sys
w = json.load(open(sys.argv[1]))["rows"]; room = sorted(json.loads(sys.argv[2])["rows"], key=lambda c: int(c["instance"]))
ok = sys.argv[3] == "yes" and len(w) == 1 and w[0]["instance"] == "1" and w[0]["bag"]["from"] == "alice" and w[0]["bag"]["text"] == "hello from alice"
ok = ok and [(c["instance"], c["bag"]["from"]) for c in room] == [("1", "alice"), ("2", "bob")]
print("RESULT", "PASS" if ok else "FAIL"); sys.exit(0 if ok else 1)
PY
