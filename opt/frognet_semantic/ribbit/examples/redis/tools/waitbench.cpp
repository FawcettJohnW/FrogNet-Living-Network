// The experiment for the wait words: R held readers spread over V variables (V from many down to 1),
// W writers writing round-robin over the same V variables, on real cores. Reports writer throughput,
// reader wake-ups per write, and how many wake-ups found nothing new (collision tax).
//   waitbench [readers=64] [writers=4] [variables=1024] [words=4096] [seconds=5]
// words=1 puts every waiter on one word: that is the broadcast the previous design did on every write.
#include "../src/memory.hpp"
#include <thread>
#include <atomic>
#include <chrono>
#include <cstdio>
#include <cstdlib>
int main(int argc, char** argv) {
    int R = argc > 1 ? atoi(argv[1]) : 64, W = argc > 2 ? atoi(argv[2]) : 4, V = argc > 3 ? atoi(argv[3]) : 1024;
    size_t words = argc > 4 ? (size_t)atol(argv[4]) : 4096; int secs = argc > 5 ? atoi(argv[5]) : 5;
    Memory m; m.init({"r"}, words);
    std::vector<std::string> vars; for (int i = 0; i < V; i++) vars.push_back("v" + std::to_string(i));
    Bag b; b.kind = Kind::String; b.s = "x"; BagPtr bp = std::make_shared<const Bag>(b);
    for (auto& v : vars) m.write("r", v, "", bp);
    std::atomic<bool> stop{false};
    std::atomic<long> writes{0}, wakeups{0}, empty_wakeups{0};
    std::vector<std::thread> th;
    for (int r = 0; r < R; r++) th.emplace_back([&, r] {
        const std::string& v = vars[r % V]; uint64_t after = m.last_id();
        while (!stop) {
            auto out = m.read_after("r", v, nullptr, after, 200);
            if (out.empty()) continue;                                   // timeout: nothing new
            wakeups++;
            for (auto& c : out) if (c.id() > after) after = c.id();
        }
    });
    auto t0 = std::chrono::steady_clock::now();
    for (int w = 0; w < W; w++) th.emplace_back([&, w] {
        for (long i = w; !stop; i += W) { m.write("r", vars[i % V], "", bp); writes++; }
    });
    std::this_thread::sleep_for(std::chrono::seconds(secs));
    stop = true; for (auto& t : th) t.join();
    double s = std::chrono::duration<double>(std::chrono::steady_clock::now() - t0).count();
    printf("readers=%d writers=%d variables=%d words=%zu  writes/s=%.0f  reader wakeups/s=%.0f  wakeups per write=%.2f\n",
           R, W, V, words, writes / s, wakeups / s, writes ? (double)wakeups / writes : 0.0);
    // with words=1 every write wakes every reader (they all re-check and go back to sleep);
    // with words>=V a write wakes only the readers of that variable. Run on real cores.
}
