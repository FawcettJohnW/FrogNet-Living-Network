#!/usr/bin/env python3
"""apply_bound_port.py RAM_SERVER_CPP -- [BOUND_PORT_SAID_V1] the host says the address it actually bound: with
--listen IP:0 the system picks the port, and "listening on IP:0" told nobody where it is. The region's listening hook
(its [FDF_VERSION_V1] line, and the mapping registration) now gets IP:<the bound port>. A program that launches a host
on port 0 (the kart lobby's race servers) reads the port from that line. Needs apply_host_announce.py first."""
import sys
p = sys.argv[1]; s = open(p).read(); tag = "[BOUND_PORT_SAID_V1]"
if tag in s: print("bound port already applied"); sys.exit(0)
old = "{ extern void rina_host_listening(const std::string&); rina_host_listening(listen_on); }"
assert s.count(old) == 1, "apply_bound_port: the listening hook call was not found exactly once (apply_host_announce.py first)"
new = ("{ extern void rina_host_listening(const std::string&); sockaddr_in bnd{}; socklen_t bl = sizeof bnd; std::string bound = listen_on;   // %s\n"
       "      if (::getsockname(srv, reinterpret_cast<sockaddr*>(&bnd), &bl) == 0) bound = listen_on.substr(0, c) + \":\" + std::to_string(ntohs(bnd.sin_port));\n"
       "      rina_host_listening(bound); }") % tag
s = s.replace(old, new, 1); open(p, "w").write(s); print("%s applied" % tag)
