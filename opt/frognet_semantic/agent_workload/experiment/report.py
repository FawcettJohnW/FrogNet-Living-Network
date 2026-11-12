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
agent_workload/experiment/report.py -- present what qualified, say what did
not.

[THE_REPORT_DECIDES_NOTHING_V1]

This layer is meant to be boring, and that is a design constraint rather than
a description. Every other layer has exactly one job -- policy says what
happened, capture preserves what was said, the analyzer decides what may be
concluded -- and the failure mode for a report is to start deciding too.

So there is no completeness check here, no isolation judgement, no notion of
which capability is absent. Those are the analyzer's, and if a formatter ever
needs to work one out, the logic has leaked upward and belongs back in
analyze.py.

What this does:

  * renders a Qualified value as its value, and a refusal as its refusal --
    never as a blank, a zero, a dash, or an omitted row, because all four
    read as "no difference" rather than "not measured";
  * carries provenance on every table, so a figure can be traced back to the
    run id, the capture directory and the schema version it came from;
  * emits the same content as text or as JSON, with the refusals intact in
    both, so the machine-readable form cannot quietly become the optimistic
    one.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional, Tuple

from .analyze import Qualified


def cell(q: Any) -> str:
    """One value, or one refusal, in a fixed width-agnostic form.

    A refusal is never rendered as an empty cell. An empty cell in a
    comparison table is read as "the same" or "nothing to report"; both are
    claims, and neither was made.
    """
    if isinstance(q, Qualified):
        if q.status == "ok" and q.value is not None:
            v = q.value
            return ("%.4g" % v) if isinstance(v, float) else str(v)
        return "[%s]" % q.status
    if q is None:
        return "--"
    return ("%.4g" % q) if isinstance(q, float) else str(q)


def provenance(summary: Dict[str, Any], run_dir: str = "") -> Dict[str, Any]:
    run = summary.get("run", {})
    return {"run_id": run.get("run_id"), "schema": run.get("schema"),
            "created": run.get("created"), "capture": run_dir,
            "meta": run.get("meta", {})}


def funnel_table(summary: Dict[str, Any]) -> str:
    lines = ["%-16s %6s %6s %6s %7s %8s %7s %10s %11s"
             % ("arm", "cands", "accept", "mat", "incorp", "avoided",
                "wasted", "bytes", "mat ms")]
    for arm, d in summary.get("arms", {}).items():
        f = d["funnel"]
        if f.get("absent"):
            # A row of zeros would read as "nothing was rejected". It was not
            # measured, and that is a different sentence.
            lines.append("%-16s %s" % (arm, "[capability_absent] %s"
                                       % f["absent"]))
            continue
        lines.append("%-16s %6s %6s %6s %7s %8s %7s %10s %11s"
                     % (arm, cell(f["candidates"]), cell(f["acceptable"]),
                        cell(f["materialised"]), cell(f["incorporated"]),
                        cell(f["avoided"]), cell(f["wasted"]),
                        cell(f["bytes_moved"]), cell(f["materialise_ms"])))
    return "\n".join(lines)


def comparison_table(rows: List[Tuple[str, Qualified]]) -> str:
    """Every comparison the analyzer produced, refusals included.

    A refused row is printed with its reason rather than dropped. Dropping it
    would leave a table in which only the favourable comparisons appear,
    which is the same paper with different evidence.
    """
    lines = ["%-28s %-14s %s" % ("metric", "value", "note")]
    for name, q in rows:
        note = q.reason if isinstance(q, Qualified) and q.status != "ok" else ""
        lines.append("%-28s %-14s %s" % (name, cell(q), note))
    return "\n".join(lines)


def render(summary: Dict[str, Any], comparisons: Optional[List] = None,
           run_dir: str = "") -> str:
    p = provenance(summary, run_dir)
    out = ["run %s   schema %s   capture %s"
           % (p["run_id"], p["schema"], p["capture"] or "(not given)")]
    if p["meta"]:
        out.append("meta: %s" % json.dumps(p["meta"], sort_keys=True))
    out.append("")
    out.append(funnel_table(summary))
    if comparisons:
        out.append("")
        out.append(comparison_table(comparisons))
    return "\n".join(out)


def as_json(summary: Dict[str, Any], comparisons: Optional[List] = None,
            run_dir: str = "") -> str:
    """The same content, refusals intact.

    The machine-readable form carries the refusal objects rather than
    omitting the rows, so a consumer that reads the JSON gets exactly what a
    reader of the text gets.
    """
    doc = {"provenance": provenance(summary, run_dir),
           "arms": {a: d["funnel"] for a, d in summary.get("arms", {}).items()},
           "comparisons": [
               {"metric": n,
                **(q.as_dict() if isinstance(q, Qualified)
                   else {"value": q, "status": "ok", "reason": ""})}
               for n, q in (comparisons or [])]}
    return json.dumps(doc, indent=2, sort_keys=True, default=str)


def write(path: str, summary: Dict[str, Any],
          comparisons: Optional[List] = None, run_dir: str = "") -> str:
    with open(path, "w") as f:
        f.write(render(summary, comparisons, run_dir))
        f.write("\n")
    with open(os.path.splitext(path)[0] + ".json", "w") as f:
        f.write(as_json(summary, comparisons, run_dir))
        f.write("\n")
    return path
