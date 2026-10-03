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
"""S1 performance, Python side: the operations of tests/bench_semwire.cpp on John's core/semcache_wire.py.
usage: bench_semwire_python.py FROGNET_SEMANTIC_ROOT"""
import sys, time
sys.dont_write_bytecode = True
sys.path.insert(0, sys.argv[1])
import core.semcache_wire as W  # noqa: E402


def bench(name, n, f):
    r = []
    for _ in range(9):
        t0 = time.perf_counter_ns()
        for _ in range(n): f()
        r.append((time.perf_counter_ns() - t0) / n)
    r.sort()
    print('%-28s median %9.1f ns/op  min %9.1f  max %9.1f  (9 x %d)' % (name, r[4], r[0], r[8], n))


h, sid, p64, p4k, hdr, body = b'\x5a' * 16, b'\x33' * 16, b'p' * 64, b'q' * 4096, b'h' * 40, b'b' * 1024
f64, f4k, frep = W.wrap_req_full(h, p64), W.wrap_req_full(h, p4k), W.wrap_req_repeat(h)
fsame, fraw, fpong = W.wrap_resp_same(sid), W.wrap_resp_raw(sid, 200, hdr, body), W.wrap_rtt_pong(7, 1, 2, 3)
N = 100000
bench('wrap_req_full 64B', N, lambda: W.wrap_req_full(h, p64))
bench('wrap_req_full 4KiB', N, lambda: W.wrap_req_full(h, p4k))
bench('wrap_req_repeat', N, lambda: W.wrap_req_repeat(h))
bench('wrap_resp_same', N, lambda: W.wrap_resp_same(sid))
bench('wrap_resp_raw 40B+1KiB', N, lambda: W.wrap_resp_raw(sid, 200, hdr, body))
bench('wrap_rtt_pong', N, lambda: W.wrap_rtt_pong(7, 1, 2, 3))
bench('try_parse REQ_FULL 64B', N, lambda: W.try_parse(f64))
bench('try_parse REQ_FULL 4KiB', N, lambda: W.try_parse(f4k))
bench('try_parse REQ_REPEAT', N, lambda: W.try_parse(frep))
bench('try_parse RESP_SAME', N, lambda: W.try_parse(fsame))
bench('try_parse RESP_RAW 40B+1KiB', N, lambda: W.try_parse(fraw))
bench('try_parse RTT_PONG', N, lambda: W.try_parse(fpong))
