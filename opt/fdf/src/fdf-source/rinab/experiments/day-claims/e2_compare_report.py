#!/usr/bin/env python3
"""e2_compare_report.py COMPARE.jsonl -- [DAY_E2_COMPARE_V1] the tables: constructions against each other and against
the exact optimum; near-optimal = cost <= 1.10 x optimum."""
import json, sys
R = [json.loads(l) for l in open(sys.argv[1])]
CONS = ["day/dest", "day/member", "spt", "frog/h=source", "frog/h=best", "frog/h=worst", "opt"]
LABEL = {"day/dest": "Day, R1 (one next hop per node)", "day/member": "Day, R2 (per-member choice of equal paths)",
         "spt": "requester's shortest-path tree (from shared state)", "frog/h=source": "FrogNet, RAM host at the source",
         "frog/h=best": "FrogNet, RAM host at the best node", "frog/h=worst": "FrogNet, RAM host at the worst node", "opt": "exact optimum"}
def q(v, p): v = sorted(x for x in v if x is not None); return v[min(len(v) - 1, int(p * len(v)))] if v else float("nan")
def table(rows, title):
    print(f"\n### {title} ({len(rows)} graphs)\n")
    print("| construction | near-optimal (<=1.10; all reached) | cost/opt p50 | p90 | max | a tree | all members reached | duplicate copies | worst stretch p50 | max | build hop-msgs p50 |")
    print("|---|---|---|---|---|---|---|---|---|---|---|")
    for c in CONS:
        rat = [r[c]["ratio"] for r in rows]; reached = sum(r[c]["reach"] == r["members"] for r in rows)
        print(f"| {LABEL[c]} | {sum(1 for x in rat if x is not None and x <= 1.10 + 1e-9)} ({100.0 * sum(1 for x in rat if x is not None and x <= 1.10 + 1e-9) / len(rows):.0f}%) | "
              f"{q(rat, .5):.3f} | {q(rat, .9):.3f} | {max((x for x in rat if x is not None), default=float('nan')):.3f} | {sum(bool(r[c]['tree']) for r in rows)} | "
              f"{reached} | {sum(r[c]['dup'] for r in rows)} | {q([r[c]['stretch'] for r in rows], .5):.3f} | {max(x for x in (r[c]['stretch'] for r in rows) if x is not None):.3f} | "
              f"{q([r[c]['build'] for r in rows], .5):.0f} |")
std = [r for r in R if r["set"] == "standard"]
table([r for r in std if not r["asym"]], "Standard graphs, symmetric links (chain, star, ring, branch; 4-11 nodes, 1-6 members)")
table([r for r in std if r["asym"]], "Standard graphs, asymmetric links")
table([r for r in R if r["set"] == "source"], "Source position: 30 branching graphs (8 nodes), every node in turn the source")
table([r for r in R if r["set"] == "oneway"], "One-way links: branching graphs with ~30% of links one direction only (strongly connected)")
print("\n### Counterexample families: cost / optimum\n")
print("| family | k | " + " | ".join(LABEL[c] for c in CONS[:-1]) + " | duplicates (Day R2) |"); print("|---|---|" + "---|" * (len(CONS) - 1) + "---|")
for fam in ("trunk", "asym", "ties"):
    for r in sorted((r for r in R if r["set"] == fam), key=lambda r: r["k"]):
        print(f"| {fam} | {r['k']} | " + " | ".join((f"{r[c]['ratio']:.3f}" if r[c]['ratio'] is not None else "misses") for c in CONS[:-1]) + f" | {r['day/member']['dup']} |")
# the three propositions
d1 = [r for r in R if r["set"] in ("standard", "source", "oneway")]
nt1 = sum(not r["day/dest"]["tree"] for r in d1); nt2 = sum(not r["day/member"]["tree"] for r in R)
un = [r for r in R if r["set"] == "oneway"]
miss_day = sum(r["day/dest"]["reach"] < r["members"] for r in un); miss_spt = sum(r["spt"]["reach"] < r["members"] for r in un)
print(f"\nProposition 1 (converging paths form a tree): R1 non-trees {nt1} of {len(d1)}; R2 non-trees {nt2} of {len(R)}.")
print(f"Proposition 2 (the tree reaches every member): one-way graphs where Day R1 misses a member: {miss_day} of {len(un)}; the requester's tree: {miss_spt} of {len(un)}.")
for s, lab in ((False, "symmetric"), (True, "asymmetric")):
    rows = [r for r in std if r["asym"] == s]; near = sum(r["day/dest"]["ratio"] is not None and r["day/dest"]["ratio"] <= 1.10 + 1e-9 for r in rows)
    print(f"Proposition 3 (near-optimal, <=1.10) for Day R1 on {lab} standard graphs: {near} of {len(rows)}; worst {max(r['day/dest']['ratio'] for r in rows if r['day/dest']['ratio'] is not None):.3f}.")
