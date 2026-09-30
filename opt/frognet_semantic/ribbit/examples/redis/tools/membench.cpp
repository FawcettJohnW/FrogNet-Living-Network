// microbenchmark of the memory alone: ns per write/read, no wire
#include "../src/memory.hpp"
#include <chrono>
#include <cstdio>
int main() {
    Memory m; m.init({"db0"});
    const int N = 10000, R = 200000;
    std::vector<std::string> keys; for (int i = 0; i < N; i++) keys.push_back("key:" + std::to_string(i));
    Bag b; b.kind = Kind::String; b.s = "xxx";
    auto t0 = std::chrono::steady_clock::now();
    for (int i = 0; i < R; i++) m.write("db0", keys[i % N], "", b);
    auto t1 = std::chrono::steady_clock::now();
    Cell c; long hits = 0;
    for (int i = 0; i < R; i++) hits += m.read("db0", keys[i % N], "", c);
    auto t2 = std::chrono::steady_clock::now();
    BagPtr bp = std::make_shared<const Bag>(b);
    for (int i = 0; i < R; i++) m.write("db0", keys[i % N], "", bp);
    auto t3 = std::chrono::steady_clock::now();
    auto ns = [](auto a, auto b2) { return std::chrono::duration_cast<std::chrono::nanoseconds>(b2 - a).count() / (double)R; };
    printf("write(Bag) %.0f ns  read %.0f ns  write(BagPtr) %.0f ns  hits=%ld\n", ns(t0, t1), ns(t1, t2), ns(t2, t3), hits);
}
