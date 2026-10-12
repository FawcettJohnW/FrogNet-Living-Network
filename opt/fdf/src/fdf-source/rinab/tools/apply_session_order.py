"""apply_session_order.py RAM_SERVER_CPP -- [SESSION_ORDER_V1] a session's requests execute in the order they arrived;
different sessions execute in parallel on the pool.

Found by measuring: with every request of a session free to run on any worker ([HOST_FANOUT_V1] as first built), a
program's writes could be applied out of the order it made them. rinaperf's perf test writes 20,000 SDUs on its data
flow, then its "stop" on the control flow; the stop could be applied before the last SDUs, so the receiver stopped
counting early (19,986-19,998 of 20,000 in some runs; the region's own record showed every SDU taken -- late, not lost).
A program is one participant: what it writes is seen in the order it wrote it.

Now each session is an actor on the pool: its tasks go onto its own lock-free MPSC list; the first task to find the
session idle schedules ONE drain of it on the work ring; the drain runs the session's tasks in arrival order, then goes
idle and re-checks. A held read (an op that waits) still parks on its own thread, as before, so it never blocks the
session's order. Applied at build after apply_host_fanout.py and apply_engine_lockless.py."""
import sys
p = sys.argv[1]; s = open(p).read()
def rep(a, b, n=1):
    global s
    assert s.count(a) == n, (a[:90], s.count(a)); s = s.replace(a, b)

rep('''    OutQueue outq; std::atomic<bool> closing{false}; std::thread writer;''',
'''    OutQueue outq; std::atomic<bool> closing{false}; std::thread writer;
    // [SESSION_ORDER_V1] this session's tasks, in arrival order; at most one drain of them runs at a time
    struct Task { std::atomic<Task*> next{nullptr}; std::function<void()> f; };
    Task tstub; std::atomic<Task*> thead{&tstub}; Task* ttail = &tstub; std::atomic<bool> scheduled{false};
    void tpush(Task* n) { n->next.store(nullptr, std::memory_order_relaxed); Task* prev = thead.exchange(n, std::memory_order_acq_rel); prev->next.store(n, std::memory_order_release); }
    Task* tpop() {                                            // the one drain only
        Task* t = ttail; Task* next = t->next.load(std::memory_order_acquire);
        if (t == &tstub) { if (!next) return nullptr; ttail = next; t = next; next = next->next.load(std::memory_order_acquire); }
        if (next) { ttail = next; return t; }
        if (t != thead.load(std::memory_order_acquire)) return nullptr;
        tstub.next.store(nullptr, std::memory_order_relaxed); Task* prev = thead.exchange(&tstub, std::memory_order_acq_rel); prev->next.store(&tstub, std::memory_order_release);
        next = t->next.load(std::memory_order_acquire); if (next) { ttail = next; return t; }
        return nullptr;
    }
    std::atomic<uint64_t> tpushed{0}; uint64_t tpopped = 0;   // tpopped: only the drain that holds `scheduled`
''')
rep('''struct Session {''', '''struct Session;
void run_in_order(const std::shared_ptr<Session>& s, std::function<void()> f);
struct Session {''')
# both frame paths hand their execution to the session's order instead of the bare pool
rep('''    work_ring().push([run, w] { run(w); });                   // [HOST_FANOUT_V1] executed by the pool, in parallel''',
    '''    run_in_order(s, [run, w] { run(w); });                   // [SESSION_ORDER_V1] in this session's order; sessions in parallel''')
rep('''    work_ring().push([=] {                                    // [HOST_FANOUT_V1] executed by the pool, in parallel''',
    '''    run_in_order(s, [=] {                                    // [SESSION_ORDER_V1] in this session's order; sessions in parallel''')
# the drain
rep('''void engine_frame(''', '''// [SESSION_ORDER_V1] push the task; if the session was idle, schedule one drain of it on the pool
void run_in_order(const std::shared_ptr<Session>& s, std::function<void()> f) {
    Session::Task* t = new Session::Task; t->f = std::move(f); s->tpush(t);
    s->tpushed.fetch_add(1, std::memory_order_seq_cst);       // counted BEFORE trying to schedule (see the drain's check)
    bool idle = false;
    if (s->scheduled.compare_exchange_strong(idle, true, std::memory_order_seq_cst)) {
        work_ring().push([s] {
            for (;;) {
                Session::Task* n = nullptr;
                while (s->tpopped != s->tpushed.load(std::memory_order_seq_cst)) {   // every counted task is (being) linked
                    if ((n = s->tpop())) { n->f(); delete n; ++s->tpopped; }
                }
                s->scheduled.store(false, std::memory_order_seq_cst);
                // a task counted after the last check: its pusher's schedule attempt may have failed while we held the
                // flag -- take the flag back and drain it (if someone else took it, they will)
                if (s->tpushed.load(std::memory_order_seq_cst) == s->tpopped) return;
                bool idle2 = false;
                if (!s->scheduled.compare_exchange_strong(idle2, true, std::memory_order_seq_cst)) return;
            }
        });
    }
}
void engine_frame(''')
open(p, 'w').write(s)
print("session order applied")
