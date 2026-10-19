#!/usr/bin/env python3
# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
#
# before_after.py -- two load_compare runs of the same brokers, side by side: stock (from the second run), each named
# broker before and after, the ratio, and p50 latency before and after. Medians of the repeats.
#
#   before_after.py before.jsonl after.jsonl remote remote-t2
import json, statistics, sys
before, after, names = sys.argv[1], sys.argv[2], sys.argv[3:]
def rows(f): return [json.loads(l) for l in open(f) if l.startswith('{')]
A, B = rows(before), rows(after)
def med(rs, b, s, k='received_per_s'):
    xs = [r[k] for r in rs if r['broker'] == b and r['scenario'] == s and k in r]
    return statistics.median(xs) if xs else None
scen = list(dict.fromkeys(r['scenario'] for r in B))
k = lambda v: '-' if v is None else f'{v/1000:.1f}k'
head = ['scenario', 'stock'] + [x for n in names for x in (f'{n} before', f'{n} after', 'x', 'p50 µs before', 'p50 µs after')]
print('| ' + ' | '.join(head) + ' |'); print('|' + '---|' * len(head))
for s in scen:
    line = [s, k(med(B, 'stock', s))]
    for n in names:
        a, b = med(A, n, s), med(B, n, s)
        line += [k(a), k(b), f'{b/a:.1f}' if a and b else '-', str(med(A, n, s, 'lat_us_p50')), str(med(B, n, s, 'lat_us_p50'))]
    print('| ' + ' | '.join(line) + ' |')
