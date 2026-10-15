"""apply_region_session_key.py RAM_SERVER_CPP -- [REGION_COALESCE_PER_SESSION_V1] a region request (op=rina, op=rina_block) is coalesced
only with identical requests from the SAME session. The region answers from the caller's session (rina_session_id():
seen, register, map own/remove, mwatch, pwatch, send ...), so an identical request from another session is a different
request. Measured on r20 before this (tests/seen_race.cpp): 0.46-0.78% of `seen` answers carried another session's
address. Memory requests keep [HOST_COALESCE_V1] across sessions unchanged. Applied at build after apply_work_moves.py."""
import sys
p = sys.argv[1]; s = open(p).read()
a = '''            const std::string key = w.req_hash;
'''
b = '''            std::string key = w.req_hash;
            { const std::string op = query(w.path)["op"]; if (op == "rina" || op == "rina_block") key.append(reinterpret_cast<const char*>(&s->epoch), sizeof s->epoch); }   // [REGION_COALESCE_PER_SESSION_V1]
'''
assert s.count(a) == 1, s.count(a)
s = s.replace(a, b)
open(p, 'w').write(s)
print("region coalesce per session applied")
