// identical_writes.cpp -- [HOST_COALESCE_V1] identical writes from different sessions, in flight together: every caller
// is answered ok, and the memory holds exactly the value written, once. Byte-identical writes may be executed once and
// share one id (coalescing); because a write replaces its cell, the result is the same as executing each.
// build/run: tools/memory-order-check.sh
// usage: identical_writes HOST:PORT SESSIONS WRITES
#include "frogram.hpp"
#include <atomic>
#include <cstdio>
#include <set>
#include <mutex>
#include <thread>
#include <vector>
static const std::string API = "/FawcettInnovations.RINA.ram_interface.php";
int main(int argc, char** argv) {
    if (argc != 4) { fprintf(stderr, "usage: identical_writes HOST:PORT SESSIONS WRITES\n"); return 2; }
    const std::string where = argv[1]; const int S = atoi(argv[2]), N = atoi(argv[3]);
    const size_t c = where.rfind(':'); const std::string host = where.substr(0, c); const int port = atoi(where.c_str() + c + 1);
    const std::string svc = "same" + std::to_string(time(nullptr));
    std::atomic<long> calls{0}, bad{0}; std::mutex m; std::set<uint64_t> ids; std::atomic<int> ready{0};
    std::vector<std::thread> ts;
    for (int k = 0; k < S; ++k) ts.emplace_back([&] {
        try { frogram::Session s(host, port, API); frogram::Memory mem(s, API);
              ++ready; while (ready.load() < S) std::this_thread::yield();
              for (int i = 0; i < N; ++i) { uint64_t id = mem.write(svc, "v", "i", "{\"x\":1}"); ++calls; std::lock_guard<std::mutex> g(m); ids.insert(id); } }
        catch (const std::exception& e) { fprintf(stderr, "session error: %s\n", e.what()); ++bad; } });
    for (auto& t : ts) t.join();
    frogram::Session s(host, port, API); frogram::Memory mem(s, API);
    auto cells = mem.read(svc, "", "", -1, 0);
    const bool one = cells.size() == 1 && cells[0].bag["x"].n == 1;
    printf("identical writes: sessions=%d calls=%ld distinct_ids=%zu (coalesced %ld) cells=%zu value_ok=%s errors=%ld\n",
           S, calls.load(), ids.size(), calls.load() - long(ids.size()), cells.size(), one ? "yes" : "NO", bad.load());
    const bool ok = bad == 0 && calls == long(S) * N && one;
    printf("RESULT %s identical_writes\n", ok ? "PASS" : "FAIL"); return ok ? 0 : 1;
}
