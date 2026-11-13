#!/usr/bin/env python3
################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#                                                              #
#  SPDX-License-Identifier: GPL-2.0-only                       #
#                                                              #
#  See COPYRIGHT and LICENSE at the root of this tree.         #
################################################################
# Map-Register -> Map-Notify throughput through a LispBoundary, with closed-loop senders (one outstanding register
# each, like perf.py) and, optionally, open-loop bursts from one sender (an ETR refreshing many mappings at once).
# Put a delay on the RAM link first to stand in for the Internet path (tools/rtt.sh on 8788), or run it against the
# droplet with RIBBIT_RAM_HOST/PORT.
#
#   CLIENTS="1 4 16 64" DUR=10 BURST=200 python3 tools/bench_register_rtt.py
#
# Prints one line per client count: registers/s, p50/p95 ms, unanswered; then the burst line; then the front's counts.
import json, os, socket, struct, subprocess, sys, threading, time
sys.path.insert(0, os.path.dirname(__file__))
from compare_lispers import map_register
RAM_HOST = os.environ.get('RIBBIT_RAM_HOST', '127.0.0.1'); RAM_PORT = os.environ.get('RIBBIT_RAM_PORT', '8788')
UDP = int(os.environ.get('UDP', '43422')); DUR = float(os.environ.get('DUR', '10')); WARM = float(os.environ.get('WARM', '2'))
CLIENTS = [int(x) for x in os.environ.get('CLIENTS', '1 4 16 64').split()]; BURST = int(os.environ.get('BURST', '200'))
p = subprocess.Popen(['./tools/lisp-handler-driver', '--ram', RAM_HOST, RAM_PORT], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
def call(op, **a):
    p.stdin.write(json.dumps({'operation': op, 'args': a}) + '\n'); p.stdin.flush(); r = json.loads(p.stdout.readline())
    if not r['ok']: raise RuntimeError(r['error'])
    return r['result']
call('boundary.open', udp_port=UDP)
call('boundary.call', engine_op='site.add', args=json.dumps({'iid': '0', 'prefix': '198.18.0.0/16', 'group': '',
                                                             'accept_more_specifics': True, 'key_id': 1, 'password': 'bench'}))
time.sleep(0.5)
dst = ('127.0.0.1', UDP)

def pct(v, q):
    v = sorted(v); return v[min(len(v) - 1, int(q / 100.0 * len(v)))] if v else 0

def closed(n):
    stop_at = time.time() + WARM + DUR; t_meas = time.time() + WARM; lat = [[] for _ in range(n)]; lost = [0] * n
    def one(i):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.settimeout(2.0); nonce = (i + 1) << 40
        eid = '198.18.%d.%d' % (200 + i // 250, i % 250 + 1); flip = False
        while time.time() < stop_at:
            nonce += 1; flip = not flip
            pkt = map_register([(eid, 32, 1440 * 60, ['192.0.2.1'] if flip else ['192.0.2.1', '198.51.100.1'])], 1, 'bench', nonce)
            t0 = time.perf_counter(); s.sendto(pkt, dst); want = struct.pack('!Q', nonce); got = False
            while True:
                try: d = s.recv(4096)
                except socket.timeout: break
                if d[4:12] == want: got = True; break
            if time.time() < t_meas: continue
            if got: lat[i].append(time.perf_counter() - t0)
            else: lost[i] += 1
    ts = [threading.Thread(target=one, args=(i,), daemon=True) for i in range(n)]
    for t in ts: t.start()
    for t in ts: t.join()
    all_ = [x for l in lat for x in l]
    print('closed-loop %3d senders: %8.1f registers/s  p50 %7.2f ms  p95 %7.2f ms  unanswered %d'
          % (n, len(all_) / DUR, pct(all_, 50) * 1e3, pct(all_, 95) * 1e3, sum(lost)), flush=True)

def burst(k):
    """One sender, k registers for k different EIDs sent back to back; time until the last Map-Notify."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.settimeout(5.0); s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1 << 22)
    base = 7 << 40; t0 = time.perf_counter()
    for j in range(k):
        s.sendto(map_register([('198.18.%d.%d' % (100 + j // 250, j % 250 + 1), 32, 1440 * 60, ['192.0.2.2'])], 1, 'bench', base + j), dst)
    seen = set()
    while len(seen) < k:
        try: d = s.recv(4096)
        except socket.timeout: break
        seen.add(struct.unpack('!Q', d[4:12])[0])
    dt = time.perf_counter() - t0
    print('burst from one sender: %d registers, %d notified in %.3f s = %.1f registers/s'
          % (k, len(seen), dt, len(seen) / dt), flush=True)

for n in CLIENTS: closed(n)
if BURST: burst(BURST)
print(call('boundary.counts')); call('boundary.stop'); p.stdin.close(); p.wait(timeout=10)
