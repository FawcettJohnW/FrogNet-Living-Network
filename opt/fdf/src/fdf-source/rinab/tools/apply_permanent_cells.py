#!/usr/bin/env python3
"""apply_permanent_cells.py RAM_SERVER_CPP -- [PERMANENT_CELL_V1] a plain write may say "permanent":true: the tuple is
filed in the region's file (rina_permanent_cell, rina_region.cpp) before it is published, and the host writes it back
into the memory when it starts again. A tuple is either Permanent or goes with its session, never both (refused, said).
Needs apply_owned_cells.py first."""
import sys
p = sys.argv[1]; s = open(p).read(); tag = "[PERMANENT_CELL_V1]"
if tag in s: print("permanent cells already applied"); sys.exit(0)
decl = 'void rina_own_cell(const std::string& s, const std::string& v, const std::string& i);   // [OWNED_CELL_GOES_WITH_THE_SESSION_V1]\n'
assert s.count(decl) == 1, "apply_permanent_cells: apply_owned_cells.py first"
s = s.replace(decl, decl + 'void rina_permanent_cell(const std::string& s, const std::string& v, const std::string& i, const std::string& bag);   // %s\n' % tag)
old = '''        if (own && rina_session_id() == 0) {'''
assert s.count(old) == 1, "apply_permanent_cells: the owned write was not found exactly once"
new = '''        const bool permanent = in["permanent"].type == Json::Bool && in["permanent"].b;                 // %s
        if (permanent && own) { out = bad(400, "a tuple is permanent or goes with its session, not both"); return true; }
        if (own && rina_session_id() == 0) {''' % tag
s = s.replace(old, new, 1)
old = '''        const uint64_t wid = MEM.write(in["service"].s, in["variable"].s, in["instance"].s, dump(in["bag"]), wall());'''
assert s.count(old) == 1, "apply_permanent_cells: the owned write was not found exactly once"
s = s.replace(old, '''        if (permanent) rina_permanent_cell(in["service"].s, in["variable"].s, in["instance"].s, dump(in["bag"]));   // durable before it is published
''' + old, 1); open(p, "w").write(s); print("%s applied" % tag)
