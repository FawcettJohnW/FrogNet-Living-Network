#!/usr/bin/env python3
# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
#
# compare.py -- two suite runs side by side (stock and ribbit), from run_suite.py's JSON lines.
#
#   compare.py stock.jsonl ribbit.jsonl [--md out.md] [--csv out.csv]
#
# Per test, the first passing attempt of each broker is compared (a test that never passed is shown as failed).
# Measured, both sides by the same harness code: wall time, time spent waiting for expected packets, bytes each way,
# CPU of everything the test started (the broker and, for Ribbit, its region host), peak RSS, context switches.
import argparse, collections, json, sys

ap = argparse.ArgumentParser(); ap.add_argument('a'); ap.add_argument('b')
ap.add_argument('--md'); ap.add_argument('--csv'); args = ap.parse_args()

def load(path):
    rows = collections.defaultdict(list)
    for line in open(path):
        r = json.loads(line); rows[r['test']].append(r)
    best, label = {}, None
    for t, rs in rows.items():
        label = rs[0]['label']
        ok = [r for r in rs if r['rc'] == 0]
        best[t] = ok[0] if ok else dict(rs[-1], failed=True)
        best[t]['attempts'] = len(rs)
    return label, best

la, A = load(args.a); lb, B = load(args.b)
tests = sorted(set(A) | set(B))
both = [t for t in tests if t in A and t in B and not A[t].get('failed') and not B[t].get('failed')]
F = ['wall_s', 'expect_ms_sum', 'tx_bytes', 'rx_bytes', 'child_user_s', 'child_sys_s', 'child_maxrss_kb', 'child_vcsw', 'child_ivcsw']

def tot(S, f): return sum(S[t][f] for t in both)
def group(t): return t[:2] if t[:2].isdigit() else 'seq'

out = []
out.append(f"# Mosquitto's broker suite: {la} vs {lb}\n")
pa = sum(1 for t in tests if t in A and not A[t].get('failed')); pb = sum(1 for t in tests if t in B and not B[t].get('failed'))
fa = sum(1 for t in tests if t in A and not A[t].get('failed') and A[t]['attempts'] == 1)
fb = sum(1 for t in tests if t in B and not B[t].get('failed') and B[t]['attempts'] == 1)
out.append(f"| | {la} | {lb} |\n|---|---|---|")
out.append(f"| tests passed | {pa}/{len(tests)} | {pb}/{len(tests)} |")
out.append(f"| passed first attempt | {fa} | {fb} |")
for f, name, scale in [('wall_s', 'wall, s (sum over tests both passed)', 1), ('expect_ms_sum', 'waiting for expected packets, s', 1e-3),
                       ('child_user_s', 'broker CPU user, s', 1), ('child_sys_s', 'broker CPU system, s', 1),
                       ('tx_bytes', 'bytes test->broker', 1), ('rx_bytes', 'bytes broker->test', 1),
                       ('child_vcsw', 'voluntary context switches', 1), ('child_ivcsw', 'involuntary context switches', 1)]:
    va, vb = tot(A, f) * scale, tot(B, f) * scale
    out.append(f"| {name} | {va:,.3f} | {vb:,.3f} (x{(vb / va if va else 0):.2f}) |" if isinstance(va, float) and va < 1e6 else f"| {name} | {va:,.0f} | {vb:,.0f} (x{(vb / va if va else 0):.2f}) |")
rss_a = max(A[t]['child_maxrss_kb'] for t in both); rss_b = max(B[t]['child_maxrss_kb'] for t in both)
out.append(f"| largest broker process RSS, kB | {rss_a:,} | {rss_b:,} |")
out.append(f"\n{len(both)} tests passed on both and are summed above.\n")

out.append("## By group\n")
out.append(f"| group | tests | wall {la} s | wall {lb} s | expect-wait {la} ms | expect-wait {lb} ms | CPU {la} s | CPU {lb} s |")
out.append("|---|---|---|---|---|---|---|---|")
g = collections.defaultdict(list)
for t in both: g[group(t)].append(t)
for k in sorted(g):
    ts = g[k]
    s = lambda S, f: sum(S[t][f] for t in ts)
    out.append(f"| {k} | {len(ts)} | {s(A,'wall_s'):.2f} | {s(B,'wall_s'):.2f} | {s(A,'expect_ms_sum'):.1f} | {s(B,'expect_ms_sum'):.1f} | "
               f"{s(A,'child_user_s')+s(A,'child_sys_s'):.2f} | {s(B,'child_user_s')+s(B,'child_sys_s'):.2f} |")

out.append("\n## Not passing\n")
for t in tests:
    fa_, fb_ = (t not in A or A[t].get('failed')), (t not in B or B[t].get('failed'))
    if fa_ or fb_:
        out.append(f"- {t}: {la} {'FAILED' if fa_ else 'passed'}, {lb} {'FAILED' if fb_ else 'passed'}")

text = '\n'.join(out) + '\n'
print(text)
if args.md: open(args.md, 'w').write(text)
if args.csv:
    with open(args.csv, 'w') as f:
        f.write('test,' + ','.join(f'{la}_{x}' for x in F + ['rc', 'attempts']) + ',' + ','.join(f'{lb}_{x}' for x in F + ['rc', 'attempts']) + '\n')
        for t in tests:
            ra = A.get(t, {}); rb = B.get(t, {})
            f.write(t + ',' + ','.join(str(ra.get(x, '')) for x in F + ['rc', 'attempts']) + ',' + ','.join(str(rb.get(x, '')) for x in F + ['rc', 'attempts']) + '\n')
