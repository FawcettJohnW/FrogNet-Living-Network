#!/usr/bin/env python3
"""[SESSION_EPOCH_V1] a session's id is a monotonic epoch assigned when the session object is made, never the object's
address. Addresses are reused after free; every record keyed by the id (node liveness, watches, session-bound mappings,
the proc table) could otherwise be matched by a later session that happened to land on the same address. One global
counter; the id is unique for the life of the process. Usage: apply_session_epoch.py <ram_server.cpp>"""
import sys, re
p = sys.argv[1]; s = open(p).read()
if "SESSION_EPOCH_V1" in s:
    print("already applied"); sys.exit(0)
old = "struct Session {\n    int req = -1, ret = -1, data = -1;"
new = ("static std::atomic<uint64_t> g_session_epoch{1};   // [SESSION_EPOCH_V1] unique per session for the life of the process\n"
       "struct Session {\n    const uint64_t epoch = g_session_epoch.fetch_add(1, std::memory_order_relaxed);   // [SESSION_EPOCH_V1] the session's id\n"
       "    int req = -1, ret = -1, data = -1;")
assert old in s, "Session struct anchor not found"; s = s.replace(old, new, 1)
n = s.count("reinterpret_cast<uint64_t>(s.get())")
assert n == 6, "expected 6 address-as-id sites, found %d" % n
s = s.replace("reinterpret_cast<uint64_t>(s.get())", "s->epoch")
if "#include <atomic>" not in s:
    s = s.replace("#include <mutex>", "#include <mutex>\n#include <atomic>", 1)
open(p, "w").write(s); print("ok: 6 sites -> s->epoch")
