"""apply_decode_alloc.py FNWP_ENGINE_HPP -- [DECODE_ALLOC_ONCE_V1] request decode on the server allocates only what is new.
 * the request reference: the merged fields are MOVED into the reference, not copied (they are not used again)
 * the repeat map (request hash -> request, the last 4,096): a full map RECYCLES its oldest entry -- the node and its two
   strings are taken out and reused for the newcomer (C++17 node handles), so a warm map allocates nothing; the age
   order is a fixed ring of 4,096 slots, not a list that allocates a node per request
 * a URL with no query values (every region call: op=... is part of the static URL) is the same text every time: made
   once when the template is learned and copied, not built.
Same results. Applied at build after apply_engine_lockless.py and apply_template_precompute.py."""
import sys
p = sys.argv[1]; s = open(p).read()
def rep(a, b, n=1):
    global s
    assert s.count(a) == n, (a[:90], s.count(a)); s = s.replace(a, b)
rep('''    std::map<std::string, std::string> seen_; std::list<std::string> seen_lru_;
    void seen_put(const std::string& h, const std::string& sem) {
        if (!seen_.count(h)) { seen_lru_.push_back(h); if (seen_lru_.size() > 4096) { seen_.erase(seen_lru_.front()); seen_lru_.pop_front(); } }
        seen_[h] = sem; }''',
'''    std::map<std::string, std::string> seen_; std::vector<std::string> seen_ring_ = std::vector<std::string>(4096); size_t seen_n_ = 0;
    void seen_put(const std::string& h, const std::string& sem) {   // [DECODE_ALLOC_ONCE_V1]
        auto it = seen_.find(h); if (it != seen_.end()) { it->second.assign(sem); return; }
        std::string& slot = seen_ring_[seen_n_++ % seen_ring_.size()];
        if (seen_n_ > seen_ring_.size()) {                       // full: the oldest entry's node is reused for this one
            auto nh = seen_.extract(slot);
            if (!nh.empty()) { nh.key().assign(h); nh.mapped().assign(sem); seen_.insert(std::move(nh)); slot.assign(h); return; }
        }
        seen_.emplace(h, sem); slot.assign(h); }''')
rep('''    reqref_[w.ik] = merged; seen_put(req_hash, sem_in);''',
    '''    reqref_[w.ik] = std::move(merged); seen_put(req_hash, sem_in);   // [DECODE_ALLOC_ONCE_V1] moved, not copied''')
rep('''        t.url_static_q = semtpl::parse_qsl(up.query); t.url_path = up.path.empty() ? "/" : up.path;''',
    '''        t.url_static_q = semtpl::parse_qsl(up.query); t.url_path = up.path.empty() ? "/" : up.path;
        t.url_fixed = semtpl::build_url_pre(t.url_path, t.url_static_q, {});   // [DECODE_ALLOC_ONCE_V1] the URL when no query value varies''')
rep('''    std::vector<std::string> req_fields; std::string url_path, req_mode; semtpl::Pairs url_static_q; pyv::Obj req_skel;''',
    '''    std::vector<std::string> req_fields; std::string url_path, req_mode, url_fixed; semtpl::Pairs url_static_q; pyv::Obj req_skel;''')
rep('''w.path = semtpl::build_url_pre(t->second.url_path, t->second.url_static_q, dec.first);''',
    '''w.path = dec.first.empty() ? t->second.url_fixed : semtpl::build_url_pre(t->second.url_path, t->second.url_static_q, dec.first);''')
open(p, 'w').write(s); print("decode alloc applied")
