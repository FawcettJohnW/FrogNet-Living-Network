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
# S5a oracle: fnwp_server.hpp against daemon/engine/session.py (response_body_hash, _diff_encode_response), core/semcache_id.py
# (same_id, secret patched to a fixed test key), core/semcache_wire.py (wrap_resp_same / wrap_resp_diff): Python repr(),
# the body hash, the same_id, and the SAME-or-DIFF reply with the response reference it leaves -- all compared exactly.
import argparse, contextlib, io, os, random, struct, subprocess, sys
from collections import Counter
sys.dont_write_bytecode = True
ap = argparse.ArgumentParser(); ap.add_argument('root'); ap.add_argument('--driver', default='./bin/fnwp-server-driver')
ap.add_argument('--seed', type=int, default=20260925); ap.add_argument('--n', type=int, default=6000); a = ap.parse_args()
os.environ.setdefault('FROGNET_BLOB_ROOT', '/tmp/fnwp-oracle-blobs'); sys.path.insert(0, a.root)
with contextlib.redirect_stdout(io.StringIO()):
    import daemon.engine.session as S, core.semcache_id as I, core.semcache_wire as W
SECRET = bytes(range(32)); I.get_secret_bytes = lambda: SECRET
rng = random.Random(a.seed)
def w8(s): return s.encode('utf-8', 'surrogatepass').hex()
def cv(v):
    if v is None: return 'N'
    if v is True: return 'B1'
    if v is False: return 'B0'
    if isinstance(v, int): return 'I%d' % v
    if isinstance(v, float): return 'F' + struct.pack('>d', v).hex()
    if isinstance(v, str): return 'S' + w8(v)
    if isinstance(v, (bytes, bytearray)): return 'R' + bytes(v).hex()
    if isinstance(v, (list, tuple)): return 'L(' + ','.join(cv(x) for x in v) + ')'
    if isinstance(v, dict): return 'D(' + ','.join('S%s=%s' % (w8(k), cv(x)) for k, x in v.items()) + ')'
def cq(p): return 'Q(' + ','.join('S%s=%s' % (w8(k), cv(x)) for k, x in p) + ')'
def err(e):
    if isinstance(e, UnicodeEncodeError): return 'raise encode'
    if isinstance(e, OverflowError): return 'raise overflow'
    if isinstance(e, ValueError): return 'raise value'
    if isinstance(e, TypeError): return 'raise type'
    return 'raise other:' + type(e).__name__
STRS = ['', 'a', "it's", 'say "hi"', 'both \' "', 'back\\slash', 'tab\t nl\n cr\r', '\x00\x1f\x7f', 'é', '\u00a0', '\u200b', '\u2028',
        '\ud800', '\udc80', '\udcff', '\U0001f600', '\U000e0001', '\x85', 'ü' * 3, 'z' * 50]
def val(d=0):
    r = rng.random()
    if r < .3: return rng.choice(STRS) + rng.choice(['', 'x'])
    if r < .45: return rng.choice([0, -1, 7, 2 ** 63 - 1, -2 ** 63, 10 ** 20])
    if r < .58: return rng.choice([0.0, -0.0, 0.1, 1e-12, 1e16, 1e22, 123.456, float('inf'), float('nan'), 2.5])
    if r < .64: return rng.choice([True, False])
    if r < .68: return None
    if r < .74: return rng.choice([b'', b'\x00\xff', b"it's", b'a"b', b'plain'])
    if d > 1: return 'leaf'
    if r < .87: return [val(d + 1) for _ in range(rng.randrange(4))]
    return {rng.choice(STRS[:6]): val(d + 1) for _ in range(rng.randrange(4))}
KEYS = ['a', 'b', 'rows', 'value', 'ok', 'é', '\udc85', '\ud801', 'Z', '0/id', '1/bag/x']
def fields(n): return [(rng.choice(KEYS), val()) for _ in range(n)]
def run(f):
    try: return f()
    except Exception as e: return err(e)
