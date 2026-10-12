"""apply_primed_templates.py FNWP_ENGINE_HPP -- [PRIMED_TEMPLATES_V1] the primed template store (John, 2026-09-18:
"the system is developed using a version of the library with FULL learning capability to get the templates; at client
packaging the portable version(s) are provided along with the PRIMED store").

A template is learned, on both ends, from ONE exchange: the request (method, path, body) and its answer, through the same
function (detail::learn). So the store is the exchanges themselves -- exemplars -- and each end learns from them exactly
as it would have from the wire, with nothing on the wire.

  RIBBIT_TEMPLATES_RECORD=FILE  (learning run) every template a client learns appends its exemplar to FILE, one JSON
                                line {"m","p","b","a"}, by one write(2) on an O_APPEND descriptor (concurrent learners
                                cannot interleave a line)
  RIBBIT_TEMPLATES=FILE         (every run) each ClientEngine and ServerEngine learns every exemplar in FILE when it is
                                made, so the first request of a primed shape goes REQ_FULL/REQ_DIFF, not REQ_RAW. The file
                                is read once per process. Both ends must be given the same file; an exemplar that no
                                longer learns (a template refused) is reported and skipped.
No file named: learning on the wire, exactly as before. Applied at build after apply_client_applier.py."""
import sys
p = sys.argv[1]; s = open(p).read()
def rep(a, b, n=1):
    global s
    assert s.count(a) == n, (a[:90], s.count(a)); s = s.replace(a, b)

rep('''// learn both templates from one exchange (S3), as both ends do; empty when learning refuses (empty arrays)''',
'''// [PRIMED_TEMPLATES_V1] the exchanges templates are learned from, recorded in a learning run and read at start
struct Exemplar { std::string m, p, b, a; };
inline std::string ex_quote(const std::string& x) {
    std::string o = "\\""; for (unsigned char c : x) { if (c == '"' || c == '\\\\') { o += '\\\\'; o += char(c); }
        else if (c < 0x20) { char u[8]; snprintf(u, sizeof u, "\\\\u%04x", c); o += u; } else o += char(c); } return o + "\\""; }
inline bool ex_unquote(const std::string& s, size_t& i, std::string& out) {
    if (i >= s.size() || s[i] != '"') { return false; }
    ++i; out.clear();
    while (i < s.size() && s[i] != '"') {
        if (s[i] == '\\\\' && i + 1 < s.size()) { const char e = s[++i];
            if (e == 'u' && i + 4 < s.size()) { out += char(strtol(s.substr(i + 1, 4).c_str(), nullptr, 16)); i += 5; continue; }
            out += e == 'n' ? '\\n' : e == 't' ? '\\t' : e == 'r' ? '\\r' : e; ++i; continue; }
        out += s[i++]; }
    if (i >= s.size()) { return false; }
    ++i; return true;
}
inline const std::vector<Exemplar>& primed() {
    static const std::vector<Exemplar> v = [] {
        std::vector<Exemplar> r; const char* f = getenv("RIBBIT_TEMPLATES"); if (!f || !*f) return r;
        FILE* fp = fopen(f, "r"); if (!fp) { fprintf(stderr, "[PRIMED_TEMPLATES_V1] cannot read %s\\n", f); return r; }
        char* line = nullptr; size_t cap = 0; ssize_t n;
        while ((n = getline(&line, &cap, fp)) > 0) {
            std::string l(line, size_t(n)); Exemplar e; std::string* dst[4] = {&e.m, &e.p, &e.b, &e.a}; const char* keys[4] = {"\\"m\\":", "\\"p\\":", "\\"b\\":", "\\"a\\":"};
            bool ok = true; for (int k = 0; k < 4 && ok; ++k) { size_t at = l.find(keys[k]); if (at == std::string::npos) { ok = false; break; } at += 4; ok = ex_unquote(l, at, *dst[k]); }
            if (ok) r.push_back(std::move(e)); else fprintf(stderr, "[PRIMED_TEMPLATES_V1] %s: a line that is not an exemplar was skipped\\n", f);
        }
        free(line); fclose(fp); fprintf(stderr, "[PRIMED_TEMPLATES_V1] %zu exemplars from %s\\n", r.size(), f); return r; }();
    return v;
}
inline void record_exemplar(const std::string& m, const std::string& p, const std::string& b, const std::string& a) {
    const char* f = getenv("RIBBIT_TEMPLATES_RECORD"); if (!f || !*f) return;
    static const int fd = ::open(f, O_WRONLY | O_CREAT | O_APPEND | O_CLOEXEC, 0644); if (fd < 0) return;
    const std::string line = "{\\"m\\":" + ex_quote(m) + ",\\"p\\":" + ex_quote(p) + ",\\"b\\":" + ex_quote(b) + ",\\"a\\":" + ex_quote(a) + "}\\n";
    if (::write(fd, line.data(), line.size()) != ssize_t(line.size())) fprintf(stderr, "[PRIMED_TEMPLATES_V1] recording to %s failed\\n", f);
}
// learn both templates from one exchange (S3), as both ends do; empty when learning refuses (empty arrays)''')

rep('''    ClientEngine(std::string target, std::string origin, std::string api, Transport t = nullptr)
        : target_(std::move(target)), origin_(std::move(origin)), api_(std::move(api)), t_(std::move(t)) {}''',
'''    ClientEngine(std::string target, std::string origin, std::string api, Transport t = nullptr)
        : target_(std::move(target)), origin_(std::move(origin)), api_(std::move(api)), t_(std::move(t)) {
        for (const auto& e : detail::primed()) {         // [PRIMED_TEMPLATES_V1] learned here, not on the wire
            uint64_t op = 0; auto tp = detail::learn(api_, e.m, e.p, e.b, e.a, op);
            if (tp) tpl_[{e.m, semtpl::canonical_semantic_key(e.m, e.p, e.b)}] = *tp;
            else fprintf(stderr, "[PRIMED_TEMPLATES_V1] client: an exemplar no longer learns (%s %s)\\n", e.m.c_str(), e.p.c_str()); }
        if (!detail::primed().empty()) publish(); }''')
rep('''    ServerEngine(std::string secret, std::string api, Exec e = nullptr) : secret_(std::move(secret)), api_(std::move(api)), exec_(std::move(e)) {}''',
'''    ServerEngine(std::string secret, std::string api, Exec e = nullptr) : secret_(std::move(secret)), api_(std::move(api)), exec_(std::move(e)) {
        for (const auto& x : detail::primed()) {         // [PRIMED_TEMPLATES_V1] the same exemplars the client learns
            uint64_t op = 0; auto tp = detail::learn(api_, x.m, x.p, x.b, x.a, op);
            if (tp) tpl_[op] = *tp; else fprintf(stderr, "[PRIMED_TEMPLATES_V1] server: an exemplar no longer learns (%s %s)\\n", x.m.c_str(), x.p.c_str()); }
        if (!detail::primed().empty()) publish_templates(); }''')
rep('''            tpl_[{c->method, semtpl::canonical_semantic_key(c->method, c->path, c->body)}] = *t; publish();''',
'''            tpl_[{c->method, semtpl::canonical_semantic_key(c->method, c->path, c->body)}] = *t; publish();
            detail::record_exemplar(c->method, c->path, c->body, a.body);   // [PRIMED_TEMPLATES_V1] a learning run keeps it''')
if '#include <fcntl.h>' not in s:
    rep('#pragma once\n', '#pragma once\n#include <fcntl.h>\n#include <unistd.h>\n#include <cstdio>\n')
open(p, 'w').write(s)
print("primed templates applied")
