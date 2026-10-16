#!/usr/bin/env python3
"""apply_exact_extract.py RIBBIT_CPP_DIR -- [EXACT_EXTRACTION_V1] the reply is not probed.

The server held an answer under its template by extracting the fields, then PROVING the template gives the answer back:
rebuild the whole answer from the fields and compare (REPLY_EXTRACTION_PROVEN_V1; PROOF_BYTES_FIRST_V1 compared bytes
first, but the rebuild orders keys its own way, so the bytes never matched and every answer was parsed twice more and
compared as JSON). At 500 members that probe was 1.8 ms of the server's 3.2 ms per read.

Now the extraction is exact: semtpl::extract_exact decides, in its one walk over the parsed answer, whether the
template gives this very answer back -- every key of the answer on a template path, every template path present, an
array path holding a list (an empty one only where no baseline blob would be put in its place), a top-level "value"
path only for an answer that is itself a list, and not the rebuild's pair case (a first field that is a two-element
list). Otherwise nullopt: the answer goes RESP_RAW, as before. No rebuild, no second parse, no compare.
Applied at build after apply_engine_exact_shortcuts.py (it replaces the block those two patches made)."""
import sys, os
d = sys.argv[1]

def patch(fn, a, b):
    p = os.path.join(d, fn); s = open(p).read()
    if s.count(a) != 1: sys.exit("apply_exact_extract: %s: anchor found %d times: %r" % (fn, s.count(a), a[:80]))
    open(p, 'w').write(s.replace(a, b))

patch('semtpl.hpp', """namespace detail {
inline List coerce_array(const Obj& v) {""", """// [EXACT_EXTRACTION_V1] extract, exactly: the fields, or nullopt when rebuild_reply would not give this very answer back
// (the caller sends it RAW). Decided in the one walk over the parsed answer: no rebuild, no second parse, no compare.
namespace detail {
inline bool exact_walk(const Dict& d, const std::string& prefix, const std::unordered_set<std::string>& leaves,
                       const std::unordered_set<std::string>& inner, const Dict& tm, const Dict& blobs, size_t& matched) {
    for (size_t i = 0; i < d.keys.size(); ++i) {
        const std::string& k = d.keys[i]; if (!safe_key(k)) return false;
        const std::string p = prefix.empty() ? k : prefix + "." + k; const Obj& v = d.vals[i];
        if (leaves.count(p)) {
            const Obj* t = get(tm, p);
            if (t && is_str(*t, "array")) {                 // the rebuild takes a list here; an empty one becomes the baseline blob
                if (!v.is<List>()) return false;
                if (v.as<List>().empty() && get(blobs, p)) return false; }
            ++matched; continue; }
        if (inner.count(p) && v.is<Dict>()) { if (!exact_walk(v.as<Dict>(), p, leaves, inner, tm, blobs, matched)) return false; continue; }
        return false;                                       // a key no template path holds: it would be dropped
    }
    return true;
}
}  // namespace detail
inline std::optional<Fields> extract_exact(const Dict& frag, std::string_view body) {
    if (body.empty()) return Fields{};
    const std::string m = detail::mode_of(frag); detail::ported_mode(m);
    if (m != "json") return Fields{{"raw", Str{std::string(body)}}};
    const std::string text = u::strip(body); if (text.empty()) return std::nullopt;
    auto parsed = detail::json_try(text); if (!parsed || has_nonfinite(*parsed)) return std::nullopt;
    const Obj& obj = *parsed; const std::vector<std::string> fo = str_list(get(frag, "field_order"));
    if (fo.empty()) { if (obj.is<Dict>() && obj.as<Dict>().keys.empty()) return Fields{}; return std::nullopt; }
    static const Dict none;
    const Obj* tmo = get(frag, "type_map"); const Dict& tm = truthy(tmo) ? tmo->as<Dict>() : none;
    if (fo.size() == 1 && fo[0] == "value") {               // the answer is the value: a list, held whole
        const Obj* t = get(tm, "value");
        if (!obj.is<List>() || !t || !is_str(*t, "array") || obj.as<List>().size() == 2) return std::nullopt;
        return Fields{{"value", obj}};
    }
    if (!obj.is<Dict>()) return std::nullopt;
    for (const auto& p : fo) if (p == "value") return std::nullopt;   // extract would hand the whole answer for it
    std::unordered_set<std::string> leaves(fo.begin(), fo.end()), inner;
    for (const auto& p : fo) for (size_t x = p.find('.'); x != std::string::npos; x = p.find('.', x + 1)) inner.insert(p.substr(0, x));
    const Obj* bbo = get(frag, "baseline_blobs"); const Dict& blobs = truthy(bbo) ? bbo->as<Dict>() : none;
    size_t matched = 0;
    if (!detail::exact_walk(obj.as<Dict>(), "", leaves, inner, tm, blobs, matched) || matched != fo.size()) return std::nullopt;
    Fields f; f.reserve(fo.size()); for (const auto& p : fo) f.emplace_back(p, get_by_path(obj, p));
    if (f[0].second.is<List>() && f[0].second.as<List>().size() == 2) return std::nullopt;   // the rebuild's pair case
    return f;
}
namespace detail {
inline List coerce_array(const Obj& v) {""")

# the engine: the probe goes; the extraction is exact
p = os.path.join(d, 'fnwp_engine.hpp'); s = open(p).read()
start = "        else {\n            dyn = semtpl::extract(t.resp_frag, a.body, true);\n            // [REPLY_EXTRACTION_PROVEN_V1]"
end = "                if (!same) dyn.reset();                 // -> RESP_RAW below; nothing is guessed\n            }\n        }\n"
i = s.find(start); j = s.find(end)
if s.count(start) != 1 or s.count(end) != 1 or j < i: sys.exit("apply_exact_extract: fnwp_engine.hpp: the reply probe block not found exactly once")
s = s[:i] + "        else dyn = semtpl::extract_exact(t.resp_frag, a.body);   // [EXACT_EXTRACTION_V1] no probe: exact, or RAW\n" + s[j + len(end):]
open(p, 'w').write(s)
if '#include <unordered_set>' not in open(os.path.join(d, 'semtpl.hpp')).read():
    patch('semtpl.hpp', '#pragma once\n', '#pragma once\n#include <unordered_set>\n')
print("exact extraction applied: the reply probe is gone")
