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
test_leg_request_oracle.py - the client's leg_request tuple round-trips to the server and
clamps THAT leg only.

Flow under test:
  1. client writes request_leg(viewer, leg_bearer) via TupleControl  (the tuple the UI writes)
  2. server reads it back via leg_request(viewer)
  3. the reconciler clamps that viewer's served rung to min(requested_level, leg_bearer),
     INDEPENDENT of other viewers' legs.

Proves:
  Q1  request_leg writes, leg_request reads the same value back (round trip)
  Q2  per-viewer scope: two viewers' requests don't collide
  Q3  compute_plan clamps the requesting leg and leaves the other leg alone
  Q4  clearing (no request) => that leg returns to its requested level (adaptive)
"""
import os, sys, types
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

# in-memory tuple backend matching TupleControl's contract (put/get_one/get)
class MemBackend:
    def __init__(self): self.space = {}
    def put(self, service, var, scope, value, addr=None):
        self.space[(service, var, scope)] = {"scope": scope, "value": value}
    def get_one(self, service, var, scope, fresh_s=0):
        row = self.space.get((service, var, scope))
        return row["value"] if row else None
    def get(self, service, var, fresh_s=0):
        return [r for (s, v, _), r in self.space.items() if s == service and v == var]

import media_stream as MS

_p = _f = 0
def ck(n, c, x=""):
    global _p, _f
    if c: _p += 1; print(f"  [PASS] {n}")
    else: _f += 1; print(f"  [FAIL] {n}  {x}")

print("=== client->server leg_request round trip ===")
be = MemBackend()
ctrl = MS.TupleControl(be, "alice-grp-1", addr="10.250.250.20")

# Q1 round trip
ctrl.request_leg("alice", 5)
ck("Q1 leg_request reads back what request_leg wrote", ctrl.leg_request("alice") == 5,
   ctrl.leg_request("alice"))

# Q2 per-viewer scope: gorp's request is separate
ctrl.request_leg("gorp", 7)
ck("Q2 per-viewer scope: alice=5, gorp=7 independent",
   ctrl.leg_request("alice") == 5 and ctrl.leg_request("gorp") == 7,
   (ctrl.leg_request("alice"), ctrl.leg_request("gorp")))

# Q3 the reconciler clamps the requesting leg only
import frognet_mediahost as MH
# build watcher dicts as compute_plan expects, folding in the leg_request as leg_bearer
streams = [{"who": "alice"}, {"who": "gorp"}]
watchers = [
    {"who": "alice", "ladder_level": 7, "leg_bearer": ctrl.leg_request("alice")},  # asked L5
    {"who": "gorp",  "ladder_level": 7, "leg_bearer": ctrl.leg_request("gorp")},   # asked L7
]
plan = MH.compute_plan(streams, watchers)
ck("Q3 alice leg clamped to L5 by her request", plan["recipients"]["alice"]["level"] == 5,
   plan["recipients"]["alice"])
ck("Q3 gorp leg stays L7 (his request) - independent", plan["recipients"]["gorp"]["level"] == 7,
   plan["recipients"]["gorp"])

# Q4 clearing alice's request (back to full) -> her leg returns to requested L7
ctrl.request_leg("alice", 7)
watchers[0]["leg_bearer"] = ctrl.leg_request("alice")
plan2 = MH.compute_plan(streams, watchers)
ck("Q4 alice leg back to L7 after clearing the downgrade",
   plan2["recipients"]["alice"]["level"] == 7, plan2["recipients"]["alice"])

print(f"\n=== {_p} passed, {_f} failed ===")
print("ORACLE GREEN" if _f == 0 else "ORACLE RED")
sys.exit(1 if _f else 0)
