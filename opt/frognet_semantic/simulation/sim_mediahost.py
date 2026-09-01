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
import os, sys
# installed layout: communicator bundle modules + media assets next to this sim
_HERE = os.path.dirname(os.path.abspath(__file__))
for _c in ["/etc/frognet_bundles/communicator", os.path.join(_HERE, "..", "..", "..", "etc", "frognet_bundles", "communicator")]:
    if os.path.isdir(_c):
        sys.path.insert(0, os.path.abspath(_c)); break
_MEDIA = os.path.join(_HERE, "media_assets")
_RUNGDIR = os.path.join(_MEDIA, "rungs")
"""
sim_mediahost.py - prove the media-host reconciliation loop with on-demand rungs, in-process
(no real ffmpeg/uplinks; we assert on the PLAN and the pipeline set the reconciler computes).

Proves:
  R1  only rungs that consumers DEMAND are started (nothing pre-encoded)
  R2  a client switching to a new ladder level STARTS that rung
  R3  the last client leaving a rung STOPS it
  R4  minus-self: each recipient's mix excludes their own source
  R5  plan is rebuildable from tuples alone (float-safe): a fresh host re-reads and matches
"""
import sys, types
# stub frognet_tuples with an in-memory space so the host runs in-container
space = {}
ft = types.ModuleType("frognet_tuples")
def put(service, var, scope, value, dbhost=None, **k): space[(service,var,scope)] = value
def get_all(service, dbhost=None, **k):
    return [{"value":v} for (s,_,_),v in space.items() if s==service]
ft.put=put; ft.get_all=get_all; ft.DEFAULT_DBHOST="databasehost_control.frognet"
sys.modules["frognet_tuples"]=ft

import frognet_mediahost as MH

_p=_f=0
def ck(n,c,x=""):
    global _p,_f
    if c: _p+=1; print(f"  [PASS] {n}")
    else: _f+=1; print(f"  [FAIL] {n}  {x}")

def decl_stream(who, session="s1"):
    put("SD","stream",f"{session}.{who}",{"type":"stream","session":session,"who":who})
def decl_watch(who, level, session="s1"):
    put("SD","watch",f"{session}.{who}",{"type":"watch","session":session,"who":who,"ladder_level":level})

print("=== media-host reconciliation: on-demand rungs ===")
# three sources, two of them also watch
decl_stream("julie"); decl_stream("dan"); decl_stream("sammy")
decl_watch("julie", 2)        # julie watches at rung 2
decl_watch("dan", 2)          # dan watches at rung 2

host = MH.MediaHost("s1")
plan = host.reconcile()

# R1: only demanded levels (2) are in the plan, not all 5 ladder rungs
ck("R1 only demanded rungs encoded (level 2 only)", plan["demanded_levels"]==[2], plan["demanded_levels"])
running_levels = sorted({lvl for (_,lvl) in host.pipelines})
ck("R1b pipelines only at demanded level", running_levels==[2], running_levels)

# R4: minus-self - julie's mix excludes julie, includes dan+sammy
jr = plan["recipients"]["julie"]
ck("R4 minus-self: julie's others = dan,sammy", sorted(jr["others"])==["dan","sammy"], jr["others"])

# R2: dan switches to level 0 (a rung not yet running) -> host starts it
decl_watch("dan", 0)
plan = host.reconcile()
running_levels = sorted({lvl for (_,lvl) in host.pipelines})
ck("R2 client switch to new level STARTS that rung", 0 in running_levels and 2 in running_levels, running_levels)
ck("R2b plan reflects both demanded levels", plan["demanded_levels"]==[0,2], plan["demanded_levels"])

# R3: dan leaves entirely (remove his watch); julie still at 2 -> level 0 has no subscriber -> stop
del space[("SD","watch","s1.dan")]
plan = host.reconcile()
running_levels = sorted({lvl for (_,lvl) in host.pipelines})
ck("R3 last subscriber leaves a rung -> STOPS it (level 0 gone)", running_levels==[2], running_levels)

# R5: float - a brand new host process re-reads the SAME space and rebuilds the SAME plan
host2 = MH.MediaHost("s1")
plan2 = host2.reconcile()
same = (sorted({lvl for (_,lvl) in host2.pipelines}) == sorted({lvl for (_,lvl) in host.pipelines})
        and plan2["recipients"].keys()==plan["recipients"].keys())
ck("R5 float-safe: fresh host rebuilds identical plan from tuples", same)

print(f"\n=== {_p} passed, {_f} failed ===")
sys.exit(1 if _f else 0)
