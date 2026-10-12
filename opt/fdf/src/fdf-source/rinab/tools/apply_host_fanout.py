"""apply_host_fanout.py RAM_SERVER_CPP -- [HOST_FANOUT_V1] the RAM host takes the daemon's shape
(daemon/engine/session.py): a reader takes frames off the request socket and decodes them in wire order; a POOL of
workers executes them in parallel; each finished reply goes onto the session's reply queue, and ONE writer thread per
session drains that queue in completion order onto the return socket -- encoding as it sends, so the response
reference moves in the order the reply goes on the wire. No shared send lock. Both queues are lock-free: the work
queue is a bounded multi-producer multi-consumer ring (Vyukov), the reply queue an intrusive multi-producer
single-consumer list (Vyukov); a thread with nothing to do sleeps on an atomic (futex), never on a mutex.

Before this the host ran every request inline on the session's one reading thread: one session's requests executed
one at a time, and the next frame was not read until the previous request had finished.

Applied at build after apply_session_hook.py; the vendored v0.63 source stays untouched."""
import sys
p = sys.argv[1]; s = open(p).read()
def rep(a, b, n=1):
    global s
    assert s.count(a) == n, (a[:80], s.count(a)); s = s.replace(a, b)

# ---- the two lock-free queues and the pool, before Session --------------------------------------------------------
rep('using Ceiling = fnwp::dataplane::Ceiling;\n', r'''using Ceiling = fnwp::dataplane::Ceiling;
// [HOST_FANOUT_V1] the work ring: bounded MPMC (Dmitry Vyukov's), one cell per task, sequence numbers instead of locks
class WorkRing {
public:
    static constexpr size_t N = 1 << 16;
    WorkRing() { for (size_t i = 0; i < N; ++i) cell_[i].seq.store(i, std::memory_order_relaxed); }
    void push(std::function<void()> f) {                      // never fails: a full ring waits for a free cell
        for (;;) {
            size_t pos = tail_.load(std::memory_order_relaxed);
            Cell& c = cell_[pos & (N - 1)]; const size_t seq = c.seq.load(std::memory_order_acquire);
            const intptr_t d = intptr_t(seq) - intptr_t(pos);
            if (d == 0) { if (tail_.compare_exchange_weak(pos, pos + 1, std::memory_order_relaxed)) {
                    c.f = std::move(f); c.seq.store(pos + 1, std::memory_order_release);
                    work_.fetch_add(1, std::memory_order_release); work_.notify_one(); return; } }
            else if (d < 0) std::this_thread::yield();        // full
        }
    }
    bool pop(std::function<void()>& f) {
        for (;;) {
            size_t pos = head_.load(std::memory_order_relaxed);
            Cell& c = cell_[pos & (N - 1)]; const size_t seq = c.seq.load(std::memory_order_acquire);
            const intptr_t d = intptr_t(seq) - intptr_t(pos + 1);
            if (d == 0) { if (head_.compare_exchange_weak(pos, pos + 1, std::memory_order_relaxed)) {
                    f = std::move(c.f); c.seq.store(pos + N, std::memory_order_release); return true; } }
            else if (d < 0) return false;                     // empty
        }
    }
    void worker() {
        for (;;) {
            std::function<void()> f;
            if (pop(f)) { f(); continue; }
            const uint32_t seen = work_.load(std::memory_order_acquire);
            if (pop(f)) { f(); continue; }
            work_.wait(seen, std::memory_order_acquire);      // futex sleep until a push
        }
    }
private:
    struct Cell { std::atomic<size_t> seq; std::function<void()> f; };
    alignas(64) std::atomic<size_t> head_{0}; alignas(64) std::atomic<size_t> tail_{0};
    alignas(64) std::atomic<uint32_t> work_{0};
    Cell cell_[N];
};
WorkRing& work_ring() {                                      // the pool: started once, sized to the machine
    static WorkRing* r = [] { WorkRing* w = new WorkRing;
        unsigned n = std::thread::hardware_concurrency(); n = n ? n * 4 : 16;
        for (unsigned i = 0; i < n; ++i) std::thread([w] { w->worker(); }).detach();
        return w; }();
    return *r;
}
// [HOST_FANOUT_V1] a session's reply queue: intrusive MPSC, any worker pushes, the session's one writer pops
struct OutItem { std::atomic<OutItem*> next{nullptr}; uint32_t seq = 0; bool engine = false; std::string frame;
                 fnwp::ServerEngine::Work w; Reply r; };
class OutQueue {
public:
    OutQueue() : head_(&stub_), tail_(&stub_) {}
    ~OutQueue() { OutItem* n; while ((n = pop())) delete n; }
    void push(OutItem* n) {
        n->next.store(nullptr, std::memory_order_relaxed);
        OutItem* prev = head_.exchange(n, std::memory_order_acq_rel);
        prev->next.store(n, std::memory_order_release);
        ready_.fetch_add(1, std::memory_order_release); ready_.notify_one();
    }
    OutItem* pop() {                                          // single consumer
        OutItem* t = tail_; OutItem* next = t->next.load(std::memory_order_acquire);
        if (t == &stub_) { if (!next) return nullptr; tail_ = next; t = next; next = next->next.load(std::memory_order_acquire); }
        if (next) { tail_ = next; return t; }
        if (t != head_.load(std::memory_order_acquire)) return nullptr;   // a push is half done: next time
        push_stub(); next = t->next.load(std::memory_order_acquire);
        if (next) { tail_ = next; return t; }
        return nullptr;
    }
    std::atomic<uint32_t> ready_{0};
private:
    void push_stub() { stub_.next.store(nullptr, std::memory_order_relaxed); OutItem* prev = head_.exchange(&stub_, std::memory_order_acq_rel); prev->next.store(&stub_, std::memory_order_release); }
    OutItem stub_; std::atomic<OutItem*> head_; OutItem* tail_;
};
''')

