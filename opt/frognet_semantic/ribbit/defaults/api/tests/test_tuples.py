#!/usr/bin/env python3
# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
"""test_tuples.py PORT -- the default API (frognet_tuples over libtuples) against a real RAM host (named-ram tuples)."""
import os, subprocess, sys, threading, time
HERE = os.path.dirname(os.path.abspath(__file__))
os.environ["FROGNET_TUPLES_RAM"] = "127.0.0.1:%s" % sys.argv[1]; os.environ["FROGNET_TUPLES_VENDOR"] = "tuples"
os.environ.setdefault("FROGNET_TUPLES_LIB", os.path.join(HERE, "..", "build"))
sys.path.insert(0, os.path.join(HERE, ".."))
import frognet_tuples as T
fails = 0
def check(ok, what):
    global fails
    print(("PASS " if ok else "FAIL ") + what); fails += 0 if ok else 1
svc = "test%d" % os.getpid()
T.put(svc, "caps", T.node_scope(), {"cam": True})
rows = T.get(svc, "caps")
check(len(rows) == 1 and rows[0]["value"]["cam"] is True and "ts" in rows[0]["value"] and rows[0]["scope"] == T.node_scope(),
      "T1 put then get: the value, stamped with ts, under this node's scope")
check(rows[0]["addr"] == T.my_ip() and rows[0]["age_s"] <= 2, "T2 the row carries the writer's address and a fresh envelope age")
T.put(svc, "caps", T.node_scope(), {"cam": False})
rows = T.get(svc, "caps")
check(len(rows) == 1 and rows[0]["value"]["cam"] is False, "T3 a put replaces the tuple")
got = []
def waiter(): got.extend(T.get_all(svc, name_like="SD:late.%", wait_s=10, min_rows=2))
t0 = time.time(); th = threading.Thread(target=waiter); th.start()
time.sleep(0.4); T.put(svc, "late", "a", {"n": 1}); time.sleep(0.3); T.put(svc, "late", "b", {"n": 2}); th.join()
check(len(got) == 2 and 0.6 <= time.time() - t0 < 5, "T4 a held get_all waits for min_rows=2 and wakes when the second arrives")
check(len(T.get_all(svc, name_like="SD:late.b")) == 1, "T5 an exact name reads one tuple")
raw = T._values_raw(svc, name_like="SD:late.%")
check(len(raw) == 2 and all(r["SensorType"] == svc and r["SensorName"].startswith("SD:late.") for r in raw), "T6 _values_raw in the node's row shape")
check(T._delete_by_id(None, raw[0]["SensorID"]) and len(T.get(svc, "late")) == 1, "T7 _delete_by_id removes that row")
time.sleep(2.2)
check(T.get_all(svc, fresh_s=1) == [], "T8 fresh_s drops rows older than it, on the memory's clock")
# own=True rows go when the process exits; own=False rows stay
code = ("import os,sys; sys.path.insert(0,%r); import frognet_tuples as T; T.put(%r,'eph','x',{'a':1}); T.put(%r,'keep','x',{'a':1},own=False)"
        % (os.path.join(HERE, ".."), svc, svc))
subprocess.run([sys.executable, "-c", code], check=True, env=os.environ)
check(T.get(svc, "eph") == [] and len(T.get(svc, "keep")) == 1, "T9 own=True tuples are removed when their writer exits; own=False ones outlive it")
T.put(svc, "node", "n", {"a": 1}, dbhost="databasehost_control.frognet")
check(len(T.get(svc, "node", dbhost="databasehost.frognet")) == 1, "T10 a node's names for its memory mean the vendor's RAM host")
try:
    T.put("x", "y", "z", {}, dbhost="mediahost.frognet")
    check(False, "T11 any other node name is refused")
except T.StoreUnreachable as e:
    check(True, "T11 any other node name as dbhost is refused, no fallback (%s)" % str(e)[:60])
print("RESULT %s default API (frognet_tuples over libtuples): %d fail" % ("FAIL" if fails else "PASS", fails))
sys.exit(1 if fails else 0)
