"""apply_client_applier.py FNWP_ENGINE_HPP FROGRAM_CPP FROGRAM_HPP -- [CLIENT_APPLIER_V1] the client engine without its
lock, and the wire-order turns without theirs.

Before: ClientEngine had one mutex (m_) taken by prepare() on the sender and by complete() on the reader AND on every
payload-landing thread; complete() also slept on a condition variable until an instance's landing RAW answer had been
applied. The wire-order turns had their own mutex (gate_m).

Now every piece of client-engine state has one owner:
  sender  (prepare)   the request references; it reads the templates from an immutable snapshot (one atomic pointer)
  applier (complete)  the response references, the RAM-answer templates, the SAME cache, template learning (it
                      publishes each new snapshot), and the wire-order turns
The applier is ONE thread per session. The reader hands it each reply as it arrives (so the applier stamps turns in
arrival order); a landing thread hands it a marker reply once its payload has landed; both through a lock-free MPSC
list. A reply to an instance whose RAW answer is still landing is held by the applier and applied after it -- no
waiting, no lock. Two things complete() used to do to the sender's state go back to the sender as resends:
  REQ_MISS            -> redo 1: the sender forgets that instance's request reference and prepares the call again
  "no templates"      -> redo 2: the sender sends the call RAW (the applier has already dropped the template)
Counters are atomics. ClientEngine::request (synchronous, for tests) follows the same redo protocol.
Applied at build after apply_client_lockless.py; the vendored v0.63 source stays untouched."""
import sys
eng, cpp, hpp = sys.argv[1], sys.argv[2], sys.argv[3]
def edit(p, pairs):
    s = open(p).read()
    for a, b in pairs:
        assert s.count(a) == 1, (p, a[:90], s.count(a)); s = s.replace(a, b)
    open(p, 'w').write(s)

