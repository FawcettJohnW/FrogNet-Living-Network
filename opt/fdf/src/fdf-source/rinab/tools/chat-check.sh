#!/bin/bash
# chat-check.sh -- [FDF_CHAT_V1][ROOM_IS_THE_FAMILY_V1] each message its sender's own cell (chat, msg, <from>.<ms>), and
# a page reading the room -- that family -- all through the shared space, with no service. PASS only if: a held room
# read waits with no message; alice's message (posted through chat_send.php) returns it as from alice; then the room
# is alice's, then bob's, in write order.
set -u; H=$(cd "$(dirname "$0")/.." && pwd); B=$H/build/bin; D=$(mktemp -d /tmp/chat-check.XXXX); echo "diag dir: $D"
export RINA_RAM=127.0.0.1:8878 RINA_RAM_NETNS=/proc/self/ns/net FDF_BIN=$B RINA_REGION_FILE=$D/region.perm
cleanup() { kill ${HP:-} ${SP:-} ${CS:-} ${WP:-} 2>/dev/null; }; trap cleanup EXIT
$B/fdf-ram --listen 127.0.0.1:8878 --api /FawcettInnovations.RINA.ram_interface.php > $D/host.log 2>&1 & HP=$!
for i in $(seq 100); do (exec 3<>/dev/tcp/127.0.0.1/8878) 2>/dev/null && break; sleep 0.05; done
# [FDF_WEB_UNDER_APACHE_V1] the pages run under the fdf.env of the install they belong to (www/fdf -> ../../fdf.env):
# served here from a copy laid out as installed, with an fdf.env naming this check's RAM and programs
mkdir -p $D/www; cp -a $H/../fdf/www/fdf $D/www/fdf; printf 'export FDF_BIN=%s RINA_RAM=%s RINA_RAM_NETNS=%s\n' $B $RINA_RAM $RINA_RAM_NETNS > $D/fdf.env
PHP_CLI_SERVER_WORKERS=4 php -S 127.0.0.1:8092 -t $D/www > $D/php.log 2>&1 & SP=$!; sleep 1
echo "page: HTTP $(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8092/fdf/chat.html)"
curl -s "http://127.0.0.1:8092/fdf/chat_wait.php?after=0" > $D/wait1.json & WP=$!; sleep 1
kill -0 $WP 2>/dev/null && HELD=yes || HELD=no; echo "room read held with no message: $HELD"
echo "alice posts: $(curl -s -d from=alice -d 'text=hello from alice' http://127.0.0.1:8092/fdf/chat_send.php)"
for i in $(seq 100); do kill -0 $WP 2>/dev/null || break; sleep 0.05; done
echo "the held room read returned: $(cat $D/wait1.json)"
echo "bob posts: $(curl -s -d from=bob -d 'text=hi alice' http://127.0.0.1:8092/fdf/chat_send.php)"; sleep 0.5
ROOM=$(curl -s "http://127.0.0.1:8092/fdf/chat_wait.php?after=0"); echo "the room: $ROOM"
python3 - "$D/wait1.json" "$ROOM" "$HELD" <<'PY'
import json, sys
w = json.load(open(sys.argv[1]))["rows"]; room = sorted(json.loads(sys.argv[2])["rows"], key=lambda c: c["id"])
ok = sys.argv[3] == "yes" and len(w) == 1 and w[0]["bag"]["from"] == "alice" and w[0]["bag"]["text"] == "hello from alice"
ok = ok and [c["bag"]["from"] for c in room] == ["alice", "bob"]
print("RESULT", "PASS" if ok else "FAIL"); sys.exit(0 if ok else 1)
PY
