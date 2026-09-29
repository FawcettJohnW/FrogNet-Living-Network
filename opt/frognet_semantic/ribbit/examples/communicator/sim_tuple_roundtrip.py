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
"""
sim_tuple_roundtrip.py -- does a value written by T.put come back from T.get
with every field it went in with?

This is the layer that was never run. Every oracle written for the call path
replaced T.put/T.get with a dict, so four days of work sat on top of an
untested round-trip, and when calls did not appear the store was suspected on
no evidence at all.

Here the REAL frognet_tuples.put and .get run against a stub that answers
exactly what api.php answers: upsert_by_name on POST, entity=sensors&
action=values&parse=1 on GET, with UpdatedAtEpoch and fresh_s filtering. The
only thing faked is the database.

  R1  every field written comes back, unchanged, including nested types
  R2  a field whose value is None comes back as None -- the store does not
      drop it, so a missing field was missing before the write
  R3  var and scope are recovered from the SensorName
  R4  a second write to the same name UPDATES, it does not insert
  R5  a different scope is a different row
  R6  fresh_s filtering is the STORE's, on the envelope, not the payload's ts
  R7  a row the store cannot date is dropped rather than presented as current
"""
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, "/home/claude/src2/opt/frognet_semantic")

FAIL = []


def ck(name, cond, got=None):
    if cond:
        print("  PASS  %s" % name)
    else:
        print("  FAIL  %s   (got %r)" % (name, got))
        FAIL.append(name)


# ---- a store that answers what api.php answers ------------------------------
ROWS = {}          # SensorName -> row dict
NEXT_ID = [1]
NOW = [int(time.time())]
DROP_ENVELOPE = [False]      # simulate an api.php that ignores fresh_s


class Api(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, obj):
        body = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        req = json.loads(self.rfile.read(n).decode() or "{}")
        name = req.get("SensorName")
        row = ROWS.get(name)
        if row is None:
            row = {"SensorID": NEXT_ID[0], "SensorName": name}
            NEXT_ID[0] += 1
            ROWS[name] = row
        row["SensorType"] = req.get("SensorType")
        row["SensorAddress"] = req.get("SensorAddress")
        # stored as JSON TEXT, the way a database column holds it
        row["jsonData"] = json.dumps(req.get("jsonData"))
        row["UpdatedAtEpoch"] = NOW[0]
        self._send({"ok": True})

    def do_GET(self):
        from urllib.parse import urlparse, parse_qs
        q = parse_qs(urlparse(self.path).query)
        service = (q.get("SensorType") or [None])[0]
        fresh_s = int((q.get("fresh_s") or [0])[0])
        out = []
        for row in ROWS.values():
            if service and row.get("SensorType") != service:
                continue
            if fresh_s and (NOW[0] - row["UpdatedAtEpoch"]) > fresh_s:
                continue
            r = dict(row)
            if DROP_ENVELOPE[0]:
                r.pop("UpdatedAtEpoch", None)
            out.append(r)
        self._send({"rows": out})


srv = HTTPServer(("127.0.0.1", 0), Api)
threading.Thread(target=srv.serve_forever, daemon=True).start()
DB = "127.0.0.1:%d" % srv.server_port

from core import frognet_tuples as T           # noqa: E402

T.my_ip = lambda: "10.250.250.20"

# ---- R1/R2: the payload ------------------------------------------------------
PAYLOAD = {"session": "abc123", "member": "John", "members": ["John"],
           "host": "10.160.160.1", "port": 9000, "gone": None,
           "nested": {"a": 1, "b": [1, 2]}, "flag": True, "f": 1.5}

ok = T.put("communicator", "CallsJoined", "user:John", dict(PAYLOAD), dbhost=DB)
ck("R1 the write succeeds", ok is True, ok)

rows = T.get("communicator", "CallsJoined", dbhost=DB, fresh_s=120)
ck("R1 one row comes back", len(rows) == 1, len(rows))
got = rows[0]["value"] if rows else {}

missing = [k for k in PAYLOAD if k not in got]
ck("R1 no field is dropped", not missing, missing)
changed = {k: (PAYLOAD[k], got.get(k)) for k in PAYLOAD
           if k != "ts" and got.get(k) != PAYLOAD[k]}
ck("R1 no field is altered", not changed, changed)
ck("R1 nested structure survives", got.get("nested") == {"a": 1, "b": [1, 2]},
   got.get("nested"))
ck("R2 an explicit None comes back as None -- the store does not drop it",
   "gone" in got and got["gone"] is None, got.get("gone", "ABSENT"))
ck("R1 ts is stamped by the writer", isinstance(got.get("ts"), int), got.get("ts"))

# ---- R3 ----------------------------------------------------------------------
ck("R3 var is recovered", rows[0]["var"] == "CallsJoined", rows[0]["var"])
ck("R3 scope is recovered", rows[0]["scope"] == "user:John", rows[0]["scope"])
ck("R3 the writer's address rides along",
   rows[0]["addr"] == "10.250.250.20", rows[0]["addr"])

# ---- R4/R5 -------------------------------------------------------------------
T.put("communicator", "CallsJoined", "user:John",
      {"session": "abc123", "member": "John", "host": "h", "port": 1}, dbhost=DB)
rows = T.get("communicator", "CallsJoined", dbhost=DB, fresh_s=120)
ck("R4 a second write to the same name UPDATES, not inserts", len(rows) == 1,
   len(rows))
ck("R4 and the new value replaces the old wholesale",
   rows[0]["value"].get("members") is None, rows[0]["value"])

T.put("communicator", "CallsJoined", "user:Dave",
      {"session": "abc123", "member": "Dave", "host": "h", "port": 1}, dbhost=DB)
rows = T.get("communicator", "CallsJoined", dbhost=DB, fresh_s=120)
ck("R5 a different scope is a different row", len(rows) == 2, len(rows))
ck("R5 and both members are readable",
   sorted(r["value"]["member"] for r in rows) == ["Dave", "John"],
   [r["value"].get("member") for r in rows])

# ---- R6 ----------------------------------------------------------------------
NOW[0] += 300
rows = T.get("communicator", "CallsJoined", dbhost=DB, fresh_s=120)
ck("R6 a row older than fresh_s is not returned", rows == [], rows)
rows = T.get("communicator", "CallsJoined", dbhost=DB, fresh_s=600)
ck("R6 and is returned when the window is wide enough", len(rows) == 2, len(rows))

# ---- R7 ----------------------------------------------------------------------
DROP_ENVELOPE[0] = True
rows = T.get("communicator", "CallsJoined", dbhost=DB, fresh_s=600)
ck("R7 a row the store cannot date is dropped, not presented as current",
   rows == [], rows)
DROP_ENVELOPE[0] = False

print()
if FAIL:
    print("FAILED %d: %s" % (len(FAIL), ", ".join(FAIL)))
    sys.exit(1)
print("ALL PASS -- the round trip is faithful; a missing field was missing "
      "before the write")
