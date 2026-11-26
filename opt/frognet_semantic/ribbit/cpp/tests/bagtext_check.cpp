// bagtext_check -- [A_STORED_BAG_IS_ASCII_V1] ribbit::stored_bag(text) is, byte for byte, the wire's own form of the same
// value, semtpl::dumps_ascii(semcodec::json_loads(text), false): for texts as writers send them (spaced as Python's
// json.dumps does, compact, raw UTF-8, escaped, every escape, pairs, numbers of every kind) and for random values
// written both ways. And it is ASCII, and storing a stored bag changes nothing.
#include "bagtext.hpp"
#include "semtpl.hpp"
#include <iostream>
#include <random>
static int fails = 0, n = 0;
static void one(const std::string& text) {
    ++n;
    std::string want = semtpl::dumps_ascii(semcodec::json_loads(text), false);
    std::string got = ribbit::stored_bag(text);
    bool ascii = true; for (char c : got) if (uint8_t(c) < 0x20 || uint8_t(c) > 0x7e) ascii = false;
    if (got != want || !ascii || ribbit::stored_bag(got) != got) {
        if (++fails <= 10) std::cout << "FAIL text:   " << text << "\n     wanted: " << want << "\n     got:    " << got << "\n";
    }
}
static std::mt19937_64 rng(1);
static std::string rstr() {
    static const char* parts[] = {"a", "value", "k.dot", "\xc3\xa9", "\xc3\xbcn\xc3\xaf", "q\\\"uote", "back\\\\slash", "\\u00e9", "\\u00E9", "\\ud83d\\ude00", "\xf0\x9f\x98\x80",
                                  "\\n", "\\t", "\\/", "\\b\\f\\r", " sp ace ", "\x7f", "\\u0001", "\xe2\x82\xac", "", "{[,:]}", "1e5", "true"};
    std::string s; int k = int(rng() % 3); for (int i = 0; i <= k; ++i) s += parts[rng() % (sizeof parts / sizeof *parts)]; return "\"" + s + "\"";
}
static std::string rval(int depth, bool spaced) {
    const char* is = spaced ? ", " : ","; const char* ks = spaced ? ": " : ":";
    switch (rng() % (depth > 2 ? 6 : 8)) {
        case 0: return std::to_string(int64_t(rng() % 2001) - 1000);
        case 1: { static const char* f[] = {"0.1", "1.5", "-2.25", "1e-12", "123456.789", "9007199254740993", "1e+16", "1E16", "3.0", "1.50", "-0", "-0.0", "0",
                                            "12345678901234567890123", "1e400", "2.5e-3", "100.0", "1e16", "-1E-7"}; return f[rng() % (sizeof f / sizeof *f)]; }
        case 2: return rstr();
        case 3: return rng() % 2 ? "true" : "false";
        case 4: return "null";
        case 5: return rstr();
        case 6: { std::string o = "["; int k = int(rng() % 4); for (int i = 0; i < k; ++i) o += (i ? is : "") + rval(depth + 1, spaced); return o + "]"; }
        default: { std::string o = "{"; int k = int(rng() % 4); for (int i = 0; i < k; ++i) o += std::string(i ? is : "") + "\"k" + std::to_string(i) + "\\u00e9\"" + ks + rval(depth + 1, spaced); return o + "}"; }
    }
}
int main() {
    one("{\"x\": 711}"); one("{ \"a\" :\t1 ,\n\"b\":[ 1 , 2 ] }"); one("{\"\xc3\xa9\": \"\xc3\xbcn\xc3\xaf\", \"a\": 1}"); one("{\"\\u00e9\": \"\\u00fcn\\u00ef\"}");
    one("{}"); one("[]"); one("{\"a\":{}}"); one("{\"n\":9007199254740993,\"f\":1e16,\"g\":1e-12,\"h\":0.1}");
    for (int i = 0; i < 200000; ++i) one("{\"bag\"" + std::string(i % 2 ? ": " : ":") + rval(0, i % 2) + "}");
    std::cout << (fails ? "RESULT FAIL" : "RESULT PASS") << " bagtext: " << n << " texts, " << fails << " not the wire's own form\n";
    return fails ? 1 : 0;
}
