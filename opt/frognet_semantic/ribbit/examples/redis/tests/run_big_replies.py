#!/usr/bin/env python3
# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
"""run_big_replies.py -- [SEEN_IS_WHAT_WAS_READ_V1] every reply reaches its front, however it is cut.

A reply larger than one frame goes as piece cells and then its cell, written one after another. The front reads them
with one standing read. The memory's follow task once took "seen" as the family's head BEFORE reading: a write whose
slot was reserved but not yet filled stopped the read, yet counted as seen -- its own wake then found nothing new, and
the row waited for the next write (Redis's string.tcl "Very big payload random access" stalled). One connection,
a front on its own memory: 100 SETs of repetitive payloads up to 500 KB, then 1000 GETs, each exact and in time.
Usage: run_big_replies.py <build-dir> <port>"""
import os, random, socket, subprocess, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coordinated import Redis
B, P = sys.argv[1], int(sys.argv[2])
proc = subprocess.Popen([B + "/ribbit-redis-front", "--port", str(P), "--save", ""], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
for _ in range(200):
    try: socket.create_connection(("127.0.0.1", P), timeout=1).close(); break
    except OSError: time.sleep(0.02)
r = Redis("127.0.0.1", P, timeout=5)
random.seed(7); vals = {}; bad = None
for j in range(100):
    size = 1 + random.randrange(100000); vals[j] = ("pl-%d" % j) * size; r.cmd("SET", "bp_%d" % j, vals[j])
t0 = time.time()
for i in range(1000):
    j = random.randrange(100)
    try: got = r.cmd("GET", "bp_%d" % j)
    except Exception as e: bad = "GET %d (the %dth, %d bytes): %s" % (j, i, len(vals[j]), e); break
    if got != vals[j]: bad = "GET %d (the %dth) differs" % (j, i); break
proc.terminate(); proc.wait()
ok = bad is None
print(("PASS" if ok else "FAIL") + " 100 SETs and 1000 GETs of payloads up to 500 KB, each exact" + ("" if ok else ": " + bad) + " (%.1f s)" % (time.time() - t0))
print("RESULT %s" % ("PASS" if ok else "FAIL"))
sys.exit(0 if ok else 1)
