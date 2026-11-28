#!/bin/bash
# check.sh LABEL NODE:IP3 [NODE:IP3 ...] -- is every node a full member? (container names with their served /24's
# first three octets). Prints one PASS/FAIL line per check; exit 1 on any FAIL.
LABEL=$1; shift; NODES=("$@"); N=${#NODES[@]}; FAILS=0
PY='cd /opt/frognet_semantic && PYTHONPATH=/opt/frognet_semantic venv/bin/python3 /root/member.py'
ck() { if [ "$1" = 0 ]; then echo "  PASS $2"; else echo "  FAIL $2"; FAILS=$((FAILS+1)); fi; }
echo "=== $LABEL ($(date -u +%H:%M:%S) UTC) ==="
declare -A ST
for e in "${NODES[@]}"; do c=${e%%:*}; docker cp "$(dirname "$0")/member.py" $c:/root/member.py >/dev/null
  ST[$c]="$(docker exec $c bash -c "$PY state" 2>/dev/null | tail -1)"; done
first=${NODES[0]%%:*}
roles0="$(echo "${ST[$first]}" | python3 -c 'import json,sys; print(json.dumps(json.load(sys.stdin)["roles"],sort_keys=True))')"
echo "  roles (as $first has them): $roles0"
for e in "${NODES[@]}"; do c=${e%%:*}; s="${ST[$c]}"
  r="$(echo "$s" | python3 -c 'import json,sys; print(json.dumps(json.load(sys.stdin)["roles"],sort_keys=True))')"
  [ "$r" = "$roles0" ] && [ "$r" != "{}" ]; ck $? "$c: same SystemRam and ControlRam as every other node"
  for o in "${NODES[@]}"; do p=${o##*:}
    echo "$s" | python3 -c "import json,sys; d=json.load(sys.stdin); sys.exit(0 if '$p.1' in d['names'] else 1)"; ck $? "$c: names the node at $p.1 in /etc/hosts"
    echo "$s" | python3 -c "import json,sys; d=json.load(sys.stdin); sys.exit(0 if any(r.startswith('$p.0/24') for r in d['routes24']) else 1)"; ck $? "$c: has a route to $p.0/24"
    echo "$s" | python3 -c "import json,sys; d=json.load(sys.stdin); sys.exit(0 if '$p.1' in d['capability_at_control'] else 1)"; ck $? "$c: reads $p.1's capability tuple in the ControlRam"
    [ "$o" = "$e" ] && continue
    docker exec $c ping -c2 -W2 -q $p.1 >/dev/null 2>&1; ck $? "$c: pings $p.1"
    docker exec $c ping -c2 -W2 -q $p.2 >/dev/null 2>&1; ck $? "$c: pings $p.2"
    docker exec $c bash -c "curl -s --max-time 6 http://$p.1/frognet_echo.php | grep -q '$p.1'"; ck $? "$c: reads $p.1's echo through the proxy"
    on="$(echo "${ST[${o%%:*}]}" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['names'].get('$p.1',''))")"
    docker exec $c bash -c "getent hosts FrogNetHost.$on | grep -q '^$p.1 '" ; ck $? "$c: resolves FrogNetHost.$on to $p.1"
  done
done
# the memory: every node holds a read for N tuples, then every node writes its own; each hold must end with all N
TAG="t$(date +%s)"; rm -f /tmp/hold.*.out
for e in "${NODES[@]}"; do c=${e%%:*}; (docker exec $c bash -c "$PY hold $N 40 $TAG" > /tmp/hold.$c.out 2>/dev/null; echo "rc=$?" >> /tmp/hold.$c.out) & done
sleep 4
for e in "${NODES[@]}"; do c=${e%%:*}; docker exec $c bash -c "$PY write $TAG" 2>/dev/null | tail -1 | grep -q "ok=True"; ck $? "$c: writes its own tuple into the SystemRam"; sleep 1; done
wait
for e in "${NODES[@]}"; do c=${e%%:*}; grep -q "rc=0" /tmp/hold.$c.out; ck $? "$c: its held read ended with all $N nodes' tuples ($(grep '^hold' /tmp/hold.$c.out | sed 's/^hold //'))"; done
echo "=== $LABEL: $([ $FAILS = 0 ] && echo ALL PASS || echo "$FAILS FAILED") ==="
[ $FAILS = 0 ]
