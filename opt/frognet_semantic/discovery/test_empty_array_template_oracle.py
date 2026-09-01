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
"""
test_empty_array_template_oracle.py -- an empty array is not a sample of a shape.

[NO_TEMPLATE_FROM_AN_EMPTY_ARRAY_V1]

A reply template is learned from whatever the first real response happened to be. A
response whose array is empty --

    {"ok":true,"rows":[]}

-- produces a template with the SAME field_order and the SAME type_map as one learned
from a populated response. Structurally it passes every check. The only difference is
that its baseline array is empty, and the baseline is what every later reply on that
opcode is reconstructed from. So the proxy answers rows:[] for ever: ok:true, HTTP
200, no error anywhere, for a database full of rows.

Seen on Seattle3, 2026-08-02. A full database clear, then the first
SensorType=communicator read went out while there were no communicator tuples. From
then on every semantic read of that opcode returned rows:[] regardless of the
database. Clearing SemCacheProxy and SemCacheDaemon did not help -- the poison is the
TEMPLATE, and templates live in frognet_request_templates /
frognet_response_templates, which the cache nuke does not touch. Only the node that IS
the data host was unaffected, because its read never enters the semantic path at all
(decision.py: a local target goes straight to Apache).

The rule: do not store a template whose learned baseline contains an empty array. No
template means the next request bootstraps RAW and learns when there is something to
learn from. That costs one raw round trip per attempt on a genuinely empty opcode,
which is the right price -- a template that lies is worse than no template.

Run: python3 test_empty_array_template_oracle.py
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

_p = _f = 0


def ck(name, cond, extra=""):
    global _p, _f
    if cond:
        _p += 1
        print("  [PASS] %s" % name)
    else:
        _f += 1
        print("  [FAIL] %s  %s" % (name, extra))


# The detector, lifted out of proxy/templates.py without importing its DB dependencies.
src = open(os.path.join(ROOT, "proxy", "templates.py")).read()
i = src.index("def _first_row_only(")
j = src.index("def learn_templates_from_real(")
ns = {}
exec(compile(src[i:j], "templates", "exec"), ns)
empty_arrays_in = ns["_empty_arrays_in"]
first_row_only = ns["_first_row_only"]

ck("the refusal is wired into the one place a template is stored",
   "_empty_arrays_in(resp_template.get(\"baseline\"))" in src
   and src.index("_empty_arrays_in(resp_template") < src.index("store.store_templates("),
   "detector must run BEFORE store_templates")


print("-- what an empty array looks like to the learner -------------------------")
sys.path.insert(0, os.path.join(ROOT, "core"))
sys.path.insert(0, ROOT)
try:
    from core.json_handler import JsonFormatHandler
    h = JsonFormatHandler()
    t_empty = h.learn_reply_template('{"ok":true,"rows":[]}')
    t_full = h.learn_reply_template(
        '{"ok":true,"rows":[{"SensorID":1,"SensorName":"SD:x",'
        '"SensorType":"communicator","jsonData":"{}","UpdatedAtEpoch":123}]}')
    ck("field_order is IDENTICAL either way -- structure cannot tell them apart",
       t_empty.get("field_order") == t_full.get("field_order"),
       (t_empty.get("field_order"), t_full.get("field_order")))
    ck("type_map is identical too",
       t_empty.get("type_map") == t_full.get("type_map"))
    ck("only the BASELINE differs",
       t_empty.get("baseline") != t_full.get("baseline"))
    ck("and the empty one would be stored happily without this rule",
       t_empty.get("mode") == "json" and bool(t_empty.get("field_order")))
    ck("the detector catches it", empty_arrays_in(t_empty.get("baseline")) == ["rows"],
       empty_arrays_in(t_empty.get("baseline")))
    ck("and passes the populated one",
       empty_arrays_in(t_full.get("baseline")) == [],
       empty_arrays_in(t_full.get("baseline")))
except ImportError as e:
    print("  SKIP live learner (%s)" % e)


print("-- the detector, at depth ------------------------------------------------")
ck("an empty top-level array", empty_arrays_in([]) == ["<root>"])
ck("an empty array under a key", empty_arrays_in({"rows": []}) == ["rows"])
ck("a populated array is fine", empty_arrays_in({"rows": [{"a": 1}]}) == [])
ck("an empty array NESTED inside a row is caught",
   empty_arrays_in({"rows": [{"tags": []}]}) == ["rows[0].tags"])
ck("every empty array is named, not just the first",
   empty_arrays_in({"rows": [], "extra": []}) == ["rows", "extra"])
ck("no arrays at all is not a refusal", empty_arrays_in({"ok": True, "n": 3}) == [])
ck("scalars and None are not arrays",
   empty_arrays_in({"a": None, "b": "", "c": 0}) == [])
ck("an empty OBJECT is not an empty array", empty_arrays_in({"rows": {}}) == [])


print("-- [ONE_ROW_IS_THE_SAMPLE_V1] one row is the whole sample ----------------")
# A template records what the fields ARE. One row carries every field name; the rest
# repeat them and inflate the baseline that gets stored and shipped.
ck("the learning sample is cut to one row BEFORE the reply template is learned",
   "_first_row_only(_sample)" in src
   and src.index("_first_row_only(_sample)") < src.index("learn_reply_template("
                                                          "resp_text_for_learning)"),
   "truncation must precede learning")

def _row(i):
    return {"SensorID": i, "SensorName": "SD:x%d" % i, "SensorType": "communicator",
            "jsonData": "{}", "UpdatedAtEpoch": 1785000000 + i,
            "data": {"id": "u%d" % i, "status": "online"}}

ck("many rows become one", first_row_only({"ok": True, "rows": [_row(0), _row(1),
                                                                _row(2)]})["rows"]
   == [_row(0)])
ck("one row stays one", first_row_only({"ok": True, "rows": [_row(0)]})["rows"]
   == [_row(0)])
ck("no rows stays no rows -- and is still refused",
   first_row_only({"ok": True, "rows": []})["rows"] == []
   and empty_arrays_in(first_row_only({"ok": True, "rows": []})) == ["rows"])
ck("every field of the kept row survives, including nested ones",
   sorted(first_row_only({"ok": True, "rows": [_row(0)]})["rows"][0].keys())
   == sorted(_row(0).keys()))
ck("nested arrays inside the kept row are cut too",
   first_row_only({"rows": [{"tags": ["a", "b", "c"]}]})["rows"][0]["tags"] == ["a"])
ck("scalars are untouched",
   first_row_only({"ok": True, "n": 3, "s": "x"}) == {"ok": True, "n": 3, "s": "x"})

try:
    from core.json_handler import JsonFormatHandler
    h2 = JsonFormatHandler()
    sizes, shapes = set(), set()
    for n in (1, 5, 25, 131):
        doc = {"ok": True, "rows": [_row(i) for i in range(n)]}
        t = h2.learn_reply_template(json.dumps(first_row_only(doc)))
        sizes.add(len(json.dumps(t)))
        shapes.add(json.dumps([t.get("field_order"), t.get("type_map")], sort_keys=True))
    ck("the template is byte-identical however many rows the sample had",
       len(sizes) == 1, sizes)
    ck("and its shape is the same one every time", len(shapes) == 1)

    whole = h2.learn_reply_template(json.dumps(
        {"ok": True, "rows": [_row(i) for i in range(5)]}))
    one = h2.learn_reply_template(json.dumps(
        first_row_only({"ok": True, "rows": [_row(i) for i in range(5)]})))
    ck("one row learns the SAME field_order as the whole reply",
       one.get("field_order") == whole.get("field_order"))
    ck("and the same type_map", one.get("type_map") == whole.get("type_map"))
    ck("while being smaller", len(json.dumps(one)) < len(json.dumps(whole)),
       (len(json.dumps(one)), len(json.dumps(whole))))
except ImportError:
    print("  SKIP live learner")


print("-- [NO_SAME_FROM_AN_EMPTY_ARRAY_V1] the CACHE must refuse it too -----------")
# Refusing to learn a template is not enough on its own. _handle_resp_raw_and_learn
# cached the body under req_hash and marked the request seen BEFORE learning and
# unconditionally, so the next identical request found is_req_seen() true, sent
# REQ_REPEAT instead of REQ_RAW, and was served RESP_SAME from that cached body. With
# no template stored, every request takes that raw path -- so refusing the template
# alone GUARANTEES the empty answer is replayed for ever.
ts = open(os.path.join(ROOT, "proxy", "transport_semantic.py")).read()
i2 = ts.index("def _is_empty_collection_payload(")
j2 = ts.index("def _handle_resp_raw_and_learn(")
ns2 = {"json": json}
exec(compile(ts[i2:j2], "ts", "exec"), ns2)
empty_payload = ns2["_is_empty_collection_payload"]

# [SAME_CACHE_IS_MEMORY_ONLY_V1] the cache write on this path is _same_lru_put,
# not semcache_db.upsert_raw. The MySQL write was removed deliberately -- it put
# a synchronous DB round-trip on the response path of every reply, on the
# request thread, inside the coalesced in-flight RPC that a whole burst waits
# on. These two checks used to name upsert_raw and were asserting the pre-change
# shape; the PROPERTY they guard is unchanged, so they follow the rename rather
# than being retired.
# Scope the ordering checks to the HANDLER BODY. Searching the whole file finds
# `def _same_lru_put(same_id` -- the definition, ~2300 lines earlier -- so a
# whole-file index() compares the check against the wrong occurrence and reports
# a bad order that is not there.
_body = ts[ts.index("def _handle_resp_raw_and_learn("):]

ck("the check runs BEFORE the cache write and mark_req_seen",
   _body.index("_is_empty_collection_payload(body, ctype)")
   < _body.index("_same_lru_put(same_id"),
   "must gate the cache write, not follow it")
ck("both the cache write AND the seen-marking are gated by the check",
   "_empty_at = _is_empty_collection_payload(body, ctype)" in _body
   and "elif _empty_at:" in _body
   and _body.index("elif _empty_at:")
       < _body.index("semcache_db.mark_req_seen(target_ip, req_hash)")
   and _body.index("elif _empty_at:") < _body.index("_same_lru_put(same_id"),
   "marking a request seen is what turns the next one into REQ_REPEAT")

ck("the exact poison is refused",
   empty_payload(b'{"ok":true,"rows":[]}', "application/json") == ["rows"])
ck("a real answer is cached as before",
   empty_payload(b'{"ok":true,"rows":[{"SensorID":1}]}', "application/json") == [])
ck("an empty array nested in a row is refused",
   empty_payload(b'{"ok":true,"rows":[{"tags":[]}]}', "application/json")
   == ["rows[0].tags"])
ck("a top-level empty array is refused",
   empty_payload(b"[]", "application/json") == ["<root>"])
ck("a reply with no arrays at all is untouched",
   empty_payload(b'{"ok":true,"n":3}', "application/json") == [])
ck("an unparseable body is untouched -- this must not widen",
   empty_payload(b"not json at all", "application/json") == [])
ck("a non-JSON content type is untouched",
   empty_payload(b'{"ok":true,"rows":[]}', "text/html") == [])
ck("an empty body is untouched", empty_payload(b"", "application/json") == [])
ck("an empty OBJECT is not an empty array",
   empty_payload(b'{"ok":true,"rows":{}}', "application/json") == [])


print()
print("=== %d passed, %d failed ===" % (_p, _f))
print("ORACLE " + ("GREEN" if _f == 0 else "RED"))
sys.exit(0 if _f == 0 else 1)
