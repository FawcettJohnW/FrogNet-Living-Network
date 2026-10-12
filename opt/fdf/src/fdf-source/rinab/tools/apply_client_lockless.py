"""apply_client_lockless.py FROGRAM_CPP -- [CLIENT_LOCKLESS_V1] the client session's fan-in and fan-out without locks.

Before (frogram.cpp): every call and post took the job-queue mutex (q_m, with two condition variables), the in-flight
map's mutex (pend_m) and the statistics mutex (st_m); each caller then slept on its own mutex + condition variable.

Now:
  fan-in   callers push their job onto a lock-free MPSC list (Vyukov); resends (made in a call's turn) go on a second
           one that the sender always drains first, as push_front did; a bounded count (QUEUE_MAX) holds callers back
           with an atomic wait, as the room_cv did; the sender sleeps on an atomic (futex) when there is nothing to send.
  in flight a ring of 65,536 slots indexed by sequence number: the sender (the only one assigning sequence numbers)
           stores the slot, the reader takes it with one exchange. A slot still occupied when its number comes round
           again holds the sender back (atomic wait) -- 65,536 requests unanswered.
  fan-out  each caller waits on its own completion word with FUTEX_WAIT (with the call's timeout); the reader sets
           the answer, then the word, then wakes it.
  counters atomics.
Death of the connection: the reader marks it dead (an atomic flag; the reason written before it), then takes every
occupied slot and finishes its caller; a sender that stored a slot re-checks the flag and takes its own slot back.
The engine's lock (ClientEngine m_), the wire-order turns (gate_m) and the coalescing table (co_m) are not touched
here. Applied at build after apply_coalesce.py and apply_wire_order.py; the vendored v0.63 source stays untouched."""
import sys
p = sys.argv[1]; s = open(p).read()
def rep(a, b, n=1):
    global s
    assert s.count(a) == n, (a[:90], s.count(a)); s = s.replace(a, b)

# ---- includes ----------------------------------------------------------------------------------------------------------
rep('struct Session::Pending {', '''}  // namespace frogram (reopened below)
#include <atomic>
#include <linux/futex.h>
#include <sys/syscall.h>
#include <climits>
namespace frogram {
// [CLIENT_LOCKLESS_V1] a futex wait on a 32-bit word, with a timeout (std::atomic::wait has none)
static inline bool futex_wait_for(std::atomic<uint32_t>& w, uint32_t expected, double timeout_s) {
    if (timeout_s > 1e9) timeout_s = 1e9;
    timespec ts; ts.tv_sec = time_t(timeout_s); ts.tv_nsec = long((timeout_s - double(ts.tv_sec)) * 1e9);
    long r = syscall(SYS_futex, reinterpret_cast<uint32_t*>(&w), FUTEX_WAIT_PRIVATE, expected, &ts, nullptr, 0);
    return !(r == -1 && errno == ETIMEDOUT);
}
static inline void futex_wake_all(std::atomic<uint32_t>& w) { syscall(SYS_futex, reinterpret_cast<uint32_t*>(&w), FUTEX_WAKE_PRIVATE, INT_MAX, nullptr, nullptr, 0); }
struct Session::Pending {''')

# ---- Pending: a completion word instead of mutex + condition variable ------------------------------------------------
rep('''struct Session::Pending { std::mutex m; std::condition_variable cv; bool done = false; fnwp::Answer answer; std::string err;''',
    '''struct Session::Pending { std::atomic<uint32_t> done{0}; fnwp::Answer answer; std::string err;   // [CLIENT_LOCKLESS_V1]''')
rep('''        { std::lock_guard<std::mutex> g(p->m); if (a) p->answer = *a; p->err = err; p->done = true; } p->cv.notify_all();''',
    '''        if (p->done.load(std::memory_order_acquire)) return;   // [CLIENT_LOCKLESS_V1] once
        if (a) { p->answer = *a; }
        p->err = err; p->done.store(1, std::memory_order_release); futex_wake_all(p->done);''')

