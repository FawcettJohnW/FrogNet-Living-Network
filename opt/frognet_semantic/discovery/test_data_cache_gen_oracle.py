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
"""test_data_cache_gen_oracle.py   [DATA_CACHE_GEN_V2]

Proves the daemon's RAM materialization is a reflection of CURRENT disk state.

The DB is a sqlite stand-in wired under data_cache._get_conn, driving the
module's real query strings and real serving logic. Writes in this oracle go
STRAIGHT TO THE DB and bump Gen the way the triggers do -- i.e. they simulate
another node, api.php, or a human at the mysql prompt. The cache never sees
them except through Gen. That is the whole point: the old cache observed only
its own writes.

Run:  python3 discovery/test_data_cache_gen_oracle.py [ROOT]
FAILS on the pre-[DATA_CACHE_GEN_V2] module. PASSES on the new one.
"""
import json
import os
import sqlite3
import sys
import time

ROOT = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else
                       os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

FAILED = []


def ck(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + (f"   [{detail}]" if detail and not cond else ""))
    if not cond:
        FAILED.append(name)


# ── sqlite stand-in for MySQL ────────────────────────────────────────────────
DB = sqlite3.connect(":memory:", check_same_thread=False)
DB.row_factory = sqlite3.Row
DB.executescript("""
CREATE TABLE Sensor (SensorID INTEGER PRIMARY KEY, FrogID TEXT, SensorAddress TEXT,
  SensorNetwork TEXT, SensorName TEXT, SensorType TEXT, SensorLocation TEXT, Tags TEXT);
CREATE TABLE SensorData (SensorID INTEGER PRIMARY KEY, FrogID TEXT, jsonData TEXT,
  UpdatedAt TEXT, UpdatedAtEpoch INTEGER);
CREATE TABLE FrogNetTableGen (TableName TEXT PRIMARY KEY, Gen INTEGER);
INSERT INTO FrogNetTableGen VALUES ('Sensor', 0);
INSERT INTO FrogNetTableGen VALUES ('SensorData', 0);
""")
NOW = [1785705000]

# [DATA_CACHE_GEN_V3] PER-TABLE counters. This fixture seeded V2's single shared
# 'SensorTables' row, so _check_wiring found neither 'Sensor' nor 'SensorData',
# disabled the cache outright, and every check below reported FELL_THROUGH --
# ten failures from one stale row. data_cache names this exact case: "A V2
# database has only the shared 'SensorTables' row."
_GEN_TABLES = ("Sensor", "SensorData")


def bump(tables=_GEN_TABLES):
    """What the triggers do, for a writer the cache never sees.

    V3's triggers bump the row for the table that was written, so a fixture that
    bumps only a shared counter no longer models them.
    """
    for t in tables:
        DB.execute("UPDATE FrogNetTableGen SET Gen=Gen+1 WHERE TableName=?", (t,))
    DB.commit()


def write_presence(sid, name, epoch, stype="communicator"):
    DB.execute("INSERT OR REPLACE INTO Sensor VALUES (?,?,?,?,?,?,?,?)",
               (sid, "frog", "10.250.250.1", "10.250.250.0/24", name,
                stype, "", None))
    DB.execute("INSERT OR REPLACE INTO SensorData VALUES (?,?,?,?,?)",
               (sid, "frog", '{"id":"dave","status":"online"}',
                "2026-08-02 14:00:00", epoch))
    bump()


class _Cur:
    def __init__(self, con):
        self._c = con.cursor()
        self._rows = []
        self._i = 0

    def execute(self, sql, params=()):
        s = " ".join(sql.split())
        if "information_schema.TRIGGERS" in s:
            self._rows = [{"n": TRIGGERS[0]}]
        elif s.startswith("SELECT TableName, Gen"):
            # [DATA_CACHE_GEN_V3] _SQL_GEN reads BOTH per-table rows in one
            # statement (WHERE TableName IN (%s, %s)). The stand-in only knew
            # V2's single-row "SELECT Gen ... TableName=%s" form and raised
            # AssertionError("unexpected SQL"), which _check_wiring caught and
            # reported as "cannot verify generation wiring" -- the cache then
            # disabled itself and every assertion read FELL_THROUGH.
            qs = ",".join("?" for _ in params)
            rows = self._c.execute(
                f"SELECT TableName, Gen FROM FrogNetTableGen "
                f"WHERE TableName IN ({qs})", params).fetchall()
            self._rows = [{"TableName": r["TableName"], "Gen": r["Gen"],
                           "DbNow": NOW[0]} for r in rows]
        elif s.startswith("SELECT Gen"):
            r = self._c.execute(
                "SELECT Gen FROM FrogNetTableGen WHERE TableName=?", params).fetchone()
            self._rows = [{"Gen": r["Gen"], "DbNow": NOW[0]}] if r else []
        elif "FROM Sensor" in s and "SensorData" not in s:
            self._rows = [dict(x) for x in
                          self._c.execute("SELECT * FROM Sensor").fetchall()]
        elif "FROM SensorData" in s:
            self._rows = [dict(x) for x in
                          self._c.execute("SELECT * FROM SensorData").fetchall()]
        else:
            raise AssertionError("unexpected SQL: " + s)
        self._i = 0

    def fetchone(self):
        if self._i < len(self._rows):
            self._i += 1
            return self._rows[self._i - 1]
        return None

    def fetchall(self):
        r = self._rows[self._i:]
        self._i = len(self._rows)
        return r

    def close(self):
        pass


