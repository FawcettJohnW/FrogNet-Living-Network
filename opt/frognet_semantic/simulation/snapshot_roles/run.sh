#!/bin/bash
# [THE_DASHBOARD_SHOWS_ROLES_V1] [A_VIEWER_IS_NOT_TOLD_HOW_A_CONNECTION_IS_PROTECTED_V1] getSnapshot.php's `roles`, run
# for real (php) on a fixture node: a hosts file, the daemon's member rows, and one stand-in server answering as the
# forward pond's RAM manager (op world: three alive nodes, one without a capability, one member not alive, a phone)
# and as a ControlRam holding channel tuples (a sealed Role, two ends' reports). The snapshot must say which node can
# host and hosts each Role, must not read the ControlRam, and must say nothing of how any connection is protected.
# Run: bash run.sh   (needs php and python3)
set -eu
cd "$(dirname "$0")"; HERE=$PWD; WWW=$HERE/../../../../var/www/html
R=$(mktemp -d); PORT=18931
mkdir -p $R/opt/fdf/var $R/etc $R/var/lib/frognet
printf 'RINA_RAM=127.0.0.1:%s\n' $PORT > $R/opt/fdf/var/join.env
echo '{"pond":"frognet","name":"Seattle7","addrs":[{"kind":"own","ip":"10.170.170.1"}]}' > $R/opt/fdf/var/facts.json
printf '10.188.188.1 databasehost.frognet\n10.188.188.1 databasehost_control.frognet\n10.170.170.1 openeocHost.frognet\n10.130.130.1 unitsHost.protocolchat\n10.170.170.1 FrogNetHost.Seattle7\n' > $R/etc/hosts
echo '[{"ip":"10.170.170.1","echo":"Seattle7,10.170.170.1,x,y","role":"node","alive":true,"guid":"g7"},{"ip":"10.188.188.1","echo":"BAMacBook,10.188.188.1,x,y","role":"node","alive":true,"guid":"gb"},{"ip":"10.130.130.1","echo":"Seattle3B,10.130.130.1,x,y","role":"node","alive":true,"guid":"g3"}]' > $R/var/lib/frognet/region_hosts.json
python3 fake_rams.py $PORT $R/control-reads & S=$!; : > $R/control-reads; trap 'kill $S 2>/dev/null; rm -rf $R' EXIT; sleep 1
FROGNET_SNAPSHOT_ROOT=$R FROGNET_SNAPSHOT_CONTROL_RAM=127.0.0.1:$PORT php $WWW/getSnapshot.php > $R/out.json
python3 - $R/out.json $R/control-reads <<'PY'
import json, sys
j = json.load(open(sys.argv[1])); r = j["roles"]; fail = 0
def check(ok, what, got=None):
    global fail
    print(("  ok    " if ok else "  FAIL  ") + what + ("" if ok else "  -- " + json.dumps(got))); fail += 0 if ok else 1
check(r["hosting"] == {"databasehost": "10.188.188.1", "databasehost_control": "10.188.188.1", "openeochost": "10.170.170.1", "unitsHost.protocolchat": "10.130.130.1"}, "hosting: every elected name and the Team's memory, each at its node", r["hosting"])
check(r["supports"] == {"10.170.170.1": ["databasehost", "databasehost_control"], "10.188.188.1": ["databasehost", "databasehost_control"]}, "supports: the alive nodes with a capability, for the Roles the capability election places (not the node without one, the member not alive, or the phone)", r["supports"])
check(not [x for x in j["errors"] if x.startswith("roles")], "no error from the reader", j["errors"])
t = json.dumps(j).lower()
check("channel" not in j and not any(w in t for w in ("chorus", "chacha", "aes-256", "sealed", "cipher", "number_tag", "4437418795604890526")), "the snapshot says nothing of any Role's protocol, whether it is sealed, its algorithm or its key")
reads = open(sys.argv[2]).read().strip()
check(reads == "", "the snapshot read nothing from the ControlRam", reads)
print("RESULT " + ("FAIL" if fail else "PASS") + " the snapshot's roles: %d fail" % fail); sys.exit(1 if fail else 0)
PY
