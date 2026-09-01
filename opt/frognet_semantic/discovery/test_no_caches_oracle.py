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
"""test_no_caches_oracle.py  [NO_CACHES_V1]

Gate for "no caches, ever, anywhere".

A cache here means: state that holds a previous ANSWER and lets a later request
be served from it instead of from the source. The SAME/DIFF held references are
NOT caches -- they hold field VALUES that the diff is computed against, and the
reply is reconstructed from them plus the template, never replayed from a stored
body. This oracle asserts the first are gone and the second still work.

Run:  python3 discovery/test_no_caches_oracle.py [ROOT]
Must FAIL on pre-[NO_CACHES_V1] source and PASS after.
"""
import json
import os
import sys

ROOT = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else
                       os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

FAILED = []


def ck(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + (f"   [{detail}]" if detail and not cond else ""))
    if not cond:
        FAILED.append(name)


def src(rel):
    p = os.path.join(ROOT, rel)
    return open(p).read() if os.path.exists(p) else None


# ── G1: the answer-cache modules are gone ────────────────────────────────────
for rel in ("proxy/local_read_cache.py",
            "proxy/cache/semcache_db.py",
            "daemon/cache/semcache_db.py"):
    ck(f"G1 {rel} does not exist", not os.path.exists(os.path.join(ROOT, rel)))

# ── G2: nothing on the request path imports or calls them ────────────────────
BANNED = ("semcache_db", "local_read_cache", "_same_lru",
          "_sensor_id_cache", "_hash_cache", "_tpl_cache")
for rel in ("daemon/engine/execution.py", "daemon/engine/session.py",
            "daemon/daemon_main.py", "proxy/transport_semantic.py",
            "proxy/proxy_main.py", "proxy/proxy_metrics.py",
            "daemon/daemon_metrics.py", "core/store.py"):
    s = src(rel)
    if s is None:
        ck(f"G2 {rel} present", False, "missing file")
        continue
    live = [b for b in BANNED
            if any(b in ln and not ln.lstrip().startswith("#") for ln in s.splitlines())]
    ck(f"G2 {rel} has no live cache reference", not live, ",".join(live))

# ── G2b: the one surviving materialization must prove itself per read ────────
# [DATA_CACHE_GEN_V2] data_cache is exempt from G1/G2 because it is not allowed
# to answer from a snapshot it cannot prove current. That property is gated in
# full by discovery/test_data_cache_gen_oracle.py; these are the structural
# guarantees that make it possible.
dc = src("daemon/engine/data_cache.py") or ""
ck("G2b data_cache checks a MySQL-maintained generation on every read",
   "FrogNetTableGen" in dc and "_read_gen" in dc and "_ensure_current" in dc)
ck("G2b data_cache fails closed when the generation wiring is absent",
   "_check_wiring" in dc and "DISABLED" in dc)
ck("G2b data_cache intercepts no writes",
   "upsert" not in dc.replace("upsert_batch", "").replace(
       "# ", "#").split("def try_intercept")[-1].split("def get_stats")[0]
   or "_write_queue" not in dc)
ck("G2b data_cache filters on an allow-list, not a deny-list",
   "_SENSOR_COLS" in dc and "unknown = [k for k in params" in dc)

# ── G3: /etc/hosts and local-IP have no TTL window ───────────────────────────
for rel in ("core/hosts_only.py", "daemon/util/hosts.py", "daemon/util/ip.py"):
    s = src(rel) or ""
    ck(f"G3 {rel} keeps no TTL cache",
       "_CACHE_TTL" not in s and "_cached_at" not in s)

# ── G4: an unchanged request is a frame, not an absence ──────────────────────
from core.codec import SemanticCodec, FLAG_DIFF          # noqa: E402
from core.tokens import TokenStore                        # noqa: E402

C = SemanticCodec()
vals = [("SensorType", "communicator"), ("parse", "1")]
wire1, ref1, ident1 = C.encode_request_diff(
    opcode=7, url_vals=vals, json_vals=[], type_map={},
    reference=None, tokens=TokenStore({}), compress=True)
wire2, ref2, ident2 = C.encode_request_diff(
    opcode=7, url_vals=vals, json_vals=[], type_map={},
    reference=ref1, tokens=TokenStore({}), compress=True)

ck("G4 unchanged request still emits a frame", len(wire2) == 8,
   f"len={len(wire2)}")
ck("G4 unchanged request is not flagged identical", ident2 is False,
   f"is_identical={ident2}")
ck("G4 the frame is a zero-change DIFF",
   len(wire2) == 8 and wire2[1] & FLAG_DIFF and wire2[6:8] == b"\x00\x00")


class _ReqTpl:
    url_query_keys = ["SensorType", "parse"]
    fragment = {"field_order": []}


try:
    url_vals, _ = C.decode_request(wire2, _ReqTpl(), TokenStore({}), reference=ref1)
except Exception as e:
    url_vals = f"UNDECODABLE: {e!r}"
ck("G4 receiver reconstructs the full request from the held reference",
   url_vals == vals, repr(url_vals))

# ── G5: an unchanged reply reconstructs the same body, nothing stored ────────
from core.json_handler import JsonFormatHandler           # noqa: E402
from core.template import ReplyTemplate                   # noqa: E402

H = JsonFormatHandler()
body = json.dumps({"ok": True, "rows": [
    {"SensorID": 1, "SensorName": "a", "jsonData": "{}"},
    {"SensorID": 2, "SensorName": "b", "jsonData": "{}"},
    {"SensorID": 3, "SensorName": "c", "jsonData": "{}"}]})
frag = H.learn_reply_template(body)
tpl = ReplyTemplate("t", 7, frag, frag.get("tokens") or {}, "json")
dyn = tpl.extract_dynamic(body)

# reply 1: daemon has no reference -> full
w1, dref, _ = C.encode_reply_diff(7, dyn, tpl.type_map, None, TokenStore(tpl.tokens))
f1 = C.decode_reply(w1, tpl, TokenStore(tpl.tokens), None)
pref = {k: v for k, v in f1}
out1 = tpl.rebuild([v for _, v in f1])

# reply 2: identical execution, both sides hold a reference
w2, _, ident = C.encode_reply_diff(7, dyn, tpl.type_map, dref, TokenStore(tpl.tokens))
ck("G5 unchanged reply still emits a frame", len(w2) == 8, f"len={len(w2)}")
try:
    f2 = C.decode_reply(w2, tpl, TokenStore(tpl.tokens), pref)
    out2 = tpl.rebuild([v for _, v in f2])
except Exception as e:
    out2 = f"UNDECODABLE: {e!r}"

ck("G5 first reply carries all 3 rows", len(json.loads(out1)["rows"]) == 3)
try:
    _n2 = len(json.loads(out2)["rows"])
except Exception:
    _n2 = out2
ck("G5 unchanged reply reconstructs all 3 rows with no stored body", _n2 == 3, str(_n2))
ck("G5 the two reconstructions are byte-identical", out1 == out2)

# ── G6: template lookups are not memoised ────────────────────────────────────
s = src("core/store.py") or ""
ck("G6 TemplateStore has no TTL cache",
   "_TPL_CACHE_TTL" not in s and "_cache_put" not in s)

print()
if FAILED:
    print(f"FAILED {len(FAILED)}:")
    for f in FAILED:
        print("  -", f)
    sys.exit(1)
print("ALL PASS")
