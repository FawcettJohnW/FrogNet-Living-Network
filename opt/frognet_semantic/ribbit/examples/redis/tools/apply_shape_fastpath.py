#!/usr/bin/env python3
# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
"""[SHAPE_NEEDS_AN_OBJECT_V1] semtpl::with_body_shape adds a body's field set to its key only when the body is a JSON
OBJECT. An object begins with '{' (after the whitespace it already strips); a body that does not begin with '{' can only
parse to something that is not an object, or not parse at all -- and both return the key unchanged. So it returns the
key at once instead of running the JSON parser to an exception. Measured: the Redis region's raw batches ("RRB1 ...")
threw and caught a JSON error on every batch out and every answer back, at both ends. Same result, no exception.
Usage: apply_shape_fastpath.py <ribbit_cpp dir>"""
import os, sys
p = os.path.join(sys.argv[1], "semtpl.hpp"); s = open(p).read()
old = '''    if (a == b) return key;
    std::optional<Obj> j;
    try { j = semcodec::json_loads(u::decode_replace(body.substr(a, b - a))); } catch (const pyv::JsonError&) { return key; }'''
new = '''    if (a == b) return key;
    if (body[a] != '{') return key;                     // [SHAPE_NEEDS_AN_OBJECT_V1] only an object has a field set
    std::optional<Obj> j;
    try { j = semcodec::json_loads(u::decode_replace(body.substr(a, b - a))); } catch (const pyv::JsonError&) { return key; }'''
if s.count(old) != 1: sys.exit("apply_shape_fastpath: anchor found %d times" % s.count(old))
s = s.replace(old, new)
# json_try: json.loads or none. JSON text (as Python's json reads it: whitespace ' \t\n\r', then a value -- NaN and
# Infinity included) can only begin with one of {["-0123456789tfnNI. Anything else is none at once, not by exception.
old = '''inline std::optional<Obj> json_try(std::string_view text) {           // json.loads, or none where the Python's except fires
    try { return semcodec::json_loads(text); }'''
new = '''inline std::optional<Obj> json_try(std::string_view text) {           // json.loads, or none where the Python's except fires
    {   size_t a = 0; while (a < text.size() && (text[a] == ' ' || text[a] == '\\t' || text[a] == '\\n' || text[a] == '\\r')) ++a;
        if (a < text.size() && !std::strchr("{[\\"-0123456789tfnNI", text[a])) return std::nullopt; }   // [SHAPE_NEEDS_AN_OBJECT_V1]
    try { return semcodec::json_loads(text); }'''
if s.count(old) != 1: sys.exit("apply_shape_fastpath: json_try anchor found %d times" % s.count(old))
s = s.replace(old, new)
open(p, "w").write(s); print("apply_shape_fastpath: applied")
