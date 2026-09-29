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
test_five_tuples_oracle.py -- [FOUR_TUPLES_V1] [MEDIACONTROL_BELONGS_TO_THE_CALL_V1]

    CallsAnnounce/host:<ip>       what this host OFFERS
    CallsInProgress/host:<ip>     what this media host SEES carrying traffic
    CallsJoined/user:<name>       what this user SAYS it is in, and its reports
    CallsInvitations/user:<name>  what this user has been ASKED to join
    CallsMediaControl/call:<id>   the CALL's media settings

Replaces test_membership_oracle, which tested the one-row-per-(session,member)
shape these five replaced, and had been crashing on a ControlPlane that no
longer has the attributes it reached for.

  F1  each var is written by the one party with standing to know it
  F2  scoped to that party -- host by host, user by name, call by id
  F3  identity is the NAME: no per-launch id reaches a key
  F4  and the display marker does not either
  F5  the codex declares exactly the fields the row uses
"""
import ast
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__)) or "."
sys.path.insert(0, HERE)

FAIL = []


def ck(name, cond, got=None):
    if cond:
        print("  PASS  %s" % name)
    else:
        print("  FAIL  %s   (got %r)" % (name, got))
        FAIL.append(name)


import comms_control as CC

cc = open(os.path.join(HERE, "comms_control.py"), encoding="utf-8").read()

# ---- F1/F2 -----------------------------------------------------------------
for var in ("CallsAnnounce", "CallsInProgress", "CallsJoined",
            "CallsInvitations", "CallsMediaControl"):
    ck("F1 %s is written" % var, '"%s"' % var in cc, None)

ck("F2 host-scoped rows key on the host, with no pid",
   CC._host_scope_stable().startswith("host:")
   and CC._host_scope_stable().count(":") == 1, CC._host_scope_stable())
ck("F2 user-scoped rows key on the name",
   CC._user_scope("John") == "user:John", CC._user_scope("John"))
ck("F2 the call's own row keys on the session",
   CC._callid_scope("abc123") == "call:abc123", CC._callid_scope("abc123"))

# ---- F3/F4 -----------------------------------------------------------------
cp = CC.ControlPlane("dave-9b71", "*Dave")
ck("F3 the per-launch id is not the key", "9b71" not in CC._user_scope(cp.me_key),
   CC._user_scope(cp.me_key))
ck("F4 the unattended marker stays in the display name",
   cp.me_name == "*Dave", cp.me_name)
ck("F4 and out of the key", cp.me_key == "Dave", cp.me_key)

# ---- F5 --------------------------------------------------------------------
# A field the codex does not declare is not on the wire, whatever is offered;
# a field it declares and nobody supplies converges to None. Both have shipped.
tree = ast.parse(cc)
for fn_name, expect in (("_calls_codex", {"calls", "ts"}),
                        ("_mediacontrol_codex",
                         {"session", "speed_bps", "geometry", "quality",
                          "set_by", "ts"})):
    fn = next((n for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef) and n.name == fn_name), None)
    ck("F5 %s exists" % fn_name, fn is not None)
    if fn is None:
        continue
    seg = ast.get_source_segment(cc, fn) or ""
    # Check the freshness VALUES, not the word: the comment explaining the rule
    # names RESIDENT_ONCE, and banning the word bans the explanation. Third
    # time I have made that mistake -- an assertion has to look at code.
    _fresh = [n for n in ast.walk(fn)
              if isinstance(n, ast.keyword) and n.arg == "freshness"]
    _res = [a.attr for k in _fresh for a in ast.walk(k.value)
            if isinstance(a, ast.Attribute)]
    ck("F5 %s declares nothing RESIDENT_ONCE" % fn_name,
       "RESIDENT_ONCE" not in _res, sorted(set(_res)))
    for f in expect:
        ck("F5 %s declares %s" % (fn_name, f), '"%s"' % f in seg, None)

print()
if FAIL:
    print("FAILED %d: %s" % (len(FAIL), ", ".join(FAIL)))
    sys.exit(1)
print("ALL PASS")
