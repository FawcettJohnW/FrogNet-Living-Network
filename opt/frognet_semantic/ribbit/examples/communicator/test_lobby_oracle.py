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
test_lobby_oracle.py -- [AN_ANNOUNCED_CALL_IS_SOMEWHERE_TO_GO_V1]

The lobby shows an announced call whether or not anybody has joined it yet.

Observed 2026-08-11: Dave appears in the flock, announces a call, and the lobby
says "none right now". Both ends on current code, both reading the same store.
The call was in list_calls the whole time; _render_calls dropped it.

  L1  a call with members is shown
  L2  a call with NO members is shown -- it is an offer, not a leftover
  L3  and it reads as an offer, naming where to connect
  L4  identity in the lobby is the NAME, so my own name is excluded from a
      label and a per-process id matches nothing
  L5  a failure to read calls is not silently an empty lobby
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


import comms_ui as UI

src = open(os.path.join(HERE, "communicator_live.py"), encoding="utf-8").read()

# ---- L1/L2: what reaches the rows -------------------------------------------
# _render_calls used to filter: calls = [c for c in calls if c["members"]].
ck("L2 the members filter is gone",
   'calls = [c for c in calls if (c.get("members") or [])]' not in src, None)
ck("L2 and the reason is recorded",
   "AN_ANNOUNCED_CALL_IS_SOMEWHERE_TO_GO_V1" in src, None)

fn = next(n for n in ast.walk(ast.parse(src))
          if isinstance(n, ast.FunctionDef) and n.name == "_render_calls")
body = ast.get_source_segment(src, fn) or ""
ck("L1 every call it is given gets a row",
   "for c in calls:" in body and "Join" in body, None)
ck("L2 'none right now' is reached only when there are NO calls",
   body.index("if not calls:") < body.index("for c in calls:"), None)

# ---- L3 ---------------------------------------------------------------------
lbl = UI.call_label({"session": "abc", "members": [], "host": "10.160.160.1"},
                    {}, "John")
ck("L3 an unjoined call reads as an offer, not as broken",
   "open call" in lbl and "empty" not in lbl, lbl)
ck("L3 and says where", "10.160.160.1" in lbl, lbl)

# ---- L4 ---------------------------------------------------------------------
ck("L4 my own name is excluded from the label",
   UI.call_label({"members": ["Dave", "John"]}, {}, "John") == "Dave",
   UI.call_label({"members": ["Dave", "John"]}, {}, "John"))
ck("L4 somebody else's is not",
   UI.call_label({"members": ["Dave"]}, {}, "John") == "Dave", None)
ck("L4 a per-process id excludes nothing -- which is why the NAME is passed",
   UI.call_label({"members": ["Dave", "John"]}, {}, "john-9b71")
   != "Dave", None)
ck("L4 the app keeps its name for exactly this",
   "self.me_name = args.name" in src and
   "UI.call_label(c, self._names, self.me_name)" in src, None)
ck("L4 and rings by name", "should_ring(invites, self.me_name" in src, None)

# ---- L5 ---------------------------------------------------------------------
poll = next(n for n in ast.walk(ast.parse(src))
            if isinstance(n, ast.FunctionDef) and n.name == "_poll")
pbody = ast.get_source_segment(src, poll) or ""
ck("L5 a failed list_calls is logged, not swallowed",
   "list_calls failed" in pbody and "LOG.exception" in pbody, None)
ck("L5 and so is a failed roster", pbody.count("LOG.exception") >= 2,
   pbody.count("LOG.exception"))
ck("L5 no bare 'except Exception:' left turning a read into an empty list",
   "except Exception:\n                calls = []" not in pbody, None)

print()
if FAIL:
    print("FAILED %d: %s" % (len(FAIL), ", ".join(FAIL)))
    sys.exit(1)
print("ALL PASS")
