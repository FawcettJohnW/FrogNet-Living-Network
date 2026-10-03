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
# bench_semtpl.py ROOT BODYFILE N: the same learn / extract / rebuild through John's Python (core/json_handler.py).
import contextlib, io, os, statistics, sys, time
sys.dont_write_bytecode = True; sys.path.insert(0, sys.argv[1])
with contextlib.redirect_stdout(io.StringIO()):
    import core.json_handler as JH
body = open(sys.argv[2]).read(); n = int(sys.argv[3]); h = JH.JsonFormatHandler(); tl, te, tr = [], [], []
for _ in range(n):
    a = time.perf_counter_ns(); frag = h.learn_reply_template(body); b = time.perf_counter_ns()
    fields = h.extract_reply_dynamic(body, frag); c = time.perf_counter_ns()
    out = h.rebuild_reply(frag, [v for _, v in fields]); d = time.perf_counter_ns()
    tl.append((b - a) / 1000); te.append((c - b) / 1000); tr.append((d - c) / 1000)
q = lambda v, p: sorted(v)[int(p * (len(v) - 1))]
print('python %d B, %d fields: learn median %.1f us p90 %.1f | extract %.1f / %.1f | rebuild %.1f / %.1f (N=%d)' % (
    len(body), len(fields), q(tl, .5), q(tl, .9), q(te, .5), q(te, .9), q(tr, .5), q(tr, .9), n))
