// coalesce_held.cpp -- [HOST_COALESCE_V1] proof with requests that overlap for certain: K sessions each issue the SAME
// held read (op=read ... after=0 wait_s=10 on a cell nobody has written), so the first parks and the rest arrive while it
// is in flight; then one write. Every session must get the written row; the host counts how many were answered by the
// one execution (printed when it exits).
#include <cstdio>
#include <thread>
#include <vector>
#include <atomic>
#include "frogram.hpp"
int main(int argc, char** argv) {
    const int K = argc > 1 ? atoi(argv[1]) : 8; const int port = argc > 2 ? atoi(argv[2]) : 8858;
    const std::string api = "/FawcettInnovations.RINA.ram_interface.php";
    std::vector<std::unique_ptr<frogram::Session>> ss; for (int i = 0; i <= K; ++i) ss.emplace_back(new frogram::Session("127.0.0.1", port, api));
    std::atomic<int> got{0}, bad{0}; std::vector<std::thread> th;
    for (int i = 0; i < K; ++i) th.emplace_back([&, i] {
        try { auto r = ss[i]->call("GET", api + "?op=read&service=t&variable=v&instance=i&after=0&wait_s=10", "", 15);
              if (r["rows"].a.size() == 1 && r["rows"].a[0]["bag"]["x"].n == 42) got++; else bad++; }
        catch (const std::exception& e) { fprintf(stderr, "session %d: %s\n", i, e.what()); bad++; } });
    std::this_thread::sleep_for(std::chrono::milliseconds(500));      // every held read is in flight
    ss[K]->call("POST", api + "?op=write", "{\"service\":\"t\",\"variable\":\"v\",\"instance\":\"i\",\"bag\":{\"x\":42}}");
    for (auto& t : th) t.join();
    printf("held reads answered with the written row: %d of %d (wrong or failed: %d)\n", got.load(), K, bad.load());
    return got.load() == K ? 0 : 1;
}
