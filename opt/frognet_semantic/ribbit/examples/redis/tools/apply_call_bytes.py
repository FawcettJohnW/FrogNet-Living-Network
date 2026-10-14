#!/usr/bin/env python3
# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
"""[RESP_ON_THE_WIRE_V1] frogram::Session::call_bytes: the same call as Session::call -- same queue, same coalescing,
same semantic wire, same permanent and data sockets -- whose answer is handed back as the bytes the far end sent instead
of being parsed as JSON. For a region API whose answer is not JSON (the Redis region's raw batch). A status of 400 or
more is refused, its body as the reason.
Usage: apply_call_bytes.py <ribbit_cpp dir>   (edits frogram.hpp and frogram.cpp; refuses unless every anchor is found once)"""
import os, sys
d = sys.argv[1]
def patch(rel, pairs):
    p = os.path.join(d, rel); s = open(p).read()
    for old, new in pairs:
        if s.count(old) != 1: sys.exit("apply_call_bytes: anchor in %s found %d times: %r" % (rel, s.count(old), old[:60]))
        s = s.replace(old, new)
    open(p, "w").write(s)
patch("frogram.hpp", [(
'''    Json call(const std::string& method, const std::string& path, const std::string& body = "",
              double timeout_s = 15.0);''',
'''    Json call(const std::string& method, const std::string& path, const std::string& body = "",
              double timeout_s = 15.0);
    // [RESP_ON_THE_WIRE_V1] call(), with the answer's bytes handed back unparsed (a status >= 400 throws Refused)
    std::string call_bytes(const std::string& method, const std::string& path, const std::string& body = "",
                           double timeout_s = 15.0);''')])
patch("frogram.cpp", [(
'''Json Session::call(const std::string& method, const std::string& path, const std::string& body, double timeout_s) {''',
'''Json Session::call(const std::string& method, const std::string& path, const std::string& body, double timeout_s) {
    std::string b = call_bytes_impl(method, path, body, timeout_s, true);
    Json obj = Json::parse(b);
    if (!obj["ok"].b) throw Refused("HTTP 200: " + obj["error"].s);
    return obj;
}
std::string Session::call_bytes(const std::string& method, const std::string& path, const std::string& body, double timeout_s) {
    return call_bytes_impl(method, path, body, timeout_s, false);
}
std::string Session::call_bytes_impl(const std::string& method, const std::string& path, const std::string& body, double timeout_s, bool json) {'''),
('''    Json obj = Json::parse(p->answer.body);
    if (p->answer.status >= 400 || !obj["ok"].b) throw Refused("HTTP " + std::to_string(p->answer.status) + ": " + obj["error"].s);
    return obj;
}''',
'''    if (p->answer.status >= 400) {
        std::string why = p->answer.body;
        if (json) { try { Json e = Json::parse(p->answer.body); if (e["error"].type == Json::Str) why = e["error"].s; } catch (const std::exception&) {} }
        throw Refused("HTTP " + std::to_string(p->answer.status) + ": " + why);
    }
    return p->answer.body;
}''')])
patch("frogram.hpp", [(
'''    struct Pending; struct Impl;''',
'''    struct Pending; struct Impl;
    std::string call_bytes_impl(const std::string& method, const std::string& path, const std::string& body, double timeout_s, bool json);''')])
print("apply_call_bytes: Session::call_bytes added")
