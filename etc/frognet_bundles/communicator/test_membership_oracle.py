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
test_membership_oracle.py -- [MEMBERSHIP_IS_SELF_ASSERTED_V1]

A fact is true only while the thing it is ABOUT keeps asserting it.

Membership was the one thing in the space that broke that rule. One row carried
a list of everyone's ids, and whoever wrote last renewed the whole list on
everyone's behalf. So John leaving did not retire John: five seconds later
Dave's heartbeat re-asserted the membership Dave remembered, with John in it,
and John's lobby rang -- from a peer that cannot address anyone in particular.
Nobody invited anybody.

[LEAVE_MEANS_FORGET_V1] fixed half: a node stopped re-asserting a call it had
left. The half left standing was that a node asserted OTHER PEOPLE'S membership,
which no amount of care by the leaver can fix.

  P1   membership is one row per member, scoped by member
  P2   refresh_calls re-asserts ONLY my own row
  P3   start_call and join_call assert me, never anyone else
  P4   invite() writes an INVITATION, not a membership
  P5   list_calls composes membership from the live rows
  P6   a member who stops asserting is gone; the session dies with the last one
  P7   leaving retires my row and my outstanding invitations, and nothing else
  P8   ringing keys on invitations, which carry a maker
  P9   an invitation with no live inviter is not an invitation
  P10  declining retires the invitation, not a membership
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

FAIL = []


def ck(name, cond, got=None):
    if cond:
        print("  PASS  %s" % name)
    else:
        print("  FAIL  %s   (got %r)" % (name, got))
        FAIL.append(name)


import comms_control as CC
import comms_ui as UI

# Captured ONCE, before any stubbing. Taking it from CC.T inside the helper
# meant the second call wrapped the first stub and recursed forever.
_realT = CC.T

if not hasattr(CC.ControlPlane, "invitations_for_me"):
    print("  FAIL  no invitations_for_me: an invitation is still a membership")
    print("\nFAILED: a remembered members list can still ring somebody who left.")
    sys.exit(1)


# ---- a store that behaves like one ----------------------------------------
class Store:
    """Rows keyed by (var, scope), each with a ts, retired by fresh_s."""

    def __init__(self):
        self.rows = {}
        self.now = 1000.0

    def put(self, var, scope, value):
        self.rows[(var, scope)] = {"scope": scope, "value": dict(value),
                                   "at": self.now}

    def get(self, var, fresh_s):
        return [dict(r) for (v, _s), r in self.rows.items()
                if v == var and (self.now - r["at"]) <= fresh_s]

    def drop(self, var, scope):
        self.rows.pop((var, scope), None)


def plane(store, me, host="10.160.160.1", port=9000):
    cp = CC.ControlPlane.__new__(CC.ControlPlane)
    cp.me_id, cp.me_name, cp.dbhost = me, me, "db"
    cp._call_ch, cp._invite_ch, cp._invited = {}, {}, {}
    cp._active_calls = {}
    cp._resolve_media = lambda: (host, port)

    def _publish(ch, var, scope):
        # Channels convey; what lands is the current value. The oracle is about
        # WHO asserts WHAT, so the codec is not in the way here.
        store.put(var, scope, ch.state if hasattr(ch, "state") else ch)
        return scope

    cp._publish = _publish
    cp._retire_row = lambda var, scope, what: store.drop(var, scope)
    return cp


# Channel's real behaviour is not what is under test; capture the offers.
class Bag(dict):
    def offer(self, k, v):
        self[k] = v
    @property
    def state(self):
        return self


_orig_setdefault = dict.setdefault


def bagged(cp):
    """Make the plane's channel dicts hand out Bags."""
    class D(dict):
        def setdefault(self, k, _v):
            if k not in self:
                self[k] = Bag()
            return self[k]
    cp._call_ch, cp._invite_ch = D(), D()
    return cp


S = Store()
dave = bagged(plane(S, "dave-1"))
john = bagged(plane(S, "john-1"))

# ---- P3/P1: starting asserts only me ---------------------------------------
call = dave.start_call()
sid = call["session"]
scopes = [sc for (v, sc) in S.rows if v == "call"]
ck("P1 membership is one row per member", len(scopes) == 1, scopes)
ck("P1 and the scope names the member",
   scopes[0] == CC._member_scope(sid, "dave-1"), scopes[0])
ck("P3 start_call asserts me and nobody else",
   S.rows[("call", scopes[0])]["value"]["member"] == "dave-1", None)

# ---- P5: composed membership ----------------------------------------------
def _stub(store):
    """Replace only the READ. session_scope and everything else stay real --
    stubbing a whole module means the oracle stops exercising the code's actual
    scope construction, which is half of what is under test."""
    class _T:
        def __getattr__(self, n):
            return getattr(_realT, n)
        @staticmethod
        def get(svc, var, dbhost=None, fresh_s=0):
            return store.get(var, fresh_s)
    return _T()
CC.T = _stub(S)
calls = dave.list_calls()
ck("P5 the session is visible with one member",
   len(calls) == 1 and calls[0]["members"] == ["dave-1"], calls)

john.join_call(sid)
calls = dave.list_calls()
ck("P5 a joiner appears by asserting itself",
   calls[0]["members"] == ["dave-1", "john-1"], calls[0]["members"])
ck("P3 join_call wrote john's own scope, not dave's",
   ("call", CC._member_scope(sid, "john-1")) in S.rows, sorted(
       sc for v, sc in S.rows if v == "call"))

# ---- P2: the heartbeat renews only mine ------------------------------------
S.now += 60
dave.refresh_calls()
ages = {sc: S.rows[("call", sc)]["at"] for v, sc in S.rows if v == "call"}
ck("P2 dave's heartbeat refreshed dave's row",
   ages[CC._member_scope(sid, "dave-1")] == S.now, ages)
