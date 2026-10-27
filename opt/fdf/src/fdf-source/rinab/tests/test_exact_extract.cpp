// test_exact_extract.cpp -- [EXACT_EXTRACTION_V1] the structural check against the probe it replaces. For many pairs
// (the answer a template was learned from, a later answer) it asks both: the probe (extract, rebuild the whole answer,
// compare as JSON) and extract_exact (one walk). The contract: extract_exact never holds an answer the rebuild would not
// give back exactly. It may send RAW an answer the probe would have held (counted and printed, never silent).
//   usage: test_exact_extract PAIRS SEED      (FROGNET_BLOB_ROOT should name a scratch directory)
#include "semtpl.hpp"
#include <cstdio>
#include <cstdlib>
#include <random>
#include <string>
using namespace semtpl;
static std::mt19937_64 rng;
static int R(int n) { return int(rng() % uint64_t(n)); }
static const char* KEYS[] = {"a", "b", "ok", "rev", "members", "facts", "ip", "value", "x_1", "a.b", "9bad", "name"};
static std::string gen(int depth);
static std::string scalar() {
    switch (R(7)) { case 0: return "null"; case 1: return R(2) ? "true" : "false"; case 2: return std::to_string(R(1000) - 500);
        case 3: return std::to_string(R(100)) + ".5"; case 4: return "\"10." + std::to_string(R(250)) + ".0.1\"";
        case 5: return "\"s" + std::to_string(R(50)) + "\""; default: return "1.0"; }
}
static std::string list(int depth, int n) { std::string o = "["; for (int i = 0; i < n; ++i) o += (i ? "," : "") + gen(depth); return o + "]"; }
static std::string dict(int depth, int n) {
    std::string o = "{"; std::vector<std::string> used;
    for (int i = 0; i < n; ++i) { std::string k = KEYS[R(12)]; bool dup = false; for (auto& u : used) dup |= u == k; if (dup) continue; used.push_back(k);
        o += std::string(used.size() > 1 ? "," : "") + "\"" + k + "\":" + gen(depth); }
    return o + "}";
}
static std::string gen(int depth) {
    if (depth <= 0) return scalar();
    switch (R(6)) { case 0: case 1: return scalar(); case 2: return list(depth - 1, R(4));
        case 3: return list(depth - 1, R(2) ? 300 + R(200) : 0); default: return dict(depth - 1, R(5)); }
}
// a later answer: the same text with some values changed, or a different shape altogether
static std::string mutate(const std::string& s) {
    switch (R(5)) {
        case 0: return s;
        case 1: { std::string t = s; for (auto& c : t) if (c >= '0' && c <= '8' && R(5) == 0) ++c; return t; }   // values move
        case 2: { std::string t = s; size_t p = t.find("[{"); if (p != std::string::npos) { size_t e = t.find("]", p); if (e != std::string::npos) t = t.substr(0, p) + "[]" + t.substr(e + 1); } return t; }
        case 3: { std::string t = s; size_t p = t.find("null"); if (p != std::string::npos) t.replace(p, 4, R(2) ? "{\"q\":1}" : "[1,2]"); return t; }
        default: return R(2) ? dict(3, 1 + R(5)) : gen(3);
    }
}
int main(int argc, char** argv) {
    if (argc != 3) { fprintf(stderr, "usage: test_exact_extract PAIRS SEED\n"); return 2; }
    const long N = atol(argv[1]); rng.seed(strtoull(argv[2], nullptr, 10));
    long both_hold = 0, both_raw = 0, now_raw = 0, now_hold_wrong = 0, learn_declined = 0;
    for (long n = 0; n < N; ++n) {
        const std::string sample = R(4) == 0 ? gen(3) : dict(3, 1 + R(6)); const std::string later = mutate(sample);
        Dict frag; try { frag = learn("json", sample, true); } catch (const std::exception&) { ++learn_declined; continue; }
        bool probe = false;
        try { auto dyn = extract(frag, later, true);
              if (dyn) { List vals; for (auto& kv : *dyn) vals.push_back(kv.second);
                  std::string rb = rebuild_reply(frag, vals); auto x = detail::json_try(later), y = detail::json_try(rb);
                  probe = (x && y) ? python_eq(*x, *y) : rb == later; } }
        catch (const std::exception&) { probe = false; }
        bool exact = false, faithful = false;
        try { auto f = extract_exact(frag, later);
              if (f) { exact = true; List vals; for (auto& kv : *f) vals.push_back(kv.second);
                  std::string rb = rebuild_reply(frag, vals); auto x = detail::json_try(later), y = detail::json_try(rb);
                  faithful = (x && y) ? python_eq(*x, *y) : rb == later; } }
        catch (const std::exception& e) { if (exact) faithful = false; }
        if (exact && !faithful) { ++now_hold_wrong; if (now_hold_wrong <= 5) printf("WRONG: held but not given back\n  sample %.200s\n  later  %.200s\n", sample.c_str(), later.c_str()); }
        else if (exact && probe) ++both_hold;
        else if (!exact && !probe) ++both_raw;
        else if (!exact && probe) { ++now_raw; if (now_raw <= 5) printf("now RAW, the probe held it:\n  sample %.200s\n  later  %.200s\n", sample.c_str(), later.c_str()); }
        else ++both_hold;   // exact, faithful, probe said no (the probe threw): held correctly
    }
    printf("pairs %ld: both hold %ld, both RAW %ld, now RAW where the probe held %ld, held but wrong %ld, learn declined %ld\n",
           N, both_hold, both_raw, now_raw, now_hold_wrong, learn_declined);
    return now_hold_wrong ? 1 : 0;
}
