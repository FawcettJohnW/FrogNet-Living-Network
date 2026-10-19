// test_rowcodec.cpp -- [ROWS_ON_THE_SEMANTIC_WIRE_V1] rows through the encoder and decoder must come out the same rows:
// the same ids, times, coordinates, and bags equal as JSON. Shapes mixed and interleaved, strings spliced, values
// UTF-8 and escaped. Prints bytes on the wire per row against the rows' own layout.
#include "rowcodec.hpp"
#include "frogram.hpp"
#include <cstdio>
#include <random>
int main() {
    std::mt19937 R(5); rowcodec::Encoder enc; rowcodec::Decoder dec;
    uint64_t raw_bytes = 0, wire_bytes = 0, rows_n = 0; int bad = 0;
    auto b64 = [&](int n) { static const char* A = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"; std::string s; for (int i = 0; i < n; i++) s += A[R() % 64]; return s; };
    uint64_t id = 1000;
    for (int batch = 0; batch < 3000; batch++) {
        std::string body; std::vector<std::tuple<uint64_t, double, std::string, std::string, std::string, std::string>> want;
        const int k = 1 + int(R() % 8);
        for (int j = 0; j < k; j++) {
            const int kind = int(R() % 10); std::string vv, iv, bag;
            if (kind < 7) { vv = "msg.boxA"; iv = std::to_string(id % 64);
                bag = "{\"b\":\"boxA\",\"e\":0,\"n\":" + std::to_string(id) + ",\"o\":0,\"pr\":\"\",\"q\":\"" + b64(14) + "eHh4eHh4eHh4eHh4eHh4eA==\",\"qos\":1,\"r\":0,\"si\":\"lp" + std::to_string(R() % 4) + "\",\"sp\":40335,\"t\":\"load/" + std::to_string(R() % 4) + "\"}"; }
            else if (kind < 9) { vv = "retain"; iv = "r/\xC3\xA9" + std::to_string(R() % 3);
                bag = "{\"live\":" + std::to_string(R() % 2) + ",\"p\":\"\\u00e9 \\\"x\\\" \xF0\x9F\x98\x80" + b64(int(R() % 6)) + "\",\"list\":[1,2.5,null,true]}"; }
            else { vv = "applied.boxA"; iv = "boxB"; bag = "{\"n\":" + std::to_string(R() % 100000) + "}"; }
            const double up = 1792339836.0 + double(id) / 1000.0; uint64_t ub; std::memcpy(&ub, &up, 8);
            rowcodec::detail::put_raw(body, id, ub, "mqtt", vv, iv, bag);
            want.emplace_back(id, up, "mqtt", vv, iv, bag); id++;
        }
        const std::string w = enc.encode(body); const std::string back = dec.decode(w);
        raw_bytes += body.size(); wire_bytes += w.size(); rows_n += want.size();
        size_t p = 0; rowcodec::detail::Raw r; size_t i = 0;
        while (rowcodec::detail::next_raw(back, p, r)) {
            if (i >= want.size()) { bad++; break; }
            auto& [wid, wup, wsv, wvv, wiv, wbag] = want[i++];
            double up; std::memcpy(&up, &r.updated_bits, 8);
            const bool same = r.id == wid && up == wup && r.service == wsv && r.variable == wvv && r.instance == wiv
                && frogram::Json::parse(std::string(r.bag)).type == frogram::Json::Obj
                && semtpl::python_eq(*semtpl::detail::json_try(std::string(r.bag)), *semtpl::detail::json_try(wbag));
            if (!same) { if (bad < 4) fprintf(stderr, "row %llu differs: got %.*s / want %s\n", (unsigned long long)wid, int(r.bag.size()), r.bag.data(), wbag.c_str()); bad++; }
        }
        if (i != want.size()) bad++;
    }
    printf("%s: %llu rows, %d wrong; %.1f B per row on the wire against %.1f B as whole rows\n", bad ? "FAIL" : "PASS",
           (unsigned long long)rows_n, bad, double(wire_bytes) / double(rows_n), double(raw_bytes) / double(rows_n));
    return bad != 0;
}
