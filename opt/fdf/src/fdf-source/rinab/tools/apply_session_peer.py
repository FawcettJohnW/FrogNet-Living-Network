#!/usr/bin/env python3
"""apply_session_peer.py RAM_SERVER_CPP -- [SESSION_PEER_V1] (FD&F NAT traversal) the region learns the address each
session arrives FROM: one call at "session up", where the server already knows both. A machine behind NAT finds out
that way that the address it is seen at is not one it holds (DESIGN-NAT.md step 1). Declared at file scope, beside the
session hook's rina_session_down (serve() is in the server's anonymous namespace). Needs apply_session_hook.py first."""
import sys
p = sys.argv[1]; s = open(p).read(); tag = "[SESSION_PEER_V1]"
if tag in s: print("session peer already applied"); sys.exit(0)
decl = 'void rina_session_down(uint64_t id);\n'
assert s.count(decl) == 1, "apply_session_peer: the session hook's declaration was not found (apply_session_hook.py first)"
s = s.replace(decl, decl + 'void rina_session_peer(uint64_t id, const std::string& peer);   // %s\n' % tag)
old = '''    logline("session up   " + peer + " " + token); uint32_t seq = 0;'''
assert s.count(old) == 1, "apply_session_peer: the session-up point was not found exactly once"
s = s.replace(old, old + '''
    rina_session_peer(reinterpret_cast<uint64_t>(s.get()), peer);   // %s''' % tag)
open(p, "w").write(s); print("session peer applied")
