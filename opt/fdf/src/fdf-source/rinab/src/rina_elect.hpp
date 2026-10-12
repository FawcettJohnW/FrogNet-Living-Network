// rina_elect.hpp -- [MR_RANKED_BY_CAPABILITY_V1] role elections, one pure function over the machines' own records
// (machine identity -> its record). Every member runs it over the same records and gets the same answer.
// A capable machine states "<key><rloc>@<cores>@<mem_mb>;" (key "cap.mr=" for the map-resolver, "cap.rtr=" for the
// RTR, [RTR_IS_A_CAPABILITY_V1]). The most capable wins (more cores, then more memory); equal capability: the lowest
// machine identity. Returns the winner's RLOC and identity, or "" when no machine is capable.
#pragma once
#include <cstdlib>
#include <map>
#include <string>
#include <utility>
namespace rina_elect {
inline std::pair<std::string, std::string> role(const std::map<std::string, std::string>& node, const std::string& key) {
    std::string rloc, who_won; long best_cores = -1, best_mem = -1;
    for (auto& [who, rec] : node) {                            // identity order: a tie keeps the earlier (lower) identity
        const size_t p = rec.find(key); if (p == std::string::npos) continue;
        const std::string v = rec.substr(p + key.size(), rec.find(';', p) - p - key.size());
        const size_t a = v.find('@'), b = a == std::string::npos ? a : v.find('@', a + 1);
        const long cores = a == std::string::npos ? 0 : atol(v.c_str() + a + 1), mem = b == std::string::npos ? 0 : atol(v.c_str() + b + 1);
        if (cores > best_cores || (cores == best_cores && mem > best_mem)) { best_cores = cores; best_mem = mem; rloc = v.substr(0, a); who_won = who; }
    }
    return {rloc, who_won};
}
inline std::string map_resolver(const std::map<std::string, std::string>& node) { return role(node, "cap.mr=").first; }
inline std::pair<std::string, std::string> rtr(const std::map<std::string, std::string>& node) { return role(node, "cap.rtr="); }
}  // namespace rina_elect
