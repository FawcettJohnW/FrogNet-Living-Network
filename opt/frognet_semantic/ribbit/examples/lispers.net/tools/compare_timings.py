#!/usr/bin/env python3
"""Side-by-side per-operation timing from two run_conformance.py --timings files ([PER_OPERATION_TIMING_V1]).
usage: tools/compare_timings.py BASELINE.json CANDIDATE.json
Ratio = candidate median / baseline median (below 1.0: the candidate is faster). Only operations both ran are compared;
operations only one side ran are listed separately."""
import json, sys
a, b = (json.load(open(p)) for p in sys.argv[1:3])
A = {r["operation"]: r for r in a["operations"]}; B = {r["operation"]: r for r in b["operations"]}
print("baseline : %s  (%s, suite_ok=%s, %.1fs)" % (a["label"], a["meta"].get("adapter"), a["meta"].get("suite_ok"), a["meta"].get("wall_s", 0)))
print("candidate: %s  (%s, suite_ok=%s, %.1fs)" % (b["label"], b["meta"].get("adapter"), b["meta"].get("suite_ok"), b["meta"].get("wall_s", 0)))
print("\n%-24s %7s %11s %7s %11s %8s   %11s %11s" % ("operation", "n base", "median base", "n cand", "median cand", "ratio", "p99 base", "p99 cand"))
g = lambda v: "-" if v is None else "%11.3f" % v
for op in sorted(set(A) & set(B)):
    x, y = A[op], B[op]
    ratio = (y["median_ms"] / x["median_ms"]) if x["median_ms"] and y["median_ms"] is not None else None
    print("%-24s %7d %s %7d %s %8s   %s %s" % (op, x["calls"], g(x["median_ms"]), y["calls"], g(y["median_ms"]),
          "-" if ratio is None else "%.3f" % ratio, g(x["p99_ms"]), g(y["p99_ms"])))
only_a, only_b = sorted(set(A) - set(B)), sorted(set(B) - set(A))
if only_a: print("\nbaseline only: " + ", ".join(only_a))
if only_b: print("candidate only: " + ", ".join(only_b))
