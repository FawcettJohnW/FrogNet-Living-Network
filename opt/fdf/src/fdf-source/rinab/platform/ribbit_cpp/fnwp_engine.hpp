// fnwp_engine.hpp -- the stateful FNWP engines for the RAM wire, transport-agnostic (frames in, frames out).
//   ClientEngine (S4): what proxy/transport_semantic.py does per request, for one far end.
//   ServerEngine (S5): what daemon/engine/session.py does per session, with the RAM op as the engine.
// Built from the proven pieces: semwire (S1), semcodec (S2), semtpl (S3), ramrows, fnwp_client (S4a, S4b reply side),
// fnwp_server (S5a). Reference rules: S4-S5-SPEC.md.
//
// Departures from the Python, each forced and stated:
//  - Templates: the daemon only looks templates up (it shares the proxy's store through the database). The RAM wire has
//    no shared store (ruled: each side's local store), so the server LEARNS from the RAW exchange it serves -- the same
//    request and answer the client learns from, with S3's deterministic learning -- and both hold identical templates.
//  - RAM read answers use ramrows: the template is the last answer both ends hold. After a RESP_RAW (shape changed) both
//    ends take that answer as the template and clear the response reference, so the next RESP_DIFF carries every field.
//  - A request body the template cannot hold goes RAW. The JSON handler's extract keeps only the template's fields; a body
//    with other keys would lose them. The client proves every extraction by rebuilding it before trusting it.
//  - REQ_RAW's request hash is sha256(target + NUL + http request)[:16]; not yet matched to _handle_raw_and_learn.
// Templates here are held in memory; the MySQL store (ruled) plugs in behind TemplateStore.
//
// [INSTANCE_REFERENCES_V1] John 2026-09-26: the template is the generally recognized format; each side's LOCAL cache holds
// many individual instances of it. An instance is the template plus the location in the message's data -- for the RAM
// wire the (service, variable, instance) the call names. Request references, response references and the RAM-answer
// template are kept per instance, so a message is differenced against the last message for the SAME location, never
// against another location that happens to share the template. Every non-FULL request carries its location fields (they
// are sent even when unchanged) so the receiver picks the matching entry from its own local cache before applying the
// differences. A template with no location fields has exactly one instance: today's behaviour, unchanged.
//
// [RAM_INTERFACE_IS_THE_VENDORS_V1] John 2026-09-26: the RAM interface's endpoint is the application vendor's to name
// (suggested: <Vendor>.<Product>.ram_interface.php), not /ram.php. Both engines take it as `api`, as frogram::Memory
// does; a call is a RAM-interface call when its path is that endpoint. The location of a RAM-interface call is the
// cell's address -- its three coordinates, whatever the application names them.
#pragma once
#include <fcntl.h>
#ifndef O_CLOEXEC                                 // [WINDOWS_PORTABLE_V1]
#define O_CLOEXEC 0
#endif
#include <atomic>
#include <list>
#include <vector>
#include <unordered_map>
#include <fcntl.h>
#include <unistd.h>
#include <cstdio>
#include "fnwp_client.hpp"
#include "fnwp_server.hpp"
#include "ramrows.hpp"
#include "dataplane.hpp"
#include <condition_variable>
#include <functional>
#include <list>
#include <deque>
#include <map>
#include <memory>
#include <mutex>
#include <set>
namespace fnwp {

struct Answer { int status = 0; std::string body; };
struct Template {                                        // one learned (method, semantic key)
    std::string method, url_static; std::vector<std::string> url_query_keys; Dict req_frag, resp_frag; bool ram_rows = false;
    std::vector<std::string> loc_fields;                // [INSTANCE_REFERENCES_V1] the fields that name the instance
    // [TEMPLATE_PRECOMPUTED_V1] computed once, at learning
    std::vector<std::string> req_fields; std::string url_path, req_mode, url_fixed; semtpl::Pairs url_static_q; pyv::Obj req_skel;
};
namespace detail {
inline std::string sv(const std::optional<std::string_view>& o) { return o ? std::string(*o) : std::string(); }
inline bool is_ram_read(const std::string& api, const std::string& method, const std::string& path) {
    return method == "GET" && path.rfind(api, 0) == 0 && path.find("op=read") != std::string::npos;
}
inline std::string sha16(const std::string& s) {
    unsigned char d[SHA256_DIGEST_LENGTH]; SHA256(reinterpret_cast<const unsigned char*>(s.data()), s.size(), d);
    return std::string(reinterpret_cast<const char*>(d), 16);
}
inline std::string sha32(const std::string& s) {
    unsigned char d[SHA256_DIGEST_LENGTH]; SHA256(reinterpret_cast<const unsigned char*>(s.data()), s.size(), d);
    return std::string(reinterpret_cast<const char*>(d), 32);
}
inline std::string http_json(const std::string& method, const std::string& path, const std::string& body) {
    return "{\"method\":" + frogram::Json::quote(method) + ",\"path\":" + frogram::Json::quote(path) + ",\"headers\":{},\"body\":" +
           frogram::Json::quote(body) + ",\"host\":\"127.0.0.1\",\"port\":8080}";
}
inline std::vector<std::string> fields_of(const Dict& frag) { return semtpl::str_list(semtpl::get(frag, "field_order")); }
using IKey = std::pair<uint64_t, std::string>;          // [INSTANCE_REFERENCES_V1] (template opcode, instance location)
// The instance location from a message's values. Every location field must be present: a frame without one cannot be
// placed in the local cache, and that is a protocol error, not something to guess.
inline std::string location(const std::vector<std::string>& loc_fields, const semcodec::Fields& a, const semcodec::Fields& b) {
    std::string loc;
    for (auto& f : loc_fields) {
        const Obj* v = nullptr;
        for (auto& kv : a) if (kv.first == f) v = &kv.second;
        for (auto& kv : b) if (kv.first == f) v = &kv.second;
        if (!v || v->is<pyv::None>()) throw std::runtime_error("fnwp: location field '" + f + "' missing from the message");
        loc += f; loc += '='; loc += pyrepr(*v); loc += '\x1f';
    }
    return loc;
}
inline Dict without(const Dict& d, const std::vector<std::string>& drop) {
    Dict r; for (size_t i = 0; i < d.size(); ++i) {
        bool skip = false; for (auto& f : drop) if (d.keys[i] == f) skip = true;
        if (!skip) r.set(d.keys[i], d.vals[i]); }
    return r;
}
// [PRIMED_TEMPLATES_V1] the exchanges templates are learned from, recorded in a learning run and read at start
struct Exemplar { std::string m, p, b, a; };
inline std::string ex_quote(const std::string& x) {
    std::string o = "\""; for (unsigned char c : x) { if (c == '"' || c == '\\') { o += '\\'; o += char(c); }
        else if (c < 0x20) { char u[8]; snprintf(u, sizeof u, "\\u%04x", c); o += u; } else o += char(c); } return o + "\""; }
inline bool ex_unquote(const std::string& s, size_t& i, std::string& out) {
    if (i >= s.size() || s[i] != '"') { return false; }
    ++i; out.clear();
    while (i < s.size() && s[i] != '"') {
        if (s[i] == '\\' && i + 1 < s.size()) { const char e = s[++i];
            if (e == 'u' && i + 4 < s.size()) { out += char(strtol(s.substr(i + 1, 4).c_str(), nullptr, 16)); i += 5; continue; }
            out += e == 'n' ? '\n' : e == 't' ? '\t' : e == 'r' ? '\r' : e; ++i; continue; }
        out += s[i++]; }
    if (i >= s.size()) { return false; }
    ++i; return true;
}
inline const std::vector<Exemplar>& primed() {
    static const std::vector<Exemplar> v = [] {
        std::vector<Exemplar> r; const char* f = getenv("RIBBIT_TEMPLATES"); if (!f || !*f) return r;
        FILE* fp = fopen(f, "r"); if (!fp) { fprintf(stderr, "[PRIMED_TEMPLATES_V1] cannot read %s\n", f); return r; }
        std::string l;                                     // [WINDOWS_PORTABLE_V1] a portable line reader (no POSIX getline)
        for (int ch = 0; ch != EOF;) {
            l.clear(); while ((ch = fgetc(fp)) != EOF) { l += char(ch); if (ch == '\n') break; }
            if (l.empty()) break;
             Exemplar e; std::string* dst[4] = {&e.m, &e.p, &e.b, &e.a}; const char* keys[4] = {"\"m\":", "\"p\":", "\"b\":", "\"a\":"};
            bool ok = true; for (int k = 0; k < 4 && ok; ++k) { size_t at = l.find(keys[k]); if (at == std::string::npos) { ok = false; break; } at += 4; ok = ex_unquote(l, at, *dst[k]); }
            if (ok) r.push_back(std::move(e)); else fprintf(stderr, "[PRIMED_TEMPLATES_V1] %s: a line that is not an exemplar was skipped\n", f);
        }
        fclose(fp); fprintf(stderr, "[PRIMED_TEMPLATES_V1] %zu exemplars from %s\n", r.size(), f); return r; }();
    return v;
}
inline void record_exemplar(const std::string& m, const std::string& p, const std::string& b, const std::string& a) {
    const char* f = getenv("RIBBIT_TEMPLATES_RECORD"); if (!f || !*f) return;
    static const int fd = ::open(f, O_WRONLY | O_CREAT | O_APPEND | O_CLOEXEC, 0644); if (fd < 0) return;
    const std::string line = "{\"m\":" + ex_quote(m) + ",\"p\":" + ex_quote(p) + ",\"b\":" + ex_quote(b) + ",\"a\":" + ex_quote(a) + "}\n";
    if (::write(fd, line.data(), line.size()) != ssize_t(line.size())) fprintf(stderr, "[PRIMED_TEMPLATES_V1] recording to %s failed\n", f);
}
// learn both templates from one exchange (S3), as both ends do; empty when learning refuses (empty arrays)
inline std::optional<Template> learn(const std::string& api, const std::string& method, const std::string& path,
                                     const std::string& body, const std::string& resp_body, uint64_t& opcode) {
    std::string key = semtpl::canonical_semantic_key(method, path, body); opcode = semtpl::opcode_for(key);
    // [RAM_READ_REQUEST_TEMPLATE_V1] A RAM read's answer is held by the RAM-answer handler (ramrows: the instance's last
    // answer is its template), not by S3's reply template. S3 refuses to learn from an empty array, so an answer with no
    // rows yet (the tuple is not written) blocked the REQUEST template too and every such read went REQ_RAW. For a RAM
    // read, learn the request template on its own; the answer, empty or not, becomes the instance's ramrows template.
    const std::string learn_resp = is_ram_read(api, method, path) ? std::string("{\"ok\":true}") : resp_body;
    auto l = semtpl::learn_templates(method, key, {}, body, {}, learn_resp, path);
    if (l.refused_empty) return std::nullopt;
    Template t; t.method = method; t.url_static = semtpl::normalize_path_for_semantics(path).first;
    t.url_query_keys = semtpl::str_list(semtpl::get(l.req, "url_query_keys")); t.req_frag = l.req; t.resp_frag = l.resp;
    {   // [TEMPLATE_PRECOMPUTED_V1]
        t.req_fields = semtpl::str_list(semtpl::get(t.req_frag, "field_order"));
        const std::string us = t.url_static.empty() ? "/" : t.url_static; semtpl::UrlParts up = semtpl::urlparse(us);
        t.url_static_q = semtpl::parse_qsl(up.query); t.url_path = up.path.empty() ? "/" : up.path;
        t.url_fixed = semtpl::build_url_pre(t.url_path, t.url_static_q, {});   // [DECODE_ALLOC_ONCE_V1] the URL when no query value varies
        const pyv::Obj* mo = semtpl::get(t.req_frag, "mode");
        t.req_mode = !mo ? "raw" : mo->is<pyv::Str>() ? mo->as<pyv::Str>().s : std::string("\x01not-a-name");
        if (t.req_mode == "json") t.req_skel = semtpl::body_skeleton(t.req_fields); }   // [BODY_PLAN_V1]
    t.ram_rows = is_ram_read(api, method, path);
    if (t.url_static == api)                            // a RAM-interface call's location: the cell's three coordinates
        for (const char* f : {"service", "variable", "instance"}) {
            for (auto& k : t.url_query_keys) if (k == f) t.loc_fields.push_back(f);
            for (auto& k : fields_of(t.req_frag)) if (k == f) t.loc_fields.push_back(f);
        }
    return t;
}
}  // namespace detail


// [ENGINES_IN_THE_SESSION_V1] The engines run inside frogram::Session and the RAM server's socket loop, where many
// requests are in flight at once and a held read may park for seconds. The rule is the Python proxy's and daemon's:
// references move in WIRE ORDER. The client commits a request reference as the frame leaves (the server commits it as
// the frame arrives); the server commits a response reference as the reply is encoded and put on the return socket
// (the client commits it as the reply is read). Nothing waits for any reply but its own. The one thing that does wait:
// a reply whose payload travels on the data socket ([DATA_MARKER_V1]) leaves its marker in the return socket's order,
// and a LATER reply to the SAME instance is applied only after that payload has landed -- because the instance's RAM
// answer template is that payload. Replies to other instances never wait.
// ------------------------------------------------------------------------------------------------ client (S4)
class ClientEngine {
public:
    using Transport = std::function<std::string(const std::string& frame)>;   // one frame out, its reply frame back
    struct Stats { uint64_t raw = 0, full = 0, diff = 0, repeat = 0, same = 0, rdiff = 0, rraw = 0, miss = 0, bytes_out = 0, bytes_in = 0; };
    struct Ctx {                                          // one request in flight
        std::string method, path, body; bool raw = false, deferred = false; int attempts = 0;
        std::string same_miss;   // [SAME_MISS_FROM_CACHE_V1] the same_id this call asked the host for
        std::string key; uint64_t op = 0; detail::IKey ik; const Template* tp = nullptr;   // [TEMPLATE_BY_POINTER_V1] into the snapshot
    };
    struct Out { std::string frame; std::shared_ptr<Ctx> ctx; };
    struct Step { bool done = false; Answer answer; Out resend; int redo = 0; };   // [CLIENT_APPLIER_V1] redo: 1 prepare again, 2 RAW
    ClientEngine(std::string target, std::string origin, std::string api, Transport t = nullptr)
        : target_(std::move(target)), origin_(std::move(origin)), api_(std::move(api)), t_(std::move(t)) {
        // [PRIMED_LEARNED_ONCE_V1] learned once per process (for the first API asked), copied into every later engine
        auto learn_all = [](const std::string& api) { std::vector<std::pair<Key, Template>> v;
            for (const auto& e : detail::primed()) { uint64_t op = 0; auto tp = detail::learn(api, e.m, e.p, e.b, e.a, op);
                if (tp) v.emplace_back(Key{e.m, semtpl::canonical_semantic_key(e.m, e.p, e.b)}, *tp);
                else fprintf(stderr, "[PRIMED_TEMPLATES_V1] client: an exemplar no longer learns (%s %s)\n", e.m.c_str(), e.p.c_str()); }
            return v; };
        static const std::string first_api = api_;
        static const std::vector<std::pair<Key, Template>> learned = learn_all(first_api);
        if (api_ == first_api) { for (const auto& kv : learned) tpl_[kv.first] = kv.second; }
        else { for (auto& kv : learn_all(api_)) tpl_[kv.first] = std::move(kv.second); }
        if (!detail::primed().empty()) publish(); }
    // The frame for a request, and its continuation. Commits the request reference as the frame leaves.
    Out prepare(const std::string& method, const std::string& path, const std::string& body);
    // Apply a reply to its continuation, in the order replies are read: the answer, or the next frame.
    Step complete(const std::shared_ptr<Ctx>& ctx, const std::string& reply);
    // [DATA_MARKER_V1] A RAW reply to this request arrived as a marker; its payload is still on the data socket. Later
    // replies to the same instance wait until complete() is called with the full reply.
    void defer(const std::shared_ptr<Ctx>& ctx);
    // The marker frame for a RAW request whose body goes on the data socket: the same request JSON without its body,
    // plus data_bytes. `body` receives the payload to send.
    static std::string request_marker(const std::string& raw_frame, std::string& body);
    static bool is_reply_marker(const std::string& reply_frame);
    // The full RESP_RAW frame from its marker and the landed payload.
    static std::string reply_from_marker(const std::string& marker_frame, const std::string& body);
    Answer request(const std::string& method, const std::string& path, const std::string& body);   // synchronous, via t_
    const std::string& api() const { return api_; }
    Stats stats() const {                                 // [CLIENT_APPLIER_V1] from atomic counters
        Stats s; s.raw = a_raw.load(); s.full = a_full.load(); s.diff = a_diff.load(); s.repeat = a_repeat.load(); s.same = a_same.load();
        s.rdiff = a_rdiff.load(); s.rraw = a_rraw.load(); s.miss = a_miss.load(); s.bytes_out = a_out.load(); s.bytes_in = a_in.load(); return s; }
    // [CLIENT_APPLIER_V1] the two resends complete() asks of the sender (the sender's state, the sender's thread)
    Out reprepare(const std::shared_ptr<Ctx>& c) { reqref_.erase(c->ik); return prepare_locked(c); }
    Out resend_raw(const std::shared_ptr<Ctx>& c) { return raw_locked(c); }
    ~ClientEngine() { delete snap_.load(); for (auto* p : retired_) delete p; }
private:
    struct Key { std::string m, k; bool operator<(const Key& o) const { return m < o.m || (m == o.m && k < o.k); } };
    Out prepare_locked(std::shared_ptr<Ctx> c);
    Out raw_locked(std::shared_ptr<Ctx> c);
    Step complete_raw_locked(const std::shared_ptr<Ctx>& c, const std::string& rf);
    Step complete_locked(std::unique_lock<std::mutex>& l, const std::shared_ptr<Ctx>& c, const std::string& rf);
    std::string target_, origin_, api_; Transport t_;
    std::atomic<uint64_t> a_raw{0}, a_full{0}, a_diff{0}, a_repeat{0}, a_same{0}, a_rdiff{0}, a_rraw{0}, a_miss{0}, a_out{0}, a_in{0};
    // [CLIENT_APPLIER_V1] the applier owns tpl_ and publishes it; the sender reads the snapshot
    using TplMap = std::map<Key, Template>;
    std::atomic<const TplMap*> snap_{new TplMap}; std::vector<const TplMap*> retired_;
    void publish() { retired_.push_back(snap_.exchange(new TplMap(tpl_), std::memory_order_acq_rel)); }
    std::unordered_map<std::string, std::string> key_by_shape_;   /* [CLIENT_KNOWN_SHAPE_V1] the sender's alone */
    std::map<Key, Template> tpl_; std::map<detail::IKey, Dict> reqref_, respref_; std::map<detail::IKey, std::string> rr_tpl_;
    std::map<std::string, std::string> same_; std::list<std::string> same_lru_;
    void same_put(const std::string& sid, const std::string& body) {
        if (!same_.count(sid)) { same_lru_.push_back(sid); if (same_lru_.size() > 256) { same_.erase(same_lru_.front()); same_lru_.pop_front(); } }
        same_[sid] = body;
    }
};
inline ClientEngine::Out ClientEngine::prepare(const std::string& method, const std::string& path, const std::string& body) {
    auto c = std::make_shared<Ctx>(); c->method = method; c->path = path; c->body = body;
    return prepare_locked(c);                             // [CLIENT_APPLIER_V1] the sender's thread only
}
inline ClientEngine::Out ClientEngine::raw_locked(std::shared_ptr<Ctx> c) {
    c->raw = true; a_raw.fetch_add(1, std::memory_order_relaxed); std::string http = detail::http_json(c->method, c->path, c->body);
    Out o{semwire::wrap_req_raw(detail::sha16(target_ + '\0' + http), http), c}; a_out.fetch_add(o.frame.size(), std::memory_order_relaxed); return o;
}
// [CLIENT_KNOWN_SHAPE_V1] the body's shape: keys and structure kept, every value elided ('"' for a string, '#' for a
// number, true, false or null). Two bodies with one shape have one field-path list, so one template key.
inline void shape_of(std::string& out, std::string_view m, std::string_view path, std::string_view b) {
    out.clear(); out.append(m.data(), m.size()); out += '\n'; out.append(path.data(), path.size()); out += '\n';
    for (size_t i = 0; i < b.size();) {
        const char c = b[i];
        if (c == '"') {
            size_t e = i + 1; while (e < b.size() && b[e] != '"') e += b[e] == '\\' ? 2 : 1;
            size_t k = e + 1; while (k < b.size() && (b[k] == ' ' || b[k] == '\t' || b[k] == '\n' || b[k] == '\r')) ++k;
            if (k < b.size() && b[k] == ':') out.append(b.data() + i, e + 1 - i); else out += '"';
            i = e + 1; continue;
        }
        if (c == '{' || c == '}' || c == '[' || c == ']' || c == ',' || c == ':') { out += c; ++i; continue; }
        if (c == ' ' || c == '\t' || c == '\n' || c == '\r') { ++i; continue; }
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
    } else c->key = semtpl::canonical_semantic_key(c->method, c->path, c->body);
    const TplMap* tm = snap_.load(std::memory_order_acquire);   // [CLIENT_APPLIER_V1] the published snapshot
    auto it = tm->find({c->method, c->key});
    if (it == tm->end()) return raw_locked(c);
    c->tp = &it->second; c->op = semtpl::opcode_for(c->key);
    auto jv = semtpl::extract(c->tp->req_frag, c->body, false);
    if (!jv) return raw_locked(c);
    if (!semcodec::fits_wire(*jv)) return raw_locked(c);   // [HOLDABLE_V1] a field the wire cannot carry: the request goes RAW
    if (!c->body.empty()) {                             // prove the template holds this body before trusting the fields
        // [CLIENT_KNOWN_SHAPE_V1] rebuilt from the skeleton into the thread's buffer; equal bytes need no parse
        const std::string rebuilt = c->tp->req_mode == "json" ? semtpl::rebuild_body_pre(c->tp->req_mode, c->tp->req_fields, *jv, &c->tp->req_skel)
                                                            : semtpl::rebuild_body(c->tp->req_frag, *jv);
        bool same = rebuilt == c->body;
        if (!same) { auto a = semtpl::detail::json_try(c->body), b = semtpl::detail::json_try(rebuilt);
                     same = (a && b) ? semtpl::python_eq(*a, *b) : false; }
        if (!same) return raw_locked(c);
    }
    semcodec::Fields uv; for (auto& kv : semtpl::extract_dynamic_query_vals(c->path, c->tp->url_query_keys)) uv.emplace_back(kv.first, pyv::Str{kv.second});
    c->ik = detail::IKey{c->op, detail::location(c->tp->loc_fields, uv, *jv)};
    auto rr = reqref_.find(c->ik);
    Built b = build_request(target_, c->path, c->op, uv, *jv, rr == reqref_.end() ? nullptr : &rr->second, origin_, target_);
    if (b.req_type == "REQ_DIFF" && !c->tp->loc_fields.empty()) {    // the location travels with every difference
        Dict ref = detail::without(rr->second, c->tp->loc_fields);
        b = build_request(target_, c->path, c->op, uv, *jv, &ref, origin_, target_);
    }
    if (b.req_type == "REQ_REPEAT") a_repeat.fetch_add(1, std::memory_order_relaxed); else if (b.req_type == "REQ_FULL") a_full.fetch_add(1, std::memory_order_relaxed); else a_diff.fetch_add(1, std::memory_order_relaxed);
    reqref_[c->ik] = b.new_reference;                   // committed as it leaves, as the server commits on decode
    a_out.fetch_add(b.frame.size(), std::memory_order_relaxed);
    return Out{b.frame, c};
}
inline ClientEngine::Step ClientEngine::complete_raw_locked(const std::shared_ptr<Ctx>& c, const std::string& rf) {
    auto m = semwire::try_parse(rf);
    if (!m || m->op != semwire::OP_RESP_RAW) throw std::runtime_error("fnwp client: REQ_RAW answered with something other than RESP_RAW");
    Answer a{int(m->status.value_or(0)), detail::sv(m->body)};
    uint64_t op = 0;
    if (a.status < 400 && !a.body.empty()) {           // learn as the proxy does; a learning failure aborts (ruled)
        auto t = detail::learn(api_, c->method, c->path, c->body, a.body, op);
        if (t) {
            tpl_[{c->method, semtpl::canonical_semantic_key(c->method, c->path, c->body)}] = *t; publish();
            detail::record_exemplar(c->method, c->path, c->body, a.body);   // [PRIMED_TEMPLATES_V1] a learning run keeps it
            if (t->ram_rows) {                           // this answer is the instance's RAM-answer template
                semcodec::Fields uv; for (auto& kv : semtpl::extract_dynamic_query_vals(c->path, t->url_query_keys))
                    uv.emplace_back(kv.first, pyv::Str{kv.second});
                detail::IKey ik{op, detail::location(t->loc_fields, uv, {})}; rr_tpl_[ik] = a.body; respref_.erase(ik);
            }
        }
    }
    same_put(detail::sv(m->same_id), a.body);
    Step s; s.done = true; s.answer = a; return s;
}
inline void ClientEngine::defer(const std::shared_ptr<Ctx>& c) { c->deferred = true; }   // [CLIENT_APPLIER_V1] the applier orders the rest
inline ClientEngine::Step ClientEngine::complete(const std::shared_ptr<Ctx>& c, const std::string& rf) {
    a_in.fetch_add(rf.size(), std::memory_order_relaxed);   // [CLIENT_APPLIER_V1] the applier's thread only; it holds a
    std::unique_lock<std::mutex> none;                       // reply to a landing instance until that instance has landed
    return complete_locked(none, c, rf);
}
inline ClientEngine::Step ClientEngine::complete_locked(std::unique_lock<std::mutex>&, const std::shared_ptr<Ctx>& c, const std::string& rf) {
    if (!c->same_miss.empty()) {                       // [SAME_MISS_FROM_CACHE_V1] the answer a SAME named, from the cache
        auto m = semwire::try_parse(rf);
        if (m && m->op == semwire::OP_ERROR) throw std::runtime_error("fnwp client: REQ_SAME_MISS: " + std::string(detail::sv(m->payload)));
        if (!m || m->op != semwire::OP_RESP_RAW) throw std::runtime_error("fnwp client: REQ_SAME_MISS answered with something other than RESP_RAW");
        const std::string body(detail::sv(m->body)); const Template& t = *c->tp; const detail::IKey& ik = c->ik;
        std::optional<semcodec::Fields> held;           // exactly the SAME hit's move ([SAME_MOVES_REFERENCE_V1])
        if (t.ram_rows) { auto rt = rr_tpl_.find(ik); if (rt != rr_tpl_.end()) held = ramrows::extract(body, ramrows::learn(rt->second)); }
        else held = semtpl::extract(t.resp_frag, body, true);
        if (!held) throw std::runtime_error("fnwp client: the answer RESP_SAME named is not one this instance's template can hold");
        Dict nr; for (auto& kv : *held) nr.set(kv.first, kv.second); respref_[ik] = nr;
        same_put(c->same_miss, body); c->same_miss.clear();
        Step s; s.done = true; s.answer = Answer{int(m->status.value_or(0)), body}; return s;
    }
    if (c->raw) return complete_raw_locked(c, rf);
    auto m = semwire::try_parse(rf);                    // views into rf: rf outlives m
    if (!m) throw std::runtime_error("fnwp client: non-FNW1 reply");
    const Template& t = *c->tp; const detail::IKey& ik = c->ik;
    if (m->op == semwire::OP_REQ_MISS) {               // resend FULL
        a_miss.fetch_add(1, std::memory_order_relaxed);
        if (++c->attempts >= 2) throw std::runtime_error("fnwp client: REQ_MISS from the far end; exhausted retries");
        Step s; s.redo = 1; s.resend.ctx = c; return s;      // [CLIENT_APPLIER_V1] the sender forgets the reference and prepares again
    }
    if (m->op == semwire::OP_ERROR) {
        if (detail::sv(m->payload).find("no templates for opcode") != std::string::npos) {
            tpl_.erase({c->method, c->key}); publish(); Step s; s.redo = 2; s.resend.ctx = c; return s; }   // [CLIENT_APPLIER_V1]
        throw std::runtime_error("fnwp client: far end error " + std::to_string(m->status.value_or(0)) + ": " + detail::sv(m->payload));
    }
    if (m->op == semwire::OP_RESP_SAME) {
        a_same.fetch_add(1, std::memory_order_relaxed); auto sb = same_.find(detail::sv(m->same_id));
        if (sb == same_.end()) {
            // [SAME_MISS_FROM_CACHE_V1] the host already ran this request and holds the answer it called SAME. Ask for
            // it by its id; never send the request again (a second send runs it a second time). No reference moves
            // here: the answer is applied in this call's turn, and moves the response reference as a SAME hit does.
            c->same_miss = std::string(detail::sv(m->same_id));
            Out o{semwire::wrap_req_same_miss(detail::sv(m->same_id)), c}; a_out.fetch_add(o.frame.size(), std::memory_order_relaxed);
            Step s; s.resend = o; return s;
        }
        // [SAME_MOVES_REFERENCE_V1] the server's reply decision sets its response reference to the answer a SAME names
        std::optional<semcodec::Fields> held;
        if (t.ram_rows) { auto rt = rr_tpl_.find(ik); if (rt != rr_tpl_.end()) held = ramrows::extract(sb->second, ramrows::learn(rt->second)); }
        else held = semtpl::extract(t.resp_frag, sb->second, true);
        if (!held) throw std::runtime_error("fnwp client: RESP_SAME names a body this instance's template cannot hold");
        Dict nr; for (auto& kv : *held) nr.set(kv.first, kv.second); respref_[ik] = nr;
        Step s; s.done = true; s.answer = Answer{200, sb->second}; return s;
    }
    if (m->op == semwire::OP_RESP_DIFF) {
        a_rdiff.fetch_add(1, std::memory_order_relaxed); auto rp = respref_.find(ik); const Dict* ref = rp == respref_.end() ? nullptr : &rp->second; std::string out;
        if (t.ram_rows) {
            auto rt = rr_tpl_.find(ik);
            if (rt == rr_tpl_.end()) throw std::runtime_error("fnwp client: RESP_DIFF for an instance with no RAM-answer template");
            Dict frag = ramrows::learn(rt->second); auto f = semcodec::decode_reply(detail::sv(m->payload), detail::fields_of(frag), ref);
            Dict nr; pyv::List vals; for (auto& kv : f) { nr.set(kv.first, kv.second); vals.push_back(kv.second); }
            out = ramrows::rebuild(frag, vals); respref_[ik] = nr;
        } else { auto a = apply_resp_diff(detail::sv(m->payload), t.resp_frag, ref); out = a.body; respref_[ik] = a.new_reference; }
        same_put(detail::sv(m->same_id), out); Step s; s.done = true; s.answer = Answer{200, out}; return s;
    }
    if (m->op == semwire::OP_RESP_RAW) {
        a_rraw.fetch_add(1, std::memory_order_relaxed); same_put(detail::sv(m->same_id), detail::sv(m->body));
        if (t.ram_rows && m->status.value_or(0) < 400) { rr_tpl_[ik] = detail::sv(m->body); respref_.erase(ik); }  // new shape = template
        Step s; s.done = true; s.answer = Answer{int(m->status.value_or(0)), detail::sv(m->body)}; return s;
    }
    throw std::runtime_error("fnwp client: unknown reply op");
}
inline std::string ClientEngine::request_marker(const std::string& raw_frame, std::string& body) {
    auto m = semwire::try_parse(raw_frame);
    if (!m || m->op != semwire::OP_REQ_RAW) throw std::runtime_error("fnwp: request_marker of a frame that is not REQ_RAW");
    frogram::Json q = frogram::Json::parse(detail::sv(m->payload)); body = q["body"].s;
    std::string j = "{\"method\":" + frogram::Json::quote(q["method"].s) + ",\"path\":" + frogram::Json::quote(q["path"].s) +
                    ",\"headers\":{},\"body\":\"\",\"host\":" + frogram::Json::quote(q["host"].s) + ",\"port\":" + std::to_string(int(q["port"].n)) +
                    ",\"" + dataplane::MARKER_KEY + "\":" + std::to_string(body.size()) + "}";
    return semwire::wrap_req_raw(detail::sv(m->req_hash), j);
}
inline bool ClientEngine::is_reply_marker(const std::string& reply_frame) {
    auto m = semwire::try_parse(reply_frame);
    if (!m || m->op != semwire::OP_RESP_RAW) return false;
    return detail::sv(m->headers).find(dataplane::MARKER_HEADER) != std::string::npos;
}
inline std::string ClientEngine::reply_from_marker(const std::string& marker_frame, const std::string& body) {
    auto m = semwire::try_parse(marker_frame);
    if (!m || m->op != semwire::OP_RESP_RAW) throw std::runtime_error("fnwp: reply_from_marker of a frame that is not RESP_RAW");
    frogram::Json h = frogram::Json::parse(detail::sv(m->headers));
    if (size_t(h[dataplane::MARKER_HEADER].n) != body.size())
        throw std::runtime_error("fnwp: the landed payload is " + std::to_string(body.size()) + " B, the marker said " + std::to_string(size_t(h[dataplane::MARKER_HEADER].n)));
    return semwire::wrap_resp_raw(detail::sv(m->same_id), uint16_t(m->status.value_or(0)), "", body);
}
inline Answer ClientEngine::request(const std::string& method, const std::string& path, const std::string& body) {
    Out o = prepare(method, path, body);
    for (;;) {
        Step s = complete(o.ctx, t_(o.frame));
        if (s.done) return s.answer;
        o = s.redo == 1 ? reprepare(s.resend.ctx) : s.redo == 2 ? resend_raw(s.resend.ctx) : s.resend;   // [CLIENT_APPLIER_V1]
    }
}

// ------------------------------------------------------------------------------------------------ server (S5)
class ServerEngine {
public:
    using Exec = std::function<Answer(const std::string& method, const std::string& path, const std::string& body)>;
    struct Work {                                         // one request between decode and reply
        std::string frame;                                // set when the reply is decided at decode (miss, error)
        bool raw = false, consider_same = false; std::string req_hash, sem_in; detail::IKey ik;
        std::string method, path, body;
        bool body_pending = false; size_t body_bytes = 0;  // [DATA_MARKER_V1] the request's body is on the data socket
        bool fresh = false;                               // [ENGINE_LOCKLESS_V1] REQ_FULL: reset ik's response reference first
        std::string same_miss;                            // [ENGINE_LOCKLESS_V1] REQ_SAME_MISS: the writer answers this same_id
    };
    ServerEngine(std::string secret, std::string api, Exec e = nullptr) : secret_(std::move(secret)), api_(std::move(api)), exec_(std::move(e)) {
        // [PRIMED_LEARNED_ONCE_V1] learned once per process (for the first API asked), copied into every later engine
        auto learn_all = [](const std::string& api) { std::vector<std::pair<uint64_t, Template>> v;
            for (const auto& x : detail::primed()) { uint64_t op = 0; auto tp = detail::learn(api, x.m, x.p, x.b, x.a, op);
                if (tp) v.emplace_back(op, *tp); else fprintf(stderr, "[PRIMED_TEMPLATES_V1] server: an exemplar no longer learns (%s %s)\n", x.m.c_str(), x.p.c_str()); }
            return v; };
        static const std::string first_api = api_;
        static const std::vector<std::pair<uint64_t, Template>> learned = learn_all(first_api);
        if (api_ == first_api) { for (const auto& kv : learned) tpl_[kv.first] = kv.second; }
        else { for (auto& kv : learn_all(api_)) tpl_[kv.first] = std::move(kv.second); }
        if (!detail::primed().empty()) publish_templates(); }
    Work begin(const std::string& frame);                // decode, in the order frames arrive (the reader only)
    void reset_response(const detail::IKey& ik) { respref_.erase(ik); last_ans_.erase(ik); }   // the writer only
    // [SAME_AS_LAST_ANSWER_V1] the last answer each instance was given (the writer's)
    struct LastAns { int status = 0; std::string body, sid; };
    std::map<detail::IKey, LastAns> last_ans_;
    std::string remember(const detail::IKey& ik, int status, const std::string& body, const std::string& sid, std::string frame) {
        LastAns& l = last_ans_[ik]; l.status = status; l.body = body; l.sid = sid; return frame; }
    std::string same_miss_frame(const std::string& sid) {                               // the writer only
        auto h = by_sid_.find(sid); auto c = h == by_sid_.end() ? cache_.end() : cache_.find(h->second);
        if (c == cache_.end()) return semwire::wrap_error(410, "REQ_SAME_MISS: the answer RESP_SAME named is no longer held; the request was not run again");
        return semwire::wrap_resp_raw(c->second.same_id, uint16_t(c->second.status), "", c->second.body); }
    ~ServerEngine() { delete tpl_snap_.load(); for (auto* p : retired_) delete p; }
    std::string finish(const Work& w, const Answer& a);  // the reply frame; the caller puts it on the wire in this order
    // [DATA_MARKER_V1] The marker for a RAW reply whose body goes on the data socket. `body` receives the payload.
    static std::string reply_marker(const std::string& raw_frame, std::string& body) {
        auto m = semwire::try_parse(raw_frame);
        if (!m || m->op != semwire::OP_RESP_RAW) throw std::runtime_error("fnwp: reply_marker of a frame that is not RESP_RAW");
        body = detail::sv(m->body);
        return semwire::wrap_resp_raw(detail::sv(m->same_id), uint16_t(m->status.value_or(0)),
                                      std::string("{\"") + dataplane::MARKER_HEADER + "\":" + std::to_string(body.size()) + "}", "");
    }
    std::string handle(const std::string& frame) { Work w = begin(frame); if (!w.frame.empty()) return w.frame; return finish(w, exec_(w.method, w.path, w.body)); }
    const std::string& api() const { return api_; }
private:
    Work semantic_locked(const std::string& req_hash, const std::string& sem, bool full, bool consider_same);
    std::string secret_, api_; Exec exec_;
    std::map<uint64_t, Template> tpl_; std::map<detail::IKey, Dict> reqref_, respref_; std::map<detail::IKey, std::string> rr_tpl_;
    // [ENGINE_LOCKLESS_V1] the reader's view of the templates: an immutable snapshot, replaced whole by the writer
    using TplMap = std::map<uint64_t, Template>;
    std::atomic<const TplMap*> tpl_snap_{new TplMap}; std::vector<const TplMap*> retired_;
    void publish_templates() { retired_.push_back(tpl_snap_.exchange(new TplMap(tpl_), std::memory_order_acq_rel)); }
    // [ENGINE_LOCKLESS_V1] the reader's own repeat map: request hash -> the request it decoded (bounded)
    std::map<std::string, std::string> seen_; std::vector<std::string> seen_ring_ = std::vector<std::string>(4096); size_t seen_n_ = 0;
    void seen_put(const std::string& h, const std::string& sem) {   // [DECODE_ALLOC_ONCE_V1]
        auto it = seen_.find(h); if (it != seen_.end()) { it->second.assign(sem); return; }
        std::string& slot = seen_ring_[seen_n_++ % seen_ring_.size()];
        if (seen_n_ > seen_ring_.size()) {                       // full: the oldest entry's node is reused for this one
            auto nh = seen_.extract(slot);
            if (!nh.empty()) { nh.key().assign(h); nh.mapped().assign(sem); seen_.insert(std::move(nh)); slot.assign(h); return; }
        }
        seen_.emplace(h, sem); slot.assign(h); }
    // [SAME_MISS_FROM_CACHE_V1] an entry keeps the answer itself (status, body), so a client that does not hold what a
    // RESP_SAME named gets it back from here; by_sid_ finds the entry by that id. An answer over SAME_BODY_MAX is not
    // cached, so no SAME is ever said for it.
    static constexpr size_t SAME_BODY_MAX = 64 * 1024;
    struct CacheE { std::string sem_req, raw_hash, same_id; int status = 0; std::string body; };
    uint64_t sid_n_ = 0;                                       // [NO_ANSWER_HASH_V1] same_ids are minted
    std::string mint_sid() { std::string sid = secret_.substr(0, 8); uint64_t n = ++sid_n_;
        for (int i = 0; i < 8; ++i) { sid += char((n >> (8 * i)) & 0xff); }
        return sid; }
    std::map<std::string, CacheE> cache_; std::list<std::string> cache_lru_;       // the memory-only REPEAT cache
    std::map<std::string, std::string> by_sid_;                                   // same_id -> request hash
    void cache_put(const std::string& h, CacheE e) {
        if (e.body.size() > SAME_BODY_MAX) { auto o = cache_.find(h); if (o != cache_.end()) { by_sid_.erase(o->second.same_id); cache_.erase(o); cache_lru_.remove(h); } return; }
        if (!cache_.count(h)) { cache_lru_.push_back(h); if (cache_lru_.size() > 4096) { auto x = cache_.find(cache_lru_.front()); if (x != cache_.end()) { by_sid_.erase(x->second.same_id); cache_.erase(x); } cache_lru_.pop_front(); } }
        else by_sid_.erase(cache_[h].same_id);
        by_sid_[e.same_id] = h; cache_[h] = std::move(e);
    }
};
inline ServerEngine::Work ServerEngine::begin(const std::string& frame) {
    Work w; auto m = semwire::try_parse(frame);
    if (!m) { w.frame = semwire::wrap_error(400, "not FNW1"); return w; }
    if (m->op == semwire::OP_REQ_SAME_MISS) {            // [SAME_MISS_FROM_CACHE_V1] from the cache; nothing runs
        w.same_miss = detail::sv(m->same_id); return w;   // [ENGINE_LOCKLESS_V1] the writer owns the cache: it answers
    }
    if (m->op == semwire::OP_REQ_RAW) {
        frogram::Json q = frogram::Json::parse(detail::sv(m->payload));
        w.raw = true; w.req_hash = detail::sv(m->req_hash); w.method = q["method"].s; w.path = q["path"].s; w.body = q["body"].s;
        if (q[dataplane::MARKER_KEY].type == frogram::Json::Num) { w.body_pending = true; w.body_bytes = size_t(q[dataplane::MARKER_KEY].n); }
        return w;
    }
    if (m->op == semwire::OP_REQ_FULL) return semantic_locked(detail::sv(m->req_hash), detail::sv(m->payload), true, false);
    if (m->op == semwire::OP_REQ_DIFF) return semantic_locked(detail::sv(m->req_hash), detail::sv(m->payload), false, true);
    if (m->op == semwire::OP_REQ_REPEAT) {
        const std::string h = detail::sv(m->req_hash); auto c = seen_.find(h);
        if (c == seen_.end()) { w.frame = semwire::wrap_req_miss(h); return w; }
        const std::string sem = c->second;                       // [ENGINE_LOCKLESS_V1] the reader's own map
        return semantic_locked(h, sem, false, true);   // not FULL: decode against the request reference
    }
    w.frame = semwire::wrap_error(400, "op not spoken here"); return w;
}
inline ServerEngine::Work ServerEngine::semantic_locked(const std::string& req_hash, const std::string& sem_in, bool full, bool consider_same) {
    Work w; w.req_hash = req_hash; w.sem_in = sem_in; w.consider_same = consider_same;
    std::string sem = sem_in;                           // strip the origin trailer (daemon/engine/packet.py)
    if (sem.size() >= 8 + 5 && uint8_t(sem[sem.size() - 2]) == 0xFA && uint8_t(sem[sem.size() - 1]) == 0xCE && uint8_t(sem[sem.size() - 3]) == 1) {
        size_t tot = 5 + uint8_t(sem[sem.size() - 5]) + uint8_t(sem[sem.size() - 4]); if (tot <= sem.size()) sem.resize(sem.size() - tot);
    }
    if (sem.size() < 6) { w.frame = semwire::wrap_req_miss(req_hash); return w; }
    const uint64_t op = uint32_t(uint8_t(sem[2]) | uint8_t(sem[3]) << 8 | uint8_t(sem[4]) << 16 | uint32_t(uint8_t(sem[5])) << 24);
    const TplMap* tm = tpl_snap_.load(std::memory_order_acquire);   // [ENGINE_LOCKLESS_V1] the published snapshot
    auto t = tm->find(op);
    if (t == tm->end()) { w.frame = semwire::wrap_error(503, "no templates for opcode=" + std::to_string(op)); return w; }
    // [INSTANCE_REFERENCES_V1] the location travels in every frame: read it first, then take that instance's reference
    const auto& fo = t->second.req_fields;              // [TEMPLATE_PRECOMPUTED_V1]
    // [TEMPLATE_PRECOMPUTED_V1] no location fields: the instance is "" -- decode once, below, not twice
    const bool need_explicit = full || !t->second.loc_fields.empty();
    std::pair<semcodec::Fields, semcodec::Fields> explicit_vals;
    if (need_explicit) explicit_vals = semcodec::decode_request(sem, t->second.url_query_keys, fo, nullptr);
    w.ik = detail::IKey{op, need_explicit ? detail::location(t->second.loc_fields, explicit_vals.first, explicit_vals.second) : std::string()};
    if (full) w.fresh = true;                           // FIX 15: the proxy starts fresh -- the writer resets the reference
    auto rr = reqref_.find(w.ik);
    if (!full && rr == reqref_.end()) { w.frame = semwire::wrap_req_miss(req_hash); return w; }
    auto dec = full ? explicit_vals : semcodec::decode_request(sem, t->second.url_query_keys, fo, &rr->second);
    Dict merged; for (auto& kv : dec.first) merged.set(kv.first, kv.second); for (auto& kv : dec.second) merged.set(kv.first, kv.second);
    reqref_[w.ik] = std::move(merged); seen_put(req_hash, sem_in);   // [DECODE_ALLOC_ONCE_V1] moved, not copied
    w.method = t->second.method; w.path = dec.first.empty() ? t->second.url_fixed : semtpl::build_url_pre(t->second.url_path, t->second.url_static_q, dec.first);   // [TEMPLATE_PRECOMPUTED_V1]
    w.body = t->second.req_frag.size() ? semtpl::rebuild_body_pre(t->second.req_mode, t->second.req_fields, dec.second, t->second.req_mode == "json" ? &t->second.req_skel : nullptr) : "";
    if (w.method == "GET" || w.method == "DELETE") w.body.clear();
    return w;
}
inline std::string ServerEngine::finish(const Work& w, const Answer& a) {     // [ENGINE_LOCKLESS_V1] the writer only
    if (w.raw) {
        if (a.status < 400 && !a.body.empty()) {
            uint64_t op = 0; auto t = detail::learn(api_, w.method, w.path, w.body, a.body, op);
            if (t) {
                tpl_[op] = *t; publish_templates();
                if (t->ram_rows) {
                    semcodec::Fields uv; for (auto& kv : semtpl::extract_dynamic_query_vals(w.path, t->url_query_keys))
                        uv.emplace_back(kv.first, pyv::Str{kv.second});
                    detail::IKey ik{op, detail::location(t->loc_fields, uv, {})}; rr_tpl_[ik] = a.body; respref_.erase(ik);
                }
            }
        }
        return semwire::wrap_resp_raw(mint_sid(), uint16_t(a.status), "", a.body);
    }
    const detail::IKey& ik = w.ik; const uint64_t op = ik.first; const Template& t = tpl_.at(op); std::optional<semcodec::Fields> dyn;
    {   // [SAME_AS_LAST_ANSWER_V1] the very answer this instance was last given: SAME, and nothing else to do
        auto la = last_ans_.find(ik);
        if (w.consider_same && la != last_ans_.end() && la->second.status == int(a.status) && la->second.body == a.body && by_sid_.count(la->second.sid))
            return semwire::wrap_resp_same(la->second.sid); }
    if (a.status < 400) {
        if (t.ram_rows) { auto it = rr_tpl_.find(ik); if (it != rr_tpl_.end()) dyn = ramrows::extract(a.body, ramrows::learn(it->second)); }
        else {
            dyn = semtpl::extract(t.resp_frag, a.body, true);
            // [REPLY_EXTRACTION_PROVEN_V1] prove the template holds this answer before trusting the fields, as the
            // client proves a request: extract keeps only the template paths (absent ones read as null, others are
            // dropped), so an answer of another shape would be rebuilt by the far end as the old shape, silently.
            if (dyn && !a.body.empty()) {
                pyv::List vals; for (auto& kv : *dyn) vals.push_back(kv.second);
                bool same = false;
                try { std::string rebuilt = semtpl::rebuild_reply(t.resp_frag, vals);
                      if (rebuilt == a.body) { same = true; } else {                     // [PROOF_BYTES_FIRST_V1]
                      auto x = semtpl::detail::json_try(a.body), y = semtpl::detail::json_try(rebuilt);
                      same = (x && y) ? semtpl::python_eq(*x, *y) : rebuilt == a.body; } }
                catch (const std::exception&) { same = false; }
                if (!same) dyn.reset();                 // -> RESP_RAW below; nothing is guessed
            }
        }
    }
    if (dyn && !semcodec::fits_wire(*dyn)) dyn.reset();  // [HOLDABLE_V1] a field the wire cannot carry: the answer goes RAW
    if (!dyn) {                                         // [RAW_SIGNAL_V1]: this answer cannot be held -> RESP_RAW
        if (t.ram_rows && a.status < 400) { rr_tpl_[ik] = a.body; respref_.erase(ik); }
        // [RAW_ANSWER_IS_CACHED_FOR_REPEAT_V1] as the daemon's _build_raw_reply: the answer's hash is kept under the
        // request hash, so a REQ_REPEAT whose answer has not changed is RESP_SAME, and a REQ_REPEAT of it is not a
        // REQ_MISS. Without this every REPEAT after a RAW answer cost a MISS and a FULL (seen on every held read).
        auto c = cache_.find(w.req_hash);
        if (w.consider_same && c != cache_.end() && c->second.status == int(a.status) && c->second.body == a.body && c->second.same_id.size() == 16)
            return semwire::wrap_resp_same(c->second.same_id);                       // [NO_ANSWER_HASH_V1] the bytes held
        const std::string sid = mint_sid();
        if (a.status < 400) cache_put(w.req_hash, CacheE{w.sem_in, "", sid, int(a.status), a.body});
        return a.status < 400 ? remember(ik, int(a.status), a.body, sid, semwire::wrap_resp_raw(sid, uint16_t(a.status), "", a.body))
                              : semwire::wrap_resp_raw(sid, uint16_t(a.status), "", a.body);
    }
    // [NO_ANSWER_HASH_V1] unchanged = the same status and bytes as the answer held for this request: RESP_SAME, and the
    // response reference becomes these fields (as daemon_reply did). Otherwise RESP_DIFF against the reference, named
    // by a minted same_id, and held for the next time.
    auto c = cache_.find(w.req_hash);
    if (w.consider_same && c != cache_.end() && c->second.status == int(a.status) && c->second.body == a.body && c->second.same_id.size() == 16) {
        Dict ref; for (auto& kv : *dyn) ref.set(kv.first, kv.second);
        respref_[ik] = ref; return semwire::wrap_resp_same(c->second.same_id);
    }
    auto rp = respref_.find(ik);
    auto enc = diff_encode_response(op, *dyn, rp == respref_.end() ? nullptr : &rp->second);
    respref_[ik] = enc.second;
    const std::string sid = mint_sid();
    if (a.status < 400) cache_put(w.req_hash, CacheE{w.sem_in, "", sid, int(a.status), a.body});
    return a.status < 400 ? remember(ik, int(a.status), a.body, sid, semwire::wrap_resp_diff(sid, enc.first)) : semwire::wrap_resp_diff(sid, enc.first);
}
}  // namespace fnwp