# ================================================================== ClientEngine ==================================
edit(eng, [
 ('''    struct Step { bool done = false; Answer answer; Out resend; };''',
  '''    struct Step { bool done = false; Answer answer; Out resend; int redo = 0; };   // [CLIENT_APPLIER_V1] redo: 1 prepare again, 2 RAW'''),
 ('''    Stats stats() const { std::lock_guard<std::mutex> g(m_); return st_; }''',
  '''    Stats stats() const {                                 // [CLIENT_APPLIER_V1] from atomic counters
        Stats s; s.raw = a_raw.load(); s.full = a_full.load(); s.diff = a_diff.load(); s.repeat = a_repeat.load(); s.same = a_same.load();
        s.rdiff = a_rdiff.load(); s.rraw = a_rraw.load(); s.miss = a_miss.load(); s.bytes_out = a_out.load(); s.bytes_in = a_in.load(); return s; }
    // [CLIENT_APPLIER_V1] the two resends complete() asks of the sender (the sender's state, the sender's thread)
    Out reprepare(const std::shared_ptr<Ctx>& c) { reqref_.erase(c->ik); return prepare_locked(c); }
    Out resend_raw(const std::shared_ptr<Ctx>& c) { return raw_locked(c); }
    ~ClientEngine() { delete snap_.load(); for (auto* p : retired_) delete p; }'''),
 ('''    std::string target_, origin_, api_; Transport t_; Stats st_; mutable std::mutex m_;
    std::condition_variable pend_cv_; std::set<detail::IKey> pending_;    // instances whose RAW answer is still landing
    std::map<Key, Template> tpl_;''',
  '''    std::string target_, origin_, api_; Transport t_;
    std::atomic<uint64_t> a_raw{0}, a_full{0}, a_diff{0}, a_repeat{0}, a_same{0}, a_rdiff{0}, a_rraw{0}, a_miss{0}, a_out{0}, a_in{0};
    // [CLIENT_APPLIER_V1] the applier owns tpl_ and publishes it; the sender reads the snapshot
    using TplMap = std::map<Key, Template>;
    std::atomic<const TplMap*> snap_{new TplMap}; std::vector<const TplMap*> retired_;
    void publish() { retired_.push_back(snap_.exchange(new TplMap(tpl_), std::memory_order_acq_rel)); }
    std::map<Key, Template> tpl_;'''),
 ('''    auto c = std::make_shared<Ctx>(); c->method = method; c->path = path; c->body = body;
    std::lock_guard<std::mutex> g(m_); return prepare_locked(c);''',
  '''    auto c = std::make_shared<Ctx>(); c->method = method; c->path = path; c->body = body;
    return prepare_locked(c);                             // [CLIENT_APPLIER_V1] the sender's thread only'''),
 ('''    c->raw = true; ++st_.raw; std::string http = detail::http_json(c->method, c->path, c->body);
    Out o{semwire::wrap_req_raw(detail::sha16(target_ + '\\0' + http), http), c}; st_.bytes_out += o.frame.size(); return o;''',
  '''    c->raw = true; a_raw.fetch_add(1, std::memory_order_relaxed); std::string http = detail::http_json(c->method, c->path, c->body);
    Out o{semwire::wrap_req_raw(detail::sha16(target_ + '\\0' + http), http), c}; a_out.fetch_add(o.frame.size(), std::memory_order_relaxed); return o;'''),
 ('''    auto it = tpl_.find({c->method, c->key});
    if (it == tpl_.end()) return raw_locked(c);''',
  '''    const TplMap* tm = snap_.load(std::memory_order_acquire);   // [CLIENT_APPLIER_V1] the published snapshot
    auto it = tm->find({c->method, c->key});
    if (it == tm->end()) return raw_locked(c);'''),
 ('''    if (b.req_type == "REQ_REPEAT") ++st_.repeat; else if (b.req_type == "REQ_FULL") ++st_.full; else ++st_.diff;
    reqref_[c->ik] = b.new_reference;                   // committed as it leaves, as the server commits on decode
    st_.bytes_out += b.frame.size();''',
  '''    if (b.req_type == "REQ_REPEAT") a_repeat.fetch_add(1, std::memory_order_relaxed); else if (b.req_type == "REQ_FULL") a_full.fetch_add(1, std::memory_order_relaxed); else a_diff.fetch_add(1, std::memory_order_relaxed);
    reqref_[c->ik] = b.new_reference;                   // committed as it leaves, as the server commits on decode
    a_out.fetch_add(b.frame.size(), std::memory_order_relaxed);'''),
 ('''            tpl_[{c->method, semtpl::canonical_semantic_key(c->method, c->path, c->body)}] = *t;''',
  '''            tpl_[{c->method, semtpl::canonical_semantic_key(c->method, c->path, c->body)}] = *t; publish();'''),
 ('''inline void ClientEngine::defer(const std::shared_ptr<Ctx>& c) {
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
inline ClientEngine::Step ClientEngine::complete_locked(std::unique_lock<std::mutex>&, const std::shared_ptr<Ctx>& c, const std::string& rf) {''',
  '''inline void ClientEngine::defer(const std::shared_ptr<Ctx>& c) { c->deferred = true; }   // [CLIENT_APPLIER_V1] the applier orders the rest
inline ClientEngine::Step ClientEngine::complete(const std::shared_ptr<Ctx>& c, const std::string& rf) {
    a_in.fetch_add(rf.size(), std::memory_order_relaxed);   // [CLIENT_APPLIER_V1] the applier's thread only; it holds a
    std::unique_lock<std::mutex> none;                       // reply to a landing instance until that instance has landed
    return complete_locked(none, c, rf);
}
inline ClientEngine::Step ClientEngine::complete_locked(std::unique_lock<std::mutex>&, const std::shared_ptr<Ctx>& c, const std::string& rf) {'''),
 ('''        ++st_.miss; reqref_.erase(ik);
        if (++c->attempts >= 2) throw std::runtime_error("fnwp client: REQ_MISS from the far end; exhausted retries");
        Step s; s.resend = prepare_locked(c); return s;''',
  '''        a_miss.fetch_add(1, std::memory_order_relaxed);
        if (++c->attempts >= 2) throw std::runtime_error("fnwp client: REQ_MISS from the far end; exhausted retries");
        Step s; s.redo = 1; s.resend.ctx = c; return s;      // [CLIENT_APPLIER_V1] the sender forgets the reference and prepares again'''),
 ('''            tpl_.erase({c->method, c->key}); Step s; s.resend = raw_locked(c); return s; }''',
  '''            tpl_.erase({c->method, c->key}); publish(); Step s; s.redo = 2; s.resend.ctx = c; return s; }   // [CLIENT_APPLIER_V1]'''),
 ('''        ++st_.same; auto sb = same_.find(detail::sv(m->same_id));''',
  '''        a_same.fetch_add(1, std::memory_order_relaxed); auto sb = same_.find(detail::sv(m->same_id));'''),
 ('''            Out o{semwire::wrap_req_same_miss(detail::sv(m->same_id)), c}; st_.bytes_out += o.frame.size();''',
  '''            Out o{semwire::wrap_req_same_miss(detail::sv(m->same_id)), c}; a_out.fetch_add(o.frame.size(), std::memory_order_relaxed);'''),
 ('''        ++st_.rdiff; auto rp = respref_.find(ik);''',
  '''        a_rdiff.fetch_add(1, std::memory_order_relaxed); auto rp = respref_.find(ik);'''),
 ('''        ++st_.rraw; same_put(detail::sv(m->same_id), detail::sv(m->body));''',
  '''        a_rraw.fetch_add(1, std::memory_order_relaxed); same_put(detail::sv(m->same_id), detail::sv(m->body));'''),
 ('''        Step s = complete(o.ctx, t_(o.frame));
        if (s.done) return s.answer;
        o = s.resend;''',
  '''        Step s = complete(o.ctx, t_(o.frame));
        if (s.done) return s.answer;
        o = s.redo == 1 ? reprepare(s.resend.ctx) : s.redo == 2 ? resend_raw(s.resend.ctx) : s.resend;   // [CLIENT_APPLIER_V1]'''),
])