# ---- Impl: lock-free queues, the in-flight ring, atomic counters --------------------------------------------------------
rep('''    std::mutex q_m; std::condition_variable q_cv, room_cv; std::deque<Job> jobs; bool stop = false;
    using InFlight = Session::InFlight;
    std::mutex pend_m; uint32_t seq = 0; std::map<uint32_t, InFlight> inflight; std::string dead;
    std::mutex st_m; Stats st; std::thread sender, reader, data_reader;''',
'''    // [CLIENT_LOCKLESS_V1] fan-in: two lock-free MPSC lists (resends first), a bounded count, a sleep word for the sender
    struct JobNode { std::atomic<JobNode*> next{nullptr}; Job j; };
    struct JobList {
        JobNode stub; std::atomic<JobNode*> head{&stub}; JobNode* tail = &stub;
        void push(JobNode* n) { n->next.store(nullptr, std::memory_order_relaxed); JobNode* prev = head.exchange(n, std::memory_order_acq_rel); prev->next.store(n, std::memory_order_release); }
        JobNode* pop() {                                   // the sender only
            JobNode* t = tail; JobNode* next = t->next.load(std::memory_order_acquire);
            if (t == &stub) { if (!next) return nullptr; tail = next; t = next; next = next->next.load(std::memory_order_acquire); }
            if (next) { tail = next; return t; }
            if (t != head.load(std::memory_order_acquire)) return nullptr;
            stub.next.store(nullptr, std::memory_order_relaxed); JobNode* prev = head.exchange(&stub, std::memory_order_acq_rel); prev->next.store(&stub, std::memory_order_release);
            next = t->next.load(std::memory_order_acquire); if (next) { tail = next; return t; }
            return nullptr;
        }
    } jobq, resendq;
    std::atomic<uint32_t> work{0};                       // bumped on every push; the sender sleeps on it
    std::atomic<uint32_t> queued{0};                     // jobs not yet taken by the sender (QUEUE_MAX)
    std::atomic<bool> stop{false};
    void enqueue(Job&& j, bool resend) {
        JobNode* n = new JobNode; n->j = std::move(j);
        if (!resend) queued.fetch_add(1, std::memory_order_acq_rel);
        (resend ? resendq : jobq).push(n);
        work.fetch_add(1, std::memory_order_release); futex_wake_all(work);
    }
    using InFlight = Session::InFlight;
    // [CLIENT_LOCKLESS_V1] in flight: a ring indexed by sequence number; the sender stores, the reader takes
    static constexpr uint32_t RING = 1u << 16;
    std::unique_ptr<std::atomic<InFlight*>[]> ring{new std::atomic<InFlight*>[RING]};
    std::atomic<uint32_t> ring_free{0};                  // bumped when a slot is taken; a sender waiting for its slot sleeps on it
    uint32_t seq = 0;                                    // the sender's
    std::atomic<bool> dead_claim{false}, dead_set{false}; std::string dead;   // dead is written once, before dead_set
    std::string dead_why() const { return dead_set.load(std::memory_order_acquire) ? dead : std::string(); }
    // counters: atomics; Stats is assembled from them
    std::atomic<uint64_t> c_coalesced{0}, c_posted{0}, c_posts_refused{0}, c_data_frames{0}, c_data_bytes{0};
    std::thread sender, reader, data_reader;''')

rep('''    static const size_t QUEUE_MAX = 4096;                // new requests beyond this wait, as TCP would hold them''',
    '''    static const uint32_t QUEUE_MAX = 4096;              // new requests beyond this wait, as TCP would hold them''')

# ---- constructor / destructor ---------------------------------------------------------------------------------------
rep('''    d_->eng.reset(new fnwp::ClientEngine(host, ip, api_));''',
    '''    d_->eng.reset(new fnwp::ClientEngine(host, ip, api_));
    for (uint32_t i = 0; i < Impl::RING; ++i) d_->ring[i].store(nullptr, std::memory_order_relaxed);   // [CLIENT_LOCKLESS_V1]''')
