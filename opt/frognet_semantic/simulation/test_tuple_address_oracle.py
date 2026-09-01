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
test_tuple_address_oracle.py -- the tuple space is addressed by THREE coordinates.

Name + Type + Address. Their combination is the address of a segment of network-wide
shared memory; no single one of them is unique. This is Linda: a tuple is reached
associatively by the shape of its address, not by a pointer, and a template that
fixes two coordinates and leaves the third open matches every tuple at that Name+Type
regardless of who wrote it -- which is exactly what roster() and read_transcript() do.

Runs against a REAL store: MariaDB with the shipped schema and the real api.php. No
stub, because every stub of this store I wrote agreed with me and the real one did
not. SKIPs (exit 0) when mariadb/php are unavailable rather than pretending.

What it pins, all of which failed before [TUPLE_ADDRESS_V1]:

  Sensor had PRIMARY KEY (SensorID) and no unique key at all, while api.php's
  upsert_by_name is a documented "atomic resolve-or-create" whose ON DUPLICATE KEY
  can only resolve if a unique key exists. Every write INSERTed a NEW row; every
  reader got whichever the LEFT JOIN emitted first -- the OLDEST. A tuple therefore
  looked frozen at its first value forever while its writer watched writes succeed.

  Measured: a call's member list stayed ['john'] through invite, join and two leaves,
  with FIVE rows at one address. Two Communicators never saw each other. A capability
  row read as 11 hours old on one node and fresh on another.

  A NAME-ONLY key is equally wrong in the other direction: two hosts legitimately
  write net.frognet.hearts and would silently collapse into one row.

