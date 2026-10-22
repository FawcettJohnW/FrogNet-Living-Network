"""apply_same_moves_reference.py FNWP_ENGINE_HPP -- [SAME_MOVES_BOTH_REFERENCES_V1] every RESP_SAME moves the server's
response reference exactly as the client's RESP_SAME moves its own ([SAME_MOVES_REFERENCE_V1], ClientEngine::complete):
  ram_rows: the fields ramrows::extract takes from the named body with this instance's RAM-answer template
  json etc: semtpl::extract(resp_frag, body, true)
and a body the client could not hold is never answered SAME (the client would throw on it); it goes on to RAW or DIFF.

Why. The server had three SAME paths and only one moved its reference:
  [SAME_AS_LAST_ANSWER_V1]          moved nothing (it assumed the reference was already that answer's)
  [RAW_ANSWER_IS_CACHED_FOR_REPEAT_V1] moved nothing, and for a RAM-answer instance changed the template and dropped the
                                    reference first, though the client changes neither on a SAME
  [NO_ANSWER_HASH_V1]               moved it
The client moves its reference on every SAME. Two requests that share one instance (a template with no location
fields: resolve name=topo and resolve name=grp, op=daf) therefore left the ends holding different references, and the
next RESP_DIFF was applied by the client to the wrong one: a body with grp's name and topo's members, delivered as an
answer, or -- when the wrong list had two entries -- Declined("pair_heuristic") (experiments/day-claims E2, scenario 14;
the E1 wrong-member answers were the same defect through the first path; [SAME_KEEPS_LAST_ANSWER_V1] repaired only that
path's bookkeeping). Applied at build after apply_same_as_last.py."""
import sys
p = sys.argv[1]; s = open(p).read()
def rep(a, b, n=1):
    global s
    assert s.count(a) == n, (a[:90], s.count(a)); s = s.replace(a, b)

# the helper, beside remember()
rep('''    std::string remember(const detail::IKey& ik, int status, const std::string& body, const std::string& sid, std::string frame) {
        LastAns& l = last_ans_[ik]; l.status = status; l.body = body; l.sid = sid; return frame; }''',
    '''    std::string remember(const detail::IKey& ik, int status, const std::string& body, const std::string& sid, std::string frame) {
        LastAns& l = last_ans_[ik]; l.status = status; l.body = body; l.sid = sid; return frame; }
    // [SAME_MOVES_BOTH_REFERENCES_V1] the reference the client will hold after a RESP_SAME naming this body, or nothing if
    // the client cannot hold it (then the answer must not be SAME). The same calls, in the same order, as the client's.
    std::optional<Dict> same_reference(const detail::IKey& ik, const Template& t, const std::string& body) {
        std::optional<semcodec::Fields> held;
        try {
            if (t.ram_rows) { auto it = rr_tpl_.find(ik); if (it != rr_tpl_.end()) held = ramrows::extract(body, ramrows::learn(it->second)); }
            else held = semtpl::extract(t.resp_frag, body, true);
        } catch (const std::exception&) { return std::nullopt; }
        if (!held) return std::nullopt;
        Dict d; for (auto& kv : *held) d.set(kv.first, kv.second); return d; }''')

# path 1: same as the last answer
rep('''        if (w.consider_same && la != last_ans_.end() && la->second.status == int(a.status) && la->second.body == a.body && by_sid_.count(la->second.sid))
            return semwire::wrap_resp_same(la->second.sid); }''',
    '''        if (w.consider_same && la != last_ans_.end() && la->second.status == int(a.status) && la->second.body == a.body && by_sid_.count(la->second.sid))
            if (auto r = same_reference(ik, t, a.body)) { respref_[ik] = std::move(*r); return semwire::wrap_resp_same(la->second.sid); } }   // [SAME_MOVES_BOTH_REFERENCES_V1]''')

# path 2: an answer that cannot be held, cached for a repeat: decide SAME before touching the RAW side's state
rep('''        if (t.ram_rows && a.status < 400) { rr_tpl_[ik] = a.body; respref_.erase(ik); }
        // [RAW_ANSWER_IS_CACHED_FOR_REPEAT_V1]''',
    '''        // [RAW_ANSWER_IS_CACHED_FOR_REPEAT_V1]''')
rep('''        if (w.consider_same && c != cache_.end() && c->second.status == int(a.status) && c->second.body == a.body && c->second.same_id.size() == 16)
            return semwire::wrap_resp_same(c->second.same_id);                       // [NO_ANSWER_HASH_V1] the bytes held
        const std::string sid = mint_sid();''',
    '''        if (w.consider_same && c != cache_.end() && c->second.status == int(a.status) && c->second.body == a.body && c->second.same_id.size() == 16)
            if (auto r = same_reference(ik, t, a.body)) {                          // [SAME_MOVES_BOTH_REFERENCES_V1]
                respref_[ik] = std::move(*r);
                return remember(ik, int(a.status), a.body, c->second.same_id, semwire::wrap_resp_same(c->second.same_id)); }
        if (t.ram_rows && a.status < 400) { rr_tpl_[ik] = a.body; respref_.erase(ik); }   // RAW: as the client does on RESP_RAW
        const std::string sid = mint_sid();''')

# path 3: a holdable answer unchanged for this request
rep('''        Dict ref; for (auto& kv : *dyn) ref.set(kv.first, kv.second);
        respref_[ik] = ref;
        return remember(ik, int(a.status), a.body, c->second.same_id, semwire::wrap_resp_same(c->second.same_id));   // [SAME_KEEPS_LAST_ANSWER_V1]
    }''',
    '''        if (auto r = same_reference(ik, t, a.body)) {                              // [SAME_MOVES_BOTH_REFERENCES_V1]
            respref_[ik] = std::move(*r);
            return remember(ik, int(a.status), a.body, c->second.same_id, semwire::wrap_resp_same(c->second.same_id)); }   // [SAME_KEEPS_LAST_ANSWER_V1]
    }''')
open(p, 'w').write(s)
print("same moves both references applied")
