#!/usr/bin/env python3
################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#                                                              #
#  SPDX-License-Identifier: GPL-2.0-only                       #
#                                                              #
#  This program is free software; you can redistribute it      #
#  and/or modify it under the terms of the GNU General Public  #
#  License as published by the Free Software Foundation;       #
#  version 2 of the License, and no other version.             #
#                                                              #
#  This program is distributed in the hope that it will be     #
#  useful, but WITHOUT ANY WARRANTY; without even the implied  #
#  warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR     #
#  PURPOSE.  See the GNU General Public License for details.   #
#                                                              #
#  See COPYRIGHT and LICENSE at the root of this tree.         #
################################################################
"""Capability-tuple lifecycle regression. Stubs the api.php HTTP layer (SensorName is
the unique key; upsert_by_name overwrites by name) with an in-memory store and proves:

  [PUT_OWN_PERSIST_V1]  own=True deletes on exit (right for a service, fatal for the
                        oneshot registrar); own=False survives so the election reads it.
  [MY_IP_FROGNET_V1]    writer identity is the FrogNet 10/8, not the WAN default-route IP.
  [ROLE_SCOPE_V1]       mediahost AND databasehost coexist as TWO rows under role_scope;
                        a bare per-host scope collides them to one flip-flopping row.
  self-prune            clears this host's deprecated bare/pid rows, keeps role_scope +
                        the other role + other hosts.
  [CAPABILITY_FRESH_V1] the election read drops stale/offline rows by ts.
"""
import json, io, urllib.parse, time, sys
import frognet_tuples as T

STORE = {}; _id = [1000]
class _R(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *a): return False
def _fake(req, timeout=None):
    p = urllib.parse.parse_qs(urllib.parse.urlparse(req.full_url).query)
    a = p.get("action", [""])[0]
    if a == "upsert_by_name":                       # key = SensorName ONLY (as in api.php)
        b = json.loads(req.data.decode()); nm = b["SensorName"]
        r = STORE.get(nm, {"SensorID": _id[0]})
        if nm not in STORE: _id[0] += 1
        r.update({"SensorName": nm, "SensorType": b["SensorType"],
                  "SensorAddress": b["SensorAddress"], "jsonData": json.dumps(b["jsonData"])})
        STORE[nm] = r; return _R(b'{"ok":true}')
    if a == "values":
        st = p.get("SensorType",[None])[0]; nm = p.get("SensorName",[None])[0]
        rows = [dict(r) for r in STORE.values()
                if (not st or r["SensorType"]==st) and (nm is None or r["SensorName"]==nm)]
        return _R(json.dumps({"rows": rows}).encode())
    if a == "delete":
        sid = int(p.get("SensorID",[0])[0])
        for k,v in list(STORE.items()):
            if v["SensorID"]==sid: del STORE[k]
        return _R(b'{"ok":true}')
    return _R(b'{"ok":false}')
T.urllib.request.urlopen = _fake
T._local_ipv4s = lambda: [("eth0","10.250.250.1")]    # I am Seattle5

ok = True
def chk(c,m):
    global ok; ok = ok and bool(c); print(f"  [{'PASS' if c else 'FAIL'}] {m}")
def caps(role="mediahost"): return T.get(role, "capability")
blob = {"ffmpeg": True, "libvpx": True, "lan_ip": "10.250.250.1"}

# --- own flag ---
STORE.clear(); T._OWNED.clear()
T.put("mediahost","capability", T.role_scope("mediahost"), dict(blob), own=True)
chk(len(caps())==1, "own=True writes")
T.cleanup_owned()
chk(len(caps())==0, "own=True self-deletes on exit (the oneshot bug)")
STORE.clear(); T._OWNED.clear()
T.put("mediahost","capability", T.role_scope("mediahost"), dict(blob), own=False)
T.cleanup_owned()
chk(len(caps())==1, "own=False SURVIVES exit -> election can read it")

# --- my_ip is the FrogNet identity ---
_sv = T._local_ipv4s
T._local_ipv4s = lambda: [("wlan1","192.168.0.21"),("eth0","10.250.250.1"),
    ("wg0","10.253.203.70"),("frognet0","10.254.1.5")]
chk(T.my_ip()=="10.250.250.1", "my_ip picks served .1, not wlan1/10.253/10.254")
T._local_ipv4s = _sv

# --- ROLE collision: role_scope keeps them apart, bare scope collides ---
STORE.clear(); T._OWNED.clear()
T.put("mediahost","capability", T.role_scope("mediahost"), dict(blob), own=False)
T.put("databasehost","capability", T.role_scope("databasehost"), dict(blob), own=False)
chk(len(STORE)==2 and len(caps("mediahost"))==1 and len(caps("databasehost"))==1,
    "role_scope: mediahost + databasehost coexist as TWO rows")
STORE.clear(); T._OWNED.clear()
T.put("mediahost","capability", T.node_scope(), dict(blob), own=False)
T.put("databasehost","capability", T.node_scope(), dict(blob), own=False)
chk(len(STORE)==1, "bare node_scope COLLIDES the two roles to one row (the reported bug)")

# --- self-prune: keep role_scope, drop bare + pid; leave other role/hosts ---
STORE.clear(); T._OWNED.clear(); now=int(time.time())
def seed(nm, ts, ip, st="mediahost"):
    STORE[nm]={"SensorID":_id[0],"SensorName":nm,"SensorType":st,"SensorAddress":ip,
               "jsonData":json.dumps({"lan_ip":ip,"ffmpeg":True,"libvpx":True,"ts":ts})}; _id[0]+=1
seed("SD:capability.host:10.250.250.1:mediahost", now, "10.250.250.1")          # keep (mine)
seed("SD:capability.host:10.250.250.1",           now, "10.250.250.1")          # bare orphan
seed("SD:capability.host:10.250.250.1:7788",      now, "10.250.250.1")          # pid orphan
seed("SD:capability.host:10.250.250.1:databasehost", now, "10.250.250.1", "databasehost")  # other role
seed("SD:capability.host:10.160.160.1:mediahost", now, "10.160.160.1")          # other host
chk(T.prune_self_stale_capability("mediahost")==2, "prune removes my bare + pid orphans")
chk("SD:capability.host:10.250.250.1:mediahost" in STORE, "keeps my role_scope row")
chk("SD:capability.host:10.250.250.1:databasehost" in STORE
    and "SD:capability.host:10.160.160.1:mediahost" in STORE, "leaves other role + other host")

# --- freshness on the election read ---
STORE.clear(); _id=[2000]
from frognet_role_elect import CAPABILITY_FRESH_S
seed("SD:capability.host:10.250.250.1:mediahost", now,      "10.250.250.1")
seed("SD:capability.host:10.160.160.1:mediahost", now,      "10.160.160.1")
seed("SD:capability.host:10.111.11.1:mediahost",  now-600,  "10.111.11.1")   # offline
fresh = {r["value"]["lan_ip"] for r in T.get("mediahost","capability",fresh_s=CAPABILITY_FRESH_S)}
chk(fresh=={"10.250.250.1","10.160.160.1"}, "fresh_s drops the offline host from the election")

print("\n[ PASS ] test_tuple_persistence" if ok else "\n[ FAIL ] test_tuple_persistence")
sys.exit(0 if ok else 1)