def py_dreply(req_hash, dyn, status, ref, craw, csid, op, consider):
    peer = '10.9.9.9'
    raw = S.response_body_hash(dyn, status)
    cached = (b'', craw, csid, False) if (craw is not None or csid is not None) else None
    if consider and cached and cached[1] and raw == cached[1] and cached[2] and len(cached[2]) == 16:
        return 'ok %s %s - -' % (W.wrap_resp_same(csid).hex(), cv({k: v for k, v in dyn}))
    sid = I.same_id(req_hash, raw)
    with S._ref_lock:
        S._response_references.pop((peer, op), None)
        if ref is not None: S._response_references[(peer, op)] = dict(ref)
    with contextlib.redirect_stdout(io.StringIO()):
        payload = S._diff_encode_response(peer, op, dyn, {})
    newref = S._get_response_reference(peer, op)
    return 'ok %s %s %s' % (W.wrap_resp_diff(sid, payload).hex(), cv(newref), ('%s %s' % (raw.hex(), sid.hex())) if status < 400 else '- -')
cases = []
for i in range(a.n):
    k = rng.random()
    if k < .3: v = val(); cases.append(('repr S', 'repr ' + cv(v), lambda v=v: 'ok S' + w8(repr(v))))
    elif k < .45:
        f = fields(rng.randrange(0, 5)); st = rng.choice([200, 404, 500, 0])
        cases.append(('rbh', 'rbh %s %d' % (cq(f), st), lambda f=f, st=st: 'ok ' + S.response_body_hash(f, st).hex()))
    elif k < .5:
        rq, rw = os.urandom(rng.choice([16, 32, 15])), os.urandom(rng.choice([32, 31]))
        cases.append(('sid', 'sid %s %s %s' % (SECRET.hex(), rq.hex(), rw.hex()), lambda rq=rq, rw=rw: 'ok ' + I.same_id(rq, rw).hex()))
    else:
        dyn = fields(rng.randrange(0, 6)); st = rng.choice([200, 200, 404]); op = rng.choice([7, 0xFFFFFFFE])
        ref = rng.choice([None, {k: v for k, v in dyn}, {k: v for k, v in fields(3)}])
        if ref is not None and dyn and rng.random() < .5: ref = dict(ref); ref[dyn[0][0]] = val()
        rq = os.urandom(16); cons = rng.random() < .7
        raw = run(lambda: S.response_body_hash(dyn, st)); raw = raw if isinstance(raw, bytes) else None
        craw, csid = rng.choice([(None, None), (raw, os.urandom(16)), (os.urandom(32), os.urandom(16)), (raw, b'short')])
        line = 'dreply %s %s %s %d %s %s %s %d %d' % (SECRET.hex(), rq.hex(), cq(dyn), st, '-' if ref is None else cv(ref),
                                                      '-' if craw is None else craw.hex(), '-' if csid is None else csid.hex(), op, 1 if cons else 0)
        cases.append(('dreply', line, lambda rq=rq, dyn=dyn, st=st, ref=ref, craw=craw, csid=csid, op=op, cons=cons: py_dreply(rq, dyn, st, ref, craw, csid, op, cons)))
lines = [c[1] for c in cases]
r = subprocess.run([a.driver], input='\n'.join(lines) + '\n', capture_output=True, text=True); got = r.stdout.split('\n')
bad, cnt, shown = Counter(), Counter(), 0
for (cat, line, f), have in zip(cases, got):
    exp = run(f); cnt[cat] += 1
    if exp != have:
        bad[cat] += 1
        if shown < 6: shown += 1; print('DIFF %s\n  in    =%s\n  python=%s\n  c++   =%s' % (cat, line[:240], exp[:240], have[:240]))
for k in sorted(cnt): print('%s %s: %d cases, %d differ' % ('PASS' if not bad[k] else 'FAIL', k, cnt[k], bad[k]))
ok = not bad and len(got) > len(cases)
print('RESULT %s fnwp S5a vs %s (seed %d, %d cases)' % ('PASS' if ok else 'FAIL', S.__file__, a.seed, len(cases))); sys.exit(0 if ok else 1)
