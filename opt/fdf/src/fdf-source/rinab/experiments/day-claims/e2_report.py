#!/usr/bin/env python3
"""e2_report.py INSTANCES.jsonl -- [DAY_E2_REPORT_V1] the E2 reference tables (markdown) from e2_ref.py instances:
tree cost against the Steiner optimum, per topology kind, symmetric and asymmetric, with tie sensitivity."""
import json, sys
R = [json.loads(l) for l in open(sys.argv[1])]
def q(v, p): v = sorted(v); return v[min(len(v) - 1, int(p * len(v)))]
bad = [r for r in R if not r["ref_agree"] or not all(r[t]["valid"] for t in ("spt", "rpt", "spt_hi", "rpt_hi"))]
print(f"{len(R)} instances (4-11 nodes, 1-6 members); the two optimum methods agree on {sum(r['ref_agree'] for r in R)}; "
      f"{len(bad)} invalid trees or reference failures\n")
print("| links | topology | n | tree | optimal | cost/opt p50 | p90 | max | worst member stretch | ties other way: p90 | max |")
print("|---|---|---|---|---|---|---|---|---|---|---|")
for asym in (False, True):
    for kind in ("chain", "star", "ring", "branch"):
        S = [r for r in R if r["kind"] == kind and r["asym"] == asym]
        for t, th in (("spt", "spt_hi"), ("rpt", "rpt_hi")):
            rat = [r[t]["ratio"] for r in S]; hi = [r[th]["ratio"] for r in S]; st = [r[t]["stretch_max"] for r in S]
            print(f"| {'asymmetric' if asym else 'symmetric'} | {kind} | {len(S)} | {'shortest-path (requester)' if t == 'spt' else 'reverse-path (responses)'} | "
                  f"{sum(x == 1 for x in rat)} | {q(rat, .5):.3f} | {q(rat, .9):.3f} | {max(rat):.3f} | {max(st):.3f} | {q(hi, .9):.3f} | {max(hi):.3f} |")
same = sum(r["spt"]["cost"] == r["rpt"]["cost"] for r in R if not r["asym"])
print(f"\nSymmetric graphs: reverse-path cost equals shortest-path cost in {same} of {sum(not r['asym'] for r in R)}.")
A = [r for r in R if r["asym"]]
worse = sum(r["rpt"]["cost"] > r["spt"]["cost"] for r in A); better = sum(r["rpt"]["cost"] < r["spt"]["cost"] for r in A)
print(f"Asymmetric graphs: reverse-path costs more than shortest-path in {worse} of {len(A)}, less in {better}.")
tie = sum(r["rpt"]["cost"] != r["rpt_hi"]["cost"] for r in R)
print(f"Tie rule changes the reverse-path cost in {tie} of {len(R)} instances.")
