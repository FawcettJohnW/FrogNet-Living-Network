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
# LispBoundary round trips over loopback UDP, measured: Map-Request -> Map-Reply (registered EID, then an unregistered
# one) and Map-Register (HMAC-SHA-256, want-notify) -> Map-Notify. Median and p90 of N, in microseconds.
import json, os, socket, statistics, struct, subprocess, sys, time
sys.path.insert(0, os.path.dirname(__file__))
RAM_HOST = os.environ.get('RIBBIT_RAM_HOST', '127.0.0.1'); RAM_PORT = os.environ.get('RIBBIT_RAM_PORT', '8788')
UDP, N = 43421, int(os.environ.get('N', '2000'))
p = subprocess.Popen(['./tools/lisp-handler-driver', '--ram', RAM_HOST, RAM_PORT], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
def call(op, **a):
    p.stdin.write(json.dumps({'operation': op, 'args': a}) + '\n'); p.stdin.flush(); r = json.loads(p.stdout.readline())
    if not r['ok']: raise RuntimeError(r['error'])
    return r['result']
def req4(target, nonce):
    q = struct.pack('!I', (1 << 28) | 1) + struct.pack('=Q', nonce) + struct.pack('!H', 0) + struct.pack('!H', 1) + bytes((192, 0, 2, 1))
    return q + bytes((0, 32)) + struct.pack('!H', 1) + socket.inet_aton(target)
call('boundary.open', udp_port=UDP)
call('boundary.call', engine_op='site.add', args=json.dumps({'iid': '0', 'prefix': '198.19.0.0/16', 'group': '', 'accept_more_specifics': True,
                                                             'key_id': 1, 'password': 'b'}))
reg = bytes.fromhex(call('encode_register', key_id=1, alg=2, password='b', notify=True, nonce=1, ttl=3, prefix='198.19.7.0/24', rloc='192.0.2.9'))
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.settimeout(2); dst = ('127.0.0.1', UDP)
s.sendto(reg, dst); s.recv(4096)
for _ in range(50):
    s.sendto(req4('198.19.7.1', 1), dst)
    if s.recv(4096)[16] > 0: break
    time.sleep(0.1)
def bench(pkt, expect_locators=None):
    t = []
    for i in range(N):
        a = time.perf_counter_ns(); s.sendto(pkt, dst); r = s.recv(4096); t.append((time.perf_counter_ns() - a) / 1000)
        if expect_locators is not None and (r[16] > 0) != expect_locators: raise SystemExit('wrong answer at %d: locators=%d expected %s reply=%s' % (i, r[16], expect_locators, r.hex()))
    t.sort(); return statistics.median(t), t[int(len(t) * 0.9)]
for name, pkt, ex in (('Map-Request -> Map-Reply (registered)', req4('198.19.7.1', 2), True),
                      ('Map-Request -> negative reply', req4('203.0.113.9', 3), False),
                      ('Map-Register -> Map-Notify', reg, None)):
    m, p90 = bench(pkt, ex); print('%-40s median %8.1f us  p90 %8.1f us  (N=%d)' % (name, m, p90, N))
print(call('boundary.counts')); call('boundary.stop'); p.stdin.close(); p.wait(timeout=10)
