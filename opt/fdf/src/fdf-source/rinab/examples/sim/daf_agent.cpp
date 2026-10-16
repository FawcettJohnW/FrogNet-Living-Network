// daf_agent.cpp -- [DAF_SIM_AGENT_V1] one simulated node's session to a RAM host's DAF region, driven over a pipe.
// A program that is not C++ (the FrogNet simulator, in Python) gives each simulated node its own agent, so every node
// has its own FNW1 session -- its own liveness -- exactly as a real member does. (The plain-HTTP front has no session,
// so it cannot carry membership: op=daf refuses it.)
//   stdin, one JSON object per line:  {"fn": ..., ...}              -> one op=daf call; its answer, one line, on stdout
//                                      {"watch": [D, name, rev, s]}  -> a held read; {"rev": N} (0: nothing changed)
//   end of input, or the process killed: the session ends, and with it everything the node held.
// usage: daf_agent HOST:PORT
#include "daf_client.hpp"
#include <cstdio>
#include <iostream>
#include <string>
using daf::Json;
int main(int argc, char** argv) {
    if (argc != 2) { fprintf(stderr, "usage: daf_agent HOST:PORT\n"); return 2; }
    std::ios::sync_with_stdio(false);
    daf::Client c(argv[1]);
    std::string line;
    while (std::getline(std::cin, line)) {
        if (line.empty()) continue;
        std::string out;
        try {
            if (line.compare(0, 9, "{\"watch\":") == 0) {
                Json w = Json::parse(line)["watch"];
                uint64_t got = 0;
                got = c.watch(w[0].s, w[1].s, uint64_t(w[2].n), w[3].n);   // 0: the wait ran out
                out = "{\"rev\":" + std::to_string((unsigned long long)got) + "}";
            } else out = c.raw(line);
        } catch (const std::exception& e) { out = "{\"ok\":false,\"error\":\"EAGENT\",\"why\":" + daf::q(e.what()) + "}"; }
        std::cout << out << '\n' << std::flush;
    }
    return 0;
}
