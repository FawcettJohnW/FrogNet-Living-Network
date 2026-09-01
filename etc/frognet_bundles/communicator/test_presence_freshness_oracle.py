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
"""test_presence_freshness_oracle.py - departed users age out of the roster.

Regression for the seconds-vs-milliseconds bug: presence ts was written in ms while the
tuple store's freshness filter (get_all) compares against now-in-seconds, so (now_s - ts_ms)
was hugely negative and NO presence ever aged out - departed users lingered forever.
This proves: (1) presence ts is written in seconds, (2) the fresh_s filter drops a stale
presence and keeps a fresh one.
"""
import sys, os, time, types
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# in-memory tuple store that mimics frognet_tuples.get_all's fresh_s semantics (seconds)
class Store:
    def __init__(self): self.rows=[]
    def role_scope(self, s): return "scope:"+s
    def put(self, service, var, scope, value, **k):
        self.rows=[r for r in self.rows if not (r[1]==var and r[2]==scope)]  # upsert by var+scope
        self.rows.append((service,var,scope,value)); return True
    def get(self, service, var, dbhost=None, fresh_s=0, **k):
        now=int(time.time()); out=[]
        for s,v,sc,val in self.rows:
            if v!=var: continue
            ts=int(val.get("ts",0) or 0)
            if fresh_s and (now-ts)>fresh_s: continue        # SAME semantics as get_all
            out.append({"var":v,"scope":sc,"value":val})
        return out

import communicator_app as A
A.T = Store()  # inject

_p=_f=0
def ck(n,c,x=""):
    global _p,_f
    if c:_p+=1;print(f"  [PASS] {n}")
    else:_f+=1;print(f"  [FAIL] {n}  {x}")

print("=== presence freshness (departed users age out) ===")
# 1. ts written in seconds (not ms)
A.presence_register("gorp","Gorp")
row=[r for r in A.T.rows if r[1]=="presence"][0][3]
now=int(time.time())
ck("P1 presence ts is in SECONDS (close to now)", abs(now-int(row["ts"]))<=2, f"ts={row['ts']} now={now}")

# 2. a fresh presence shows in the list
lst=A.presence_list(fresh_s=90)
ck("P2 fresh user present in roster", any(u["id"]=="gorp" for u in lst), lst)

# 3. a stale presence (ts 200s ago) is dropped by the 90s filter
A.T.put(A.SERVICE,"presence","presence.alice",{"id":"alice","name":"Alice","status":"online","ts":int(time.time())-200})
lst=A.presence_list(fresh_s=90)
ck("P3 departed (stale) user AGED OUT", not any(u["id"]=="alice" for u in lst), lst)
ck("P3 fresh user still present", any(u["id"]=="gorp" for u in lst), lst)

print(f"\n=== {_p} passed, {_f} failed ===")
sys.exit(1 if _f else 0)
