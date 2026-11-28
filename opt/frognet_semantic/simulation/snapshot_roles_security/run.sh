#!/bin/bash
# [THE_DASHBOARD_SHOWS_ROLES_AND_SECURITY_V1] getSnapshot.php's `roles` and `channel`, run for real (php) on a fixture
# node: a hosts file, the daemon's member rows, and one stand-in server answering as the forward pond's RAM manager
# (op world: three alive nodes, one without a capability, one member not alive, a phone) and as the ControlRam
# (the channel tuples: a sealed Role, an unsealed one, two ends' reports). Run: bash run.sh   (needs php and python3)
set -eu
cd "$(dirname "$0")"; HERE=$PWD; WWW=$HERE/../../../../var/www/html
R=$(mktemp -d); PORT=18931
mkdir -p $R/opt/fdf/var $R/etc $R/var/lib/frognet
printf 'RINA_RAM=127.0.0.1:%s\n' $PORT > $R/opt/fdf/var/join.env
echo '{"pond":"frognet","name":"Seattle7","addrs":[{"kind":"own","ip":"10.170.170.1"}]}' > $R/opt/fdf/var/facts.json
printf '10.188.188.1 databasehost.frognet\n10.188.188.1 databasehost_control.frognet\n10.170.170.1 openeocHost.frognet\n10.130.130.1 unitsHost.protocolchat\n10.170.170.1 FrogNetHost.Seattle7\n' > $R/etc/hosts
echo '[{"ip":"10.170.170.1","echo":"Seattle7,10.170.170.1,x,y","role":"node","alive":true,"guid":"g7"},{"ip":"10.188.188.1","echo":"BAMacBook,10.188.188.1,x,y","role":"node","alive":true,"guid":"gb"},{"ip":"10.130.130.1","echo":"Seattle3B,10.130.130.1,x,y","role":"node","alive":true,"guid":"g3"}]' > $R/var/lib/frognet/region_hosts.json
python3 fake_rams.py $PORT & S=$!; trap 'kill $S 2>/dev/null; rm -rf $R' EXIT; sleep 1
FROGNET_SNAPSHOT_ROOT=$R FROGNET_SNAPSHOT_CONTROL_RAM=127.0.0.1:$PORT php $WWW/getSnapshot.php > $R/out.json
python3 - $R/out.json <<'PY'
import json, sys
j = json.load(open(sys.argv[1])); r = j["roles"]; c = j["channel"]; fail = 0
def check(ok, what, got=None):
    global fail
    print(("  ok    " if ok else "  FAIL  ") + what + ("" if ok else "  -- " + json.dumps(got))); fail += 0 if ok else 1
check(r["hosting"] == {"databasehost": "10.188.188.1", "databasehost_control": "10.188.188.1", "openeochost": "10.170.170.1", "unitsHost.protocolchat": "10.130.130.1"}, "hosting: every elected name and the Team's memory, each at its node", r["hosting"])
check(r["carried_as"] == {"databasehost": "", "databasehost_control": "", "openeochost": "openeoc", "unitsHost.protocolchat": "units"}, "each memory's Role, as the protocol layer reads the name", r["carried_as"])
check(r["supports"] == {"10.170.170.1": ["databasehost", "databasehost_control"], "10.188.188.1": ["databasehost", "databasehost_control"]}, "supports: the alive nodes with a capability, for the Roles the capability election places (not the node without one, the member not alive, or the phone)", r["supports"])
s = c["settings"]
check(s["units"] == {"protocol": "chorus", "sealed": True, "cipher": "chacha20-poly1305", "number_tag": s["units"].get("number_tag")} and len(s["units"]["number_tag"]) == 4, "a sealed Role: protocol, algorithm and a four-character tag of its number", s["units"])
check(s["mediahost"] == {"protocol": "tcp", "sealed": False, "cipher": ""} and s["semantic"] == {"protocol": "", "sealed": False, "cipher": ""}, "an unsealed Role, and the setting of no Role with the carrier's own protocol", [s["mediahost"], s["semantic"]])
e = {x["ip"]: x for x in c["ends"]}
check(e["10.170.170.1"]["roles"]["units"].get("step") == 3 and e["10.170.170.1"]["roles"]["units"]["number_tag"] == s["units"]["number_tag"], "an end's report: the same tag as the setting it is on, and its step", e["10.170.170.1"])
check(e["10.188.188.1"]["roles"]["units"].get("ignored") is True, "an end that ignored the Role's setting says so", e["10.188.188.1"])
t = json.dumps(j["channel"]) + json.dumps(j["roles"])
check("4437418795604890526" not in t and '"check"' not in t and "number\"" not in t.replace("number_tag", ""), "no setting's number, check, key or secret is in the snapshot")
check(not [x for x in j["errors"] if x.startswith(("roles", "channel"))], "no error from either reader", j["errors"])
print("RESULT " + ("FAIL" if fail else "PASS") + " the snapshot's roles and channel: %d fail" % fail); sys.exit(1 if fail else 0)
PY
