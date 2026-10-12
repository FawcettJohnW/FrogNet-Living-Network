"""apply_engine_lockless.py FNWP_ENGINE_HPP RAM_SERVER_CPP -- [ENGINE_LOCKLESS_V1] the server engine without its mutex.

Before: one std::mutex per session's engine, taken by begin() (the reader, decoding in wire order) and by finish() (the
session's writer, encoding in send order) -- a lock on the fan-in/fan-out path.

Now each piece of state has ONE owner, and nothing is shared that is written:
  reader (begin):  the request references (reqref_) and its own repeat map (request hash -> the request it decoded),
                   so a REQ_REPEAT is decoded without asking the writer
  writer (finish): the response references, the RAM-answer templates, the answer cache (SAME, SAME-miss), template
                   learning
  templates:       learned by the writer, published to the reader as an immutable snapshot through one atomic
                   pointer; a snapshot is never changed and is freed with the engine. The writer publishes before the
                   RAW reply that taught it goes on the wire, so a client that saw that reply finds it here.
Two things the reader used to do to writer state now travel to the writer, in order, through the session's reply
queue (lock-free MPSC, [HOST_FANOUT_V1]):
  REQ_FULL ("start fresh") resets that instance's response reference -- as an item queued at decode time, so every
           reply encoded after it is encoded without the old reference, exactly as the erase at decode did
  REQ_SAME_MISS is answered by the writer from the answer cache it owns.
Applied at build after every other engine and host patch; the vendored v0.63 source stays untouched."""
import sys
eng, srv = sys.argv[1], sys.argv[2]
def edit(p, pairs):
    s = open(p).read()
    for a, b in pairs:
        assert s.count(a) == 1, (p, a[:80], s.count(a)); s = s.replace(a, b)
    open(p, 'w').write(s)

edit(eng, [
 ('#pragma once\n', '#pragma once\n#include <atomic>\n#include <list>\n#include <vector>\n'),
 # Work carries what the writer must do
 ('''        bool body_pending = false; size_t body_bytes = 0;  // [DATA_MARKER_V1] the request's body is on the data socket
    };
    ServerEngine(''',
  '''        bool body_pending = false; size_t body_bytes = 0;  // [DATA_MARKER_V1] the request's body is on the data socket
        bool fresh = false;                               // [ENGINE_LOCKLESS_V1] REQ_FULL: reset ik's response reference first
        std::string same_miss;                            // [ENGINE_LOCKLESS_V1] REQ_SAME_MISS: the writer answers this same_id
    };
    ServerEngine('''),
 # writer-side entry points for the two queued actions, and the template snapshot
 ('''    Work begin(const std::string& frame);                // decode, in the order frames arrive''',
  '''    Work begin(const std::string& frame);                // decode, in the order frames arrive (the reader only)
    void reset_response(const detail::IKey& ik) { respref_.erase(ik); }                 // the writer only
    std::string same_miss_frame(const std::string& sid) {                               // the writer only
        auto h = by_sid_.find(sid); auto c = h == by_sid_.end() ? cache_.end() : cache_.find(h->second);
        if (c == cache_.end()) return semwire::wrap_error(410, "REQ_SAME_MISS: the answer RESP_SAME named is no longer held; the request was not run again");
        return semwire::wrap_resp_raw(c->second.same_id, uint16_t(c->second.status), "", c->second.body); }
    ~ServerEngine() { delete tpl_snap_.load(); for (auto* p : retired_) delete p; }'''),
 ('''    std::string secret_, api_; Exec exec_; std::mutex m_;
    std::map<uint64_t, Template> tpl_; std::map<detail::IKey, Dict> reqref_, respref_; std::map<detail::IKey, std::string> rr_tpl_;''',
  '''    std::string secret_, api_; Exec exec_;
    std::map<uint64_t, Template> tpl_; std::map<detail::IKey, Dict> reqref_, respref_; std::map<detail::IKey, std::string> rr_tpl_;
    // [ENGINE_LOCKLESS_V1] the reader's view of the templates: an immutable snapshot, replaced whole by the writer
    using TplMap = std::map<uint64_t, Template>;
    std::atomic<const TplMap*> tpl_snap_{new TplMap}; std::vector<const TplMap*> retired_;
    void publish_templates() { retired_.push_back(tpl_snap_.exchange(new TplMap(tpl_), std::memory_order_acq_rel)); }
    // [ENGINE_LOCKLESS_V1] the reader's own repeat map: request hash -> the request it decoded (bounded)
    std::map<std::string, std::string> seen_; std::list<std::string> seen_lru_;
    void seen_put(const std::string& h, const std::string& sem) {
        if (!seen_.count(h)) { seen_lru_.push_back(h); if (seen_lru_.size() > 4096) { seen_.erase(seen_lru_.front()); seen_lru_.pop_front(); } }
        seen_[h] = sem; }'''),
 # begin: no lock; SAME-miss and REPEAT from what the reader owns
 ('''    std::lock_guard<std::mutex> g(m_);
    if (m->op == semwire::OP_REQ_SAME_MISS) {            // [SAME_MISS_FROM_CACHE_V1] from the cache; nothing runs
        auto h = by_sid_.find(detail::sv(m->same_id)); auto c = h == by_sid_.end() ? cache_.end() : cache_.find(h->second);
        if (c == cache_.end()) { w.frame = semwire::wrap_error(410, "REQ_SAME_MISS: the answer RESP_SAME named is no longer held; the request was not run again"); return w; }
        w.frame = semwire::wrap_resp_raw(c->second.same_id, uint16_t(c->second.status), "", c->second.body); return w;
    }''',
  '''    if (m->op == semwire::OP_REQ_SAME_MISS) {            // [SAME_MISS_FROM_CACHE_V1] from the cache; nothing runs
        w.same_miss = detail::sv(m->same_id); return w;   // [ENGINE_LOCKLESS_V1] the writer owns the cache: it answers
    }'''),
 ('''        const std::string h = detail::sv(m->req_hash); auto c = cache_.find(h);
        if (c == cache_.end()) { w.frame = semwire::wrap_req_miss(h); return w; }
        return semantic_locked(h, c->second.sem_req, false, true);   // not FULL: decode against the request reference''',
  '''        const std::string h = detail::sv(m->req_hash); auto c = seen_.find(h);
        if (c == seen_.end()) { w.frame = semwire::wrap_req_miss(h); return w; }
        const std::string sem = c->second;                       // [ENGINE_LOCKLESS_V1] the reader's own map
        return semantic_locked(h, sem, false, true);   // not FULL: decode against the request reference'''),
 ('''    auto t = tpl_.find(op);
    if (t == tpl_.end()) { w.frame = semwire::wrap_error(503, "no templates for opcode=" + std::to_string(op)); return w; }''',
  '''    const TplMap* tm = tpl_snap_.load(std::memory_order_acquire);   // [ENGINE_LOCKLESS_V1] the published snapshot
    auto t = tm->find(op);
    if (t == tm->end()) { w.frame = semwire::wrap_error(503, "no templates for opcode=" + std::to_string(op)); return w; }'''),
 ('''    if (full) respref_.erase(w.ik);                     // FIX 15: the proxy starts fresh -- send every field''',
  '''    if (full) w.fresh = true;                           // FIX 15: the proxy starts fresh -- the writer resets the reference'''),
 ('''    reqref_[w.ik] = merged;''',
  '''    reqref_[w.ik] = merged; seen_put(req_hash, sem_in);'''),
 # finish: no lock; publish learned templates
 ('''inline std::string ServerEngine::finish(const Work& w, const Answer& a) {
    std::lock_guard<std::mutex> g(m_);''',
  '''inline std::string ServerEngine::finish(const Work& w, const Answer& a) {     // [ENGINE_LOCKLESS_V1] the writer only'''),
 ('''                tpl_[op] = *t;''',
  '''                tpl_[op] = *t; publish_templates();'''),
])