ck("P2 and did NOT touch john's",
   ages[CC._member_scope(sid, "john-1")] < S.now, ages)

# ---- P6: ceasing is leaving -------------------------------------------------
# John goes away without a word. Dave keeps beating. John must disappear.
for _ in range(3):
    S.now += 60
    dave.refresh_calls()
calls = dave.list_calls()
ck("P6 a member who stops asserting is gone",
   calls and calls[0]["members"] == ["dave-1"], calls)
ck("P6 and dave's own call survived his heartbeat", len(calls) == 1, calls)

S.now += 200                       # dave stops too
ck("P6 the session dies with its last member", dave.list_calls() == [],
   dave.list_calls())

# ---- P4/P7: invitations ----------------------------------------------------
S = Store()
def _stub(store):
    """Replace only the READ. session_scope and everything else stay real --
    stubbing a whole module means the oracle stops exercising the code's actual
    scope construction, which is half of what is under test."""
    class _T:
        def __getattr__(self, n):
            return getattr(_realT, n)
        @staticmethod
        def get(svc, var, dbhost=None, fresh_s=0):
            return store.get(var, fresh_s)
    return _T()
CC.T = _stub(S)
dave = bagged(plane(S, "dave-1"))
john = bagged(plane(S, "john-1"))
call = dave.start_call()
sid = call["session"]
dave.invite(sid, ["john-1"])

ck("P4 an invitation is its own var",
   any(v == "invite" for v, _ in S.rows), sorted(S.rows))
ck("P4 and it did not make john a member",
   dave.list_calls()[0]["members"] == ["dave-1"],
   dave.list_calls()[0]["members"])

inv = john.invitations_for_me()
ck("P8 the invitation names its maker",
   len(inv) == 1 and inv[0]["from"] == "dave-1", inv)
ck("P8 and the session to join", inv and inv[0]["session"] == sid, inv)
ck("P8 dave is not invited to his own call",
   dave.invitations_for_me() == [], dave.invitations_for_me())

# ---- P9: an invitation with no live inviter is not one ---------------------
S.now += 200                        # dave stops beating entirely
ck("P9 the invitation retires with its inviter",
   john.invitations_for_me() == [], john.invitations_for_me())

# ---- P7: leaving retires mine and my invitations, and nothing else ---------
S = Store()
def _stub(store):
    """Replace only the READ. session_scope and everything else stay real --
    stubbing a whole module means the oracle stops exercising the code's actual
    scope construction, which is half of what is under test."""
    class _T:
        def __getattr__(self, n):
            return getattr(_realT, n)
        @staticmethod
        def get(svc, var, dbhost=None, fresh_s=0):
            return store.get(var, fresh_s)
    return _T()
CC.T = _stub(S)
dave = bagged(plane(S, "dave-1"))
john = bagged(plane(S, "john-1"))
sid = dave.start_call()["session"]
john.join_call(sid)
dave.invite(sid, ["sue-1"])
dave.leave_call(sid)
ck("P7 leaving retired my membership row",
   ("call", CC._member_scope(sid, "dave-1")) not in S.rows, sorted(S.rows))
ck("P7 and my outstanding invitation",
   not any(v == "invite" for v, _ in S.rows), sorted(S.rows))
ck("P7 and left john's row alone",
   ("call", CC._member_scope(sid, "john-1")) in S.rows, sorted(S.rows))
ck("P7 so the call is still john's call",
   john.list_calls()[0]["members"] == ["john-1"],
   john.list_calls()[0]["members"])

# ---- P8/P10: the ring rule -------------------------------------------------
r = UI.Rings() if hasattr(UI, "Rings") else None
if r is None:
    for _n in dir(UI):
        _o = getattr(UI, _n)
        if isinstance(_o, type) and hasattr(_o, "should_ring"):
            r = _o()
            break
ck("P8 there is a ring rule", r is not None)

invites = [{"session": "s1", "invitee": "john-1", "from": "dave-1",
            "host": "h", "port": 1}]
ck("P8 an invitation naming me rings",
   [i["session"] for i in r.should_ring(invites, "john-1", busy=False)] == ["s1"])
ck("P8 and only once", r.should_ring(invites, "john-1", busy=False) == [])
ck("P8 busy suppresses it",
   r.should_ring([{"session": "s2", "invitee": "john-1", "from": "d"}],
                 "john-1", busy=True) == [])
ck("P8 an invitation with no maker does not ring",
   r.should_ring([{"session": "s3", "invitee": "john-1"}], "john-1",
                 busy=False) == [])
ck("P8 somebody else's invitation does not ring me",
   r.should_ring([{"session": "s4", "invitee": "sue-1", "from": "d"}],
                 "john-1", busy=False) == [])

# a membership list can no longer ring anything: it is not what is read
ck("P8 a members list is not an invitation",
   r.should_ring([{"session": "s5", "members": ["john-1", "dave-1"]}],
                 "john-1", busy=False) == [])

r.declined("s1")
ck("P10 declining lets the same session ring again",
   [i["session"] for i in r.should_ring(invites, "john-1", busy=False)] == ["s1"])

src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "communicator_live.py"), encoding="utf-8").read()
ck("P10 the UI declines the invitation rather than leaving a call",
   "self.cp.decline(session)" in src and "cp.leave_call(session)" not in
   src[src.index("def _decline"):src.index("def _decline") + 1200], None)

print()
if FAIL:
    print("FAILED %d: %s" % (len(FAIL), ", ".join(FAIL)))
    sys.exit(1)
print("ALL PASS")
