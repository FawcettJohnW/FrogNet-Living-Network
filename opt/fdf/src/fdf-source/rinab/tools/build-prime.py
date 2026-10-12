#!/usr/bin/env python3
"""build-prime.py OUT IN... -- [FDF_PRIME_V1] (plan item 5) build a BLDC-1 dictionary (primed templates) from exemplars
recorded in learning runs (RIBBIT_TEMPLATES_RECORD). One exemplar per (path, op, field shape): the first seen wins -- two bodies of one op with different fields are
different templates (a program's control hello and its data-set hello). Prints what it kept."""
import json, sys
out, seen, kept = sys.argv[1], set(), []
for f in sys.argv[2:]:
    for line in open(f):
        line = line.strip()
        if not line: continue
        e = json.loads(line)
        try: body = json.loads(e["b"]); op = body.get("op", "?"); shape = tuple(sorted(body))
        except Exception: op, shape = "?", ()
        k = (e["p"], op, shape)
        if k in seen: continue
        seen.add(k); kept.append(line)
        print(f"{op:20} {','.join(shape)[:60]}")
open(out, "w").write("\n".join(kept) + "\n")
print(f"{len(kept)} exemplars -> {out}")
