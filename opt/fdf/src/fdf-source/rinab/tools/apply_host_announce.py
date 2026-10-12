#!/usr/bin/env python3
"""apply_host_announce.py RAM_SERVER_CPP -- [HOST_ANNOUNCES_ITSELF_V1] (FD&F experiment 5b, John's ruling: neighbour
propagation). Once the host is LISTENING (not before: a member reconnecting on the announcement must find it), it
tells its own machine's agent, which announces the host change to the DIF's links. One call after the listen; the
hook is defined by the region (src/rina_region.cpp, rina_host_listening)."""
import sys
p = sys.argv[1]; s = open(p).read()
tag = "[HOST_ANNOUNCES_ITSELF_V1]"
if tag in s: print("host announce already applied"); sys.exit(0)
old = '''    logline(std::string("ram-server ") + RIBBIT_LISP_VERSION + ": FNW1 and the memory, in one process, on " + listen_on);
    std::thread([srv] {'''
assert s.count(old) == 1, "apply_host_announce: the listen point was not found exactly once"
s = s.replace(old, '''    logline(std::string("ram-server ") + RIBBIT_LISP_VERSION + ": FNW1 and the memory, in one process, on " + listen_on);
    { extern void rina_host_listening(const std::string&); rina_host_listening(listen_on); }   // %s
    std::thread([srv] {''' % tag)
open(p, "w").write(s); print("host announce applied")
