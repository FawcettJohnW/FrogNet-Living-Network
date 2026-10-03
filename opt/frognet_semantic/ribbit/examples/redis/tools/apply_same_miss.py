#!/usr/bin/env python3
################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#                                                              #
#  SPDX-License-Identifier: GPL-2.0-only                       #
#                                                              #
#  This program is free software; you can redistribute it      #
#  and/or modify it under the terms of the GNU General Public  #
#  License as published by the Free Software Foundation;       #
#  version 2 of the License, and no other version.             #
#                                                              #
#  This program is distributed in the hope that it will be     #
#  useful, but WITHOUT ANY WARRANTY; without even the implied  #
#  warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR     #
#  PURPOSE.  See the GNU General Public License for details.   #
#                                                              #
#  See COPYRIGHT and LICENSE at the root of this tree.         #
################################################################
"""[SAME_MISS_FROM_CACHE_V1] John 2026-09-29: "If a response had previously been sent as RESP_X and the other side said
it didn't have the answer, it is not necessary to run the prompt again. The correct full RESP can be constructed from
the cache."

Before: a client that received RESP_SAME naming an answer it no longer held (its SAME store keeps 256, the host's
REPEAT cache 4096) resent the whole request RAW, and the host ran it again -- a write applied twice, silently.
After: the client asks for the answer by its id (REQ_SAME_MISS [0x22][same_id:16]); the host answers from the cache
entry that let it say SAME -- which now keeps the answer's status and body -- as RESP_RAW, at decode, running nothing.
An entry no longer held is a loud error, never a re-run. Only answers up to SAME_BODY_MAX are cached, so SAME is only
ever said for an answer the host can give back whole.

Usage: apply_same_miss.py <ribbit_cpp dir>   (edits semwire.hpp and fnwp_engine.hpp in place; refuses on any anchor
that is not found exactly once)"""
import sys, os
d = sys.argv[1]
def patch(path, pairs):
    s = open(path).read()
    for old, new in pairs:
        n = s.count(old)
        if n != 1: sys.exit("apply_same_miss: %s: anchor found %d times: %r" % (os.path.basename(path), n, old[:70]))
        s = s.replace(old, new)
    open(path, "w").write(s)

patch(os.path.join(d, "semwire.hpp"), [
    ("OP_RESP_DIFF = 0x11, OP_RESP_SAME = 0x13, OP_RESP_RAW = 0x14, OP_REQ_MISS = 0x21, OP_ERROR = 0x30,",
     "OP_RESP_DIFF = 0x11, OP_RESP_SAME = 0x13, OP_RESP_RAW = 0x14, OP_REQ_MISS = 0x21, OP_REQ_SAME_MISS = 0x22, OP_ERROR = 0x30,"),
    ("inline std::string wrap_req_miss(std::string_view req_hash) { return detail::hash_only(OP_REQ_MISS, req_hash); }",
     "inline std::string wrap_req_miss(std::string_view req_hash) { return detail::hash_only(OP_REQ_MISS, req_hash); }\n"
     "// [SAME_MISS_FROM_CACHE_V1] the client does not hold the answer a RESP_SAME named: [0x22][same_id:16]\n"
     "inline std::string wrap_req_same_miss(std::string_view same_id) {\n"
     "    detail::need_same_id(same_id); std::string s = detail::head(OP_REQ_SAME_MISS, SAME_ID_LEN); s.append(same_id); return s;\n"
     "}"),
    ("    case OP_RESP_SAME:\n        if (f.size() < off + SAME_ID_LEN) throw ValueError(\"short RESP_SAME frame\");",
     "    case OP_REQ_SAME_MISS:\n        if (f.size() < off + SAME_ID_LEN) throw ValueError(\"short REQ_SAME_MISS frame\");\n"
     "        m.same_id = f.substr(off, SAME_ID_LEN);\n        return m;\n"
     "    case OP_RESP_SAME:\n        if (f.size() < off + SAME_ID_LEN) throw ValueError(\"short RESP_SAME frame\");"),
    ("    case OP_REQ_MISS: return \"REQ_MISS\";",
     "    case OP_REQ_MISS: return \"REQ_MISS\";\n    case OP_REQ_SAME_MISS: return \"REQ_SAME_MISS\";"),
])

