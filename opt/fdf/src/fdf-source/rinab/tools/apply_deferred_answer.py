"""apply_deferred_answer.py RAM_SERVER_CPP -- [DEFERRED_ANSWER_V1] a held read parks as a CONTINUATION, not a thread.

Before: a request that had to wait (a held read: a program's wait with nothing yet addressed to it, a sender's credit
wait) made the host start a thread for it (std::thread(...).detach()), which slept until its answer existed. One
thread per held read, made and destroyed on every round trip -- and every thread that ever touched the epoch
reclamation stayed registered with it, so its cost grew with every round trip (measured: rr fell from 1,500 to 570
transactions/s, perf's delivery rate fell by two thirds over a run).

Now: a region operation that cannot answer yet calls rina_defer(), which hands it this request's continuation (its
session, its sequence number, its decoded work, its coalescing entry) and marks the request answered-later; the host
then sends nothing. Whoever makes the answer exist -- the producer of the event, the receiver whose taking frees credit
-- calls rina_answer(continuation, body), which sends it through the session's writer exactly as an immediate answer
would go (and answers any identical requests coalesced onto it). No thread waits anywhere.
Applied at build after apply_host_coalesce.py."""
import sys
p = sys.argv[1]; s = open(p).read()
def rep(a, b, n=1):
    global s
    assert s.count(a) == n, (a[:90], s.count(a)); s = s.replace(a, b)
rep('''void engine_frame(std::shared_ptr<Session> s, uint32_t seq, const std::string& f) {''',
'''// [DEFERRED_ANSWER_V1] a request's continuation, handed to the region when it cannot answer yet
struct RinaDeferred { std::shared_ptr<Session> s; uint32_t seq; fnwp::ServerEngine::Work w; const CoEntry* mine; };
static thread_local RinaDeferred* tl_current = nullptr;   // the request this thread is executing (set around api())
static thread_local bool tl_deferred = false;             // the region took its continuation
}  // namespace
void* rina_defer() { if (!tl_current) return nullptr; tl_deferred = true; return new RinaDeferred(*tl_current); }
void rina_answer(void* h, const std::string& body) {
    RinaDeferred* d = static_cast<RinaDeferred*>(h); if (!d) return;
    Reply r{200, body};
    if (d->mine) co_table().answer(d->mine, r);
    d->s->finish_and_send(d->seq, d->w, r);
    delete d;
}
namespace {
void engine_frame(std::shared_ptr<Session> s, uint32_t seq, const std::string& f) {''')
rep('''        Reply r; if (api(w.method, w.path, w.body, false, r)) { if (mine) co_table().answer(mine, r); s->finish_and_send(seq, w, r); return; }''',
'''        RinaDeferred here{s, seq, w, mine}; tl_current = &here; tl_deferred = false;   // [DEFERRED_ANSWER_V1]
        Reply r; const bool done = api(w.method, w.path, w.body, false, r); tl_current = nullptr;
        if (tl_deferred) { tl_deferred = false; return; }   // the region answers it later (rina_answer)
        if (done) { if (mine) co_table().answer(mine, r); s->finish_and_send(seq, w, r); return; }''')
open(p, 'w').write(s)
print("deferred answer applied")
