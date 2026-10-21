#!/usr/bin/env python3
# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
#
# load_table.py -- load_compare.py's JSON lines as side-by-side tables: received msgs/s (median of repeats), p50 latency,
# broker CPU per 1k messages, broker RSS. The first broker named is the baseline for the ratio columns.
#
#   load_table.py results.jsonl [--brokers stock,t1,t2,t4] [--md]
import argparse, collections, json, statistics

ap = argparse.ArgumentParser()
ap.add_argument('file'); ap.add_argument('--brokers', default=None); ap.add_argument('--md', action='store_true')
a = ap.parse_args()
rows = [json.loads(l) for l in open(a.file) if l.startswith('{')]
by = collections.defaultdict(list)
for r in rows: by[(r['scenario'], r['broker'])].append(r)
brokers = a.brokers.split(',') if a.brokers else list(dict.fromkeys(r['broker'] for r in rows))
scenarios = list(dict.fromkeys(r['scenario'] for r in rows))
med = lambda xs: statistics.median(xs) if xs else None

def table(title, field, fmt, ratio):
    head = ['scenario'] + brokers + ([f'{b}/{brokers[0]}' for b in brokers[1:]] if ratio else [])
    out = [head]
    for s in scenarios:
        vals = [med([r[field] for r in by.get((s, b), []) if field in r]) for b in brokers]
        line = [s] + [fmt(v) if v is not None else '-' for v in vals]
        if ratio:
            line += [f'{v / vals[0]:.2f}' if v is not None and vals[0] else '-' for v in vals[1:]]
        out.append(line)
    if a.md:
        print(f'\n**{title}**\n')
        print('| ' + ' | '.join(out[0]) + ' |'); print('|' + '---|' * len(out[0]))
        for l in out[1:]: print('| ' + ' | '.join(l) + ' |')
    else:
        print(f'\n{title}')
        w = [max(len(str(l[i])) for l in out) for i in range(len(out[0]))]
        for l in out: print('  '.join(str(x).rjust(w[i]) if i else str(x).ljust(w[i]) for i, x in enumerate(l)))

n = max((len(v) for v in by.values()), default=0)
print(f'{a.file}: {len(rows)} runs, up to {n} per cell, medians')
table('received msgs/s', 'received_per_s', lambda v: f'{v/1000:.1f}k', True)
# [DRAIN_COUNT_V1] messages sent in the window that never reached a subscriber, even after the drain (QoS 0 rows: drops
# the protocol allows; QoS 1/2 rows: acknowledged to the publisher and dropped by a full subscriber queue)
if any('lost' in r for r in rows):
    for r in rows:
        if 'lost' in r: r['lost_pct'] = 100.0 * r['lost'] / max(1, r.get('expected_received', 0))
    table('lost: sent in the window, never delivered (% of expected)', 'lost_pct', lambda v: f'{v:.1f}%', False)
    table('late: delivered after the window (backlog held at the end)', 'late', lambda v: f'{v:.0f}', False)
# [HANG_DUMP_V1] runs that passed their deadline or whose broker would not stop: counted here, dumped in the run's output
for r in rows: r['hung_n'] = int(bool(r.get('hung') or r.get('broker_stuck') or r.get('broker_not_listening') or r.get('left_behind')))
if any(r['hung_n'] for r in rows):
    for s in scenarios:
        for b in brokers:
            n = sum(r['hung_n'] for r in by.get((s, b), []))
            if n: print(f'\nHUNG: {b} {s}: {n} of {len(by[(s, b)])} runs (see [HANG_DUMP_V1] lines in the run output and the row)')
table('latency p50 (us)', 'lat_us_p50', lambda v: f'{v:.0f}', False)
table('broker CPU ms per 1k msgs', 'broker_cpu_per_1k_msgs_ms', lambda v: f'{v:.2f}', False)
table('broker RSS (MB)', 'broker_rss_kb', lambda v: f'{v/1024:.0f}', False)