patch(os.path.join(d, "fnwp_engine.hpp"), [
    # client: ask for the answer; never run the request again; touch no reference -- the answer, when it lands IN THIS
    # CALL'S TURN ([WIRE_ORDER_TURNS_V1]), makes exactly the move a SAME hit makes ([SAME_MOVES_REFERENCE_V1])
    ("        std::string method, path, body; bool raw = false, deferred = false; int attempts = 0;",
     "        std::string method, path, body; bool raw = false, deferred = false; int attempts = 0;\n"
     "        std::string same_miss;   // [SAME_MISS_FROM_CACHE_V1] the same_id this call asked the host for"),
    ("        if (sb == same_.end()) { reqref_.erase(ik); respref_.erase(ik); tpl_.erase({c->method, c->key}); Step s; s.resend = raw_locked(c); return s; }",
     "        if (sb == same_.end()) {\n"
     "            // [SAME_MISS_FROM_CACHE_V1] the host already ran this request and holds the answer it called SAME. Ask for\n"
     "            // it by its id; never send the request again (a second send runs it a second time). No reference moves\n"
     "            // here: the answer is applied in this call's turn, and moves the response reference as a SAME hit does.\n"
     "            c->same_miss = std::string(detail::sv(m->same_id));\n"
     "            Out o{semwire::wrap_req_same_miss(detail::sv(m->same_id)), c}; st_.bytes_out += o.frame.size();\n"
     "            Step s; s.resend = o; return s;\n"
     "        }"),
    ("    if (c->raw) return complete_raw_locked(c, rf);\n    auto m = semwire::try_parse(rf);                    // views into rf: rf outlives m",
     "    if (!c->same_miss.empty()) {                       // [SAME_MISS_FROM_CACHE_V1] the answer a SAME named, from the cache\n"
     "        auto m = semwire::try_parse(rf);\n"
     "        if (m && m->op == semwire::OP_ERROR) throw std::runtime_error(\"fnwp client: REQ_SAME_MISS: \" + std::string(detail::sv(m->payload)));\n"
     "        if (!m || m->op != semwire::OP_RESP_RAW) throw std::runtime_error(\"fnwp client: REQ_SAME_MISS answered with something other than RESP_RAW\");\n"
     "        const std::string body(detail::sv(m->body)); const Template& t = c->t; const detail::IKey& ik = c->ik;\n"
     "        std::optional<semcodec::Fields> held;           // exactly the SAME hit's move ([SAME_MOVES_REFERENCE_V1])\n"
     "        if (t.ram_rows) { auto rt = rr_tpl_.find(ik); if (rt != rr_tpl_.end()) held = ramrows::extract(body, ramrows::learn(rt->second)); }\n"
     "        else held = semtpl::extract(t.resp_frag, body, true);\n"
     "        if (!held) throw std::runtime_error(\"fnwp client: the answer RESP_SAME named is not one this instance's template can hold\");\n"
     "        Dict nr; for (auto& kv : *held) nr.set(kv.first, kv.second); respref_[ik] = nr;\n"
     "        same_put(c->same_miss, body); c->same_miss.clear();\n"
     "        Step s; s.done = true; s.answer = Answer{int(m->status.value_or(0)), body}; return s;\n"
     "    }\n"
     "    if (c->raw) return complete_raw_locked(c, rf);\n    auto m = semwire::try_parse(rf);                    // views into rf: rf outlives m"),
    # host: the cache keeps the answer
    ("    struct CacheE { std::string sem_req, raw_hash, same_id; };\n"
     "    std::map<std::string, CacheE> cache_; std::list<std::string> cache_lru_;       // the memory-only REPEAT cache\n"
     "    void cache_put(const std::string& h, CacheE e) {\n"
     "        if (!cache_.count(h)) { cache_lru_.push_back(h); if (cache_lru_.size() > 4096) { cache_.erase(cache_lru_.front()); cache_lru_.pop_front(); } }\n"
     "        cache_[h] = std::move(e);\n"
     "    }",
     "    // [SAME_MISS_FROM_CACHE_V1] an entry keeps the answer itself (status, body), so a client that does not hold what a\n"
     "    // RESP_SAME named gets it back from here; by_sid_ finds the entry by that id. The ENTRY always stays -- it is the\n"
     "    // REPEAT cache and the base the next answer is decided against; an answer over SAME_BODY_MAX is simply not kept\n"
     "    // (held = false), and SAME is said only for an answer held here, as the Python daemon's answer_held.\n"
     "    static constexpr size_t SAME_BODY_MAX = 64 * 1024;\n"
     "    struct CacheE { std::string sem_req, raw_hash, same_id; int status = 0; std::string body; bool held = false; };\n"
     "    std::map<std::string, CacheE> cache_; std::list<std::string> cache_lru_;       // the memory-only REPEAT cache\n"
     "    std::map<std::string, std::string> by_sid_;                                   // same_id -> request hash (held answers)\n"
     "    void cache_put(const std::string& h, CacheE e) {\n"
     "        e.held = e.body.size() <= SAME_BODY_MAX; if (!e.held) { e.body.clear(); e.body.shrink_to_fit(); }\n"
     "        if (!cache_.count(h)) { cache_lru_.push_back(h); if (cache_lru_.size() > 4096) { auto x = cache_.find(cache_lru_.front()); if (x != cache_.end()) { by_sid_.erase(x->second.same_id); cache_.erase(x); } cache_lru_.pop_front(); } }\n"
     "        else by_sid_.erase(cache_[h].same_id);\n"
     "        if (e.held) by_sid_[e.same_id] = h;\n"
     "        cache_[h] = std::move(e);\n"
     "    }"),
    ("        if (w.consider_same && c != cache_.end() && c->second.raw_hash == raw_hash) return semwire::wrap_resp_same(c->second.same_id);",
     "        if (w.consider_same && c != cache_.end() && c->second.held && c->second.raw_hash == raw_hash) return semwire::wrap_resp_same(c->second.same_id);"),
    ("    auto c = cache_.find(w.req_hash); std::optional<Cached> cached; if (c != cache_.end()) cached = Cached{c->second.raw_hash, c->second.same_id};",
     "    auto c = cache_.find(w.req_hash); std::optional<Cached> cached; if (c != cache_.end() && c->second.held) cached = Cached{c->second.raw_hash, c->second.same_id};   // SAME only for a held answer"),
    ("        if (a.status < 400) cache_put(w.req_hash, CacheE{w.sem_in, raw_hash, sid});",
     "        if (a.status < 400) cache_put(w.req_hash, CacheE{w.sem_in, raw_hash, sid, int(a.status), a.body});"),
    ("    if (r.cache) cache_put(w.req_hash, CacheE{w.sem_in, r.cache->raw_hash, r.cache->same_id});",
     "    if (r.cache) cache_put(w.req_hash, CacheE{w.sem_in, r.cache->raw_hash, r.cache->same_id, int(a.status), a.body});"),
    # host: answer a REQ_SAME_MISS at decode from the cache, running nothing
    ("    Work w; auto m = semwire::try_parse(frame);\n"
     "    if (!m) { w.frame = semwire::wrap_error(400, \"not FNW1\"); return w; }\n"
     "    std::lock_guard<std::mutex> g(m_);",
     "    Work w; auto m = semwire::try_parse(frame);\n"
     "    if (!m) { w.frame = semwire::wrap_error(400, \"not FNW1\"); return w; }\n"
     "    std::lock_guard<std::mutex> g(m_);\n"
     "    if (m->op == semwire::OP_REQ_SAME_MISS) {            // [SAME_MISS_FROM_CACHE_V1] from the cache; nothing runs\n"
     "        auto h = by_sid_.find(detail::sv(m->same_id)); auto c = h == by_sid_.end() ? cache_.end() : cache_.find(h->second);\n"
     "        if (c == cache_.end()) { w.frame = semwire::wrap_error(410, \"REQ_SAME_MISS: the answer RESP_SAME named is no longer held; the request was not run again\"); return w; }\n"
     "        w.frame = semwire::wrap_resp_raw(c->second.same_id, uint16_t(c->second.status), \"\", c->second.body); return w;\n"
     "    }"),
])
print("apply_same_miss: applied")
