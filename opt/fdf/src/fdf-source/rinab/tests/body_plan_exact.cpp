// body_plan_exact.cpp -- [BODY_PLAN_V1] proof: a request body rendered from the per-template skeleton is byte-identical to
// rebuild_json + dumps_ascii, for field orders with nesting, the rows list, a field given twice, and conflicting paths.
#include <cstdio>
#include "fnwp_engine.hpp"
using namespace pyv;
int main() {
    std::vector<std::vector<std::string>> fos = {{"op", "proc", "after"}, {"op", "sdus"}, {"a.b", "a.c", "d"}, {"rows.x", "rows.y", "z"},
        {"a", "a.b"}, {"a.b", "a"}, {"k", "k"}, {"x.y.z", "x.w", "x.y.q"}};
    int bad = 0, n = 0;
    for (auto& fo : fos) {
        semtpl::Fields jv; List vals;
        for (size_t i = 0; i < fo.size(); ++i) { Obj v = (i % 3 == 0) ? Obj(Str{"v\"" + std::to_string(i)}) : (i % 3 == 1) ? Obj(Int(int64_t(i * 7))) : Obj(List{Obj(Str{"q"}), Obj(None{})});
            jv.emplace_back(fo[i], v); }
        for (size_t i = 0; i < fo.size(); ++i) { const Obj* v = nullptr; for (size_t j = jv.size(); j-- > 0;) if (jv[j].first == fo[i]) { v = &jv[j].second; break; } vals.push_back(v ? *v : Obj(None{})); }
        std::string want = semtpl::dumps_ascii(semtpl::rebuild_json(vals, fo), false);
        Obj sk = semtpl::body_skeleton(fo);
        std::string got = semtpl::rebuild_body_pre("json", fo, jv, &sk);
        ++n; if (got != want) { ++bad; printf("DIFF\n want %s\n got  %s\n", want.c_str(), got.c_str()); }
    }
    printf("%d of %d field orders render identically\n", n - bad, n);
}
