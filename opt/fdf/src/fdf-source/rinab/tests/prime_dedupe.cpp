// prime_dedupe.cpp IN OUT -- [PRIMED_TEMPLATES_V1] one exemplar per TEMPLATE, keyed exactly as the engines key a template
// (method + semtpl::canonical_semantic_key). A learning run records an exemplar each time a client learns; this keeps
// the first exemplar of each key and reports how many keys the store holds.
#include <cstdio>
#include <map>
#include <string>
#include "fnwp_engine.hpp"
int main(int argc, char** argv) {
    if (argc < 3) { fprintf(stderr, "usage: prime-dedupe IN OUT\n"); return 2; }
    setenv("RIBBIT_TEMPLATES", argv[1], 1);
    const auto& all = fnwp::detail::primed();
    std::map<std::string, const fnwp::detail::Exemplar*> keep;
    for (const auto& e : all) keep.emplace(e.m + "\n" + semtpl::canonical_semantic_key(e.m, e.p, e.b), &e);
    FILE* out = fopen(argv[2], "w"); if (!out) { perror(argv[2]); return 1; }
    for (const auto& kv : keep) { const auto* e = kv.second;
        fprintf(out, "{\"m\":%s,\"p\":%s,\"b\":%s,\"a\":%s}\n", fnwp::detail::ex_quote(e->m).c_str(), fnwp::detail::ex_quote(e->p).c_str(),
                fnwp::detail::ex_quote(e->b).c_str(), fnwp::detail::ex_quote(e->a).c_str()); }
    fclose(out); printf("%zu exemplars -> %zu templates\n", all.size(), keep.size()); return 0;
}