rep('''    { std::lock_guard<std::mutex> g(d_->q_m); d_->stop = true; } d_->q_cv.notify_all(); d_->room_cv.notify_all();''',
    '''    d_->stop.store(true, std::memory_order_release); d_->work.fetch_add(1); futex_wake_all(d_->work);
    d_->queued.store(0); futex_wake_all(d_->queued); d_->ring_free.fetch_add(1); futex_wake_all(d_->ring_free);''')

# ---- the sender -----------------------------------------------------------------------------------------------------------
rep('''        Impl::Job j;
        { std::unique_lock<std::mutex> l(d_->q_m); d_->q_cv.wait(l, [&] { return d_->stop || !d_->jobs.empty(); });
          if (d_->jobs.empty()) return;
          j = std::move(d_->jobs.front()); d_->jobs.pop_front(); }
        d_->room_cv.notify_one();''',
'''        Impl::Job j;
        {   // [CLIENT_LOCKLESS_V1] resends first (they are made in a call's turn), then new requests; sleep on the work word
            Impl::JobNode* n = nullptr; bool resend = false;
            for (;;) {
                const uint32_t seen = d_->work.load(std::memory_order_acquire);
                if ((n = d_->resendq.pop())) { resend = true; break; }
                if ((n = d_->jobq.pop())) break;
                if (d_->stop.load(std::memory_order_acquire)) return;
                syscall(SYS_futex, reinterpret_cast<uint32_t*>(&d_->work), FUTEX_WAIT_PRIVATE, seen, nullptr, nullptr, 0);
            }
            j = std::move(n->j); delete n;
            if (!resend && d_->queued.fetch_sub(1, std::memory_order_acq_rel) == Impl::QUEUE_MAX) futex_wake_all(d_->queued);
        }''')
rep('''        catch (const std::exception& e) { Impl::finish(j.waiter, nullptr, std::string("frogram: ") + e.what()); if (!j.waiter) { std::lock_guard<std::mutex> g(d_->st_m); ++d_->st.posts_refused; } continue; }''',
    '''        catch (const std::exception& e) { Impl::finish(j.waiter, nullptr, std::string("frogram: ") + e.what()); if (!j.waiter) d_->c_posts_refused.fetch_add(1); continue; }''')
rep('''        uint32_t seq = 0;
        { std::lock_guard<std::mutex> g(d_->pend_m);
          if (!d_->dead.empty()) why = d_->dead;
          else { seq = d_->seq++; d_->inflight[seq] = Impl::InFlight{o.ctx, j.waiter, j.ticket, j.in_turn}; } }''',
'''        uint32_t seq = 0;
        why = d_->dead_why();
        if (why.empty()) {                                // [CLIENT_LOCKLESS_V1] the slot for this sequence number
            seq = d_->seq++;
            std::atomic<Impl::InFlight*>& slot = d_->ring[seq & (Impl::RING - 1)];
            for (;;) {                                    // 65,536 unanswered: wait for this slot to be taken
                const uint32_t seen = d_->ring_free.load(std::memory_order_acquire);
                if (!slot.load(std::memory_order_acquire) || d_->stop.load()) break;
                syscall(SYS_futex, reinterpret_cast<uint32_t*>(&d_->ring_free), FUTEX_WAIT_PRIVATE, seen, nullptr, nullptr, 0);
            }
            Impl::InFlight* mine = new Impl::InFlight{o.ctx, j.waiter, j.ticket, j.in_turn};
            slot.store(mine, std::memory_order_release);
            if (!d_->dead_why().empty()) {                // died meanwhile: take it back if the reader's sweep did not
                Impl::InFlight* back = slot.exchange(nullptr, std::memory_order_acq_rel);
                if (back) { delete back; why = d_->dead_why(); }
            }
        }''')
rep('''                if (via_data) { d_->dsend->submit(seq, payload); std::lock_guard<std::mutex> g(d_->st_m); ++d_->st.data_frames; d_->st.data_bytes += payload.size(); }''',
    '''                if (via_data) { d_->dsend->submit(seq, payload); d_->c_data_frames.fetch_add(1); d_->c_data_bytes.fetch_add(payload.size()); }''')

