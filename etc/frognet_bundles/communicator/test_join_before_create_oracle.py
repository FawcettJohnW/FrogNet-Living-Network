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
test_join_before_create_oracle.py -- [JOIN_BEFORE_YOU_CREATE_V1]

Pressing Call with nobody selected JOINS an open call on this relay. It creates
one only when there is none.

_start_call minted a new session unconditionally, so two people pressing Call
sat in two different sessions. Before [FAN_IS_PER_SESSION_V1] the shared relay
port made those interoperate by accident; that accident was the bug, and it was
also the only thing bringing the two ends together. With the fan enforcing
sessions, the same two presses produced two clients transmitting into black
holes -- measured 2026-08-10, 374 KB/s out and frames=0 back, on both sides.

  J1  an open call is joined, and no new session is minted
  J2  with none open, a call is created
  J3  the pick is DETERMINISTIC: two callers racing must land in the same room,
      which an arbitrary pick would not guarantee
  J4  most-populated wins, so a second caller joins the conversation rather
      than the empty room somebody left behind
  J5  `Call <person>` still CREATES -- a request to talk to someone must not
      drop the caller into a stranger's room
  J6  a failed join falls through to creating rather than leaving no call
  J7  a failed list falls through too, and says so
"""
import ast
import os
import sys

FAIL = []


def ck(name, cond, got=None):
    if cond:
        print("  PASS  %s" % name)
    else:
        print("  FAIL  %s   (got %r)" % (name, got))
        FAIL.append(name)


HERE = os.path.dirname(os.path.abspath(__file__)) or "."
src = open(os.path.join(HERE, "communicator_live.py"), encoding="utf-8").read()

tree = ast.parse(src)
fn = None
for node in ast.walk(tree):
    if isinstance(node, ast.FunctionDef) and node.name == "_start_call":
        fn = node
        break

if fn is None:
    print("  FAIL  no _start_call")
    sys.exit(1)

body = ast.get_source_segment(src, fn) or ""


def calls_named(name):
    return [n for n in ast.walk(fn)
            if isinstance(n, ast.Call)
            and getattr(n.func, "attr", "") == name]


# ---- J1/J2 -----------------------------------------------------------------
# On a tree without the change _start_call only creates, so every assertion
# below would raise on a missing substring rather than report. Fail cleanly and
# say what is absent -- an oracle that crashes is not an oracle that answered.
if not calls_named("join_call"):
    print("  FAIL  _start_call never joins: it mints a new session every time")
    print("\nFAILED: two callers land in two sessions and neither hears the "
          "other.")
    sys.exit(1)
ck("J1 _start_call can join", True)
ck("J2 _start_call can still create", bool(calls_named("start_call")), None)

# `start_call(` also matches this function's own `def _start_call(` at offset 5,
# which made the ordering check compare the join against the def line. Anchor on
# the call through the control plane, which is the thing being ordered.
_j = body.index("cp.join_call(")
_s = body.index("cp.start_call(")
ck("J1 the join is attempted BEFORE the create", _j < _s, (_j, _s))
ck("J1 both are reached through the control plane, not re-implemented",
   body.count("cp.join_call(") == 1 and body.count("cp.start_call(") == 1,
   (body.count("cp.join_call("), body.count("cp.start_call(")))
ck("J1 and a successful join returns without creating",
   "self._enter_call(info)" in body[_j:_s] and "return" in body[_j:_s], None)

# ---- J3/J4: the pick ------------------------------------------------------
ck("J4 the most-populated call wins",
   "-len(" in body and "members" in body, None)
ck("J3 ties break on the session id, so the pick is deterministic",
   'c["session"]' in body and ".sort(" in body, None)

# Simulate the documented rule against the documented input.
def pick(open_calls):
    o = [c for c in open_calls if c.get("session")]
    if not o:
        return None
    o.sort(key=lambda c: (-len(c.get("members") or []), c["session"]))
    return o[0]["session"]

rooms = [{"session": "bbb", "members": ["Dave"]},
         {"session": "aaa", "members": []},
         {"session": "ccc", "members": ["Dave", "Sue"]}]
ck("J4 a populated room beats an abandoned one", pick(rooms) == "ccc",
   pick(rooms))
ck("J3 two callers seeing the same list pick the same room",
   pick(rooms) == pick(list(reversed(rooms))), None)
ck("J3 equal population falls back to the session id",
   pick([{"session": "zzz", "members": ["A"]},
         {"session": "aaa", "members": ["B"]}]) == "aaa", None)
ck("J2 nothing open means nothing to join", pick([]) is None, None)
ck("J1 a row with no session is not a room",
   pick([{"session": "", "members": ["X"]}]) is None, None)

# ---- J5: an addressed call still creates ----------------------------------
ck("J5 the join path is gated on nobody being selected",
   "if not members:" in body, None)
_g = body.index("if not members:")
ck("J5 and the gate comes before the join",
   _g < body.index("cp.join_call("), None)

# ---- J6/J7: falling through ------------------------------------------------
ck("J6 a failed join falls through to creating",
   "join of open call" in body and "starting a new one" in body, None)
ck("J7 a failed list falls through too", "could not list open calls" in body,
   None)
ck("J7 and neither failure is silent",
   body.count("LOG.warning") >= 2, body.count("LOG.warning"))
ck("J1 a successful join is logged as a join, not a start",
   "did not create" in body, None)

print()
if FAIL:
    print("FAILED %d: %s" % (len(FAIL), ", ".join(FAIL)))
    sys.exit(1)
print("ALL PASS")
