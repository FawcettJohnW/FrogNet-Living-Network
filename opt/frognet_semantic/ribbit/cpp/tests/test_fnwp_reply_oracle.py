#!/usr/bin/env python3
# S4b oracle (pure reply side): fnwp::apply_resp_diff against the proxy's _handle_resp_diff composition -- codec.decode_reply
# (sem_blob, ReplyTemplate, tokens, response reference) -> fields -> new reference and ReplyTemplate.rebuild(values). The
# RESP_DIFF payloads come from the daemon side itself (daemon/engine/session.py _diff_encode_response) over reply
# templates learned by core/json_handler.py from real-shaped bodies, against no / matching / drifted references.
import argparse, contextlib, io, json, os, random, struct, subprocess, sys
from collections import Counter
sys.dont_write_bytecode = True
ap = argparse.ArgumentParser(); ap.add_argument('root'); ap.add_argument('--driver', default='./bin/fnwp-driver')
ap.add_argument('--seed', type=int, default=20260925); ap.add_argument('--n', type=int, default=4000); a = ap.parse_args()
os.environ.setdefault('FROGNET_BLOB_ROOT', '/tmp/fnwp-oracle-blobs'); sys.path.insert(0, a.root)
with contextlib.redirect_stdout(io.StringIO()):
    import daemon.engine.session as S, core.json_handler as JH, core.template as TP
    from core.codec import SemanticCodec
    from core.tokens import TokenStore
codec = SemanticCodec(); rng = random.Random(a.seed); JS = JH.JsonFormatHandler()
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
def leaf():
    return rng.choice([rng.randrange(-99, 99), 0.5, -0.0, 1e-12, 'ok', 'é', 'h.frognet', '10.0.0.%d' % rng.randrange(9), True, None, [1, 'x']])
def body():
    rows = [{'SensorID': i, 'value': leaf(), 'status': rng.choice(['OK', 'DOWN']), 'dev': 'eth0'} for i in range(rng.randrange(1, 3))]
    return {'ok': True, 'count': rng.randrange(9), 'meta': {'host': leaf(), 'ts': leaf()}, 'rows': rows, 'tag': leaf()}
def run(f):
    try: return f()
    except Exception as e: return 'raise ' + {'ValueError': 'value', 'error': 'struct'}.get(type(e).__name__, 'other:' + type(e).__name__)
cases = []
for i in range(a.n):
    frag = JS.learn_reply_template(json.dumps(body()))
    b2 = body(); b2 = {k: b2[k] for k in ['ok', 'count', 'meta', 'rows', 'tag']}
    fields = JS.extract_reply_dynamic(json.dumps(b2), frag)
    r = rng.random()
    if r < .3: ref = None
    elif r < .7: ref = {k: v for k, v in JS.extract_reply_dynamic(json.dumps(body()), frag)}
    else: ref = {k: v for k, v in fields}
    op = 7; peer = '10.1.1.1'
    with S._ref_lock:
        S._response_references.pop((peer, op), None)
        if ref is not None: S._response_references[(peer, op)] = dict(ref)
    with contextlib.redirect_stdout(io.StringIO()):
        blob = S._diff_encode_response(peer, op, fields, frag.get('type_map') or {})
    def py(frag=frag, blob=blob, ref=ref):
        t = TP.ReplyTemplate('t', op, json.loads(json.dumps(frag)), frag.get('tokens') or {})
        f = codec.decode_reply(blob, t, TokenStore(t.tokens), ref)
        vals = [v for _, v in f]; fo = frag.get('field_order') or []; tm = frag.get('type_map') or {}
        # the S3 contract: where rebuild_reply substitutes, the C++ declines (same order as the Python walks)
        if fo and vals and isinstance(vals[0], (list, tuple)) and len(vals[0]) == 2: return 'raise declined pair_heuristic'
        vm = {fo[i]: vals[i] for i in range(min(len(fo), len(vals)))}
        for p_ in fo:
            if tm.get(p_) != 'array': continue
            v = vm.get(p_)
            if v is None or isinstance(v, list): continue
            if isinstance(v, str):
                t_ = v.strip()
                if not t_: continue
                try:
                    if isinstance(json.loads(t_), list): continue
                except Exception: pass
                return 'raise declined array_literal'
            return 'raise declined array_type'
        return 'ok S%s %s' % (w8(t.rebuild(vals)), cv({k: v for k, v in f}))
    cases.append(('rdiff %s %s %s' % (cv(frag), blob.hex(), '-' if ref is None else cv(ref)), py))
r = subprocess.run([a.driver], input='\n'.join(c[0] for c in cases) + '\n', capture_output=True, text=True); got = r.stdout.split('\n')
bad, shown = 0, 0
for (line, f), have in zip(cases, got):
    exp = run(f)
    if exp != have:
        bad += 1
        if shown < 5: shown += 1; print('DIFF\n  in    =%s\n  python=%s\n  c++   =%s' % (line[:200], exp[:240], have[:240]))
print('%s rdiff: %d cases, %d differ' % ('PASS' if not bad else 'FAIL', len(cases), bad))
print('RESULT %s fnwp S4b-reply vs %s (seed %d)' % ('PASS' if not bad and len(got) > len(cases) else 'FAIL', S.__file__, a.seed)); sys.exit(1 if bad else 0)
