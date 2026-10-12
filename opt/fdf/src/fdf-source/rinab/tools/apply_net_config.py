#!/usr/bin/env python3
"""[NET_CONFIG_IS_THE_RAM_MANAGERS_V1] (pond, config, net) -- the network's well-known configuration (what outsiders may
reach, whether a colliding reader may map it, its hub and leaf block) -- is written only by the RAM manager itself, from
its net.json at start (rina_region.cpp). The network API reads it like any cell but may neither write nor remove it: a
member that could rewrite it could open the network to itself.
Usage: apply_net_config.py <ram_server.cpp> <ram_memory.hpp>"""
import sys
srv, mem = sys.argv[1], sys.argv[2]

s = open(srv).read()
old = '''        if (private_service(in["service"].s)) { out = bad(403, "service " + in["service"].s + " is not served"); return true; }
        out = Reply{200, "{\\"ok\\":true,\\"id\\":" + std::to_string(MEM.write('''
new = '''        if (private_service(in["service"].s)) { out = bad(403, "service " + in["service"].s + " is not served"); return true; }
        if (in["service"].s == "pond" && in["variable"].s == "config") {   // [NET_CONFIG_IS_THE_RAM_MANAGERS_V1]
            out = bad(403, "(pond, config) is the RAM manager's: it is written from its net.json, never over the network"); return true; }
        out = Reply{200, "{\\"ok\\":true,\\"id\\":" + std::to_string(MEM.write('''
if "NET_CONFIG_IS_THE_RAM_MANAGERS_V1" not in s:
    assert s.count(old) == 1, "write pattern not found in " + srv
    s = s.replace(old, new); open(srv, "w").write(s)

m = open(mem).read()
old = '''                if (!private_too && !r->service.empty() && r->service[0] == '#') continue;'''
new = old + '''
                if (!private_too && r->service == "pond" && r->variable == "config") continue;   // [NET_CONFIG_IS_THE_RAM_MANAGERS_V1]'''
if "NET_CONFIG_IS_THE_RAM_MANAGERS_V1" not in m:
    assert m.count(old) == 1, "remove pattern not found in " + mem
    m = m.replace(old, new); open(mem, "w").write(m)
print("ok")
