"""apply_no_answer_hash.py FNWP_ENGINE_HPP -- [NO_ANSWER_HASH_V1] (John, 2026-10-04: the RAM host detecting a change
by SHA-256-hashing every answer and comparing hashes is a bug; fixing it is authorized.)

Before: every answer the host sent was hashed -- response_body_hash (SHA-256 of a repr of every field) and same_id
(HMAC-SHA-256 of request hash + that hash) on the semantic path, sha32 + HMAC on the raw path -- 38 k instructions per
answer measured (callgrind, rr), to decide "unchanged" and to name the answer.

Now:
  * "unchanged" is decided against the answer the engine already holds for that request (CacheE keeps it, for
    SAME-miss recovery): same status and the same bytes. Exact -- no hash, no chance of collision.
  * a same_id NAMES an answer; it is minted: 8 bytes of the engine's secret (per engine) + an 8-byte counter.
    Unique for the engine's life; the client keeps answers by it as before.
  * the response reference and RESP_DIFF encoding are unchanged.
Applied at build after apply_reply_proof.py; the vendored v0.63 source stays untouched."""
import sys
p = sys.argv[1]; s = open(p).read()
def rep(a, b, n=1):
    global s
    assert s.count(a) == n, (a[:80], s.count(a)); s = s.replace(a, b)

# a minted same_id
rep('    struct CacheE { std::string sem_req, raw_hash, same_id; int status = 0; std::string body; };',
    '    struct CacheE { std::string sem_req, raw_hash, same_id; int status = 0; std::string body; };\n'
    '    uint64_t sid_n_ = 0;                                       // [NO_ANSWER_HASH_V1] same_ids are minted\n'
    '    std::string mint_sid() { std::string sid = secret_.substr(0, 8); uint64_t n = ++sid_n_;\n'
    '        for (int i = 0; i < 8; ++i) { sid += char((n >> (8 * i)) & 0xff); }\n        return sid; }')
# raw path, learning a template
rep('        return semwire::wrap_resp_raw(same_id(secret_, w.req_hash, detail::sha32(a.body)), uint16_t(a.status), "", a.body);',
    '        return semwire::wrap_resp_raw(mint_sid(), uint16_t(a.status), "", a.body);')
# raw path, an answer that cannot be held
rep('''        const std::string raw_hash = detail::sha32(a.body);
        auto c = cache_.find(w.req_hash);
        if (w.consider_same && c != cache_.end() && c->second.raw_hash == raw_hash) return semwire::wrap_resp_same(c->second.same_id);
        const std::string sid = same_id(secret_, w.req_hash, raw_hash);
        if (a.status < 400) cache_put(w.req_hash, CacheE{w.sem_in, raw_hash, sid, int(a.status), a.body});''',
'''        auto c = cache_.find(w.req_hash);
        if (w.consider_same && c != cache_.end() && c->second.status == int(a.status) && c->second.body == a.body && c->second.same_id.size() == 16)
            return semwire::wrap_resp_same(c->second.same_id);                       // [NO_ANSWER_HASH_V1] the bytes held
        const std::string sid = mint_sid();
        if (a.status < 400) cache_put(w.req_hash, CacheE{w.sem_in, "", sid, int(a.status), a.body});''')
# semantic path: SAME against the answer held, else RESP_DIFF with a minted same_id
rep('''    auto c = cache_.find(w.req_hash); std::optional<Cached> cached; if (c != cache_.end()) cached = Cached{c->second.raw_hash, c->second.same_id};
    auto rp = respref_.find(ik);
    Reply r = daemon_reply(secret_, w.req_hash, *dyn, a.status, rp == respref_.end() ? nullptr : &rp->second, cached, op, w.consider_same);
    if (r.new_reference) respref_[ik] = *r.new_reference;
    if (r.cache) cache_put(w.req_hash, CacheE{w.sem_in, r.cache->raw_hash, r.cache->same_id, int(a.status), a.body});
    return r.frame;''',
'''    // [NO_ANSWER_HASH_V1] unchanged = the same status and bytes as the answer held for this request: RESP_SAME, and the
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
    return semwire::wrap_resp_diff(sid, enc.first);''')
open(p, 'w').write(s)
print("no answer hash applied")
