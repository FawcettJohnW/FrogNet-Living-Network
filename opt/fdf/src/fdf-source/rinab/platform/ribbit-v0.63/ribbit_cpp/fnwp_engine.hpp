/***************************************************************
 *  Copyright (C) 2016-2026 Fawcett Innovations LLC            *
 *                                                             *
 *  SPDX-License-Identifier: GPL-2.0-only                      *
 *                                                             *
 *  This program is free software; you can redistribute it     *
 *  and/or modify it under the terms of the GNU General Public *
 *  License as published by the Free Software Foundation;      *
 *  version 2 of the License, and no other version.            *
 *                                                             *
 *  This program is distributed in the hope that it will be    *
 *  useful, but WITHOUT ANY WARRANTY; without even the implied *
 *  warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR    *
 *  PURPOSE.  See the GNU General Public License for details.  *
 *                                                             *
 *  See COPYRIGHT and LICENSE at the root of this tree.        *
 **************************************************************/
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
        std::string key; uint64_t op = 0; detail::IKey ik; Template t;
    };
    struct Out { std::string frame; std::shared_ptr<Ctx> ctx; };
    struct Step { bool done = false; Answer answer; Out resend; };
    ClientEngine(std::string target, std::string origin, std::string api, Transport t = nullptr)
        : target_(std::move(target)), origin_(std::move(origin)), api_(std::move(api)), t_(std::move(t)) {}
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
    Stats stats() const { std::lock_guard<std::mutex> g(m_); return st_; }