# ---- Session: replies go onto its queue; its writer encodes and sends them, in completion order ----------------------
rep('''    void reply(uint32_t seq, const std::string& frame) {
        std::lock_guard<std::mutex> g(send_m); reply_locked(seq, frame); }''',
'''    // [HOST_FANOUT_V1] a reply is handed to the session's writer; nothing that produces one sends it
    OutQueue outq; std::atomic<bool> closing{false}; std::thread writer;
    void reply(uint32_t seq, const std::string& frame) {
        if (closing.load(std::memory_order_acquire)) return;
        OutItem* o = new OutItem; o->seq = seq; o->frame = frame; outq.push(o); }
    void writer_run() {                                       // the session's ONE writer
        for (;;) {
            OutItem* o;
            while ((o = outq.pop())) {
                if (o->engine) encode_and_send(o->seq, o->w, o->r); else reply_locked(o->seq, o->frame);
                delete o;
            }
            if (closing.load(std::memory_order_acquire)) return;
            const uint32_t seen = outq.ready_.load(std::memory_order_acquire);
            if ((o = outq.pop())) { if (o->engine) encode_and_send(o->seq, o->w, o->r); else reply_locked(o->seq, o->frame); delete o; continue; }
            outq.ready_.wait(seen, std::memory_order_acquire);
        }
    }''')
rep('''    void finish_and_send(uint32_t seq, const fnwp::ServerEngine::Work& w, const Reply& r) {
        std::lock_guard<std::mutex> g(send_m);
        std::string fr;''',
'''    void finish_and_send(uint32_t seq, const fnwp::ServerEngine::Work& w, const Reply& r) {   // [HOST_FANOUT_V1] queued
        if (closing.load(std::memory_order_acquire)) return;
        OutItem* o = new OutItem; o->seq = seq; o->engine = true; o->w = w; o->r = r; outq.push(o); }
    void encode_and_send(uint32_t seq, const fnwp::ServerEngine::Work& w, const Reply& r) {    // on the writer only
        std::string fr;''')

# ---- execution goes to the pool -------------------------------------------------------------------------------------
rep('''    }
    run(w);
}''', '''    }
    work_ring().push([run, w] { run(w); });                   // [HOST_FANOUT_V1] executed by the pool, in parallel
}''')
rep('''    tl_session_id = reinterpret_cast<uint64_t>(s.get());
    if (api(method, path, body, false, r)) { s->reply(seq, wire_reply(h, http_req, have_old ? &old : nullptr, r)); return; }
    std::thread([=] { tl_session_id = reinterpret_cast<uint64_t>(s.get()); Reply rr;''',
'''    work_ring().push([=] {                                    // [HOST_FANOUT_V1] executed by the pool, in parallel
    Reply r;
    tl_session_id = reinterpret_cast<uint64_t>(s.get());
    if (api(method, path, body, false, r)) { s->reply(seq, wire_reply(h, http_req, have_old ? &old : nullptr, r)); return; }
    std::thread([=] { tl_session_id = reinterpret_cast<uint64_t>(s.get()); Reply rr;''')
rep('''Cached o = old; api(method, path, body, true, rr); s->reply(seq, wire_reply(h, http_req, have_old ? &o : nullptr, rr)); }).detach();   // a parked read
}''', '''Cached o = old; api(method, path, body, true, rr); s->reply(seq, wire_reply(h, http_req, have_old ? &o : nullptr, rr)); }).detach();   // a parked read
    });
}''')
rep('''    std::string method = q["method"].s, path = q["path"].s, body = q["body"].s; Reply r;
    work_ring()''', '''    std::string method = q["method"].s, path = q["path"].s, body = q["body"].s;
    work_ring()''')

# ---- the writer lives as long as the session's socket loop -----------------------------------------------------------
rep('''    logline("session up   " + peer + " " + token); uint32_t seq = 0;''',
'''    logline("session up   " + peer + " " + token); uint32_t seq = 0;
    { Session* sp = s.get(); s->writer = std::thread([sp] { sp->writer_run(); }); }   // [HOST_FANOUT_V1]''')
rep('''    rina_session_down(reinterpret_cast<uint64_t>(s.get()));''',
'''    s->closing.store(true, std::memory_order_release); s->outq.ready_.fetch_add(1); s->outq.ready_.notify_one();
    if (s->writer.joinable()) s->writer.join();                 // [HOST_FANOUT_V1] replies after this have no socket to go to
    rina_session_down(reinterpret_cast<uint64_t>(s.get()));''')
open(p, 'w').write(s)
print("host fan-out applied")
