#!/usr/bin/env python3
# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
"""summarize.py scale.log [more.log ...]

Turns the output of scale.sh into tables: requests per second for every row,
and every row divided by the stock-Redis row from the same run.
Markdown on stdout, so it can be pasted straight into an issue or a README.
"""
import re, sys

LINE = re.compile(r'^([A-Z_]+)(?: \([^)]*\))?:\s*([\d.]+) requests per second(?:, p50=([\d.]+) msec)?')

def parse(path):
    header, rows, cur, keyset = [], [], None, None
    for raw in open(path, encoding='utf-8', errors='replace'):
        s = raw.strip()
        if s.startswith('# ') and not cur: header.append(s[2:]); continue
        if s.startswith('=== '): cur = {'label': s[4:], 'sets': {}}; rows.append(cur); continue
        if s.startswith('--- '): keyset = 'hot' if 'hot' in s else 'spread'; continue
        m = LINE.match(s)
        if m and cur is not None and keyset:
            cur['sets'].setdefault(keyset, {})[m.group(1)] = float(m.group(2))
    return header, rows

def k(v): return '%.0fk' % (v / 1000) if v < 1e6 else '%.2fM' % (v / 1e6)

def table(rows, keyset, ratio):
    tests = []
    for r in rows:
        for t in r['sets'].get(keyset, {}):
            if t not in tests: tests.append(t)
    if not tests: return ''
    base = next((r for r in rows if r['label'].startswith('stock')), None)
    out = ['| %s | %s |' % ('' , ' | '.join(tests)), '|---|' + '---|' * len(tests)]
    for r in rows:
        vals = r['sets'].get(keyset, {})
        cells = []
        for t in tests:
            v = vals.get(t)
            if v is None: cells.append('–')
            elif ratio:
                b = base['sets'].get(keyset, {}).get(t) if base else None
                cells.append('%.2f×' % (v / b) if b else '–')
            else: cells.append(k(v))
        out.append('| %s | %s |' % (r['label'], ' | '.join(cells)))
    return '\n'.join(out)

for path in sys.argv[1:] or ['/dev/stdin']:
    header, rows = parse(path)
    print('## %s\n' % path)
    for h in header: print('- ' + h)
    for keyset, title in (('spread', 'Keys spread over a million-key range'), ('hot', '100 hot keys')):
        t = table(rows, keyset, False)
        if not t: continue
        print('\n### %s — requests per second\n\n%s\n' % (title, t))
        print('### %s — relative to stock Redis on one core\n\n%s\n' % (title, table(rows, keyset, True)))
