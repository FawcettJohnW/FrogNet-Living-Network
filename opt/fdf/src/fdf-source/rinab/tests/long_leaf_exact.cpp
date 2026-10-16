// long_leaf_exact.cpp -- [LONG_LEAF_ONE_PASS_V1] the type semtpl::leaf_type gives every string, printed one per line.
// Built twice -- against the platform as vendored (platform/ribbit-v0.63) and as patched (build/platform) -- and the
// two outputs must be identical (tools/long-leaf-check.sh). The strings: 300,000 generated from an alphabet weighted
// toward what a host, an interface, an enum or an address is made of, lengths 0..400, plus edge cases.
#include "semtpl.hpp"
#include <cstdio>
#include <random>
#include <string>
#include <vector>
int main() {
    std::vector<std::string> cases = {
        std::string(65, 'a'), "  " + std::string(70, 'b') + ".com  ", "FrogNetHost" + std::string(60, 'x'), std::string(80, 'A'),
        "-" + std::string(70, 'a') + ".b", std::string(70, 'a') + ".b-", std::string(70, '1') + ".2", "\t" + std::string(66, 'z') + ".io\n",
        std::string(66, 'x') + "\xc2\xa0", "\xc2\xa0" + std::string(66, 'x') + ".com", "eth" + std::string(70, '0'), std::string(70, 'Q') + "_9",
        std::string(65, '.'), "a" + std::string(64, '.') + "a", "FrogNetHost.example" + std::string(50, 'q'), std::string(100, ' ') };
    const char* alpha[] = {"a", "Z", "q", "0", "7", ".", "-", "_", " ", "\t", "\n", "\x1d", "\xc2\xa0", "\xe2\x80\x83", "FrogNetHost", "eth", "/", ":"};
    std::mt19937_64 r(20261016);
    for (int n = 0; n < 300000; ++n) {
        std::string s; const int len = int(r() % 400);
        const int bias = int(r() % 4);                     // some strings are mostly host-shaped
        while (int(s.size()) < len) { int k = int(r() % 18); if (bias == 0 && k > 6) k = int(r() % 7); s += alpha[k]; }
        cases.push_back(s);
    }
    for (auto& c : cases) { pyv::Obj o = pyv::Str{c}; puts(semtpl::leaf_type(o, "").c_str()); puts(semtpl::leaf_type(o, "peer_name").c_str()); puts(semtpl::leaf_type(o, "status").c_str()); }
}
