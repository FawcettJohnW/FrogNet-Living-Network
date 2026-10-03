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
"""[BOUNDARY_NEVER_WAITS_V1] A Map-Request must not wait behind Map-Registers.

usage: tools/test_boundary_hol.py BOUNDARY_BIN RAM_HOST RAM_PORT UDP_PORT
Point RAM_HOST:RAM_PORT at a RAM server behind a wire with a real round trip (qualify: tools/shaped_relay.py, 22 ms).
16 senders (16 source ports) each fire 5 Map-Registers at once; while they are being processed, Map-Requests go in
one at a time and each one's answer time is measured -- every one must stay under one round trip. It costs no round
trip, so its answer time must stay far below ONE round trip whatever the registers are doing. FAILS when the receive
thread processes registers itself (the requests queue behind their round trips); PASSES when it hands them off."""
import json, socket, statistics, subprocess, sys, threading, time
sys.path.insert(0, ".")
from tests.test_wire_contract import reg4, req4
BIN, RH, RP, UDP = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])
b = subprocess.Popen([BIN, "--ram", RH, RP, "--udp", str(UDP)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
def cfg(op, **a):
    b.stdin.write(json.dumps({"operation": op, "args": a}) + "\n"); b.stdin.flush()
    r = json.loads(b.stdout.readline())
    if not r.get("ok"): raise RuntimeError("%s: %s" % (op, r.get("error")))
    return r.get("result")
cfg("site.add", iid="0", prefix="198.18.0.0/16", group="", accept_more_specifics=True)
cfg("resolver.wait_site", iid="0", prefix="198.18.0.0/16", group="", active=True)
q = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); q.settimeout(5)
WAIT_S = 5.0
def ask(i):
    """Answer time of one Map-Request, matched to its own reply by nonce; WAIT_S*1000 if no answer came in WAIT_S."""
    nonce = 0x1000 + i
    pkt = req4(target=(198, 18, 200 + (i % 50), 9), nonce=nonce)
    t0 = time.perf_counter(); q.sendto(pkt, ("127.0.0.1", UDP))
    while True:
        left = WAIT_S - (time.perf_counter() - t0)
        if left <= 0: return WAIT_S * 1000
        q.settimeout(left)
        try: d, _ = q.recvfrom(4096)
        except socket.timeout: return WAIT_S * 1000
        if len(d) >= 12 and d[4:12] == pkt[4:12]: return (time.perf_counter() - t0) * 1000   # the reply echoes the nonce
for i in range(5): ask(i)                                      # warm: the held views the requests use are armed
idle = sorted(ask(i) for i in range(20))
senders = [socket.socket(socket.AF_INET, socket.SOCK_DGRAM) for _ in range(16)]
t_reg = time.perf_counter()
for k, s in enumerate(senders):
    for j in range(5):
        s.sendto(reg4(prefix=(198, 18, k * 5 + j, 0), mask=24, rloc=(192, 0, 2, 1 + k), nonce=0x5000 + k * 16 + j), ("127.0.0.1", UDP))
busy = []
deadline = time.perf_counter() + 20
while time.perf_counter() < deadline and len(busy) < 40:
    busy.append(ask(100 + len(busy))); time.sleep(0.005)
# every register must also have been APPLIED -- a boundary that answers requests by dropping registers is not a pass
missing = []
for k in range(16):
    for j in range(5):
        r = cfg("resolver.wait", iid="0", prefix="198.18.%d.0/24" % (k * 5 + j), group="", present=True,
                rloc="192.0.2.%d" % (1 + k), timeout_s=30)
        if r != "good": missing.append((k, j, r))
t_applied = (time.perf_counter() - t_reg) * 1000
p = lambda xs, f: sorted(xs)[min(len(xs) - 1, int(f * (len(xs) - 1)))]
print("Map-Request answer time, idle : median %.2f ms, max %.2f ms (%d)" % (statistics.median(idle), max(idle), len(idle)))
print("Map-Request answer time, busy : median %.2f ms, p90 %.2f ms, max %.2f ms (%d, while 80 Map-Registers are processed)"
      % (statistics.median(busy), p(busy, 0.9), max(busy), len(busy)))
print("all 80 Map-Registers applied %.0f ms after they were sent%s" % (t_applied, "" if not missing else "; NOT applied: %r" % missing[:5]))
ok = max(busy) < 20.0 and not missing                                     # every one of them: under one 22 ms round trip
print("%s: a Map-Request %s behind Map-Registers" % ("PASS" if ok else "FAIL", "does not wait" if ok else "WAITS"))
sys.stdout.flush()
b.stdin.close()
try: b.wait(timeout=20)
except subprocess.TimeoutExpired: b.kill(); print("the boundary did not stop within 20 s of stdin EOF: killed"); ok = False
sys.exit(0 if ok else 1)
