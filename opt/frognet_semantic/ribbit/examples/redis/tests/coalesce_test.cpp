// [CLIENT_COALESCES_V1] 20 threads send the identical request at once (across a 24 ms path, so they overlap in flight):
// one goes to the host; the others wait at the client and are served by that one response.
#include "frogram.hpp"
#include <cstdio>
#include <set>
#include <thread>
#include <vector>
int main(int, char** argv) {
    frogram::Session s("127.0.0.1", atoi(argv[1]), "/Fawcett.Redis.ram_interface.php");
    std::vector<std::string> ids(20); std::vector<std::thread> th;
    for (int i = 0; i < 20; i++) th.emplace_back([&, i] {
        auto r = s.call("POST", "/Fawcett.Redis.ram_interface.php?op=redis", "{\"new_id\":true,\"n\":\"same-for-all\"}");
        ids[i] = std::to_string((long long)r["id"].n) + ":" + r["token"].s; });
    for (auto& t : th) t.join();
    std::set<std::string> distinct(ids.begin(), ids.end());
    printf("calls 20, distinct answers %zu, coalesced %llu\n", distinct.size(), (unsigned long long)s.stats().coalesced);
    printf("RESULT %s\n", (distinct.size() < 20 && s.stats().coalesced + distinct.size() == 20) ? "GREEN" : "RED");
}
