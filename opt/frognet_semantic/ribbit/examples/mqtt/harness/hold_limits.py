#!/usr/bin/env python3
# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
#
# hold_limits.py -- [HOLD_FOR_ROOM_V1] drives a broker into its hold limits on purpose and counts, per message, what
# the publishers were told (PUBACK) against what the subscriber eventually received.
#
#   hold_limits.py --broker LABEL=PATH [--broker ...] [--env LABEL:NAME=VALUE] [--case all|name,name]
#
# One QoS 1 subscriber on hl/#, which reads but does not acknowledge for --silent seconds (so its 20 in-flight slots and
# its max_queued_messages queue fill), then acknowledges everything and is drained until 2 s pass with nothing new.
# Publishers each send their messages at QoS 1 with 20 unacknowledged at most. Every payload is unique, so:
#   acked      messages the broker acknowledged to their publisher
#   delivered  distinct messages the subscriber received, at any time
#   lost       acknowledged and never delivered
# and from $SYS/broker/ribbit/held/... (Ribbit only): held, publishers paused, delivered at the bound.
#
# Cases (max_queued_messages 1000 in every broker):
#   under-age   1 publisher x 3000, subscriber silent 0.5 s   -- inside both bounds
#   age         1 publisher x 3000, subscriber silent 3 s     -- past the 1 s age bound
#   count       20 publishers x 300, subscriber silent 0.5 s  -- past the count bound (run with RIBBIT_HOLD_MAX=100)
import argparse, json, os, socket, struct, subprocess, sys, tempfile, threading, time

CASES = {
    'under-age': dict(pubs=1, per=3000, silent=0.5),
    'age':       dict(pubs=1, per=3000, silent=3.0),
    'count':     dict(pubs=20, per=300, silent=0.5),
}


def varint(n):
    out = b''
    while True:
        b = n % 128; n //= 128
        out += bytes([b | (128 if n else 0)])
        if not n: return out


def packet(h, body): return bytes([h]) + varint(len(body)) + body
def s16(s): b = s.encode() if isinstance(s, str) else s; return struct.pack('!H', len(b)) + b


class Conn:
    def __init__(self, port, cid):
        self.s = socket.create_connection(('127.0.0.1', port)); self.s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.buf = b''
        self.s.sendall(packet(0x10, s16('MQTT') + bytes([4, 0x02]) + struct.pack('!H', 60) + s16(cid)))
        t, _ = self.read()
        if t != 2: raise RuntimeError('no CONNACK')

    def read(self, timeout=None):
        self.s.settimeout(timeout)
        while True:
            got = self.parse()
            if got: return got
            try: d = self.s.recv(65536)
            except socket.timeout: return None, None
            if not d: raise RuntimeError('closed')
            self.buf += d

    def parse(self):
        b = self.buf
        if len(b) < 2: return None
        n, mul, i = 0, 1, 1
        while True:
            if i >= len(b): return None
            c = b[i]; n += (c & 127) * mul; mul *= 128; i += 1
            if not c & 128: break
        if len(b) < i + n: return None
        h, body = b[0], b[i:i + n]; self.buf = b[i + n:]
        return h >> 4, (h, body)


def publisher(port, p, per, out):
    c = Conn(port, f'hl-p{p}')
    acked, inflight, mid, sent = set(), {}, 0, 0
    deadline = time.time() + 60
    while (sent < per or inflight) and time.time() < deadline:
        while sent < per and len(inflight) < 20:
            mid = mid % 65535 + 1; key = f'{p}:{sent}'
            c.s.sendall(packet(0x32, s16(f'hl/{p}') + struct.pack('!H', mid) + key.encode()))
            inflight[mid] = key; sent += 1
        t, pk = c.read(timeout=0.5)
        if t == 4:
            m = struct.unpack('!H', pk[1][:2])[0]
            if m in inflight: acked.add(inflight.pop(m))
    out[p] = (sent, acked)
    c.s.close()


def run(exe, env, case, port):
    cfg = tempfile.NamedTemporaryFile('w', suffix='.conf', delete=False)
    cfg.write(f'listener {port} 127.0.0.1\nallow_anonymous true\nmax_queued_messages 1000\nsys_interval 1\n'); cfg.close()
    os.chmod(cfg.name, 0o644)
    b = subprocess.Popen([exe, '-c', cfg.name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
    for _ in range(100):
        try: socket.create_connection(('127.0.0.1', port), timeout=0.2).close(); break
        except OSError: time.sleep(0.05)
    try:
        sub = Conn(port, 'hl-sub')
        sub.s.sendall(packet(0x82, struct.pack('!H', 1) + s16('hl/#') + b'\x01'))
        while sub.read(timeout=2)[0] != 9: pass
        delivered, pending = set(), []
        out = {}
        ths = [threading.Thread(target=publisher, args=(port, p, case['per'], out)) for p in range(case['pubs'])]
        for t in ths: t.start()
        t_end_silent = time.time() + case['silent']
        quiet_since = None
        while True:
            t, pk = sub.read(timeout=0.1)
            now = time.time()
            if t == 3:
                h, body = pk; tl = struct.unpack('!H', body[:2])[0]; q = (h >> 1) & 3; p = 2 + tl
                mid = struct.unpack('!H', body[p:p + 2])[0] if q else 0; p += 2 if q else 0
                delivered.add(body[p:].decode())
                if q: pending.append(mid)
                quiet_since = None
            elif quiet_since is None:
                quiet_since = now
            if now >= t_end_silent and pending:
                sub.s.sendall(b''.join(packet(0x40, struct.pack('!H', m)) for m in pending)); pending = []
                quiet_since = None                              # acknowledging frees the queue: wait for what follows
            if now >= t_end_silent and quiet_since and now - quiet_since > 2 and not any(t.is_alive() for t in ths): break
        for t in ths: t.join()
        sent = sum(v[0] for v in out.values()); acked = set().union(*(v[1] for v in out.values())) if out else set()
        held = {}
        try:
            m = Conn(port, 'hl-sys')
            m.s.sendall(packet(0x82, struct.pack('!H', 1) + s16('$SYS/broker/ribbit/held/#') + b'\x00'))
            end = time.time() + 2.5
            while time.time() < end and len(held) < 4:
                t, pk = m.read(timeout=0.3)
                if t == 3:
                    body = pk[1]; tl = struct.unpack('!H', body[:2])[0]
                    held[body[2:2 + tl].decode().rsplit('/', 1)[1]] = int(body[2 + tl:].decode())
        except Exception:
            pass
        return dict(sent=sent, acked=len(acked), delivered=len(delivered),
                    lost=len(acked - delivered), held=held)
    finally:
        b.terminate()
        try: b.wait(5)
        except subprocess.TimeoutExpired: b.kill()
        os.unlink(cfg.name)


ap = argparse.ArgumentParser()
ap.add_argument('--broker', action='append', required=True); ap.add_argument('--env', action='append', default=[])
ap.add_argument('--case', default='all')
a = ap.parse_args()
names = list(CASES) if a.case == 'all' else a.case.split(',')
port = 19100
for name in names:
    for spec in a.broker:
        label, exe = spec.split('=', 1)
        env = dict(os.environ)
        for e in a.env:
            l, kv = e.split(':', 1)
            if l == label: k, v = kv.split('=', 1); env[k] = v
        port += 1
        r = run(exe, env, CASES[name], port)
        r.update(case=name, broker=label, **CASES[name])
        print(json.dumps(r), flush=True)
