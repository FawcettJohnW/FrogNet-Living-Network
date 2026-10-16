// daf_agent.cpp -- [DAF_SIM_AGENT_V1] one simulated node's session to its area's DAF region, driven over a pipe.
// A program that is not C++ (the FrogNet simulator, in Python) gives each simulated node its own agent, so every node
// has its own FNW1 session -- its own liveness -- exactly as a real member does. (The plain-HTTP front has no session,
// so it cannot carry membership: op=daf refuses it.)
// [DAF_FROG_V1] The region is found as every FD&F participant finds it (RINA_RAM, or the area's one name): the agent is
// given no host.
//   stdin, one JSON object per line:  {"fn": ..., ...}           -> one op=daf call; its answer, one line, on stdout
//                                      {"watch": [D, name, rev]}  -> a held read, answered by the change: {"rev": N}
//   end of input, or the process killed: the session ends, and with it everything the node held.
// usage: daf_agent
#include "daf_client.hpp"
#include <cstdio>
#include <iostream>
#include <string>
using daf::Json;
int main(int argc, char**) {
    if (argc != 1) { fprintf(stderr, "usage: daf_agent   (the region: RINA_RAM, or the area's name)\n"); return 2; }
    std::ios::sync_with_stdio(false);
    daf::Client c;
    std::string line;
    while (std::getline(std::cin, line)) {
        if (line.empty()) continue;
        std::string out;
        try {
            if (line.compare(0, 9, "{\"watch\":") == 0) {
                Json w = Json::parse(line)["watch"];
                out = "{\"rev\":" + std::to_string((unsigned long long)c.watch(w[0].s, w[1].s, uint64_t(w[2].n))) + "}";
            } else out = c.raw(line);
        } catch (const std::exception& e) { out = "{\"ok\":false,\"error\":\"EAGENT\",\"why\":" + daf::q(e.what()) + "}"; }   // said, on its line
        std::cout << out << '\n' << std::flush;
    }
    return 0;
}