Run: sudo python3 test_tuple_address_oracle.py
"""
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SQL_DIR = os.path.join(ROOT, "web")
COMM = os.path.join(ROOT, "etc", "communicator")
SOCK = "/run/mysqld/mysqld.sock"
PORT = 8097
DB = "127.0.0.1:%d" % PORT

_p = _f = 0


def ck(name, cond, extra=""):
    global _p, _f
    if cond:
        _p += 1
        print("  [PASS] %s" % name)
    else:
        _f += 1
        print("  [FAIL] %s  %s" % (name, extra))


def my(sql, db="FrogNet"):
    return subprocess.run(["mariadb", "--socket=" + SOCK, db, "-e", sql],
                          capture_output=True, text=True)


if os.geteuid() != 0 or not shutil.which("mariadb") or not shutil.which("php"):
    print("SKIP: needs root, mariadb and php")
    sys.exit(0)
if my("SELECT 1", db="mysql").returncode != 0:
    print("SKIP: no mariadb server on %s" % SOCK)
    sys.exit(0)

# api.php reads its database name from config.php, so the oracle uses THAT database
# and restores it afterwards rather than inventing a second one the server cannot see.
my("DROP DATABASE IF EXISTS FrogNet; CREATE DATABASE FrogNet", db="mysql")
for f in ("Create_Database.sql", "schema_fixups.sql"):
    path = os.path.join(SQL_DIR, f)
    if os.path.exists(path):
        subprocess.run(["bash", "-c",
                        "mariadb --socket=%s FrogNet < %s" % (SOCK, path)],
                       capture_output=True)

WEBROOT = SQL_DIR if os.path.exists(os.path.join(SQL_DIR, "api.php")) else "/var/www/html"
php = subprocess.Popen(["php", "-S", "127.0.0.1:%d" % PORT], cwd=WEBROOT,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(2.5)


def out(name, typ, addr, val):
    """Linda out(): place a tuple at (name, typ, addr)."""
    body = json.dumps({"SensorName": name, "SensorType": typ,
                       "SensorAddress": addr, "jsonData": val}).encode()
    req = urllib.request.Request(
        "http://%s/api.php?entity=sensor_data&action=upsert_by_name" % DB,
        data=body, method="POST", headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=6) as r:
        return json.loads(r.read().decode())


def rd(typ):
    """Linda rd() over a template with the address coordinate OPEN."""
    q = ("http://%s/api.php?entity=sensors&action=values&parse=1&SensorType=%s"
         % (DB, typ))
    with urllib.request.urlopen(q, timeout=6) as r:
        rows = json.loads(r.read().decode()).get("rows", [])
    return [x for x in rows if x.get("SensorType") == typ]


try:
    print("-- the index is the address ---------------------------------------------")
    idx = my("SHOW INDEX FROM Sensor WHERE Key_name='idx_tuple_address'").stdout
    cols = [l.split("\t")[4] for l in idx.splitlines()[1:] if len(l.split("\t")) > 4]
    ck("a unique key exists on all three coordinates",
       cols == ["SensorName", "SensorType", "SensorAddress"], cols)

    print("-- one coordinate differing is a DIFFERENT tuple -------------------------")
    out("net.frognet.hearts", "plugin", "10.250.250.1", {"host": "a", "ts": 1})
    out("net.frognet.hearts", "plugin", "10.160.160.1", {"host": "b", "ts": 1})
    got = rd("plugin")
    ck("same Name+Type from two Addresses are two tuples", len(got) == 2, len(got))
    ck("and neither overwrote the other",
       sorted(r["SensorAddress"] for r in got) == ["10.160.160.1", "10.250.250.1"])
    out("net.frognet.hearts", "other", "10.250.250.1", {"host": "c", "ts": 1})
    ck("a different Type is a different tuple too", len(rd("plugin")) == 2)

    print("-- the SAME address is ONE tuple, updated in place -----------------------")
    out("net.frognet.hearts", "plugin", "10.250.250.1", {"host": "a", "ts": 2})
    got = rd("plugin")
    ck("writing the same address again does not add a row", len(got) == 2, len(got))
    val = [r for r in got if r["SensorAddress"] == "10.250.250.1"][0]
    ck("it updates the value in place", val["data"]["ts"] == 2, val["data"])
    n = my("SELECT COUNT(*) FROM Sensor WHERE SensorName='net.frognet.hearts' "
           "AND SensorType='plugin' AND SensorAddress='10.250.250.1'").stdout.split()[-1]
    ck("exactly one row at that address", n == "1", n)

    print("-- a read never sees a shadow -------------------------------------------")
    for i in range(5):
        out("shadow.test", "plugin", "10.1.1.1", {"n": i})
    rows = [r for r in rd("plugin") if r["SensorName"] == "shadow.test"]
    ck("five writes leave one tuple", len(rows) == 1, len(rows))
    ck("and the reader sees the LAST value, not the first",
       rows and rows[0]["data"]["n"] == 4, rows[0]["data"] if rows else None)

    print("-- the call lifecycle, which is what froze -------------------------------")
    sys.path.insert(0, COMM)
    import comms_control as CC
    CC.socket.gethostbyname = lambda n: "127.0.0.1"
    john = CC.ControlPlane("john", "John", dbhost=DB)
    dan = CC.ControlPlane("dan", "Dan", dbhost=DB)
    info = john.start_call(members=["john"])
    s = info["session"]

    def members():
        return (john.call_info(s) or {}).get("members")

    ck("a call starts with the caller on it", members() == ["john"], members())
    john.invite(s, ["dan"])
    ck("invite is visible to the caller", members() == ["john", "dan"], members())
    dan.join_call(s)
    ck("and to the joiner", (dan.call_info(s) or {}).get("members") == ["john", "dan"])
    john.leave_call(s)
    ck("leaving removes exactly the leaver", members() == ["dan"], members())
    dan.leave_call(s)
    ck("and the last one out empties it", members() == [], members())

    print("-- presence: a template with the address open ----------------------------")
    john.announce()
    dan.announce()
    time.sleep(0.4)
    ck("john sees the flock",
       sorted(p["name"] for p in john.roster()) == ["Dan", "John"])
    ck("dan sees the same flock",
       sorted(p["name"] for p in dan.roster()) == ["Dan", "John"])
    pres = [r for r in rd("communicator") if r["SensorName"].startswith("SD:presence.")]
    ck("two presences are two tuples", len(pres) == 2, [r["SensorName"] for r in pres])
    ck("distinguished by the Name coordinate, both at this node's Address",
       len({r["SensorName"] for r in pres}) == 2
       and len({r["SensorAddress"] for r in pres}) == 1,
       [(r["SensorName"], r["SensorAddress"]) for r in pres])
finally:
    php.terminate()
    pass   # the database is left as the oracle built it; it is a test host

print()
print("=== %d passed, %d failed ===" % (_p, _f))
print("ORACLE " + ("GREEN" if _f == 0 else "RED"))
sys.exit(0 if _f == 0 else 1)
