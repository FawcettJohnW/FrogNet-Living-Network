"""apply_same_as_last.py FNWP_ENGINE_HPP -- [SAME_AS_LAST_ANSWER_V1] an answer identical to the last answer this
instance was given is RESP_SAME, decided BEFORE any extraction, proof, difference or compression.

The engine already answered SAME for a repeated REQUEST whose answer had not changed. But most answers that repeat do so
under requests that never repeat: every posted SDU is a new request (its sequence number moves) and every one is
answered {"ok":true}. Each of those answers was extracted, proved (rebuilt and both parsed), differenced, and offered to
LZ4 -- callgrind: ServerEngine::finish was 39% of the host on rr.

Now the writer keeps, per instance, the status, bytes and same_id of the last answer it sent. Same status and same bytes
(an exact comparison; nothing is hashed) -> RESP_SAME naming that same_id. Nothing else to do on either side: the
instance's response reference is already those fields (they were the last answer's), the client holds that body under
that same_id (it caches every RAW and DIFF answer by its id), and the server holds it for a SAME-miss (by_sid_). A
REQ_FULL reset clears the instance's last answer with its reference. Applied at build after apply_engine_lockless.py."""
import sys
p = sys.argv[1]; s = open(p).read()
def rep(a, b, n=1):
    global s
    assert s.count(a) == n, (a[:90], s.count(a)); s = s.replace(a, b)
rep('''    void reset_response(const detail::IKey& ik) { respref_.erase(ik); }                 // the writer only''',
    '''    void reset_response(const detail::IKey& ik) { respref_.erase(ik); last_ans_.erase(ik); }   // the writer only
    // [SAME_AS_LAST_ANSWER_V1] the last answer each instance was given (the writer's)
    struct LastAns { int status = 0; std::string body, sid; };
    std::map<detail::IKey, LastAns> last_ans_;
    std::string remember(const detail::IKey& ik, int status, const std::string& body, const std::string& sid, std::string frame) {
        LastAns& l = last_ans_[ik]; l.status = status; l.body = body; l.sid = sid; return frame; }''')
rep('''    const detail::IKey& ik = w.ik; const uint64_t op = ik.first; const Template& t = tpl_.at(op); std::optional<semcodec::Fields> dyn;''',
    '''    const detail::IKey& ik = w.ik; const uint64_t op = ik.first; const Template& t = tpl_.at(op); std::optional<semcodec::Fields> dyn;
    {   // [SAME_AS_LAST_ANSWER_V1] the very answer this instance was last given: SAME, and nothing else to do
        auto la = last_ans_.find(ik);
        if (w.consider_same && la != last_ans_.end() && la->second.status == int(a.status) && la->second.body == a.body && by_sid_.count(la->second.sid))
            return semwire::wrap_resp_same(la->second.sid); }''')
rep('''        if (a.status < 400) cache_put(w.req_hash, CacheE{w.sem_in, "", sid, int(a.status), a.body});
        return semwire::wrap_resp_raw(sid, uint16_t(a.status), "", a.body);
    }''',
    '''        if (a.status < 400) cache_put(w.req_hash, CacheE{w.sem_in, "", sid, int(a.status), a.body});
        return a.status < 400 ? remember(ik, int(a.status), a.body, sid, semwire::wrap_resp_raw(sid, uint16_t(a.status), "", a.body))
                              : semwire::wrap_resp_raw(sid, uint16_t(a.status), "", a.body);
    }''')
rep('''    if (a.status < 400) cache_put(w.req_hash, CacheE{w.sem_in, "", sid, int(a.status), a.body});
    return semwire::wrap_resp_diff(sid, enc.first);''',
    '''    if (a.status < 400) cache_put(w.req_hash, CacheE{w.sem_in, "", sid, int(a.status), a.body});
    return a.status < 400 ? remember(ik, int(a.status), a.body, sid, semwire::wrap_resp_diff(sid, enc.first)) : semwire::wrap_resp_diff(sid, enc.first);''')
open(p, 'w').write(s)
print("same as last applied")
