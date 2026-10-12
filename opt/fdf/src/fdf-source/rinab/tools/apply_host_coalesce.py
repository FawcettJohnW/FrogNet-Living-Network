"""apply_host_coalesce.py RAM_SERVER_CPP -- [HOST_COALESCE_V1] the RAM host runs ONE execution per request in flight,
across sessions, as the daemon does (daemon/engine/session.py, REQUEST COALESCING: "One upstream execution per
req_hash. If two proxies send the same req_hash while the first is still executing, the second waits and gets the same
reply"). Here the second does not wait: it leaves a continuation and its session goes on; when the first execution
answers, every session that asked gets that answer, each ENCODED BY ITS OWN SESSION's writer (each holds its own
references -- the daemon shares reply bytes because its references are per proxy too; here the answer is shared and
each reply is made for its own session).

The key is the request hash the FNWP frame carries (16 bytes); a request without one (RAW framing without a hash) is
not coalesced. Loops are the existing loop detector's ([LOOP_DETECT_9009_V1]); nothing is added here for them (ruling,
2026-10-05).

Lock-free: a table of 4,096 slots, each an atomic pointer to the entry of a request in flight; an entry's waiters are a
Treiber stack sealed by the first execution when it answers; a waiter that finds the stack sealed runs its own
execution. Entries are freed through the platform's epoch reclamation (ribbit::Ebr), so a session still reading one is
never handed freed memory. Applied at build after apply_session_order.py."""
import sys
p = sys.argv[1]; s = open(p).read()
def rep(a, b, n=1):
    global s
    assert s.count(a) == n, (a[:90], s.count(a)); s = s.replace(a, b)

rep('''void engine_frame(std::shared_ptr<Session> s, uint32_t seq, const std::string& f) {''',
'''// [HOST_COALESCE_V1] requests in flight, by request hash
struct CoWaiter { std::shared_ptr<Session> s; uint32_t seq; fnwp::ServerEngine::Work w; CoWaiter* next = nullptr; };
struct CoEntry { std::string key; std::atomic<CoWaiter*> waiters{nullptr}; };
static CoWaiter* const CO_SEALED = reinterpret_cast<CoWaiter*>(uintptr_t(1));
struct CoTable {
    static constexpr size_t N = 4096;
    std::atomic<const CoEntry*> slot[N];
    ribbit::Ebr ebr;
    std::atomic<uint64_t> led{0}, joined_n{0}, alone{0};      // executions led, requests answered by another's execution, ran alone
    CoTable() { for (auto& x : slot) x.store(nullptr, std::memory_order_relaxed); }
    static size_t home(const std::string& k) { uint64_t h = 1469598103934665603ull; for (unsigned char c : k) { h ^= c; h *= 1099511628211ull; } return size_t(h); }
    // the first to claim a key leads (true); a later identical request joins as a waiter (false, joined); if the
    // leader has already answered (sealed), or the table is full near home, the request runs on its own (false, !joined)
    bool claim(const std::string& key, CoWaiter* me, const CoEntry*& mine, bool& joined) {
        auto g = ebr.guard(); joined = false; const size_t h0 = home(key);
        for (size_t i = 0; i < 32; ++i) {
            std::atomic<const CoEntry*>& sl = slot[(h0 + i) & (N - 1)];
            const CoEntry* e = g.protect(sl);
            if (!e) {
                CoEntry* n = new CoEntry; n->key = key; const CoEntry* expect = nullptr;
                if (sl.compare_exchange_strong(expect, n, std::memory_order_acq_rel)) { mine = n; return true; }
                delete n; e = expect; if (!e) continue;
            }
            if (e->key != key) continue;
            CoEntry* ce = const_cast<CoEntry*>(e); CoWaiter* head = ce->waiters.load(std::memory_order_acquire);
            for (;;) {
                if (head == CO_SEALED) return false;              // answered already: run it ourselves
                me->next = head;
                if (ce->waiters.compare_exchange_weak(head, me, std::memory_order_acq_rel)) { joined = true; return false; }
            }
        }
        return false;                                               // no slot near home: run it ourselves
    }
    // the leader, with its answer: seal, unpublish, hand every waiter the answer
    void answer(const CoEntry* mine, const Reply& r) {
        CoEntry* ce = const_cast<CoEntry*>(mine);
        CoWaiter* list = ce->waiters.exchange(CO_SEALED, std::memory_order_acq_rel);
        const size_t h0 = home(ce->key);
        for (size_t i = 0; i < 32; ++i) { std::atomic<const CoEntry*>& sl = slot[(h0 + i) & (N - 1)]; const CoEntry* expect = mine;
            if (sl.compare_exchange_strong(expect, nullptr, std::memory_order_acq_rel)) break; }
        ebr.retire(mine);
        while (list) { CoWaiter* nx = list->next; list->s->finish_and_send(list->seq, list->w, r); delete list; list = nx; }
    }
};
CoTable& co_table() { static CoTable* t = [] { CoTable* c = new CoTable;
    std::atexit([] { CoTable& x = co_table(); if (getenv("RINA_DIAG")) fprintf(stderr, "[HOST_COALESCE_V1] executions led %llu, requests answered by another's execution %llu, ran alone %llu\\n",
        (unsigned long long)x.led.load(), (unsigned long long)x.joined_n.load(), (unsigned long long)x.alone.load()); });
    return c; }(); return *t; }
void engine_frame(std::shared_ptr<Session> s, uint32_t seq, const std::string& f) {''')

rep('''    auto run = [s, seq](fnwp::ServerEngine::Work w) {
        tl_session_id = reinterpret_cast<uint64_t>(s.get());
        Reply r; if (api(w.method, w.path, w.body, false, r)) { s->finish_and_send(seq, w, r); return; }
        std::thread([=] { tl_session_id = reinterpret_cast<uint64_t>(s.get()); Reply rr; api(w.method, w.path, w.body, true, rr); s->finish_and_send(seq, w, rr); }).detach();   // a parked read
    };''',
'''    auto run = [s, seq](fnwp::ServerEngine::Work w) {
        tl_session_id = reinterpret_cast<uint64_t>(s.get());
        // [HOST_COALESCE_V1] one execution per request in flight: join an identical one, or lead
        const CoEntry* mine = nullptr; bool joined = false;
        if (w.req_hash.size() == 16) {
            CoWaiter* me = new CoWaiter{s, seq, w};
            const bool lead = co_table().claim(w.req_hash, me, mine, joined);
            if (joined) { co_table().joined_n.fetch_add(1, std::memory_order_relaxed); return; }   // answered with the leader's answer, by our writer
            delete me; if (!lead) mine = nullptr;
            (lead ? co_table().led : co_table().alone).fetch_add(1, std::memory_order_relaxed);
        }
        Reply r; if (api(w.method, w.path, w.body, false, r)) { if (mine) co_table().answer(mine, r); s->finish_and_send(seq, w, r); return; }
        std::thread([=] { tl_session_id = reinterpret_cast<uint64_t>(s.get()); Reply rr; api(w.method, w.path, w.body, true, rr);
                          if (mine) co_table().answer(mine, rr); s->finish_and_send(seq, w, rr); }).detach();   // a parked read
    };''')
if '#include "ebr.hpp"' not in s:
    rep('#include "lisp_engine.hpp"\n', '#include "lisp_engine.hpp"\n#include "ebr.hpp"\n')
open(p, 'w').write(s)
print("host coalesce applied")