# ================================================================== Session (header) ===============================
edit(hpp, [
 ('''    void stamp(InFlight& inf); void ready(const InFlight& inf, const std::string& reply); void turn_done(const InFlight& inf);   // [WIRE_ORDER_TURNS_V1]''',
  '''    void stamp(InFlight& inf); void ready(const InFlight& inf, const std::string& reply); void turn_done(const InFlight& inf);   // [WIRE_ORDER_TURNS_V1]
    void apply_loop();                                   // [CLIENT_APPLIER_V1] the one thread that applies replies'''),
])

# ================================================================== Session (frogram.cpp) ===========================
edit(cpp, [
 # Job carries a redo
 ('''    struct Job { std::string method, path, body; std::shared_ptr<Pending> waiter; fnwp::ClientEngine::Out resend; bool is_resend = false;''',
  '''    struct Job { std::string method, path, body; std::shared_ptr<Pending> waiter; fnwp::ClientEngine::Out resend; bool is_resend = false; int redo = 0;'''),
 # the applier's queue and its held-for-landing map; the turn maps lose their lock (the applier owns them)
 ('''    std::mutex gate_m; std::map<fnwp::detail::IKey, uint64_t> issued, applied;''',
  '''    std::map<fnwp::detail::IKey, uint64_t> issued, applied;   // [CLIENT_APPLIER_V1] the applier's alone (no gate_m)
    // [CLIENT_APPLIER_V1] the applier's queue: kind 0 a reply as it arrived, 1 a marker (its payload is landing),
    // 2 a marker's full reply once landed
    struct ApplyNode { std::atomic<ApplyNode*> next{nullptr}; int kind = 0; uint32_t seq = 0; Session::InFlight inf; std::string reply; };
    struct ApplyList {
        ApplyNode stub; std::atomic<ApplyNode*> head{&stub}; ApplyNode* tail = &stub;
        void push(ApplyNode* n) { n->next.store(nullptr, std::memory_order_relaxed); ApplyNode* prev = head.exchange(n, std::memory_order_acq_rel); prev->next.store(n, std::memory_order_release); }
        ApplyNode* pop() {
            ApplyNode* t = tail; ApplyNode* next = t->next.load(std::memory_order_acquire);
            if (t == &stub) { if (!next) return nullptr; tail = next; t = next; next = next->next.load(std::memory_order_acquire); }
            if (next) { tail = next; return t; }
            if (t != head.load(std::memory_order_acquire)) return nullptr;
            stub.next.store(nullptr, std::memory_order_relaxed); ApplyNode* prev = head.exchange(&stub, std::memory_order_acq_rel); prev->next.store(&stub, std::memory_order_release);
            next = t->next.load(std::memory_order_acquire); if (next) { tail = next; return t; }
            return nullptr;
        }
    } applyq;
    std::atomic<uint32_t> apply_work{0}; std::atomic<bool> apply_stop{false}; std::thread applier;
    void to_applier(int kind, const Session::InFlight& inf, std::string reply, uint32_t seq = 0) {
        ApplyNode* n = new ApplyNode; n->kind = kind; n->seq = seq; n->inf = inf; n->reply = std::move(reply); applyq.push(n);
        apply_work.fetch_add(1, std::memory_order_release); futex_wake_all(apply_work);
    }
    std::set<fnwp::detail::IKey> landing;                // instances whose RAW answer is still landing
    std::map<uint32_t, Session::InFlight> marked;        // a marker's call, stamped at arrival, by sequence number
    std::map<fnwp::detail::IKey, std::vector<std::pair<Session::InFlight, std::string>>> after_landing;'''),
 # start / stop the applier
 ('''    d_->sender = std::thread([this] { send_loop(); });''',
  '''    d_->sender = std::thread([this] { send_loop(); });
    d_->applier = std::thread([this] { apply_loop(); });    // [CLIENT_APPLIER_V1]'''),
 ('''    if (d_->reader.joinable()) d_->reader.join();''',
  '''    if (d_->reader.joinable()) d_->reader.join();
    d_->apply_stop.store(true, std::memory_order_release); d_->apply_work.fetch_add(1); futex_wake_all(d_->apply_work);
    if (d_->applier.joinable()) d_->applier.join();         // [CLIENT_APPLIER_V1]'''),
 # the sender honours a redo
 ('''        try { o = j.is_resend ? j.resend : d_->eng->prepare(j.method, j.path, j.body); }''',
  '''        try { o = !j.is_resend ? d_->eng->prepare(j.method, j.path, j.body)
                  : j.redo == 1 ? d_->eng->reprepare(j.resend.ctx) : j.redo == 2 ? d_->eng->resend_raw(j.resend.ctx) : j.resend; }'''),
 # turns: no lock
 ('''    std::lock_guard<std::mutex> g(d_->gate_m); inf.ticket = ++d_->issued[inf.ctx->ik];''',
  '''    inf.ticket = ++d_->issued[inf.ctx->ik];              // [CLIENT_APPLIER_V1] on the applier'''),
 ('''    if (inf.ticket && !inf.in_turn) {
        std::lock_guard<std::mutex> g(d_->gate_m);
        if (d_->applied[inf.ctx->ik] != inf.ticket - 1)''',
  '''    if (inf.ticket && !inf.in_turn) {                   // [CLIENT_APPLIER_V1] on the applier
        if (d_->applied[inf.ctx->ik] != inf.ticket - 1)'''),
 ('''    { std::lock_guard<std::mutex> g(d_->gate_m);
      uint64_t a = ++d_->applied[inf.ctx->ik];''',
  '''    {                                                    // [CLIENT_APPLIER_V1] on the applier
      uint64_t a = ++d_->applied[inf.ctx->ik];'''),
 # apply_reply: a reply to a landing instance waits for it; a redo goes to the sender; after a landed reply, the held ones
 ('''void Session::apply_reply(const InFlight& inf, const std::string& reply) {
    try {
        fnwp::ClientEngine::Step s = d_->eng->complete(inf.ctx, reply);''',
  '''void Session::apply_reply(const InFlight& inf, const std::string& reply) {
    // [CLIENT_APPLIER_V1] the instance's RAW answer is still landing: this reply is applied after it
    if (inf.ctx && !inf.ctx->raw && !inf.ctx->deferred && d_->landing.count(inf.ctx->ik)) { d_->after_landing[inf.ctx->ik].emplace_back(inf, reply); return; }
    const bool landed = inf.ctx && inf.ctx->deferred && !inf.ctx->raw;
    struct Release { Session* s; const InFlight& inf; bool on; ~Release() {
        if (!on) { return; }
        s->d_->landing.erase(inf.ctx->ik);
        auto h = s->d_->after_landing.find(inf.ctx->ik);
        if (h == s->d_->after_landing.end()) { return; }
        auto held = std::move(h->second); s->d_->after_landing.erase(h);
        for (auto& x : held) s->apply_reply(x.first, x.second); } } release{this, inf, landed};
    try {
        fnwp::ClientEngine::Step s = d_->eng->complete(inf.ctx, reply);'''),
 ('''        Impl::Job j; j.is_resend = true; j.resend = s.resend; j.waiter = inf.waiter;''',
  '''        Impl::Job j; j.is_resend = true; j.resend = s.resend; j.redo = s.redo; j.waiter = inf.waiter;'''),
 # the reader hands replies to the applier, in arrival order; a landing thread hands the landed reply
 ('''                stamp(inf);                               // [WIRE_ORDER_TURNS_V1] its place is its arrival
                std::lock_guard<std::mutex> g(d_->land_m);
                d_->landers.emplace_back([this, inf, reply, seq] {
                    try { ready(inf, fnwp::ClientEngine::reply_from_marker(reply, d_->landed.take(seq))); }
                    catch (const std::exception& e) { Impl::finish(inf.waiter, nullptr, std::string("frogram: ") + e.what()); }
                });
                continue;
            }
            stamp(inf); ready(inf, reply);               // [WIRE_ORDER_TURNS_V1] in its turn, or held for it''',
  '''                d_->to_applier(1, inf, reply, seq);      // [CLIENT_APPLIER_V1] its place is its arrival
                std::lock_guard<std::mutex> g(d_->land_m);
                d_->landers.emplace_back([this, inf, reply, seq] {
                    try { d_->to_applier(2, inf, fnwp::ClientEngine::reply_from_marker(reply, d_->landed.take(seq)), seq); }
                    catch (const std::exception& e) { Impl::finish(inf.waiter, nullptr, std::string("frogram: ") + e.what()); }
                });
                continue;
            }
            d_->to_applier(0, inf, reply);               // [CLIENT_APPLIER_V1] stamped and applied on the applier, in this order'''),
 # held replies at death: the applier owns them, so the applier finishes them
 ('''    { std::map<fnwp::detail::IKey, std::map<uint64_t, std::pair<InFlight, std::string>>> h;
      { std::lock_guard<std::mutex> g(d_->gate_m); h.swap(d_->held); }
      for (auto& i : h) for (auto& t : i.second) Impl::finish(t.second.first.waiter, nullptr, d_->dead); }''',
  '''    d_->to_applier(3, Session::InFlight{}, "");         // [CLIENT_APPLIER_V1] the applier finishes what it holds'''),
 # the applier itself
 ('''// [ROUND_TRIPS_V1] Blocking calls made by THIS thread:''',
  '''// [CLIENT_APPLIER_V1] the one thread that applies replies: stamps turns in arrival order, keeps a landing instance's
// later replies until it has landed, and owns everything the engine's completion touches
void Session::apply_loop() {
    for (;;) {
        const uint32_t seen = d_->apply_work.load(std::memory_order_acquire);
        Impl::ApplyNode* n = d_->applyq.pop();
        if (!n) {
            if (d_->apply_stop.load(std::memory_order_acquire)) return;
            syscall(SYS_futex, reinterpret_cast<uint32_t*>(&d_->apply_work), FUTEX_WAIT_PRIVATE, seen, nullptr, nullptr, 0);
            continue;
        }
        try {
            if (n->kind == 0) { stamp(n->inf); ready(n->inf, n->reply); }
            else if (n->kind == 1) { stamp(n->inf); d_->eng->defer(n->inf.ctx); if (n->inf.ctx && !n->inf.ctx->raw) d_->landing.insert(n->inf.ctx->ik);
                                     d_->marked[n->seq] = n->inf; }   // the stamped call: its landed reply is applied as this one
            else if (n->kind == 2) { auto m = d_->marked.find(n->seq); InFlight inf = m != d_->marked.end() ? m->second : n->inf;
                                     if (m != d_->marked.end()) d_->marked.erase(m);
                                     ready(inf, n->reply); }
            else {                                        // 3: the connection is dead: everything held ends with it
                for (auto& i : d_->held) for (auto& t : i.second) Impl::finish(t.second.first.waiter, nullptr, d_->dead_why());
                d_->held.clear();
                for (auto& i : d_->after_landing) for (auto& t : i.second) Impl::finish(t.first.waiter, nullptr, d_->dead_why());
                d_->after_landing.clear(); d_->landing.clear();
            }
        } catch (const std::exception& e) { Impl::finish(n->inf.waiter, nullptr, std::string("frogram: ") + e.what()); }
        delete n;
    }
}
// [ROUND_TRIPS_V1] Blocking calls made by THIS thread:'''),
])
print("client applier applied")
