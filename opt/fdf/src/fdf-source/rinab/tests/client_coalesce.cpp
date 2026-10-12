// client_coalesce.cpp -- [CLIENT_COALESCE_LOCKLESS_V1] proof: K threads of ONE session make the same held read at once
// (so all are in flight together), then a second session writes. Every thread must get the written row, and the session
// must count K-1 calls served by the one request (stats().coalesced).
#include <cstdio>
#include <thread>
#include <vector>
#include <atomic>
#include "frogram.hpp"
int main(int argc, char** argv) {
    const int K = argc > 1 ? atoi(argv[1]) : 8; const int port = argc > 2 ? atoi(argv[2]) : 8859;
    const std::string api = "/FawcettInnovations.RINA.ram_interface.php";
    frogram::Session a("127.0.0.1", port, api), w("127.0.0.1", port, api);
    std::atomic<int> got{0}, bad{0}; std::vector<std::thread> th;
    for (int i = 0; i < K; ++i) th.emplace_back([&] {
        try { auto r = a.call("GET", api + "?op=read&service=t&variable=c&instance=i&after=0&wait_s=10", "", 15);
              if (r["rows"].a.size() == 1 && r["rows"].a[0]["bag"]["x"].n == 7) got++; else bad++; }
        catch (const std::exception& e) { fprintf(stderr, "%s\n", e.what()); bad++; } });
    std::this_thread::sleep_for(std::chrono::milliseconds(500));
    w.call("POST", api + "?op=write", "{\"service\":\"t\",\"variable\":\"c\",\"instance\":\"i\",\"bag\":{\"x\":7}}");
    for (auto& t : th) t.join();
    const auto st = a.stats();
    printf("held reads answered with the written row: %d of %d (wrong or failed %d); calls served by another's request: %llu\n",
           got.load(), K, bad.load(), (unsigned long long)st.coalesced);
    return got.load() == K && st.coalesced == uint64_t(K - 1) ? 0 : 1;
}
