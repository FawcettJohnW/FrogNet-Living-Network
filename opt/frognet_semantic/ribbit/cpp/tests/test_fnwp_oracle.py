#!/usr/bin/env python3
# S4a oracle: fnwp::build_request (include/fnwp_client.hpp through tools/fnwp-driver) against John's Python, composed
# exactly as proxy/transport_semantic.py _handle_semantic_request composes it: core/codec.py encode_request_diff,
# transport_semantic._compute_req_hash, proxy/origin.py inject_origin_into_semantic_request, core/semcache_wire.py
# wrap_req_repeat / wrap_req_full / wrap_req_diff. Frames, request hashes and new references compared exactly.
# Sequences are chained: each case's reference is the new reference an earlier case produced (as the proxy keeps one
# per (target, opcode)), or none, or a mutated copy.
import argparse, contextlib, io, json, os, random, struct, subprocess, sys
from collections import Counter
sys.dont_write_bytecode = True
ap = argparse.ArgumentParser(); ap.add_argument('root'); ap.add_argument('--driver', default='./bin/fnwp-driver')
ap.add_argument('--seed', type=int, default=20260925); ap.add_argument('--n', type=int, default=20000); a = ap.parse_args()
os.environ.setdefault('FROGNET_BLOB_ROOT', '/tmp/fnwp-oracle-blobs')
sys.path.insert(0, a.root)
with contextlib.redirect_stdout(io.StringIO()):
    from core.codec import SemanticCodec
    import core.semcache_wire as W
    import proxy.origin as O
    import proxy.transport_semantic as TS
codec = SemanticCodec(); rng = random.Random(a.seed)


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
    raise TypeError(type(v))
def cq(pairs): return 'Q(' + ','.join('S%s=%s' % (w8(k), cv(x)) for k, x in pairs) + ')'


def py(target, path, op, u, j, ref, origin, dest):
    try:
        sem, newref, ident = codec.encode_request_diff(opcode=op, url_vals=u, json_vals=j, type_map={}, reference=ref, tokens=None, compress=True)
        h = TS._compute_req_hash(target, path, op, newref)
        if not ident: sem = O.inject_origin_into_semantic_request(sem, origin, dest)
        if ident: t, f = 'REQ_REPEAT', W.wrap_req_repeat(h)
        elif ref is None: t, f = 'REQ_FULL', W.wrap_req_full(h, sem)
        else: t, f = 'REQ_DIFF', W.wrap_req_diff(h, sem)
        return 'ok %s %s %s %s' % (t, f.hex(), h.hex(), cv(newref))
    except UnicodeEncodeError: return 'raise encode'
    except TypeError: return 'raise type'
    except Exception as e: return 'raise other:' + type(e).__name__


KEYS = ['after', 'service', 'variable', 'instance', 'wait_s', 'op', 'SensorID', 'value', 'rows', 'a.b', 'é', '_dst', 'z', '']
def val():
    r = rng.random()
    if r < .35: return rng.choice(['1', '', 'rr', 'v0', '10.0.0.1', 'é', '\U0001f600', 'a b', '0', 'x' * rng.randrange(0, 300)])
    if r < .5: return rng.choice([0, 1, -1, 2 ** 63 - 1, -2 ** 63, rng.randrange(-10 ** 9, 10 ** 9)])
    if r < .6: return rng.choice([0.0, -0.0, 0.1, 1e-12, 2e-12, 1.0, 1e300, 123.456])
    if r < .67: return rng.choice([True, False])
    if r < .72: return None
    if r < .86: return [rng.randrange(9), 'x', {'k': rng.choice([1, 1.0, 'v'])}]
    return {'in': rng.randrange(5), 'n': {'z': rng.choice(['p', 'q', 1])}, 'b': rng.choice([True, None])}
def fields(n): return [(rng.choice(KEYS), val()) for _ in range(n)]
ORIGINS = ['', '10.10.1.1', '192.168.0.9', 'é' * 200, 'h\ud800x', 'x' * 300]
TARGETS = ['10.123.123.1', '10.251.251.1', '127.0.0.1', 'node.frognet']
PATHS = ['/ram.php?op=read&service=rr&variable=v0', '/api.php?entity=sensors&action=values&limit=5', '/', '/ram.php?op=write']

cases, refs = [], []
for i in range(a.n):
    op = rng.choice([1, 7, 0xFFFFFFFE, rng.randrange(1, 2 ** 32 - 1)])
    u = [(k, v if isinstance(v, str) else str(v)) for k, v in fields(rng.randrange(0, 4))]
    j = fields(rng.randrange(0, 6))
    r = rng.random()
    if r < .25 or not refs: ref = None
    elif r < .6: ref = dict(rng.choice(refs))                               # the proxy's stored reference
    elif r < .8:                                                            # the same request again -> REQ_REPEAT
        prev = rng.choice(cases); cases.append(prev); continue
    else: ref = dict(rng.choice(refs)); ref[rng.choice(KEYS)] = val()        # a reference that differs in one field
    c = (rng.choice(TARGETS), rng.choice(PATHS), op, u, j, ref, rng.choice(ORIGINS), rng.choice(ORIGINS + TARGETS))
    cases.append(c)
    out = py(*c)
    if out.startswith('ok'):
        sem, newref, _ = codec.encode_request_diff(opcode=op, url_vals=u, json_vals=j, type_map={}, reference=ref, tokens=None, compress=True)
        refs.append(newref)
        if rng.random() < .3: cases.append(c[:5] + (newref,) + c[6:])    # then the identical request against it
lines = ['build S%s S%s %d %s %s %s S%s S%s' % (w8(c[0]), w8(c[1]), c[2], cq(c[3]), cq(c[4]), '-' if c[5] is None else cv(c[5]), w8(c[6]), w8(c[7]))
         for c in cases]
r = subprocess.run([a.driver], input='\n'.join(lines) + '\n', capture_output=True, text=True)
got = r.stdout.split('\n'); bad = 0; kinds = Counter(); shown = 0
for c, line, have in zip(cases, lines, got):
    exp = py(*c); kinds[exp.split(' ')[1] if exp.startswith('ok') else exp] += 1
    if exp != have:
        bad += 1
        if shown < 8: shown += 1; print('DIFF\n  in    =%s\n  python=%s\n  c++   =%s' % (line[:300], exp[:300], have[:300]))
print('outcomes (python):', dict(kinds))
print('%s build: %d cases, %d differ' % ('PASS' if not bad else 'FAIL', len(cases), bad))
print('RESULT %s fnwp S4a vs %s (seed %d, %d cases)' % ('PASS' if not bad and len(got) > len(cases) else 'FAIL', TS.__file__, a.seed, len(cases)))
sys.exit(0 if not bad else 1)
