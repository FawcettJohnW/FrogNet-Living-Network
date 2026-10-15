// seen_race.cpp -- does the RAM host answer one session with another session's result?
// `seen` answers "the address this session arrives from" (rina_region.cpp:515-520). Each thread opens its own session
// and asks `seen` ITERS times; every answer must be this process's own source address (SRC_IP). Run two copies at once
// from different source addresses (srcbind.so). A wrong answer means an identical request from another session was
// executed once and its answer handed to this one ([HOST_COALESCE_V1]).
// build/run: tools/memory-order-check.sh
// usage: seen_race HOST:PORT THREADS ITERS   (env SRC_IP = expected address)
#include "rina_region_client.hpp"
#include <atomic>
#include <cstdio>
#include <thread>
#include <vector>
int main(int argc, char** argv) {
    if (argc != 4 || !getenv("SRC_IP")) { fprintf(stderr, "usage: SRC_IP=a.b.c.d seen_race HOST:PORT THREADS ITERS\n"); return 2; }
    const std::string where = argv[1], me = getenv("SRC_IP"); const int threads = atoi(argv[2]), iters = atoi(argv[3]);
    std::atomic<long> right{0}, wrong{0}, errors{0}; std::atomic<int> ready{0};
    std::vector<std::thread> ts;
    for (int t = 0; t < threads; ++t) ts.emplace_back([&] {
        try {
            rina_region::Client c(where);
            ++ready; while (ready.load() < threads) std::this_thread::yield();   // all sessions up, then all ask at once
            for (int i = 0; i < iters; ++i) {
                frogram::Json r = c.call("{\"op\":\"seen\"}");
                if (!r["ok"].b) { ++errors; continue; }
                (r["addr"].s == me ? right : wrong)++;
                if (r["addr"].s != me && wrong.load() <= 3) fprintf(stderr, "WRONG: expected %s, got %s\n", me.c_str(), r["addr"].s.c_str());
            }
        } catch (const std::exception& e) { fprintf(stderr, "session error: %s\n", e.what()); ++errors; }
    });
    for (auto& t : ts) t.join();
    printf("src=%s answers=%ld right=%ld wrong=%ld errors=%ld\n", me.c_str(), right.load() + wrong.load(), right.load(), wrong.load(), errors.load());
    return wrong.load() ? 1 : 0;
}
