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
# test_ramrows.py -- the RAM-answer handler (include/ramrows.hpp) against the REAL RAM server's answers.
# A small FNW1 client (HELLO pairing, REQ_RAW, RESP_RAW) reads the exact answer text. Random workload: writes, rewrites
# of one member, removes, nested bags, arrays, numbers of every kind, unicode. For every consecutive pair of answers to
# the same read, the handler must either say cannot-hold -- and the shapes must really differ -- or rebuild the new
# answer byte for byte through semcodec encode_reply_diff / decode_reply. Effect: bytes of the diff vs the answer.
# usage: test_ramrows.py PORT [SEED] [STEPS]
import json, os, random, socket, struct, subprocess, sys
PORT = int(sys.argv[1]); SEED = int(sys.argv[2]) if len(sys.argv) > 2 else 1; STEPS = int(sys.argv[3]) if len(sys.argv) > 3 else 300
M = b'FNW1'


class Ram:
    def __init__(self, port):
        self.req = socket.create_connection(('127.0.0.1', port)); tok = os.urandom(8).hex().encode()
        self._send(self.req, M + bytes([0x50, len(tok)]) + tok)
        self.ret = socket.create_connection(('127.0.0.1', port)); self._send(self.ret, M + bytes([0x50, 7 + len(tok)]) + b'RETURN:' + tok)
        self._recv(self.ret)                                             # the server's HELLO
        self.n = 0

    @staticmethod
    def _send(s, f): s.sendall(struct.pack('!I', len(f)) + f)

    @staticmethod
    def _rx(s, n):
        b = b''
        while len(b) < n:
            d = s.recv(n - len(b))
            if not d: raise ConnectionError('closed')
            b += d
        return b

    def _recv(self, s): return self._rx(s, struct.unpack('!I', self._rx(s, 4))[0])

    def call(self, method, path, body=''):
        http = json.dumps({'method': method, 'path': path, 'headers': {}, 'body': body, 'host': '127.0.0.1', 'port': 8080}).encode()
        self.n += 1; h = struct.pack('!QQ', self.n, 0x5eed)
        self._send(self.req, M + bytes([0x03]) + h + struct.pack('!I', len(http)) + http)
        f = self._recv(self.ret)[4:]
        if f[4] != 0x14: raise RuntimeError('expected RESP_RAW, got op 0x%02x' % f[4])
        plen = struct.unpack('!I', f[21:25])[0]; pl = f[25:25 + plen]
        status, hlen = struct.unpack('!HH', pl[:4])
        return status, pl[4 + hlen:].decode('utf-8')


def shape(text):
    """The spec's shape: the address set, and per address its bag's object structure (keys in order, nesting); arrays
    and scalars are leaves. The handler may say cannot-hold only when this differs."""
    obj = json.loads(text, object_pairs_hook=lambda p: ('OBJ', p))
    def st(v): return ('OBJ', [(k, st(x)) for k, x in v[1]]) if isinstance(v, tuple) and v[0] == 'OBJ' else 'leaf'
    rows = dict(obj[1])['rows']; out = {}
    for r in rows:
        d = dict(r[1]); out[(d['service'], d['variable'], d['instance'])] = st(d['bag'])
    return out