# ---- apply_reply: counters; resend through the resend list ------------------------------------------------------------
rep('''            if (!inf.waiter) { std::lock_guard<std::mutex> g(d_->st_m); if (s.answer.status >= 400) ++d_->st.posts_refused; }''',
    '''            if (!inf.waiter && s.answer.status >= 400) d_->c_posts_refused.fetch_add(1);''')
rep('''        { std::lock_guard<std::mutex> g(d_->q_m); d_->jobs.push_front(std::move(j)); } d_->q_cv.notify_one();''',
    '''        d_->enqueue(std::move(j), true);                 // [CLIENT_LOCKLESS_V1] ahead of new requests''')
rep('''        if (!inf.waiter) { std::lock_guard<std::mutex> g(d_->st_m); ++d_->st.posts_refused; }
        Impl::finish(inf.waiter, nullptr, std::string("frogram: ") + e.what());''',
    '''        if (!inf.waiter) d_->c_posts_refused.fetch_add(1);
        Impl::finish(inf.waiter, nullptr, std::string("frogram: ") + e.what());''')

# ---- the reader ---------------------------------------------------------------------------------------------------------
rep('''                { std::lock_guard<std::mutex> g(d_->st_m); d_->st.data_bytes += f.size() + 4; }''', '''                d_->c_data_bytes.fetch_add(f.size() + 4);''')
rep('''                { std::lock_guard<std::mutex> g(d_->st_m); ++d_->st.data_frames; }''', '''                d_->c_data_frames.fetch_add(1);''')
rep('''            Impl::InFlight inf;
            { std::lock_guard<std::mutex> g(d_->pend_m); auto it = d_->inflight.find(seq);
              if (it == d_->inflight.end()) throw Refused("reply tagged a seq that was never sent");
              inf = it->second; d_->inflight.erase(it); }''',
'''            Impl::InFlight inf;
            {   // [CLIENT_LOCKLESS_V1] take this sequence number's slot
                Impl::InFlight* got = d_->ring[seq & (Impl::RING - 1)].exchange(nullptr, std::memory_order_acq_rel);
                if (!got) throw Refused("reply tagged a seq that was never sent");
                inf = std::move(*got); delete got;
                d_->ring_free.fetch_add(1, std::memory_order_release); futex_wake_all(d_->ring_free);
            }''')
rep('''    std::map<uint32_t, Impl::InFlight> lost;
    { std::lock_guard<std::mutex> g(d_->pend_m); if (d_->dead.empty()) d_->dead = why.empty() ? "connection closed" : why; lost.swap(d_->inflight); }
    d_->landed.close();
    for (auto& kv : lost) Impl::finish(kv.second.waiter, nullptr, d_->dead);''',
'''    {   // [CLIENT_LOCKLESS_V1] dead: one reader claims it and writes the reason, then the flag; the other waits for it
        bool claim = false;
        if (d_->dead_claim.compare_exchange_strong(claim, true, std::memory_order_acq_rel)) {
            d_->dead = why.empty() ? "connection closed" : why; d_->dead_set.store(true, std::memory_order_release);
        } else while (!d_->dead_set.load(std::memory_order_acquire)) std::this_thread::yield();
    }
    d_->landed.close();
    for (uint32_t i = 0; i < Impl::RING; ++i)
        if (Impl::InFlight* got = d_->ring[i].exchange(nullptr, std::memory_order_acq_rel)) { Impl::finish(got->waiter, nullptr, d_->dead); delete got; }
    d_->ring_free.fetch_add(1); futex_wake_all(d_->ring_free);''')

