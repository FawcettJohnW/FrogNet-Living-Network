// order_race.cpp -- can a reader that follows `after` miss a write for good?
// Writers each publish N distinct cells (a fresh instance per write, so nothing is overwritten and nothing coalesces).
// A reader follows the memory the way a participant does: held read with after=A, then A = the largest id it got.
// At the end one full read lists every cell; any id the follower never received is a write it skipped permanently.
//   mode "service":  writers write to different variables, the follower reads the whole service (variable empty)
//   mode "variable": writers write to the SAME variable, the follower reads that variable (the write-window path)
// build/run: tools/memory-order-check.sh
// usage: order_race HOST:PORT MODE WRITERS WRITES_PER_WRITER
#include "frogram.hpp"
#include <atomic>
#include <cstdio>
#include <set>
#include <thread>
#include <vector>
static const std::string API = "/FawcettInnovations.RINA.ram_interface.php";
int main(int argc, char** argv) {
    if (argc != 5) { fprintf(stderr, "usage: order_race HOST:PORT service|variable WRITERS N\n"); return 2; }
    const std::string where = argv[1], mode = argv[2]; const int W = atoi(argv[3]), N = atoi(argv[4]);
    const size_t c = where.rfind(':'); const std::string host = where.substr(0, c); const int port = atoi(where.c_str() + c + 1);
    const std::string svc = "ord" + std::to_string(time(nullptr)) + mode;
    std::atomic<int> done{0};
    std::set<uint64_t> got;
    std::thread follower([&] {
        frogram::Session s(host, port, API); frogram::Memory m(s, API);
        int64_t after = 0;
        const std::string var = mode == "variable" ? "v" : "";
        for (;;) {
            const bool last = done.load() == W;
            for (auto& cell : m.read(svc, var, "", after, last ? 0.0 : 1.0)) { got.insert(cell.id); if (int64_t(cell.id) > after) after = cell.id; }
            if (last) break;
        }
    });
    std::vector<std::thread> ws;
    for (int w = 0; w < W; ++w) ws.emplace_back([&, w] {
        frogram::Session s(host, port, API); frogram::Memory m(s, API);
        const std::string var = mode == "variable" ? "v" : "v" + std::to_string(w);
        for (int i = 0; i < N; ++i) m.write(svc, var, "w" + std::to_string(w) + "-" + std::to_string(i), "{\"i\":" + std::to_string(i) + "}");
        ++done;
    });
    for (auto& t : ws) t.join();
    follower.join();
    frogram::Session s(host, port, API); frogram::Memory m(s, API);
    std::vector<frogram::Cell> all = m.read(svc, mode == "variable" ? "v" : "", "", -1, 0);
    size_t missed = 0;
    for (auto& cell : all) if (!got.count(cell.id)) { if (++missed <= 3) fprintf(stderr, "missed id %llu (%s/%s)\n", (unsigned long long)cell.id, cell.variable.c_str(), cell.instance.c_str()); }
    printf("mode=%s writers=%d writes=%zu followed=%zu missed=%zu\n", mode.c_str(), W, all.size(), got.size(), missed);
    return missed ? 1 : 0;
}