rng = random.Random(SEED)
ram = Ram(PORT)
drv = subprocess.Popen(['./bin/ramrows-driver'], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
def check(ref, new):
    drv.stdin.write(json.dumps({'op': 'check', 'ref': ref, 'new': new}) + '\n'); drv.stdin.flush()
    return json.loads(drv.stdout.readline())

WORDS = ['a', 'b', 'x', 'value', 'n', 'ts', 'name', 'nested', 'list', 'flag', 'none', 'k.dot', 'é']
def leaf():
    r = rng.random()
    if r < .15: return rng.randrange(-1000, 1000)
    if r < .25: return rng.choice([0.1, 1.5, -2.25, 1e-12, 123456.789, 2 ** 53 + 1, 1e16, 3.0])
    if r < .45: return rng.choice(['ok', 'down', 'h.frognet', '10.0.0.1', 'ünï', 'q"uote', 'back\\slash', ''])
    if r < .55: return rng.choice([True, False])
    if r < .6: return None
    if r < .75: return [rng.randrange(10), 'x', 2.5]
    return {'in': rng.randrange(5), 'deep': {'z': rng.choice(['p', 'q'])}}
def bag(shape_seed):
    r2 = random.Random(shape_seed); keys = r2.sample(WORDS, r2.randrange(1, 6))
    return {k: leaf() for k in keys}

addrs = [('v%d' % (i % 3), 'i%d' % i) for i in range(18)]
shape_of = {a: rng.randrange(1000) for a in addrs}
ids = {}
queries = ['/ram.php?op=read&service=rr&variable=v0', '/ram.php?op=read&service=rr&variable=v1', '/ram.php?op=read&service=rr',
           '/ram.php?op=read&service=rr&variable=v2&instance=i2']
prev = {}
st = {'same': 0, 'changed_held': 0, 'changed_diff_bytes': 0, 'changed_raw_bytes': 0, 'pairs': 0, 'hold': 0, 'exact': 0, 'raw': 0, 'raw_really_changed': 0, 'diff_bytes': 0, 'raw_bytes': 0, 'errors': 0}
fails = []
for a in addrs:
    s, t = ram.call('POST', '/ram.php?op=write', json.dumps({'service': 'rr', 'variable': a[0], 'instance': a[1], 'bag': bag(shape_of[a])}))
    ids[a] = json.loads(t)['id']
for step in range(STEPS):
    r = rng.random(); a = rng.choice(addrs)
    if r < .6:                                   # rewrite: same shape, one member changed (the storm case)
        b = bag(shape_of[a]); k = rng.choice(list(b)); b[k] = leaf()
        s, t = ram.call('POST', '/ram.php?op=write', json.dumps({'service': 'rr', 'variable': a[0], 'instance': a[1], 'bag': b})); ids[a] = json.loads(t)['id']
    elif r < .7:                                 # a new shape at an address
        shape_of[a] = rng.randrange(1000)
        s, t = ram.call('POST', '/ram.php?op=write', json.dumps({'service': 'rr', 'variable': a[0], 'instance': a[1], 'bag': bag(shape_of[a])})); ids[a] = json.loads(t)['id']
    elif r < .75 and a in ids:                   # remove, then it comes back later
        ram.call('DELETE', '/ram.php?op=remove&id=%d' % ids.pop(a))
    elif a not in ids:
        s, t = ram.call('POST', '/ram.php?op=write', json.dumps({'service': 'rr', 'variable': a[0], 'instance': a[1], 'bag': bag(shape_of[a])})); ids[a] = json.loads(t)['id']
    for q in queries:
        s, t = ram.call('GET', q)
        if q in prev:
            r = check(prev[q], t); st['pairs'] += 1
            if not r['ok']:
                st['errors'] += 1
                if len(fails) < 5: fails.append('handler error: %s' % r['error'])
            elif r['hold']:
                st['hold'] += 1; st['diff_bytes'] += r['diff_bytes']; st['raw_bytes'] += r['raw_bytes']
                if r['diff_bytes'] == 0: st['same'] += 1
                else: st['changed_held'] += 1; st['changed_diff_bytes'] += r['diff_bytes']; st['changed_raw_bytes'] += r['raw_bytes']
                if r['exact']: st['exact'] += 1
                elif len(fails) < 5: fails.append('NOT EXACT at step %d %s' % (step, q))
            else:
                st['raw'] += 1
                if shape(prev[q]) != shape(t): st['raw_really_changed'] += 1
                elif len(fails) < 5: fails.append('cannot-hold but the shape did not change, step %d %s' % (step, q))
        prev[q] = t
drv.stdin.close(); drv.wait()
print(json.dumps(st))
for f in fails: print('FAIL', f)
ok = st['errors'] == 0 and st['hold'] == st['exact'] and st['raw'] == st['raw_really_changed'] and st['hold'] > 0
if st['changed_raw_bytes']: print('CHANGED answers carried as a diff: %d, %d B of diff vs %d B of answers (%.1f%%, %.0fx less)' % (
    st['changed_held'], st['changed_diff_bytes'], st['changed_raw_bytes'], 100.0 * st['changed_diff_bytes'] / st['changed_raw_bytes'],
    st['changed_raw_bytes'] / max(1, st['changed_diff_bytes'])))
if st['raw_bytes']: print('held answers: diff %d B vs answers %d B (%.1f%%)' % (st['diff_bytes'], st['raw_bytes'], 100.0 * st['diff_bytes'] / st['raw_bytes']))
print('RESULT %s ramrows (seed %d, %d steps, %d pairs)' % ('PASS' if ok else 'FAIL', SEED, STEPS, st['pairs']))
sys.exit(0 if ok else 1)
