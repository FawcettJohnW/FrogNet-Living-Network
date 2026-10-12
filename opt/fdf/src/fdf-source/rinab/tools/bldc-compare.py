#!/usr/bin/env python3
"""bldc-compare.py FROGNET_SEMANTIC_ROOT EXEMPLAR_FILE N -- [FDF_PRIME_V1] (plan item 5) FrogNet's Python BLDC-1 on the
same `send` sequence FD&F puts on its FNWP wire: the codec (core.codec.SemanticCodec.encode_request_diff) and the frame
wrappers (core.semcache_wire), driven exactly as ribbit/cpp/tests/test_fnwp_oracle.py drives them (origin injected as
the proxy does). N sends with the sequence number counting up and the data from the recorded exemplar, in both body
shapes (flat {fid,q,d}; array {sdus:[[fid,q,d]]}). Prints frame bytes: the first, and the steady state."""
import contextlib, io, json, os, sys
root, ex, n = sys.argv[1], sys.argv[2], int(sys.argv[3])
os.environ.setdefault('FROGNET_BLOB_ROOT', '/tmp/bldc-compare-blobs'); sys.path.insert(0, root)
with contextlib.redirect_stdout(io.StringIO()):
    from core.codec import SemanticCodec
    import core.semcache_wire as W
    import proxy.origin as O
    import proxy.transport_semantic as TS
flat = None
for line in open(ex):
    e = json.loads(line); b = json.loads(e["b"])
    if b.get("op") == "send" and "fid" in b: flat = b; path = e["p"]; break
assert flat, "no flat send exemplar"
def run(shape):
    codec, ref, sizes = SemanticCodec(), None, []
    for q in range(flat["q"], flat["q"] + n):
        body = {"op": "send", "fid": flat["fid"], "q": q, "d": flat["d"]} if shape == "flat" else {"op": "send", "sdus": [[flat["fid"], q, flat["d"]]]}
        sem, newref, ident = codec.encode_request_diff(opcode=1, url_vals=[("op", "rina")], json_vals=list(body.items()), type_map={}, reference=ref, tokens=None, compress=True)
        h = TS._compute_req_hash("127.0.0.1", path, 1, newref)
        if not ident: sem = O.inject_origin_into_semantic_request(sem, "127.0.0.1", "127.0.0.1")
        f = W.wrap_req_repeat(h) if ident else (W.wrap_req_full(h, sem) if ref is None else W.wrap_req_diff(h, sem))
        sizes.append(len(f)); ref = newref
    steady = sizes[1:]
    print(f"python BLDC-1, {shape:5} send: first {sizes[0]} bytes, then {min(steady)}-{max(steady)} (mean {sum(steady)/len(steady):.1f}) over {n}")
run("flat"); run("array")
