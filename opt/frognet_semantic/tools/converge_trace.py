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
converge_trace.py - turn "the looping persists" into the exact dest that won't settle.

A merge re-arms runAgain (-> another merge) iff a /24 winner moved (slash24_mutated),
a .frognet name reassigned (name_changed), or a concurrent lock bailed. A converged
mesh leaves all three at 0. So a chain that never stops has SOMETHING flagged every
pass - and runMerge already logs which, plus the exact /24(s) that moved:

  converge_decision ... runAgain=1 slash24_mutated=1 name_changed=0 concurrent=0
                        slash24_dests=['10.120.120.0/24']

This reads every converge_decision in a merge log, tallies what drove each runAgain,
and ranks the /24s by how often they re-armed it. The /24 at the top of that list,
appearing pass after pass, is the churning dest dragging the whole chain (and, via
computed_wan -> reach_plane, the databasehost with it).

Usage:  python3 converge_trace.py <merge.log> [more.log ...]
"""
import ast
import re
import sys
from collections import Counter

_KV = re.compile(r"(\w+)=("
                 r"\[[^\]]*\]"          # a python list literal  e.g. ['10.x/24']
                 r"|'[^']*'"            # a quoted string
                 r"|[^\s]+)")           # a bareword / number


def _parse(line):
    """Pull every key=value off a converge_decision line into a dict."""
    seg = line.split("converge_decision", 1)[1]
    d = {}
    for k, v in _KV.findall(seg):
        v = v.strip()
        if v.startswith("[") or v.startswith("'"):
            try:
                v = ast.literal_eval(v)
            except Exception:
                pass
        d[k] = v
    return d


def analyze(paths):
    decisions = []
    for p in paths:
        try:
            with open(p, "r", errors="replace") as f:
                for line in f:
                    if "converge_decision" in line and "slash24_dests=" in line:
                        decisions.append(_parse(line))
        except OSError as e:
            print(f"!! cannot read {p}: {e}")

    if not decisions:
        print("no converge_decision lines with slash24_dests found "
              "(old build, or wrong log) - nothing to trace.")
        return 1

    rearm = [d for d in decisions if str(d.get("runAgain")) == "1"]
    drove_routes = sum(1 for d in rearm if str(d.get("slash24_mutated")) == "1")
    drove_name   = sum(1 for d in rearm if str(d.get("name_changed")) == "1")
    drove_conc   = sum(1 for d in rearm if str(d.get("concurrent")) == "1")

    # rank the /24s that re-armed runAgain
    churn = Counter()
    for d in rearm:
        for s24 in (d.get("slash24_dests") or []):
            churn[s24] += 1

    print(f"converge_decisions: {len(decisions)}   re-armed runAgain: {len(rearm)}"
          f"   clean (converged): {len(decisions) - len(rearm)}")
    print(f"driver of the re-arms:  slash24_mutated={drove_routes}"
          f"   name_changed={drove_name}   concurrent={drove_conc}")
    print()

    if churn:
        print("/24s that re-armed runAgain (most persistent first):")
        for s24, n in churn.most_common():
            bar = "#" * min(40, n)
            print(f"  {n:>4}x  {s24:<20} {bar}")
        print()

    # verdict
    top = churn.most_common(1)[0] if churn else None
    if drove_routes and top and top[1] >= max(2, len(rearm) // 2):
        print(f"VERDICT: route churn. {top[0]} re-armed runAgain {top[1]} of "
              f"{len(rearm)} passes - its /24 winner is flipping every merge "
              f"(WINNER_HYSTERESIS_V1 not holding it). That moves computed_wan -> "
              f"reach_plane -> the databasehost with it. Fix the flap on {top[0]}; "
              f"the name follows.")
    elif drove_name and drove_name >= drove_routes:
        print("VERDICT: name churn with routes comparatively quiet. A .frognet name "
              "is reassigning each pass - chase what its eval reads (the malformed-"
              "blob guard removes one such cause). converge_decision doesn't carry "
              "the name; correlate with the SERVICE_ELECT lines at each depth.")
    else:
        print("VERDICT: mixed / no single dominant dest - inspect the ranked list above.")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(analyze(sys.argv[1:]))
