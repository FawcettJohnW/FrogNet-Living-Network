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
sim_call_visibility.py -- can one client SEE and JOIN another's call?

Two real ControlPlanes, the real Codex and Channel, the real _publish, against a
store that behaves like the store. NOTHING is stubbed between the write and the
read -- only T.put/T.get are redirected into a dict, and the row that lands is
whatever _publish actually produced.

That gap is why this exists. test_membership_oracle stubbed _publish, so twelve
assertions passed while every call row on the wire carried member=None:

  * `member` was RESIDENT_ONCE in `freshness` but absent from `resident`, so the
    codex converged it to None. [RESIDENT_ONCE_NEEDS_A_RESIDENT_VALUE_V1]
  * before that, `member` was not in field_order at all, so it never reached the
    wire. [MEMBERSHIP_IS_SELF_ASSERTED_V1]

Both were invisible to an oracle that did not run the codex. This runs it.

  S1  Dave publishes: a row lands, and it names Dave
  S2  John, who created nothing, SEES Dave's call
  S3  John joins it -- one session, both members, no second session
  S4  the invitation Dave sends reaches John and names Dave
  S5  John stops: he is gone from the membership, Dave is not
  S6  Dave stops: the session is gone
  S7  no row ever carries a None in a declared field
"""
import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) or ".")

FAIL = []


def ck(name, cond, got=None):
    if cond:
        print("  PASS  %s" % name)
    else:
        print("  FAIL  %s   (got %r)" % (name, got))
        FAIL.append(name)


import frognet_tuples as T
import comms_control as CC


class Store:
    """Rows keyed (var, scope). Ages out by fresh_s, like the real one."""

    def __init__(self):
        self.rows = {}
        self.clock = 1_000_000

    def put(self, service, var, scope, value, dbhost=None, own=False, **kw):
        self.rows[(var, scope)] = {"scope": scope, "value": dict(value),
                                   "at": self.clock, "addr": "10.0.0.1"}
        return True

    def get(self, service, var, dbhost=None, fresh_s=0, **kw):
        return [dict(r) for (v, _), r in self.rows.items()
                if v == var and (self.clock - r["at"]) <= fresh_s]

    def values_raw(self, service, dbhost=None, **kw):
        return [{"SensorName": "SD:%s.x" % v, "SensorID": i,
                 "scope": r["scope"], "data": r["value"]}
                for i, ((v, _s), r) in enumerate(self.rows.items())]

    def delete_by_id(self, dbhost, sid):
        keys = list(self.rows)
        if 0 <= sid < len(keys):
            self.rows.pop(keys[sid])
        return True


S = Store()
T.put = S.put
T.get = S.get
T._values_raw = S.values_raw
T._delete_by_id = S.delete_by_id
T.my_ip = lambda: "10.0.0.1"


def plane(me):
    cp = CC.ControlPlane(me, me)
    cp._resolve_media = lambda: ("10.160.160.1", 9000)
    return cp


dave = plane("dave-1")
john = plane("john-1")

# ---- S1 --------------------------------------------------------------------
call = dave.start_call()
sid = call["session"]
rows = [(sc, r["value"]) for (v, sc), r in S.rows.items() if v == "call"]
ck("S1 publishing lands exactly one call row", len(rows) == 1, rows)
val = rows[0][1] if rows else {}
ck("S1 and the row NAMES the member -- not None, not absent",
   val.get("member") == "dave-1", val)
ck("S1 and carries the session", val.get("session") == sid, val)
ck("S1 and where to connect",
   val.get("host") == "10.160.160.1" and int(val.get("port") or 0) == 9000, val)

# ---- S7: the failure mode that shipped twice -------------------------------
nones = [(sc, k) for (v, sc), r in S.rows.items()
         for k, x in r["value"].items() if x is None]
ck("S7 no declared field converged to None", not nones, nones)

# ---- S2: the whole question -------------------------------------------------
seen = john.list_calls()
ck("S2 a client that created nothing SEES the open call",
   len(seen) == 1 and seen[0]["session"] == sid, seen)
ck("S2 with Dave listed on it", seen and seen[0]["members"] == ["dave-1"], seen)

# ---- S3 --------------------------------------------------------------------
joined = john.join_call(sid)
ck("S3 joining returns where to point fnav",
   joined and joined["session"] == sid and joined["host"] == "10.160.160.1",
   joined)
after = john.list_calls()
ck("S3 ONE session, not two", len(after) == 1, after)
ck("S3 with both members on it",
   after[0]["members"] == ["dave-1", "john-1"], after[0]["members"])
ck("S3 and Dave sees the same thing", dave.list_calls() == after, dave.list_calls())

# ---- S4 --------------------------------------------------------------------
S2p = plane("sue-1")
dave.invite(sid, ["sue-1"])
inv = S2p.invitations_for_me()
ck("S4 the invitation reaches the invitee", len(inv) == 1, inv)
ck("S4 and names who made it", inv and inv[0].get("from") == "dave-1", inv)
ck("S4 and which session", inv and inv[0].get("session") == sid, inv)
ck("S4 nobody else is invited", john.invitations_for_me() == [],
   john.invitations_for_me())
invrows = [r["value"] for (v, _), r in S.rows.items() if v == "invite"]
ck("S4 no None in the invitation row either",
   all(x is not None for r in invrows for x in r.values()), invrows)

# ---- S5: ceasing is leaving ------------------------------------------------
for _ in range(3):
    S.clock += 60
    dave.refresh_calls()            # John does NOT
alive = dave.list_calls()
ck("S5 a member who stopped asserting is gone",
   alive and alive[0]["members"] == ["dave-1"], alive)
ck("S5 and the one still asserting is not", len(alive) == 1, alive)

# ---- S6 --------------------------------------------------------------------
S.clock += 300                       # Dave stops too
ck("S6 the session dies with its last member", dave.list_calls() == [],
   dave.list_calls())

print()
if FAIL:
    print("FAILED %d: %s" % (len(FAIL), ", ".join(FAIL)))
    sys.exit(1)
print("ALL PASS")
