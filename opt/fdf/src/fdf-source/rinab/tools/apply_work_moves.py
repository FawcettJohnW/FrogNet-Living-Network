"""apply_work_moves.py RAM_SERVER_CPP -- [WORK_MOVED_NOT_COPIED_V1] a request's decoded Work (its hash, frame, method,
path and body strings) is MOVED through the host, not copied. Before, every request copied it four or five times on its
way: into the session-order lambda, into run()'s parameter, into a coalescing waiter that was deleted again whenever the
request led, into the continuation kept for a deferred answer whether or not it was deferred, and into the writer's
queue item. Now: moved into the lambda and into run(); a coalescing waiter takes it and gives it back if the request
leads; the continuation holds a pointer and copies the Work only if the region actually defers; the writer's item takes
it by move at the request's last use. Applied at build after apply_deferred_answer.py."""
import sys
p = sys.argv[1]; s = open(p).read()
def rep(a, b, n=1):
    global s
    assert s.count(a) == n, (a[:90], s.count(a)); s = s.replace(a, b)
rep('''    void finish_and_send(uint32_t seq, const fnwp::ServerEngine::Work& w, const Reply& r) {   // [HOST_FANOUT_V1] queued''',
    '''    void finish_and_send(uint32_t seq, fnwp::ServerEngine::Work&& w, const Reply& r) {   // [WORK_MOVED_NOT_COPIED_V1] its last use
        if (closing.load(std::memory_order_acquire)) return;
        OutItem* o = new OutItem; o->seq = seq; o->engine = true; o->w = std::move(w); o->r = r; outq.push(o); }
    void finish_and_send(uint32_t seq, const fnwp::ServerEngine::Work& w, const Reply& r) {   // [HOST_FANOUT_V1] queued''')
rep('''struct RinaDeferred { std::shared_ptr<Session> s; uint32_t seq; fnwp::ServerEngine::Work w; const CoEntry* mine; };
static thread_local RinaDeferred* tl_current = nullptr;   // the request this thread is executing (set around api())''',
    '''struct RinaDeferred { std::shared_ptr<Session> s; uint32_t seq; fnwp::ServerEngine::Work w; const CoEntry* mine; };
struct RinaCurrent { const std::shared_ptr<Session>* s; uint32_t seq; const fnwp::ServerEngine::Work* w; const CoEntry* mine; };   // [WORK_MOVED_NOT_COPIED_V1]
static thread_local RinaCurrent* tl_current = nullptr;    // the request this thread is executing (set around api())''')
rep('''void* rina_defer() { if (!tl_current) return nullptr; tl_deferred = true; return new RinaDeferred(*tl_current); }''',
    '''void* rina_defer() { if (!tl_current) return nullptr; tl_deferred = true;   // the Work is copied only here, when deferred
    return new RinaDeferred{*tl_current->s, tl_current->seq, *tl_current->w, tl_current->mine}; }''')
rep('''            CoWaiter* me = new CoWaiter{s, seq, w};
            const bool lead = co_table().claim(w.req_hash, me, mine, joined);
            if (joined) { co_table().joined_n.fetch_add(1, std::memory_order_relaxed); return; }   // answered with the leader's answer, by our writer
            delete me; if (!lead) mine = nullptr;''',
    '''            const std::string key = w.req_hash;
            CoWaiter* me = new CoWaiter{s, seq, std::move(w)};   // [WORK_MOVED_NOT_COPIED_V1] the waiter takes it...
            const bool lead = co_table().claim(key, me, mine, joined);
            if (joined) { co_table().joined_n.fetch_add(1, std::memory_order_relaxed); return; }   // answered with the leader's answer, by our writer
            w = std::move(me->w); delete me; if (!lead) mine = nullptr;   // ...and gives it back when this request runs''')
rep('''        RinaDeferred here{s, seq, w, mine}; tl_current = &here; tl_deferred = false;   // [DEFERRED_ANSWER_V1]''',
    '''        RinaCurrent here{&s, seq, &w, mine}; tl_current = &here; tl_deferred = false;   // [DEFERRED_ANSWER_V1] [WORK_MOVED_NOT_COPIED_V1]''')
rep('''        if (done) { if (mine) co_table().answer(mine, r); s->finish_and_send(seq, w, r); return; }''',
    '''        if (done) { if (mine) co_table().answer(mine, r); s->finish_and_send(seq, std::move(w), r); return; }''')
rep('''    run_in_order(s, [run, w] { run(w); });                   // [SESSION_ORDER_V1] in this session's order; sessions in parallel''',
    '''    run_in_order(s, [run, w = std::move(w)]() mutable { run(std::move(w)); });   // [SESSION_ORDER_V1] [WORK_MOVED_NOT_COPIED_V1]''')
open(p, 'w').write(s)
print("work moves applied")
