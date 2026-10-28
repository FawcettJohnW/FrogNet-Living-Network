#!/usr/bin/env python3
"""apply_owned_cells.py RAM_SERVER_CPP -- [OWNED_CELL_GOES_WITH_THE_SESSION_V1] a plain write may say "own":true: its
tuple is then this session's, and the region writes it {} when the session ends (rina_own_cell, rina_region.cpp). A
participant is there while its session is up, so nothing re-writes itself to stay fresh and no reader filters by age.
A plain-HTTP write has no session: own:true there is refused, said. Needs apply_session_hook.py first."""
import sys
p = sys.argv[1]; s = open(p).read(); tag = "[OWNED_CELL_GOES_WITH_THE_SESSION_V1]"
if tag in s: print("owned cells already applied"); sys.exit(0)
decl = 'void rina_session_down(uint64_t id);\n'
assert s.count(decl) == 1, "apply_owned_cells: the session hook's declaration was not found (apply_session_hook.py first)"
s = s.replace(decl, decl + 'void rina_own_cell(const std::string& s, const std::string& v, const std::string& i);   // %s\n' % tag)
old = '''        out = Reply{200, "{\\"ok\\":true,\\"id\\":" + std::to_string(MEM.write(in["service"].s, in["variable"].s, in["instance"].s, dump(in["bag"]), wall())) + "}"}; return true;'''
assert s.count(old) == 1, "apply_owned_cells: the write op was not found exactly once"
new = '''        const bool own = in["own"].type == Json::Bool && in["own"].b;                                   // %s
        if (own && rina_session_id() == 0) { out = bad(400, "own:true needs a session (FNW1); a plain-HTTP write has none"); return true; }
        const uint64_t wid = MEM.write(in["service"].s, in["variable"].s, in["instance"].s, dump(in["bag"]), wall());
        if (own) rina_own_cell(in["service"].s, in["variable"].s, in["instance"].s);
        out = Reply{200, "{\\"ok\\":true,\\"id\\":" + std::to_string(wid) + "}"}; return true;''' % tag
s = s.replace(old, new, 1)
open(p, "w").write(s); print("%s applied" % tag)
