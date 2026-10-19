#!/usr/bin/env python3
# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
"""run_same_after_raw.py -- [SAME_ONLY_WHEN_BOTH_MOVE_V1] the session's answer references stay equal on both ends.

An answer the reply template cannot hold goes RAW and moves neither end's response reference. If the same answer is
given again and the far end calls it SAME, the near end moves its reference to that answer while the far end does not,
and the next difference is applied to a reference it was not encoded against (seen in Redis's own incr.tcl: the front
stopped on "a splice longer than its reference"). One connection, a front on its own memory:
  SET (OK), INCR (an error: RAW), SET, the same INCR (the same error), SET (OK), DECRBY (another error), PING
Every reply must be Redis's, and the front must still serve after it.
Usage: run_same_after_raw.py <build-dir> <port>"""
import os, socket, subprocess, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coordinated import Redis
B, P = sys.argv[1], int(sys.argv[2])
proc = subprocess.Popen([B + "/ribbit-redis-front", "--port", str(P), "--save", ""], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
for _ in range(200):
    try: socket.create_connection(("127.0.0.1", P), timeout=1).close(); break
    except OSError: time.sleep(0.02)
r = Redis("127.0.0.1", P, timeout=5)
steps = [(("SET", "n", "    11"), "OK"), (("INCR", "n"), "ERR value is not an integer or out of range"),
         (("SET", "n", "11    "), "OK"), (("INCR", "n"), "ERR value is not an integer or out of range"),
         (("SET", "x", "0"), "OK"), (("DECRBY", "x", "-9223372036854775808"), "ERR decrement would overflow"), (("PING",), "PONG")]
bad = []
for cmd, want in steps:
    try: got = r.cmd(*cmd)
    except Exception as e: got = e
    got = str(got)
    if got != want: bad.append("%s -> %r (want %r)" % (" ".join(cmd), got, want))
alive = proc.poll() is None
proc.terminate()
log = proc.communicate(timeout=5)[0].decode(errors="replace")
stop = [l for l in log.split("\n") if "stops" in l]
ok = not bad and alive
print(("PASS" if ok else "FAIL") + " the same RAW answer twice, then a difference: every reply Redis's, the front serves on")
for b in bad: print("  " + b)
for s in stop: print("  front: " + s.split("# ", 1)[-1])
print("RESULT %s" % ("PASS" if ok else "FAIL"))
sys.exit(0 if ok else 1)