private:
    struct Key { std::string m, k; bool operator<(const Key& o) const { return m < o.m || (m == o.m && k < o.k); } };
    Out prepare_locked(std::shared_ptr<Ctx> c);
    Out raw_locked(std::shared_ptr<Ctx> c);
    Step complete_raw_locked(const std::shared_ptr<Ctx>& c, const std::string& rf);
    Step complete_locked(std::unique_lock<std::mutex>& l, const std::shared_ptr<Ctx>& c, const std::string& rf);
    std::string target_, origin_, api_; Transport t_; Stats st_; mutable std::mutex m_;
    std::condition_variable pend_cv_; std::set<detail::IKey> pending_;    // instances whose RAW answer is still landing
    std::map<Key, Template> tpl_; std::map<detail::IKey, Dict> reqref_, respref_; std::map<detail::IKey, std::string> rr_tpl_;
    std::map<std::string, std::string> same_; std::list<std::string> same_lru_;
    void same_put(const std::string& sid, const std::string& body) {
        if (!same_.count(sid)) { same_lru_.push_back(sid); if (same_lru_.size() > 256) { same_.erase(same_lru_.front()); same_lru_.pop_front(); } }
        same_[sid] = body;
    }
};
inline ClientEngine::Out ClientEngine::prepare(const std::string& method, const std::string& path, const std::string& body) {
    auto c = std::make_shared<Ctx>(); c->method = method; c->path = path; c->body = body;
    std::lock_guard<std::mutex> g(m_); return prepare_locked(c);
}
inline ClientEngine::Out ClientEngine::raw_locked(std::shared_ptr<Ctx> c) {
    c->raw = true; ++st_.raw; std::string http = detail::http_json(c->method, c->path, c->body);
    Out o{semwire::wrap_req_raw(detail::sha16(target_ + '\0' + http), http), c}; st_.bytes_out += o.frame.size(); return o;
}
inline ClientEngine::Out ClientEngine::prepare_locked(std::shared_ptr<Ctx> c) {
    c->raw = false; c->key = semtpl::canonical_semantic_key(c->method, c->path, c->body);
    auto it = tpl_.find({c->method, c->key});
    if (it == tpl_.end()) return raw_locked(c);
    c->t = it->second; c->op = semtpl::opcode_for(c->key);
    auto jv = semtpl::extract(c->t.req_frag, c->body, false);
    if (!jv) return raw_locked(c);
    if (!semcodec::fits_wire(*jv)) return raw_locked(c);   // [HOLDABLE_V1] a field the wire cannot carry: the request goes RAW
    if (!c->body.empty()) {                             // prove the template holds this body before trusting the fields
        std::string rebuilt = semtpl::rebuild_body(c->t.req_frag, *jv);
        auto a = semtpl::detail::json_try(c->body), b = semtpl::detail::json_try(rebuilt);
        bool same = (a && b) ? semtpl::python_eq(*a, *b) : rebuilt == c->body;
        if (!same) return raw_locked(c);
    }
    semcodec::Fields uv; for (auto& kv : semtpl::extract_dynamic_query_vals(c->path, c->t.url_query_keys)) uv.emplace_back(kv.first, pyv::Str{kv.second});
    c->ik = detail::IKey{c->op, detail::location(c->t.loc_fields, uv, *jv)};
    auto rr = reqref_.find(c->ik);
    Built b = build_request(target_, c->path, c->op, uv, *jv, rr == reqref_.end() ? nullptr : &rr->second, origin_, target_);
    if (b.req_type == "REQ_DIFF" && !c->t.loc_fields.empty()) {    // the location travels with every difference
        Dict ref = detail::without(rr->second, c->t.loc_fields);
        b = build_request(target_, c->path, c->op, uv, *jv, &ref, origin_, target_);
    }
    if (b.req_type == "REQ_REPEAT") ++st_.repeat; else if (b.req_type == "REQ_FULL") ++st_.full; else ++st_.diff;
    reqref_[c->ik] = b.new_reference;                   // committed as it leaves, as the server commits on decode
    st_.bytes_out += b.frame.size();
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
            tpl_[{c->method, semtpl::canonical_semantic_key(c->method, c->path, c->body)}] = *t;
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
inline void ClientEngine::defer(const std::shared_ptr<Ctx>& c) {
    std::lock_guard<std::mutex> g(m_); c->deferred = true; if (!c->raw) pending_.insert(c->ik);
}
inline ClientEngine::Step ClientEngine::complete(const std::shared_ptr<Ctx>& c, const std::string& rf) {
    std::unique_lock<std::mutex> l(m_); st_.bytes_in += rf.size();
    if (!c->raw && !c->deferred) pend_cv_.wait(l, [&] { return !pending_.count(c->ik); });   // the instance's RAW answer first
    try {
        Step s = complete_locked(l, c, rf);
        if (c->deferred && !c->raw) { pending_.erase(c->ik); pend_cv_.notify_all(); }
        return s;
    } catch (...) { if (c->deferred && !c->raw) { pending_.erase(c->ik); pend_cv_.notify_all(); } throw; }
}
inline ClientEngine::Step ClientEngine::complete_locked(std::unique_lock<std::mutex>&, const std::shared_ptr<Ctx>& c, const std::string& rf) {
    if (c->raw) return complete_raw_locked(c, rf);
    auto m = semwire::try_parse(rf);                    // views into rf: rf outlives m
    if (!m) throw std::runtime_error("fnwp client: non-FNW1 reply");
    const Template& t = c->t; const detail::IKey& ik = c->ik;
    if (m->op == semwire::OP_REQ_MISS) {               // resend FULL
        ++st_.miss; reqref_.erase(ik);
        if (++c->attempts >= 2) throw std::runtime_error("fnwp client: REQ_MISS from the far end; exhausted retries");
        Step s; s.resend = prepare_locked(c); return s;
    }
    if (m->op == semwire::OP_ERROR) {
        if (detail::sv(m->payload).find("no templates for opcode") != std::string::npos) {
            tpl_.erase({c->method, c->key}); Step s; s.resend = raw_locked(c); return s; }
        throw std::runtime_error("fnwp client: far end error " + std::to_string(m->status.value_or(0)) + ": " + detail::sv(m->payload));
    }
    if (m->op == semwire::OP_RESP_SAME) {
        ++st_.same; auto sb = same_.find(detail::sv(m->same_id));
        if (sb == same_.end()) { reqref_.erase(ik); respref_.erase(ik); tpl_.erase({c->method, c->key}); Step s; s.resend = raw_locked(c); return s; }
        // [SAME_MOVES_REFERENCE_V1] the server's reply decision sets its response reference to the answer a SAME names
        std::optional<semcodec::Fields> held;
        if (t.ram_rows) { auto rt = rr_tpl_.find(ik); if (rt != rr_tpl_.end()) held = ramrows::extract(sb->second, ramrows::learn(rt->second)); }
        else held = semtpl::extract(t.resp_frag, sb->second, true);
        if (!held) throw std::runtime_error("fnwp client: RESP_SAME names a body this instance's template cannot hold");
        Dict nr; for (auto& kv : *held) nr.set(kv.first, kv.second); respref_[ik] = nr;
        Step s; s.done = true; s.answer = Answer{200, sb->second}; return s;
    }
    if (m->op == semwire::OP_RESP_DIFF) {
        ++st_.rdiff; auto rp = respref_.find(ik); const Dict* ref = rp == respref_.end() ? nullptr : &rp->second; std::string out;
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
        ++st_.rraw; same_put(detail::sv(m->same_id), detail::sv(m->body));
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
        o = s.resend;
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
    };
    ServerEngine(std::string secret, std::string api, Exec e = nullptr) : secret_(std::move(secret)), api_(std::move(api)), exec_(std::move(e)) {}
    Work begin(const std::string& frame);                // decode, in the order frames arrive
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
    std::string secret_, api_; Exec exec_; std::mutex m_;
    std::map<uint64_t, Template> tpl_; std::map<detail::IKey, Dict> reqref_, respref_; std::map<detail::IKey, std::string> rr_tpl_;
    struct CacheE { std::string sem_req, raw_hash, same_id; };
    std::map<std::string, CacheE> cache_; std::list<std::string> cache_lru_;       // the memory-only REPEAT cache
    void cache_put(const std::string& h, CacheE e) {
        if (!cache_.count(h)) { cache_lru_.push_back(h); if (cache_lru_.size() > 4096) { cache_.erase(cache_lru_.front()); cache_lru_.pop_front(); } }
        cache_[h] = std::move(e);
    }
};
inline ServerEngine::Work ServerEngine::begin(const std::string& frame) {
    Work w; auto m = semwire::try_parse(frame);
    if (!m) { w.frame = semwire::wrap_error(400, "not FNW1"); return w; }
    std::lock_guard<std::mutex> g(m_);
    if (m->op == semwire::OP_REQ_RAW) {
        frogram::Json q = frogram::Json::parse(detail::sv(m->payload));
        w.raw = true; w.req_hash = detail::sv(m->req_hash); w.method = q["method"].s; w.path = q["path"].s; w.body = q["body"].s;
        if (q[dataplane::MARKER_KEY].type == frogram::Json::Num) { w.body_pending = true; w.body_bytes = size_t(q[dataplane::MARKER_KEY].n); }
        return w;
    }
    if (m->op == semwire::OP_REQ_FULL) return semantic_locked(detail::sv(m->req_hash), detail::sv(m->payload), true, false);
    if (m->op == semwire::OP_REQ_DIFF) return semantic_locked(detail::sv(m->req_hash), detail::sv(m->payload), false, true);
    if (m->op == semwire::OP_REQ_REPEAT) {
        const std::string h = detail::sv(m->req_hash); auto c = cache_.find(h);
        if (c == cache_.end()) { w.frame = semwire::wrap_req_miss(h); return w; }
        return semantic_locked(h, c->second.sem_req, false, true);   // not FULL: decode against the request reference
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
    auto t = tpl_.find(op);
    if (t == tpl_.end()) { w.frame = semwire::wrap_error(503, "no templates for opcode=" + std::to_string(op)); return w; }
    // [INSTANCE_REFERENCES_V1] the location travels in every frame: read it first, then take that instance's reference
    const auto fo = detail::fields_of(t->second.req_frag);
    auto explicit_vals = semcodec::decode_request(sem, t->second.url_query_keys, fo, nullptr);
    w.ik = detail::IKey{op, detail::location(t->second.loc_fields, explicit_vals.first, explicit_vals.second)};
    if (full) respref_.erase(w.ik);                     // FIX 15: the proxy starts fresh -- send every field
    auto rr = reqref_.find(w.ik);
    if (!full && rr == reqref_.end()) { w.frame = semwire::wrap_req_miss(req_hash); return w; }
    auto dec = full ? explicit_vals : semcodec::decode_request(sem, t->second.url_query_keys, fo, &rr->second);
    Dict merged; for (auto& kv : dec.first) merged.set(kv.first, kv.second); for (auto& kv : dec.second) merged.set(kv.first, kv.second);
    reqref_[w.ik] = merged;
    w.method = t->second.method; w.path = semtpl::build_url(t->second.url_static, dec.first);
    w.body = t->second.req_frag.size() ? semtpl::rebuild_body(t->second.req_frag, dec.second) : "";
    if (w.method == "GET" || w.method == "DELETE") w.body.clear();
    return w;
}
inline std::string ServerEngine::finish(const Work& w, const Answer& a) {
    std::lock_guard<std::mutex> g(m_);
    if (w.raw) {
        if (a.status < 400 && !a.body.empty()) {
            uint64_t op = 0; auto t = detail::learn(api_, w.method, w.path, w.body, a.body, op);
            if (t) {
                tpl_[op] = *t;
                if (t->ram_rows) {
                    semcodec::Fields uv; for (auto& kv : semtpl::extract_dynamic_query_vals(w.path, t->url_query_keys))
                        uv.emplace_back(kv.first, pyv::Str{kv.second});
                    detail::IKey ik{op, detail::location(t->loc_fields, uv, {})}; rr_tpl_[ik] = a.body; respref_.erase(ik);
                }
            }
        }
        return semwire::wrap_resp_raw(same_id(secret_, w.req_hash, detail::sha32(a.body)), uint16_t(a.status), "", a.body);
    }
    const detail::IKey& ik = w.ik; const uint64_t op = ik.first; const Template& t = tpl_.at(op); std::optional<semcodec::Fields> dyn;
    if (a.status < 400) {
        if (t.ram_rows) { auto it = rr_tpl_.find(ik); if (it != rr_tpl_.end()) dyn = ramrows::extract(a.body, ramrows::learn(it->second)); }
        else dyn = semtpl::extract(t.resp_frag, a.body, true);
    }
    if (dyn && !semcodec::fits_wire(*dyn)) dyn.reset();  // [HOLDABLE_V1] a field the wire cannot carry: the answer goes RAW
    if (!dyn) {                                         // [RAW_SIGNAL_V1]: this answer cannot be held -> RESP_RAW
        if (t.ram_rows && a.status < 400) { rr_tpl_[ik] = a.body; respref_.erase(ik); }
        // [RAW_ANSWER_IS_CACHED_FOR_REPEAT_V1] as the daemon's _build_raw_reply: the answer's hash is kept under the
        // request hash, so a REQ_REPEAT whose answer has not changed is RESP_SAME, and a REQ_REPEAT of it is not a
        // REQ_MISS. Without this every REPEAT after a RAW answer cost a MISS and a FULL (seen on every held read).
        const std::string raw_hash = detail::sha32(a.body);
        auto c = cache_.find(w.req_hash);
        if (w.consider_same && c != cache_.end() && c->second.raw_hash == raw_hash) return semwire::wrap_resp_same(c->second.same_id);
        const std::string sid = same_id(secret_, w.req_hash, raw_hash);
        if (a.status < 400) cache_put(w.req_hash, CacheE{w.sem_in, raw_hash, sid});
        return semwire::wrap_resp_raw(sid, uint16_t(a.status), "", a.body);
    }
    auto c = cache_.find(w.req_hash); std::optional<Cached> cached; if (c != cache_.end()) cached = Cached{c->second.raw_hash, c->second.same_id};
    auto rp = respref_.find(ik);
    Reply r = daemon_reply(secret_, w.req_hash, *dyn, a.status, rp == respref_.end() ? nullptr : &rp->second, cached, op, w.consider_same);
    if (r.new_reference) respref_[ik] = *r.new_reference;
    if (r.cache) cache_put(w.req_hash, CacheE{w.sem_in, r.cache->raw_hash, r.cache->same_id});
    return r.frame;
}
}  // namespace fnwp
