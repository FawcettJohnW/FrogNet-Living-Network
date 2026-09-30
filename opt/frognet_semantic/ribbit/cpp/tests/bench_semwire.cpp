// S1 performance: ns per operation for semwire encode and parse, single thread, in memory (no socket, no copy out).
// 9 batches per operation; prints median, min and max of the per-batch ns/op. tools/bench_semwire_python.py times the
// same operations on John's core/semcache_wire.py.
#include "semwire.hpp"
#include <algorithm>
#include <chrono>
#include <cstdio>
#include <functional>
#include <vector>

using namespace semwire;
static volatile uint64_t sink;

static void bench(const char* name, long n, const std::function<uint64_t()>& f) {
    std::vector<double> r;
    for (int b = 0; b < 9; ++b) {
        uint64_t acc = 0;
        auto t0 = std::chrono::steady_clock::now();
        for (long i = 0; i < n; ++i) acc += f();
        auto t1 = std::chrono::steady_clock::now();
        sink = sink + acc;
        r.push_back(std::chrono::duration<double, std::nano>(t1 - t0).count() / double(n));
    }
    std::sort(r.begin(), r.end());
    std::printf("%-28s median %9.1f ns/op  min %9.1f  max %9.1f  (9 x %ld)\n", name, r[4], r[0], r[8], n);
}

int main() {
    const std::string h(16, '\x5a'), sid(16, '\x33'), p64(64, 'p'), p4k(4096, 'q'), hdr(40, 'h'), body(1024, 'b');
    const std::string f64 = wrap_req_full(h, p64), f4k = wrap_req_full(h, p4k), frep = wrap_req_repeat(h);
    const std::string fsame = wrap_resp_same(sid), fraw = wrap_resp_raw(sid, 200, hdr, body);
    const std::string fpong = wrap_rtt_pong(7, 1, 2, 3);
    bench("wrap_req_full 64B", 2000000, [&] { return wrap_req_full(h, p64).size(); });
    bench("wrap_req_full 4KiB", 500000, [&] { return wrap_req_full(h, p4k).size(); });
    bench("wrap_req_repeat", 2000000, [&] { return wrap_req_repeat(h).size(); });
    bench("wrap_resp_same", 2000000, [&] { return wrap_resp_same(sid).size(); });
    bench("wrap_resp_raw 40B+1KiB", 1000000, [&] { return wrap_resp_raw(sid, 200, hdr, body).size(); });
    bench("wrap_rtt_pong", 2000000, [&] { return wrap_rtt_pong(7, 1, 2, 3).size(); });
    bench("try_parse REQ_FULL 64B", 5000000, [&] { return try_parse(f64)->payload->size(); });
    bench("try_parse REQ_FULL 4KiB", 5000000, [&] { return try_parse(f4k)->payload->size(); });
    bench("try_parse REQ_REPEAT", 5000000, [&] { return try_parse(frep)->req_hash->size(); });
    bench("try_parse RESP_SAME", 5000000, [&] { return try_parse(fsame)->same_id->size(); });
    bench("try_parse RESP_RAW 40B+1KiB", 5000000, [&] { return try_parse(fraw)->body->size(); });
    bench("try_parse RTT_PONG", 5000000, [&] { return *try_parse(fpong)->daemon_t_reply_ns; });
    return 0;
}
