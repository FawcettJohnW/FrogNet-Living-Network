#!/usr/bin/env python3
"""Compare the shape of two RepeaterTastic vector sets: per vector, the header fields that
encode protocol behaviour (not ids, keys, addresses, timestamps). Prints SAME / DIFF per field."""
import json, sys, pathlib
A, B = map(pathlib.Path, sys.argv[1:3])
F = ["udp.dst_group", "channel_hash", "hop_limit", "hop_start", "want_ack", "via_mqtt", "next_hop!=0", "relay_node!=0",
     "priority_name", "transport_mechanism", "pki_encrypted", "all_set_fields", "crypto.type",
     "decoded.portnum_name", "decoded.want_response", "decoded.request_id!=0", "decoded.all_set_fields", "decoded.bitfield"]
def get(v, f):
    nz = f.endswith("!=0"); f = f[:-3] if nz else f
    for p in f.split("."): v = v.get(p) if isinstance(v, dict) else None
    if f == "channel_hash" and v not in (0, None): v = "nonzero"
    return (v not in (0, None)) if nz else v
na = {p.stem for p in A.glob("*.json") if p.stem != "index"}
nb = {p.stem for p in B.glob("*.json") if p.stem != "index"}
print(f"only in {A.name}: {sorted(na - nb)}\nonly in {B.name}: {sorted(nb - na)}")
ndiff = 0
for n in sorted(na & nb):
    a = json.load(open(A / f"{n}.json")); b = json.load(open(B / f"{n}.json"))
    d = [(f, get(a, f), get(b, f)) for f in F if get(a, f) != get(b, f)]
    ndiff += bool(d)
    print(f"{'SAME' if not d else 'DIFF'} {n}" + "".join(f"\n     {f}: {x!r} -> {y!r}" for f, x, y in d))
print(f"{len(na & nb)} common, {ndiff} with shape differences")
