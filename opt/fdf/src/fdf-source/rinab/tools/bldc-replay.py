#!/usr/bin/env python3
"""bldc-replay.py FROGNET_SEMANTIC_ROOT SEND_TRACE -- [FDF_PRIME_V1] FrogNet's Python BLDC-1 on the EXACT `send` bodies
FD&F posted (RINA_SEND_TRACE), one reference per flow (FD&F's engine keeps references per instance). Prints the FNW1
frame bytes per send: the first of each flow, then the steady state."""
import contextlib, io, json, os, sys
root, tr = sys.argv[1], sys.argv[2]
os.environ.setdefault('FROGNET_BLOB_ROOT', '/tmp/bldc-replay-blobs'); sys.path.insert(0, root)
with contextlib.redirect_stdout(io.StringIO()):
    from core.codec import SemanticCodec
    import core.semcache_wire as W
    import proxy.origin as O
    import proxy.transport_semantic as TS
codec, refs, first, steady = SemanticCodec(), {}, [], []
for line in open(tr):
    b = json.loads(line); ref = refs.get(b["fid"])
    sem, nr, ident = codec.encode_request_diff(opcode=1, url_vals=[("op", "rina")], json_vals=list(b.items()), type_map={}, reference=ref, tokens=None, compress=True)
    h = TS._compute_req_hash("127.0.0.1", "/p", 1, nr)
    if not ident: sem = O.inject_origin_into_semantic_request(sem, "127.0.0.1", "127.0.0.1")
    f = W.wrap_req_repeat(h) if ident else (W.wrap_req_full(h, sem) if ref is None else W.wrap_req_diff(h, sem))
    (first if ref is None else steady).append(len(f)); refs[b["fid"]] = nr
print(f"python BLDC-1 on the {len(first) + len(steady)} real sends: first {first}, then {min(steady)}-{max(steady)} bytes (mean {sum(steady)/len(steady):.1f})")
