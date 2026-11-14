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
agent_workload/experiment/analyze.py -- derive, qualify, and refuse.

[A_COMPARISON_MUST_QUALIFY_ITSELF_V1]

The post-processor reads immutable captures and produces derived views. It
does not interpret ambiguous counters, because there are no longer ambiguous
counters to interpret: the lifecycle records carry orthogonal facts and the
stat schema carries aggregation semantics.

What it must do that nothing upstream can is refuse. Three refusals, each
earned by a real mistake:

  INCOMPLETE   An arm that did not finish the case is not a baseline. A
               velocity ratio was printed against an arm that finished 2 of 4
               workers covering 4 of 8 shards, because both arms had SOME
               throughput and the ratio was arithmetically computable.

  ABSENT       Where one side lacks the concept entirely, say so. The 0906
               tree has no READ_BY_IDENTITY, no incarnation, no lazy
               materialisation. The honest output is "capability absent", not
               a number manufactured by treating absence as zero.

  UNISOLATED   A quantity measured under conditions that were not controlled
               is an observation about one run. Strict-identity participation
               of 1.48 was a one-core scheduling artefact and was nearly
               promoted into an architectural constraint.

Every derived figure carries which of these it survived. A caller cannot get
a comparison out of this module without also getting its qualification.
"""

from __future__ import annotations

import os
from typing import Any, Dict, Iterable, List, Optional, Tuple

from . import capture

OK = "ok"
INCOMPLETE = "incomplete"
ABSENT = "capability_absent"
UNISOLATED = "unisolated"


class Qualified:
    """A number and what may be done with it.

    Truthiness is deliberately tied to the qualification, so a caller that
    writes `if q:` cannot accidentally print a figure that did not qualify.
    """

    __slots__ = ("value", "status", "reason")

    def __init__(self, value, status=OK, reason=""):
        self.value = value
        self.status = status
        self.reason = reason

    def __bool__(self):
        return self.status == OK and self.value is not None

    def __repr__(self):
        if self.status == OK:
            return "%s" % (self.value,)
        return "<%s: %s>" % (self.status, self.reason)

    def as_dict(self):
        return {"value": self.value if self.status == OK else None,
                "status": self.status, "reason": self.reason}


def load(run_dir: str) -> Tuple[Dict, List[Dict]]:
    """Metadata and every envelope, exactly as captured."""
    return capture.read_run(run_dir), list(capture.read_events(run_dir))


def by(envelopes: Iterable[Dict], key: str) -> Dict[Any, List[Dict]]:
    out: Dict[Any, List[Dict]] = {}
    for e in envelopes:
        out.setdefault(e.get(key), []).append(e)
    return out


def funnel(envelopes: Iterable[Dict]) -> Dict[str, Any]:
    """The four transitions plus avoided and wasted work, derived from the
    orthogonal facts rather than read from a status field."""
    ev = [e.get("event", {}) for e in envelopes]
    ev = [x for x in ev if "present" in x]
    if not ev:
        # [A_COMPARISON_MUST_QUALIFY_ITSELF_V1] No candidate records is not a
        # funnel of zeros. "Nothing was rejected" and "nothing was measured"
        # are different statements, and a row of zeros asserts the first.
        return {"absent": "no per-candidate lifecycle records on this path",
                "candidates": 0}
    got = {
        "candidates": len(ev),
        "present": sum(1 for x in ev if x.get("present")),
        "acceptable": sum(1 for x in ev if x.get("acceptable")),
        "materialised": sum(1 for x in ev if x.get("materialised")),
        "incorporated": sum(1 for x in ev if x.get("incorporated")),
        # [AVOIDED_MEANS_NOT_FETCHED_V1] see policy.funnel
        "avoided": sum(1 for x in ev if not x.get("materialised")),
        "wasted": sum(1 for x in ev
                      if x.get("materialised") and not x.get("incorporated")),
        "superseded": sum(1 for x in ev if x.get("outcome") == "superseded"),
        "unreachable": sum(1 for x in ev if x.get("outcome") == "unreachable"),
        "departing_seen": sum(1 for x in ev if x.get("departing")),
        "bytes_moved": sum(x.get("bytes") or 0 for x in ev),
        "materialise_ms": round(sum(x.get("materialise_ms") or 0.0
                                    for x in ev), 4),
    }
    return got


def qualify_arm(result: Dict[str, Any]) -> Qualified:
    """Did this arm actually do the job? Nothing may be compared against it
    until it did."""
    if not result:
        return Qualified(None, INCOMPLETE, "arm produced no result")
    if result.get("capability_absent"):
        return Qualified(None, ABSENT, str(result["capability_absent"]))
    fin, exp = result.get("finished"), result.get("expected")
    if fin is None or exp is None:
        return Qualified(None, INCOMPLETE, "arm did not report completion")
    if fin < exp:
        return Qualified(None, INCOMPLETE,
                         "finished %d of %d workers" % (fin, exp))
    cov, want = result.get("units_covered"), result.get("units_expected")
    if cov is not None and want is not None and len(cov) < want:
        return Qualified(None, INCOMPLETE,
                         "covered %d of %d units" % (len(cov), want))
    return Qualified(result, OK)


def compare(metric: str, a_name: str, a: Dict, b_name: str, b: Dict,
            isolated: bool = True,
            unisolated_reason: str = "") -> Qualified:
    """A ratio, or a refusal that says which arm disqualified it.

    `isolated` is the caller's assertion that the variable producing this
    metric was controlled. It defaults True because most metrics are, and it
    is a parameter at all because wall clock on a host with fewer cores than
    workers is not.
    """
    qa, qb = qualify_arm(a), qualify_arm(b)
    for name, q in ((a_name, qa), (b_name, qb)):
        if not q:
            return Qualified(None, q.status,
                             "%s: %s -- %s is not comparable"
                             % (name, q.reason, name))
    if not isolated:
        return Qualified(None, UNISOLATED,
                         unisolated_reason or
                         "the variable producing %s was not controlled" % metric)
    va, vb = a.get(metric), b.get(metric)
    if va in (None, 0) or vb is None:
        return Qualified(None, INCOMPLETE,
                         "%s missing or zero in one arm" % metric)
    return Qualified(vb / va, OK)


def summarise(run_dir: str) -> Dict[str, Any]:
    """Everything derivable from one capture, per arm."""
    meta, envs = load(run_dir)
    arms = by(envs, "arm")
    return {"run": meta,
            "arms": {arm: {"funnel": funnel(es), "events": len(es)}
                     for arm, es in sorted(arms.items(), key=lambda kv:
                                           str(kv[0]))}}


def render(summary: Dict[str, Any]) -> str:
    lines = ["run %s (schema %s)" % (summary["run"].get("run_id"),
                                     summary["run"].get("schema"))]
    hdr = ("%-14s %6s %6s %6s %6s %8s %7s %8s"
           % ("arm", "cands", "accept", "mat", "incorp", "avoided",
              "wasted", "bytes"))
    lines.append(hdr)
    for arm, d in summary["arms"].items():
        f = d["funnel"]
        lines.append("%-14s %6d %6d %6d %6d %8d %7d %8d"
                     % (arm, f["candidates"], f["acceptable"],
                        f["materialised"], f["incorporated"], f["avoided"],
                        f["wasted"], f["bytes_moved"]))
    return "\n".join(lines)
