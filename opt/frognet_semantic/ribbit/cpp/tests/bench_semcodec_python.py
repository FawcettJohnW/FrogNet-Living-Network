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
"""S2 performance, Python side: the operations of tests/bench_semcodec.cpp on John's core/codec.py.
usage: bench_semcodec_python.py FROGNET_SEMANTIC_ROOT"""
import sys, time, types
sys.dont_write_bytecode = True
sys.path.insert(0, sys.argv[1])
import core.codec as C  # noqa: E402

c = C.SemanticCodec()


def bench(name, n, f):
    r = []
    for _ in range(9):
        t0 = time.perf_counter_ns()
        for _ in range(n): f()
        r.append((time.perf_counter_ns() - t0) / n)
    r.sort()
    print('%-34s median %10.1f ns/op  min %10.1f  max %10.1f  (9 x %d)' % (name, r[4], r[0], r[8], n))


f5 = [('id', 123456), ('name', 'alice@example.com'), ('active', True), ('score', 98.25), ('tags', ['a', 'b', 3])]
f5b = [('id', 123457)] + f5[1:]
ref = dict(f5)
fj = [('doc', {'key%d' % i: 'value number %d' % i for i in range(40)})]
blob = ''.join('row %d status=ok;' % (i % 16) for i in range(256))[:4000]
fc = [('body', blob)]
T = lambda names: types.SimpleNamespace(fragment={'field_order': names})
t5, tj, tc = T([k for k, _ in f5]), T(['doc']), T(['body'])
p5, pd = c.encode_reply(7, f5, {}, None), c.encode_reply_diff(7, f5b, {}, ref, None)[0]
pj, pc = c.encode_reply(7, fj, {}, None), c.encode_reply(7, fc, {}, None)
N = 20000
bench('encode_reply 5 fields', N, lambda: c.encode_reply(7, f5, {}, None))
bench('encode_reply_diff 5, identical', N, lambda: c.encode_reply_diff(7, f5, {}, ref, None))
bench('encode_reply_diff 5, one changed', N, lambda: c.encode_reply_diff(7, f5b, {}, ref, None))
bench('encode_reply JSON dict 40 keys', N, lambda: c.encode_reply(7, fj, {}, None))
bench('encode_reply 4000 B str, LZ4', N, lambda: c.encode_reply(7, fc, {}, None))
bench('decode_reply 5 fields', N, lambda: c.decode_reply(p5, t5, None))
bench('decode_reply diff + reference', N, lambda: c.decode_reply(pd, t5, None, ref))
bench('decode_reply JSON dict 40 keys', N, lambda: c.decode_reply(pj, tj, None))
bench('decode_reply 4000 B str, LZ4', N, lambda: c.decode_reply(pc, tc, None))
print('sizes: 5 fields %d B, diff %d B, JSON dict %d B, 4000 B str compressed to %d B' % (len(p5), len(pd), len(pj), len(pc)))