class _Conn:
    def cursor(self, dictionary=False, buffered=False):
        return _Cur(DB)

    def is_connected(self):
        return True

    def start_transaction(self, consistent_snapshot=False):
        pass

    def commit(self):
        pass


TRIGGERS = [6]

import daemon.engine.data_cache as D                       # noqa: E402
D._get_conn = lambda: _Conn()

BASE = "/api.php?entity=sensors&action=values&parse=1&SensorType=communicator"


def rows(path):
    out = D.try_intercept(path, "")
    if out is None:
        return "FELL_THROUGH"
    return json.loads(out)["rows"]


# ── G1: a row written by somebody else is visible on the very next read ──────
write_presence(1, "SD:presence.host:10.250.250.1:presence:dave-a", NOW[0] - 5)
r = rows(BASE)
ck("G1 first read sees the row", r != "FELL_THROUGH" and len(r) == 1, str(r)[:80])

write_presence(2, "SD:presence.host:10.250.250.1:presence:dave-b", NOW[0] - 5)
r = rows(BASE)
ck("G1 a row inserted by another writer appears on the NEXT read",
   r != "FELL_THROUGH" and len(r) == 2, f"rows={len(r) if r != 'FELL_THROUGH' else r}")

DB.execute("DELETE FROM Sensor WHERE SensorID=2")
DB.execute("DELETE FROM SensorData WHERE SensorID=2")
bump()
r = rows(BASE)
ck("G1 a row deleted by another writer disappears on the NEXT read",
   r != "FELL_THROUGH" and len(r) == 1, f"rows={len(r) if r != 'FELL_THROUGH' else r}")

DB.execute("UPDATE SensorData SET jsonData=? WHERE SensorID=1",
           ('{"id":"dave","status":"busy"}',))
bump()
r = rows(BASE)
ck("G1 an update by another writer is reflected on the NEXT read",
   r != "FELL_THROUGH" and r[0]["data"]["status"] == "busy",
   json.dumps(r[0]["data"]) if r != "FELL_THROUGH" else r)

# ── G2: unchanged generation is served without reloading ─────────────────────
before = D.get_stats()["reloads"]
# Separate baseline. This compared gen_checks against the RELOADS count
# (`gen_checks >= before + 3` where before was reloads), so it only passed when
# the two counters happened to sit close together -- an accident of how many
# reloads the preceding cases caused, not a property of the code. The claim is
# that each of the three reads checks the generation, so the baseline has to be
# gen_checks itself.
gen_before = D.get_stats()["gen_checks"]
rows(BASE); rows(BASE); rows(BASE)
ck("G2 three reads at an unchanged generation cause no reload",
   D.get_stats()["reloads"] == before, f"reloads={D.get_stats()['reloads'] - before}")
ck("G2 but the generation is checked on every one of them",
   D.get_stats()["gen_checks"] >= gen_before + 3,
   f"gen_checks +{D.get_stats()['gen_checks'] - gen_before}")

# ── G3: fresh_s is answered, on the store's clock, not treated as a column ───
r = rows(BASE + "&fresh_s=30")
ck("G3 fresh_s=30 returns the 5-second-old row",
   r != "FELL_THROUGH" and len(r) == 1, f"rows={len(r) if r != 'FELL_THROUGH' else r}")

NOW[0] += 600
r = rows(BASE + "&fresh_s=30")
ck("G3 the same row ten minutes later is correctly outside the window",
   r != "FELL_THROUGH" and len(r) == 0, f"rows={len(r) if r != 'FELL_THROUGH' else r}")

write_presence(3, "SD:presence.host:10.250.250.1:presence:dave-c", NOW[0] - 1)
r = rows(BASE + "&fresh_s=30")
ck("G3 a freshly written row is inside the window",
   r != "FELL_THROUGH" and len(r) == 1, f"rows={len(r) if r != 'FELL_THROUGH' else r}")

# ── G4: anything inexpressible goes to MySQL, never answered from RAM ────────
ck("G4 __like falls through", rows(BASE + "&SensorName__like=SD:%") == "FELL_THROUGH")
ck("G4 an unknown query key falls through",
   rows(BASE + "&some_future_filter=7") == "FELL_THROUGH")
ck("G4 a write is never intercepted",
   D.try_intercept("/api.php?entity=sensor_data&action=upsert_by_name", "{}") is None)

# ── G5: no generation wiring means no cache at all ───────────────────────────
TRIGGERS[0] = 3
D._wiring_ok = None
ck("G5 missing triggers disable the cache entirely",
   rows(BASE) == "FELL_THROUGH")
TRIGGERS[0] = 6
D._wiring_ok = None
ck("G5 restoring the triggers re-enables it", rows(BASE) != "FELL_THROUGH")

# ── G6: order then limit, the way api.php does it ────────────────────────────
NOW[0] += 1
for i, nm in ((10, "zzz"), (11, "aaa"), (12, "mmm")):
    write_presence(i, nm, NOW[0], stype="ordertest")
ORD = "/api.php?entity=sensors&action=values&parse=1&SensorType=ordertest"
r = rows(ORD + "&order=SensorName&limit=2")
ck("G6 order is applied before limit",
   r != "FELL_THROUGH" and [x["SensorName"] for x in r] == ["aaa", "mmm"],
   str([x["SensorName"] for x in r] if r != "FELL_THROUGH" else r))

print()
if FAILED:
    print(f"FAILED {len(FAILED)}:")
    for f in FAILED:
        print("  -", f)
    sys.exit(1)
print("ALL PASS")
