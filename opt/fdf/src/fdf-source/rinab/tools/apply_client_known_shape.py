"""apply_client_known_shape.py FNWP_ENGINE_HPP -- [CLIENT_KNOWN_SHAPE_V1] the client prepares a request without
re-deriving what its shape already tells it. Measured on a rinaperf rr client (callgrind): prepare_locked was 70% of
the program, canonical_semantic_key alone 30% -- the body parsed and its whole schema inferred on every request -- and
the template proof parsed the body twice more.

 * The template key. Outside the sensor-data upsert (whose key reads SensorType / SensorName VALUES), the key is a
   function of the method, the path and the body's FIELD PATHS (with_body_shape: the crc32 of the field order) -- never of
   the values. So it is kept per shape: the body with every value elided (keys and structure kept), with the method and
   path, is the lookup; the key and its opcode are computed once per shape. The fingerprint is written into a buffer
   that keeps its high-water mark: a known shape costs one scan and one lookup, no allocation.
 * The template proof. The body is rebuilt from the template's skeleton ([BODY_PLAN_V1]) into the thread's buffer and
   compared byte for byte; only when the bytes differ are both parsed and compared as JSON, as before.
Exactly the keys and the decisions the long way gives. Applied at build after apply_template_precompute.py and
apply_client_applier.py."""
import sys
p = sys.argv[1]; s = open(p).read()
def rep(a, b, n=1):
    global s
    assert s.count(a) == n, (a[:90], s.count(a)); s = s.replace(a, b)
rep('''inline ClientEngine::Out ClientEngine::prepare_locked(std::shared_ptr<Ctx> c) {
    c->raw = false; c->key = semtpl::canonical_semantic_key(c->method, c->path, c->body);''',
'''// [CLIENT_KNOWN_SHAPE_V1] the body's shape: keys and structure kept, every value elided ('"' for a string, '#' for a
// number, true, false or null). Two bodies with one shape have one field-path list, so one template key.
inline void shape_of(std::string& out, std::string_view m, std::string_view path, std::string_view b) {
    out.clear(); out.append(m.data(), m.size()); out += '\\n'; out.append(path.data(), path.size()); out += '\\n';
    for (size_t i = 0; i < b.size();) {
        const char c = b[i];
        if (c == '"') {
            size_t e = i + 1; while (e < b.size() && b[e] != '"') e += b[e] == '\\\\' ? 2 : 1;
            size_t k = e + 1; while (k < b.size() && (b[k] == ' ' || b[k] == '\\t' || b[k] == '\\n' || b[k] == '\\r')) ++k;
            if (k < b.size() && b[k] == ':') out.append(b.data() + i, e + 1 - i); else out += '"';
            i = e + 1; continue;
        }
        if (c == '{' || c == '}' || c == '[' || c == ']' || c == ',' || c == ':') { out += c; ++i; continue; }
        if (c == ' ' || c == '\\t' || c == '\\n' || c == '\\r') { ++i; continue; }
        out += '#'; while (i < b.size() && b[i] != ',' && b[i] != '}' && b[i] != ']' && b[i] != ' ') ++i;
    }
}
inline ClientEngine::Out ClientEngine::prepare_locked(std::shared_ptr<Ctx> c) {
    c->raw = false;
    if (c->path.find("entity=sensor_data") == std::string::npos) {     // [CLIENT_KNOWN_SHAPE_V1] keyed by shape
        thread_local std::string fp; shape_of(fp, c->method, c->path, c->body);
        auto k = key_by_shape_.find(fp);
        if (k == key_by_shape_.end()) {
            if (key_by_shape_.size() > 4096) key_by_shape_.clear();      // a bound, not an expectation: shapes are few
            k = key_by_shape_.emplace(fp, semtpl::canonical_semantic_key(c->method, c->path, c->body)).first;
        }
        c->key = k->second;
    } else c->key = semtpl::canonical_semantic_key(c->method, c->path, c->body);''')
rep('''        std::string rebuilt = semtpl::rebuild_body(c->t.req_frag, *jv);
        auto a = semtpl::detail::json_try(c->body), b = semtpl::detail::json_try(rebuilt);
        bool same = (a && b) ? semtpl::python_eq(*a, *b) : rebuilt == c->body;''',
'''        // [CLIENT_KNOWN_SHAPE_V1] rebuilt from the skeleton into the thread's buffer; equal bytes need no parse
        const std::string rebuilt = c->t.req_mode == "json" ? semtpl::rebuild_body_pre(c->t.req_mode, c->t.req_fields, *jv, &c->t.req_skel)
                                                            : semtpl::rebuild_body(c->t.req_frag, *jv);
        bool same = rebuilt == c->body;
        if (!same) { auto a = semtpl::detail::json_try(c->body), b = semtpl::detail::json_try(rebuilt);
                     same = (a && b) ? semtpl::python_eq(*a, *b) : false; }''')
rep('''    std::map<Key, Template> tpl_;''', '''    std::unordered_map<std::string, std::string> key_by_shape_;   /* [CLIENT_KNOWN_SHAPE_V1] the sender's alone */
    std::map<Key, Template> tpl_;''', 1)
if '#include <unordered_map>' not in s:
    rep('#pragma once\n', '#pragma once\n#include <unordered_map>\n')
open(p, 'w').write(s); print("client known shape applied")