# ---- call / post / stats --------------------------------------------------------------------------------------------------
rep('''    if (joined) { std::lock_guard<std::mutex> g(d_->st_m); ++d_->st.coalesced; }
    if (!joined)
    { std::unique_lock<std::mutex> l(d_->q_m);
      { std::string dead; { std::lock_guard<std::mutex> g(d_->pend_m); dead = d_->dead; }
        if (!dead.empty()) { Impl::finish(p, nullptr, dead); throw Unreachable(dead); } }
      d_->room_cv.wait(l, [&] { return d_->stop || d_->jobs.size() < Impl::QUEUE_MAX; });
      if (d_->stop) { Impl::finish(p, nullptr, "session shut down"); throw Unreachable("session shut down"); }
      Impl::Job j; j.method = method; j.path = path; j.body = body; j.waiter = p; d_->jobs.push_back(std::move(j)); }
    d_->q_cv.notify_one();
    std::unique_lock<std::mutex> l(p->m);
    if (!p->cv.wait_for(l, std::chrono::duration<double>(timeout_s), [&] { return p->done; })) throw Unreachable("no reply within the timeout");''',
'''    if (joined) d_->c_coalesced.fetch_add(1);
    if (!joined) {                                       // [CLIENT_LOCKLESS_V1]
        { const std::string dead = d_->dead_why(); if (!dead.empty()) { Impl::finish(p, nullptr, dead); throw Unreachable(dead); } }
        for (;;) { const uint32_t q = d_->queued.load(std::memory_order_acquire);
                   if (q < Impl::QUEUE_MAX || d_->stop.load()) break;
                   syscall(SYS_futex, reinterpret_cast<uint32_t*>(&d_->queued), FUTEX_WAIT_PRIVATE, q, nullptr, nullptr, 0); }
        if (d_->stop.load()) { Impl::finish(p, nullptr, "session shut down"); throw Unreachable("session shut down"); }
        Impl::Job j; j.method = method; j.path = path; j.body = body; j.waiter = p; d_->enqueue(std::move(j), false);
    }
    {   const auto deadline = std::chrono::steady_clock::now() + std::chrono::duration<double>(timeout_s > 1e9 ? 1e9 : timeout_s);
        while (!p->done.load(std::memory_order_acquire)) {
            const double left = std::chrono::duration<double>(deadline - std::chrono::steady_clock::now()).count();
            if (left <= 0) throw Unreachable("no reply within the timeout");
            futex_wait_for(p->done, 0, left);
        } }''')
rep('''    { std::unique_lock<std::mutex> l(d_->q_m);
      { std::lock_guard<std::mutex> g(d_->pend_m); if (!d_->dead.empty()) throw Unreachable(d_->dead); }
      d_->room_cv.wait(l, [&] { return d_->stop || d_->jobs.size() < Impl::QUEUE_MAX; });
      if (d_->stop) throw Unreachable("session shut down");
      Impl::Job j; j.method = method; j.path = path; j.body = body; d_->jobs.push_back(std::move(j)); }
    d_->q_cv.notify_one();
    std::lock_guard<std::mutex> g(d_->st_m); ++d_->st.posted;''',
'''    {   const std::string dead = d_->dead_why(); if (!dead.empty()) throw Unreachable(dead); }   // [CLIENT_LOCKLESS_V1]
    for (;;) { const uint32_t q = d_->queued.load(std::memory_order_acquire);
               if (q < Impl::QUEUE_MAX || d_->stop.load()) break;
               syscall(SYS_futex, reinterpret_cast<uint32_t*>(&d_->queued), FUTEX_WAIT_PRIVATE, q, nullptr, nullptr, 0); }
    if (d_->stop.load()) throw Unreachable("session shut down");
    Impl::Job j; j.method = method; j.path = path; j.body = body; d_->enqueue(std::move(j), false);
    d_->c_posted.fetch_add(1);''')
rep('''    Stats s; { std::lock_guard<std::mutex> g(d_->st_m); s = d_->st; }''',
    '''    Stats s;                                             // [CLIENT_LOCKLESS_V1] from the atomic counters
    s.coalesced = d_->c_coalesced.load(); s.posted = d_->c_posted.load(); s.posts_refused = d_->c_posts_refused.load();
    s.data_frames = d_->c_data_frames.load(); s.data_bytes = d_->c_data_bytes.load();''')
open(p, 'w').write(s)
print("client lockless applied")
