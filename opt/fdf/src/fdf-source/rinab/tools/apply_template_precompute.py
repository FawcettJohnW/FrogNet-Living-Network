"""apply_template_precompute.py RIBBIT_CPP_DIR -- [TEMPLATE_PRECOMPUTED_V1] what a template always gives is computed
ONCE, when the template is learned, and kept in it; the request path uses it instead of recomputing it per request.
Exactly the same results; fewer allocations (the malloc sampler had these among the top sites of the host's request
decode).

  * the request's field order (str_list of req_frag.field_order) -- was a fresh vector of strings per request, twice
  * the URL's static part: urlparse(url_static) and parse_qsl(its query) -- were reparsed per request
  * the request body's mode (json / raw / ...) -- was looked up and copied per request
  * a template with no location fields: its instance is "" whatever the message says, so the message is decoded once
    (against the reference), not twice (once just to read a location that does not exist)."""
import sys, os
d = sys.argv[1]
def patch(fn, pairs):
    p = os.path.join(d, fn); s = open(p).read()
    for a, b in pairs:
        assert s.count(a) == 1, (fn, a[:80], s.count(a)); s = s.replace(a, b)
    open(p, 'w').write(s)

patch('semtpl.hpp', [
 ('''inline std::string rebuild_body(const Dict& frag, const Fields& json_vals) {''',
  '''// [TEMPLATE_PRECOMPUTED_V1] build_url and rebuild_body with the template-constant parts computed once
inline std::string build_url_pre(const std::string& path0, const Pairs& static_q, const Fields& url_vals) {
    Pairs q; q.reserve(static_q.size() + url_vals.size()); q = static_q;
    for (const auto& kv : url_vals) {
        if (kv.second.is<None>()) throw pyv::ValueError("build_url: " + kv.first + " is None for " + path0 + " - refusing to drop the parameter");
        q.emplace_back(kv.first, detail::py_str(kv.second));
    }
    std::string qs = q.empty() ? "" : urlencode(q);
    return qs.empty() ? path0 : path0 + "?" + qs;
}
// [BODY_PLAN_V1] a JSON request body's SHAPE depends only on the template's field order, so it is built once: rebuild_json
// run on placeholders ("\\x00<n>" for field n) gives the exact tree rebuild_json would build -- key order, nesting, the
// rows list, a field given twice -- and each request renders that tree straight into a buffer that keeps its high-water
// mark, writing field n's value where placeholder n stands. No tree is built per request.
inline Obj body_skeleton(const std::vector<std::string>& fo) {
    List ph; ph.reserve(fo.size()); for (size_t n = 0; n < fo.size(); ++n) ph.push_back(Str{std::string(1, '\\0') + std::to_string(n)});
    return rebuild_json(ph, fo);
}
template <class V> inline void render_skeleton(std::string& o, const Obj& sk, const V& value_of) {
    if (sk.is<Str>()) { const std::string& s = sk.as<Str>().s;
        if (!s.empty() && s[0] == '\\0') { detail::dumps_ascii(o, value_of(size_t(std::stoul(s.substr(1)))), false); return; } }
    if (sk.is<Dict>()) { const Dict& d = sk.as<Dict>(); o += '{';
        for (size_t i = 0; i < d.size(); ++i) { if (i) o += ','; detail::dumps_ascii_str(o, d.keys[i]); o += ':'; render_skeleton(o, d.vals[i], value_of); } o += '}'; return; }
    if (sk.is<List>()) { o += '['; bool f = true; for (const Obj& x : sk.as<List>()) { if (!f) o += ','; f = false; render_skeleton(o, x, value_of); } o += ']'; return; }
    detail::dumps_ascii(o, sk, false);
}
inline std::string rebuild_body_pre(const std::string& mode, const std::vector<std::string>& fo, const Fields& json_vals, const Obj* skel = nullptr) {
    // as Dict::set did: a key given twice keeps its LAST value
    auto find = [&](const std::string& k) -> const Obj* { for (size_t i = json_vals.size(); i-- > 0;) if (json_vals[i].first == k) return &json_vals[i].second; return nullptr; };
    if (mode == "json" && skel) {                        // [BODY_PLAN_V1]
        static const Obj none{None{}};
        thread_local std::string buf; buf.clear();          // keeps its high-water mark: no growth once warm
        render_skeleton(buf, *skel, [&](size_t n) -> const Obj& { const Obj* v = n < fo.size() ? find(fo[n]) : nullptr; return v ? *v : none; });
        return buf;
    }
    if (mode == "json") {
        List vals; vals.reserve(fo.size());
        for (const auto& f : fo) { const Obj* v = find(f); vals.push_back(v ? *v : Obj(None{})); }
        return dumps_ascii(rebuild_json(vals, fo), false);
    }
    if (mode == "raw" || mode == "text" || mode == "html" || mode == "xml") {
        const Obj* r = find("raw"); if (!r) return ""; return r->is<None>() ? "" : detail::py_str(*r);
    }
    if (json_vals.size()) { const Obj& v0 = json_vals[0].second; return v0.is<None>() ? "" : detail::py_str(v0); }
    return "";
}
inline std::string rebuild_body(const Dict& frag, const Fields& json_vals) {'''),
])
patch('fnwp_engine.hpp', [
 ('''    std::vector<std::string> loc_fields;                // [INSTANCE_REFERENCES_V1] the fields that name the instance
};''',
  '''    std::vector<std::string> loc_fields;                // [INSTANCE_REFERENCES_V1] the fields that name the instance
    // [TEMPLATE_PRECOMPUTED_V1] computed once, at learning
    std::vector<std::string> req_fields; std::string url_path, req_mode; semtpl::Pairs url_static_q; pyv::Obj req_skel;
};'''),
 ('''    t.url_query_keys = semtpl::str_list(semtpl::get(l.req, "url_query_keys")); t.req_frag = l.req; t.resp_frag = l.resp;''',
  '''    t.url_query_keys = semtpl::str_list(semtpl::get(l.req, "url_query_keys")); t.req_frag = l.req; t.resp_frag = l.resp;
    {   // [TEMPLATE_PRECOMPUTED_V1]
        t.req_fields = semtpl::str_list(semtpl::get(t.req_frag, "field_order"));
        const std::string us = t.url_static.empty() ? "/" : t.url_static; semtpl::UrlParts up = semtpl::urlparse(us);
        t.url_static_q = semtpl::parse_qsl(up.query); t.url_path = up.path.empty() ? "/" : up.path;
        const pyv::Obj* mo = semtpl::get(t.req_frag, "mode");
        t.req_mode = !mo ? "raw" : mo->is<pyv::Str>() ? mo->as<pyv::Str>().s : std::string("\\x01not-a-name");
        if (t.req_mode == "json") t.req_skel = semtpl::body_skeleton(t.req_fields); }   // [BODY_PLAN_V1]'''),
 ('''    const auto fo = detail::fields_of(t->second.req_frag);
    auto explicit_vals = semcodec::decode_request(sem, t->second.url_query_keys, fo, nullptr);
    w.ik = detail::IKey{op, detail::location(t->second.loc_fields, explicit_vals.first, explicit_vals.second)};''',
  '''    const auto& fo = t->second.req_fields;              // [TEMPLATE_PRECOMPUTED_V1]
    // [TEMPLATE_PRECOMPUTED_V1] no location fields: the instance is "" -- decode once, below, not twice
    const bool need_explicit = full || !t->second.loc_fields.empty();
    std::pair<semcodec::Fields, semcodec::Fields> explicit_vals;
    if (need_explicit) explicit_vals = semcodec::decode_request(sem, t->second.url_query_keys, fo, nullptr);
    w.ik = detail::IKey{op, need_explicit ? detail::location(t->second.loc_fields, explicit_vals.first, explicit_vals.second) : std::string()};'''),
 ('''    w.method = t->second.method; w.path = semtpl::build_url(t->second.url_static, dec.first);
    w.body = t->second.req_frag.size() ? semtpl::rebuild_body(t->second.req_frag, dec.second) : "";''',
  '''    w.method = t->second.method; w.path = semtpl::build_url_pre(t->second.url_path, t->second.url_static_q, dec.first);   // [TEMPLATE_PRECOMPUTED_V1]
    w.body = t->second.req_frag.size() ? semtpl::rebuild_body_pre(t->second.req_mode, t->second.req_fields, dec.second, t->second.req_mode == "json" ? &t->second.req_skel : nullptr) : "";'''),
])
print("template precompute applied")
