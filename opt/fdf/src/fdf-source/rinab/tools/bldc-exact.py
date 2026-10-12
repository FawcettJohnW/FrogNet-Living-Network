#!/usr/bin/env python3
"""bldc-exact.py FROGNET_SEMANTIC_ROOT FNWP_DRIVER SEND_TRACE -- [FDF_PRIME_V1] the EXACT `send` bodies FD&F posted, through
FrogNet's Python BLDC-1 AND through FD&F's C++ FNWP engine (FrogNet's tests/fnwp_driver.cpp built against this tree's
patched platform), in the canonical value form of FrogNet's own ribbit/cpp/tests/test_fnwp_oracle.py. Each side chains
its own reference per flow. Reports byte-identical frames and the mean frame size of each."""
import contextlib, io, json, os, struct, subprocess, sys
root, drv, tr = sys.argv[1], sys.argv[2], sys.argv[3]
os.environ.setdefault('FROGNET_BLOB_ROOT', '/tmp/bldc-exact-blobs'); sys.path.insert(0, root)
with contextlib.redirect_stdout(io.StringIO()):
    from core.codec import SemanticCodec
    import core.semcache_wire as W
    import proxy.origin as O
    import proxy.transport_semantic as TS
def w8(s): return s.encode('utf-8', 'surrogatepass').hex()
def cv(v):
    if v is None: return 'N'
    if v is True: return 'B1'
    if v is False: return 'B0'
    if isinstance(v, int): return 'I%d' % v
    if isinstance(v, float): return 'F' + struct.pack('>d', v).hex()
    if isinstance(v, str): return 'S' + w8(v)
    if isinstance(v, (list, tuple)): return 'L(' + ','.join(cv(x) for x in v) + ')'
    if isinstance(v, dict): return 'D(' + ','.join('S%s=%s' % (w8(k), cv(x)) for k, x in v.items()) + ')'
    raise TypeError(type(v))
def cq(pairs): return 'Q(' + ','.join('S%s=%s' % (w8(k), cv(x)) for k, x in pairs) + ')'
T, P, OP, ORG, DST = '127.0.0.1', '/FawcettInnovations.RINA.ram_interface.php', 1, '127.0.0.1', '127.0.0.1'
codec = SemanticCodec(); d = subprocess.Popen([drv], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)
pref, cref, same, n, psz, csz = {}, {}, 0, 0, [], []
for line in open(tr):
    b = json.loads(line); fid = b["fid"]; u, j = [("op", "rina")], list(b.items())
    ref = pref.get(fid)
    sem, nr, ident = codec.encode_request_diff(opcode=OP, url_vals=u, json_vals=j, type_map={}, reference=ref, tokens=None, compress=True)
    h = TS._compute_req_hash(T, P, OP, nr)
    if not ident: sem = O.inject_origin_into_semantic_request(sem, ORG, DST)
    pf = W.wrap_req_repeat(h) if ident else (W.wrap_req_full(h, sem) if ref is None else W.wrap_req_diff(h, sem))
    pref[fid] = nr
    cr = cref.get(fid, '-')
    d.stdin.write('build S%s S%s %d %s %s %s S%s S%s\n' % (w8(T), w8(P), OP, cq(u), cq(j), cr, w8(ORG), w8(DST))); d.stdin.flush()
    out = d.stdout.readline().split()
    if len(out) < 5 or out[0] != 'ok': sys.exit(f"driver: {' '.join(out)[:200]}")
    cf = bytes.fromhex(out[2]); cref[fid] = out[4]
    n += 1; same += (cf == pf)
    if ref is not None: psz.append(len(pf)); csz.append(len(cf))
print(f"{n} sends; byte-identical frames: {same} of {n}")
print(f"steady-state frame bytes: python {sum(psz)/len(psz):.1f} (range {min(psz)}-{max(psz)}), C++ {sum(csz)/len(csz):.1f} (range {min(csz)}-{max(csz)})")