edit(srv, [
 # the writer's item kinds
 ('''struct OutItem { std::atomic<OutItem*> next{nullptr}; uint32_t seq = 0; bool engine = false; std::string frame;''',
  '''struct OutItem { std::atomic<OutItem*> next{nullptr}; uint32_t seq = 0; bool engine = false; std::string frame;
                 int action = 0;                         // [ENGINE_LOCKLESS_V1] 1: reset w.ik's response reference; 2: answer SAME-miss'''),
 ('''                if (o->engine) encode_and_send(o->seq, o->w, o->r); else reply_locked(o->seq, o->frame);
                delete o;
            }''',
  '''                if (o->action == 1) eng->reset_response(o->w.ik);
                else if (o->action == 2) reply_locked(o->seq, eng->same_miss_frame(o->w.same_miss));
                else if (o->engine) encode_and_send(o->seq, o->w, o->r); else reply_locked(o->seq, o->frame);
                delete o;
            }'''),
 ('''            if ((o = outq.pop())) { if (o->engine) encode_and_send(o->seq, o->w, o->r); else reply_locked(o->seq, o->frame); delete o; continue; }''',
  '''            if ((o = outq.pop())) {
                if (o->action == 1) eng->reset_response(o->w.ik);
                else if (o->action == 2) reply_locked(o->seq, eng->same_miss_frame(o->w.same_miss));
                else if (o->engine) encode_and_send(o->seq, o->w, o->r); else reply_locked(o->seq, o->frame);
                delete o; continue; }'''),
 # the reader hands those two to the writer, in decode order
 ('''    if (!w.frame.empty()) { s->reply(seq, w.frame); return; }
    if (std::getenv("FROGRAM_TRACE"))''',
  '''    if (!w.frame.empty()) { s->reply(seq, w.frame); return; }
    if (!w.same_miss.empty()) { OutItem* o = new OutItem; o->seq = seq; o->action = 2; o->w = w; s->outq.push(o); return; }   // [ENGINE_LOCKLESS_V1]
    if (w.fresh) { OutItem* o = new OutItem; o->action = 1; o->w = w; s->outq.push(o); }                                     // [ENGINE_LOCKLESS_V1]
    if (std::getenv("FROGRAM_TRACE"))'''),
])
print("engine lockless applied")
